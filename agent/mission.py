"""Mail Adagio run by Orchestra: one mission, contract 1, protocol stdio-json.

In:  one JSON line per message on stdin. The first is the mission; the next ones are Orchestra's
     messages (arret, pause, reprendre, validation).
Out: one JSON line per message on stdout: {"evenement": {...}} while it works, then {"compte_rendu": {...}}.

Three kinds of task (taches[].type in manifeste.json):
- trier-mails: sort the next batch of the inbox and apply it (labels + out of the Inbox), undoable;
- apercu-tri: read and classify recent mails, change nothing in Gmail;
- demande: a request in your own words (agent mode), the model picks tools of tools.py.
Each mission writes its report in the project folder (format_retour.destination, never over a file).

The model is Orchestra's gate only (ORCHESTRA_MODELE_URL, ORCHESTRA_JETON): no other model, no fallback.
The Trash, restoring, unsubscribing and sending mail are never done in a mission.
Events and the report are in French, as Orchestra asks.
"""
import hashlib
import json
import os
import queue
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import ask as agent_ask
from . import config, core, demo, models, tools
from .mail import Gmail

AGENT_ID = "mail-adagio"
TACHES = ("trier-mails", "apercu-tri", "demande")
REQUIRED = ("version", "id", "agent_id", "projet", "but", "critere_reussite", "contexte", "limites",
            "format_retour", "validation", "cree_le")
EVERY = 10  # an "etape" event every 10 mails


class Arret(Exception):
    """Orchestra asked to stop: the current step is finished, the mission ends cleanly."""


class Refus(Exception):
    """The mission cannot run as asked: etat + motif."""

    def __init__(self, etat, motif):
        super().__init__(motif)
        self.etat, self.motif = etat, motif


def maintenant():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Session:
    """One mission: its messages from Orchestra, its events, what it declares."""

    def __init__(self, out=None):
        self.out = out or sys.stdout
        self.lock = threading.Lock()
        self.messages = queue.Queue()
        self.mission = {}
        self.actions = []
        self.stopped_at = None

    # -- talking to Orchestra ----------------------------------------------------------------------
    def send(self, obj):
        with self.lock:
            self.out.write(json.dumps(obj, ensure_ascii=False) + "\n")
            self.out.flush()

    def event(self, kind, message, **extra):
        self.send({"evenement": {"mission_id": str(self.mission.get("id", "")), "agent_id": AGENT_ID,
                                 "type": kind, "message": message, "t": maintenant(), **extra}})

    def listen(self, stream):
        for line in stream:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict):
                self.messages.put(msg)

    def between(self, etape):
        """Between two steps: a stop ends the mission cleanly, a pause waits for reprendre or arret."""
        while not self.messages.empty():
            msg = self.messages.get()
            if msg.get("type") == "arret":
                raise Arret(etape)
            if msg.get("type") == "pause":
                self.event("en_pause", f"En pause avant l'étape « {etape} »")
                while True:
                    msg = self.messages.get()
                    if msg.get("type") == "arret":
                        raise Arret(etape)
                    if msg.get("type") == "reprendre":
                        self.event("etape", f"Reprise : {etape}")
                        break

    def declare(self, action, detail, raison):
        self.actions.append({"t": maintenant(), "action": action, "detail": detail, "raison": raison, "essai": 1})


