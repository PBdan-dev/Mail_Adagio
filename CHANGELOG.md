# Changelog — mail-adagio

## 0.9.0 — 2026-10-29
Mise en conformité avec le kit de certification Orchestra (contrat 1, manifeste 2.0).
- Mode mission : `python adagio.py mission`, protocole stdio-json (mission en entrée, événements puis compte rendu en sortie). Trois tâches : `trier-mails`, `apercu-tri`, `demande`. Arrêt propre avec point de reprise, pause et reprise. Rapport Markdown dans le dossier du projet, sans rien écraser.
- En mission, le seul modèle est la porte d'Orchestra (`ORCHESTRA_MODELE_URL`, `ORCHESTRA_JETON`) : pas d'autre modèle, pas de secours.
- Préfixes : chaque appel à Gmail, à la base, au disque, au réseau ou au coffre-fort passe par une fonction `read_`, `action_`, `action_sensitive_`, `memory_` ou `secret_`. `read_imap` refuse toute commande qui écrit ; `action_imap` refuse la Corbeille et la suppression ; seule `action_sensitive_trash` met à la Corbeille (clic confirmé dans la fenêtre, jamais en mission).
- Secrets : le mot de passe d'application Gmail et la clé d'API vont dans le coffre-fort Windows (`orchestra/mail-adagio/<nom>`, bibliothèque keyring). Ceux de `config.yaml` y sont déplacés au premier lancement puis retirés du fichier.
- Mémoire propre : réglages, base, journal dans `memoire/`. Les fichiers des versions précédentes y sont rangés au premier lancement.
- Le contenu des mails, les mots-clés appris et les corrections sont balisés `<donnee_externe>` dans les prompts.
- Mode agent : chaque outil proposé au modèle est une fonction `read_`, `memory_` ou `action_` ; chaque `action_` exige une raison, écrite au journal et dans le compte rendu.
- Ajouts : `manifeste.json`, `modeles/`, tests de mission dans `tests/`, licence MIT.

## 0.8.1 — 2026-10-27
- Renommer un thème par le mode agent, plus de console, fermer l'onglet arrête l'application.

## 0.8.0 — 2026-10-27
- Mode agent (barre « Ask Adagio »), onglets de Gmail comme indice pour le modèle.