# -- the mission's settings ------------------------------------------------------------------------
def check_mission(m):
    """The mission as Orchestra sends it (contract 1). Refus("echec", ...) naming what is wrong."""
    if not isinstance(m, dict):
        raise Refus("echec", "mission mal formée : un objet JSON est attendu")
    for field in REQUIRED:
        if field not in m:
            raise Refus("echec", f"mission mal formée : champ « {field} » absent")
    if m["version"] != 1:
        raise Refus("echec", f"mission mal formée : version {m['version']} inconnue (contrat 1 attendu)")
    if m["agent_id"] != AGENT_ID:
        raise Refus("echec", f"mission mal formée : agent_id « {m['agent_id']} » au lieu de « {AGENT_ID} »")
    if m.get("type_tache") not in TACHES:
        raise Refus("echec", f"mission mal formée : type_tache « {m.get('type_tache')} » inconnu "
                             f"(attendu : {', '.join(TACHES)})")
    donnees = m.get("donnees") or {}
    if not isinstance(donnees, dict):
        raise Refus("echec", "mission mal formée : « donnees » doit être un objet")
    if donnees.get("boite", "gmail") not in ("gmail", "demo"):
        raise Refus("echec", "mission mal formée : donnees.boite doit valoir « gmail » ou « demo »")
    if m["type_tache"] == "demande":
        if not str(donnees.get("texte") or "").strip():
            raise Refus("echec", "mission mal formée : donnees.texte absent (la demande à faire)")
    else:
        n = donnees.get("nombre", 50)
        top = 200 if m["type_tache"] == "apercu-tri" else 1000
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= top:
            raise Refus("echec", f"mission mal formée : donnees.nombre doit être un entier de 1 à {top}")
    fmt = m["format_retour"]
    if not isinstance(fmt, dict) or fmt.get("type") not in ("fichier", "texte"):
        raise Refus("echec", "mission mal formée : format_retour.type doit valoir « fichier » ou « texte »")
    projet = Path(str(m["projet"]))
    if not projet.is_dir():
        raise Refus("bloque", f"dossier du projet introuvable : {projet}")
    return donnees


def destination(m):
    """The report file, inside the project folder; never one that exists already. None for format texte."""
    fmt = m["format_retour"]
    if fmt["type"] == "texte":
        return None
    projet = Path(str(m["projet"])).resolve()
    name = str(fmt.get("destination") or f"mail-adagio-{m['type_tache']}-{datetime.now():%Y-%m-%d_%H%M}.md")
    target = (projet / name).resolve()
    if projet not in target.parents:
        raise Refus("echec", f"format_retour.destination « {name} » sort du dossier du projet")
    if target.suffix.lower() != ".md":
        raise Refus("echec", f"format_retour.destination « {name} » : un fichier .md est attendu")
    if target.exists():
        raise Refus("echec", f"format_retour.destination « {name} » existe déjà : rien n'est écrasé")
    return target


def memory_snapshot():
    """Fingerprint of every file of memoire/, to list the ones the mission changed."""
    out = {}
    if config.MEMOIRE.is_dir():
        for f in config.MEMOIRE.rglob("*"):
            if f.is_file():
                try:
                    out[f.relative_to(config.ROOT).as_posix()] = hashlib.sha256(f.read_bytes()).hexdigest()
                except OSError:
                    pass
    return out


def memory_prepare(donnees):
    """The settings and memory for this mission: memoire/ for Gmail, memoire/demo/ for the made-up mailbox.
    Returns (cfg, gmail_factory, config path, save to config.yaml)."""
    if donnees.get("boite") == "demo":
        folder = config.MEMOIRE / "demo"
        folder.mkdir(parents=True, exist_ok=True)
        core.MEMORY_PATH, core.LOG_PATH = folder / "adagio-demo.db", folder / "adagio-demo.log"
        own = folder / "config.yaml"  # a test may give its own settings (memory test)
        cfg = config.load(own) if own.exists() else config.yaml.safe_load(config.EXAMPLE.read_text(encoding="utf-8"))
        cfg["gmail"].update(email="demo@gmail.com", app_password="demo")
        config.check(cfg)
        demo.FakeIMAP.reset(copies=1)
        extra = folder / "boite.json"  # a test may add its own mails (trap mails)
        if extra.exists():
            demo.add_mails(json.loads(extra.read_text(encoding="utf-8")))
        return cfg, demo.gmail_factory, str(own), False
    path = config.DEFAULT_PATH
    created = config.ensure_file(path)
    if donnees.get("compte"):
        current = config.yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if (current.get("gmail") or {}).get("email") != donnees["compte"]:
            config.save_values(path, {"email": str(donnees["compte"]).strip()})
    cfg = config.load(path)
    if not cfg["gmail"].get("email"):
        raise Refus("bloque", "adresse Gmail inconnue : indiquez-la dans donnees.compte"
                    + (" (réglages créés dans memoire/config.yaml)" if created else ""))
    if not cfg["gmail"].get("app_password"):
        raise Refus("bloque", "mot de passe d'application Gmail absent du coffre-fort Windows "
                              "(orchestra/mail-adagio/gmail_mot_de_passe_application)")
    return cfg, Gmail, str(path), True


# -- the three tasks ---------------------------------------------------------------------------------
def progress_of(session, etape):
    """Called for each mail sorted: an event every 10 mails, pause and stop between two mails."""
    def progress(i, n, row):
        if not i:
            return None
        if i % EVERY == 0 or i == n:
            session.event("etape", f"{etape} : {i} mails sur {n}")
        try:
            session.between(f"{etape}, mail {i + 1} sur {n}")
        except Arret as a:
            session.stopped_at = str(a)
            return "stop"
        return None
    return progress


def failed(r):
    """A failed Mail Adagio action, said in French (the details stay as Mail Adagio wrote them)."""
    code = r.get("error_code") or ""
    head = {"gmail_login": "Gmail a refusé la connexion", "offline": "Gmail est injoignable",
            "ollama_down": "le modèle ne répond pas", "ollama_missing": "le modèle ne répond pas"}.get(code)
    if not head and ("model" in r.get("error", "").lower() or "gate" in r.get("error", "").lower()):
        head = "le modèle (porte d'Orchestra) n'a pas répondu"
    etat = "bloque" if code in ("gmail_login", "offline") else "echec"
    return Refus(etat, f"{head or 'échec de Mail Adagio'} : {r.get('error', '')}")


def trier(session, m, donnees, ctx):
    n = donnees.get("nombre", 50)
    session.between("lecture des mails")
    session.event("etape", f"Lecture et tri des {n} mails suivants de la boîte de réception")
    r = core.handle({"action": "plan", "scope": {"kind": "count", "value": n}, "batch_size": max(n, 50),
                     "act_alone": False, **ctx["request"]}, cfg=ctx["cfg"], gmail_factory=ctx["gmail"],
                    ask=ctx["ask"], progress=progress_of(session, "Tri"))
    if not r["ok"]:
        raise failed(r)
    plan = r.get("plan")
    if session.stopped_at:  # stopped while sorting: the plan made so far waits, nothing changed in Gmail
        raise Arret(session.stopped_at)
    if not plan:
        return {"texte": "Rien à trier : tous les mails demandés sont déjà triés.", "lignes": [],
                "rapport": rapport_vide(m), "controle": ["aucun mail à trier (avancement relu)"],
                "non_controle": []}
    session.between("application du lot dans Gmail")
    session.event("etape", f"Application du lot {plan['batch_id']} dans Gmail ({plan['processed']} mails)")
    a = core.handle({"action": "apply", "batch_id": plan["batch_id"], "batch_size": max(n, 50), **ctx["request"]},
                    cfg=ctx["cfg"], gmail_factory=ctx["gmail"])
    if not a["ok"]:
        raise failed(a)
    session.declare("action_imap", f"lot {plan['batch_id']} : libellés thème et statut posés sur {a['applied']} mails, "
                                   f"{a['moved']} sortis de la boîte de réception, {a['read']} marqués comme lus "
                                   f"(libellés créés dans Gmail s'ils manquaient)", m["but"])
    texte = (f"Lot {plan['batch_id']} trié : {a['applied']} mails libellés, {a['moved']} sortis de la boîte de "
             f"réception, {plan['to_check']} à vérifier. Annulable avec « Annuler » dans Mail Adagio.")
    return {"texte": texte, "rapport": rapport_tri(m, plan, a),
            "controle": [f"Gmail a accepté les changements de libellés de {a['applied']} mails"],
            "non_controle": ["justesse du thème et du statut de chaque mail (à vérifier : "
                             f"{plan['to_check']} marqués « To check »)"]}


def apercu(session, m, donnees, ctx):
    n = donnees.get("nombre", 20)
    session.between("lecture des mails")
    session.event("etape", f"Lecture et tri des {n} mails les plus récents, sans rien changer dans Gmail")
    r = core.handle({"action": "preview", "limit": n, **({"since_days": donnees["jours"]} if donnees.get("jours") else {}),
                     **ctx["request"]}, cfg=ctx["cfg"], gmail_factory=ctx["gmail"], ask=ctx["ask"],
                    progress=progress_of(session, "Aperçu"))
    if not r["ok"]:
        raise failed(r)
    if session.stopped_at:
        raise Arret(session.stopped_at)
    texte = (f"Aperçu de {r['processed']} mails : rien n'a été changé dans Gmail. "
             f"{r['to_check']} seraient à vérifier.")
    return {"texte": texte, "rapport": rapport_apercu(m, r),
            "controle": ["aucune écriture dans Gmail (lecture seule, BODY.PEEK : les mails restent non lus)"],
            "non_controle": ["justesse du thème et du statut proposés"]}


def demande(session, m, donnees, ctx):
    forbidden = tuple((m.get("limites") or {}).get("outils_interdits") or ())
    session.between("compréhension de la demande")
    session.event("etape", "Le modèle choisit les étapes de la demande")

    def between(n, action):
        session.between(f"étape {n} ({action})")
        session.event("etape", f"Étape {n} : {action}")
    try:
        out = agent_ask.request(ctx["cfg"], {"text": donnees["texte"], **ctx["request"]}, ctx["gmail"],
                                ask=ctx["ask"], between=between, forbidden=forbidden)
    except models.ModelError as e:
        raise Refus("echec", f"le modèle (porte d'Orchestra) n'a pas répondu : {e}")
    except ValueError as e:
        raise Refus("echec", f"demande refusée : {e}")
    for step in out["steps"]:
        tool = tools.TOOLS.get(step["action"])
        if step.get("ok") and tool and not tool.__name__.startswith("read_"):
            session.declare(tool.__name__, step.get("log") or step["action"], step.get("raison") or m["but"])
    if out.get("refused"):
        raise Refus("echec", f"demande refusée : {out['refused']}")
    failed_steps = [s for s in out["steps"] if not s.get("ok")]
    return {"texte": out["answer"], "rapport": rapport_demande(m, donnees["texte"], out),
            "partiel": failed_steps and len(failed_steps) < len(out["steps"]),
            "echec": failed_steps and len(failed_steps) == len(out["steps"]),
            "manquant": [f"étape {s['action']} : {s.get('error', 'échec')}" for s in failed_steps],
            "controle": [f"{sum(s.get('ok', False) for s in out['steps'])} étape(s) faite(s) sur {len(out['steps'])}, "
                         "chacune vérifiée par le code avant de s'exécuter"],
            "non_controle": ["que les étapes choisies par le modèle répondent bien à la demande"]}


TASKS = {"trier-mails": trier, "apercu-tri": apercu, "demande": demande}


# -- reports (Markdown, in French) -----------------------------------------------------------------
def cell(text, n=70):
    text = " ".join(str(text or "").split()).replace("|", "/")
    return text if len(text) <= n else text[:n - 1] + "…"


def entete(m, titre):
    return [f"# {titre}", "", f"Mission {m['id']} · {datetime.now():%Y-%m-%d %H:%M} · Mail Adagio {core.VERSION}", "",
            f"But : {cell(m['but'], 300)}", ""]


def comptes(by_theme, by_status):
    lines = ["## Par thème", "", "| Thème | Mails |", "| --- | --- |"]
    lines += [f"| {cell(k)} | {v} |" for k, v in by_theme.items()]
    lines += ["", "## Par statut", "", "| Statut | Mails |", "| --- | --- |"]
    lines += [f"| {cell(k)} | {v} |" for k, v in by_status.items()]
    return lines + [""]


def liste(mails):
    lines = ["## Mails", "", "| Date | Expéditeur | Sujet | Thème | Statut | À vérifier |", "| --- | --- | --- | --- | --- | --- |"]
    lines += [f"| {(r.get('date') or '')[:10]} | {cell(r.get('sender'), 40)} | {cell(r.get('subject'))} | "
              f"{cell(r.get('theme'))} | {cell(r.get('status'))} | {'oui' if r.get('to_check') else ''} |" for r in mails]
    return lines + [""]


def rapport_tri(m, plan, a):
    lines = entete(m, f"Tri des mails : lot {plan['batch_id']}")
    lines += [f"- Mails triés : {a['applied']} (dont {plan['by_rule']} par une règle)",
              f"- Sortis de la boîte de réception : {a['moved']}",
              f"- Marqués comme lus : {a['read']}",
              f"- À vérifier (libellé « To check ») : {plan['to_check']}",
              f"- Modèle : {plan['model']}",
              "- Annulable : oui, « Annuler » dans Mail Adagio remet le lot comme avant", ""]
    return "\n".join(lines + comptes(plan["by_theme"], plan["by_status"]) + liste(plan["mails"]))


def rapport_apercu(m, r):
    lines = entete(m, f"Aperçu du tri : {r['processed']} mails")
    lines += [f"- Mails lus : {r['processed']} (rien n'a été changé dans Gmail)",
              f"- Seraient à vérifier : {r['to_check']}", f"- Modèle : {r['model']}", ""]
    return "\n".join(lines + comptes(r["by_theme"], r["by_status"]) + liste(r["mails"]))


def rapport_demande(m, texte, out):
    lines = entete(m, "Demande à Mail Adagio")
    lines += [f"Demande : {cell(texte, 500)}", "", f"Réponse : {cell(out['answer'], 1000)}", "",
              "## Étapes", "", "| N° | Outil | Raison | Résultat |", "| --- | --- | --- | --- |"]
    lines += [f"| {i} | {cell(s['action'])} | {cell(s.get('raison'), 120)} | "
              f"{'fait : ' + cell(s.get('log'), 120) if s.get('ok') else 'échec : ' + cell(s.get('error'), 120)} |"
              for i, s in enumerate(out["steps"], 1)]
    return "\n".join(lines + [""])


def rapport_vide(m):
    return "\n".join(entete(m, "Tri des mails") + ["Rien à trier : tous les mails demandés sont déjà triés.", ""])


def action_write_report(target, text, raison):
    """Write the report in the project folder: a new file, nothing is overwritten."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "x", encoding="utf-8") as f:
        f.write(text)
    return target


# -- one mission -------------------------------------------------------------------------------------
def compte_rendu(m, etat, texte="", fichiers=(), session=None, motif=None, memoire=(), controle=(), non_controle=(),
                 reprise=None, manquant=None):
    out = {"mission_id": str((m or {}).get("id") or "inconnue"), "etat": etat,
           "resultat": {"texte": texte, "fichiers": list(fichiers)},
           "actions_declarees": session.actions if session else [], "memoire_modifiee": list(memoire),
           "verification": {"controle": list(controle), "non_controle": list(non_controle)},
           "point_de_reprise": reprise}
    if motif:
        out["motif"] = motif
    if manquant:
        out["manquant"] = list(manquant)
    return out


def run(session, m):
    session.mission = m if isinstance(m, dict) else {}
    try:
        donnees = check_mission(m)
        target = destination(m)
    except Refus as r:
        return compte_rendu(m if isinstance(m, dict) else {}, r.etat, motif=r.motif, session=session,
                            non_controle=["mission refusée avant de commencer : rien n'a été fait"])
    before = memory_snapshot()
    session.event("demarrage", f"Mail Adagio {core.VERSION} : {m['type_tache']}"
                               + (" (boîte de démonstration)" if donnees.get("boite") == "demo" else ""))
    fichiers, reprise, etat, motif, result = [], None, "termine", None, {}
    try:
        models.ONLY_PORTE["on"] = True
        try:
            model_cfg = models.porte_config((m.get("manifeste") or {}).get("modele", {}).get("demande", "inherit"))
        except models.ModelError as e:
            raise Refus("echec", f"porte du modèle absente : {e}")
        cfg, gmail, path, save = memory_prepare(donnees)
        ctx = {"cfg": cfg, "gmail": gmail, "ask": models.make_ask(model_cfg),
               "request": {"config": path, "save_keywords": save, "model_note": "modèle de la porte d'Orchestra"}}
        result = TASKS[m["type_tache"]](session, m, donnees, ctx)
        if result.get("echec"):
            raise Refus("echec", "aucune étape de la demande n'a réussi : " + "; ".join(result["manquant"]))
        session.between("écriture du rapport")
        if target:
            session.event("etape", f"Écriture du rapport {target.name}")
            action_write_report(target, result["rapport"], raison="Livrer le rapport demandé par la mission")
            rel = target.relative_to(Path(str(m["projet"])).resolve()).as_posix()
            session.declare("action_write_report", rel, "Livrer le rapport demandé par la mission")
            fichiers.append(rel)
        if result.get("partiel"):
            etat, motif = "partiel", "certaines étapes de la demande ont échoué"
        session.event("termine", result["texte"][:300])
    except Arret as a:
        etat, reprise = "arrete_proprement", {
            "etape": str(a), "type_tache": m["type_tache"],
            "reprise": "relancer la même mission : le lot en attente est repris là où il en était, "
                       "rien de ce qui a été fait dans Gmail n'est refait"}
        result = {"texte": f"Arrêté proprement ({a}). Rien n'a été changé dans Gmail pour l'étape non commencée.",
                  "controle": [], "non_controle": ["mission interrompue avant la fin"]}
    except Refus as r:
        etat, motif = r.etat, r.motif
        if etat == "bloque":
            session.event("blocage", r.motif[:300])
        result = {"texte": "", "controle": [], "non_controle": ["mission non terminée"]}
    except Exception as e:  # a bug must still give a report
        etat, motif = "echec", f"erreur inattendue de Mail Adagio : {type(e).__name__}: {e}"
        result = {"texte": "", "controle": [], "non_controle": ["mission non terminée"]}
    finally:
        models.ONLY_PORTE["on"] = False
    after = memory_snapshot()
    changed = sorted(k for k in after if before.get(k) != after[k]) + sorted(set(before) - set(after))
    return compte_rendu(m, etat, result.get("texte", ""), fichiers, session, motif, changed,
                        result.get("controle", ()), result.get("non_controle", ()), reprise,
                        result.get("manquant") if etat == "partiel" else None)


def main(stdin=None, stdout=None):
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    try:
        stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    session = Session(stdout)
    line = stdin.readline()
    try:
        m = json.loads(line)
    except ValueError:
        session.send({"compte_rendu": compte_rendu({}, "echec", motif="mission illisible : JSON invalide")})
        return 1
    threading.Thread(target=session.listen, args=(stdin,), daemon=True).start()
    report = run(session, m)
    session.send({"compte_rendu": report})
    return 0 if report["etat"] in ("termine", "partiel", "arrete_proprement") else 1


if __name__ == "__main__":
    sys.exit(main())
