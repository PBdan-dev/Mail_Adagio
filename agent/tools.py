"""The tools the model may pick in agent mode (a request in your own words, or a mission "demande").

One function per tool, named after what it does:
- read_: reads Gmail, changes nothing;
- memory_: reads or changes only Mail Adagio's own memory (memoire/: rules, keywords, plans, journal);
- action_: changes Gmail, and can be undone (undo, or renaming back). Each takes the model's reason
  (raison), written in the journal and in the mission report.
There is no action_sensitive_ tool: the Trash, restoring, unsubscribing and sending are never offered.
The parameters were checked by skills.check before a tool runs.
"""
from . import core


def _run(action, cfg, request, gmail_factory, ask, progress, raison=""):
    if raison:
        core.memory_log(f"agent: {action}, reason: {' '.join(str(raison).split())[:200]}")
    return core.handle({**request, "action": action}, cfg=cfg, gmail_factory=gmail_factory, ask=ask,
                       progress=progress, door="agent")


def _reason(action, raison):
    if not str(raison or "").strip():
        raise ValueError(f"{action}: the reason (raison) is missing")


# read_: Gmail, read only ----------------------------------------------------------------
def read_check(cfg, request, gmail_factory, ask=None, progress=None):
    """check: test the Gmail connection and count the mails."""
    return _run("check", cfg, request, gmail_factory, ask, progress)


def read_preview(cfg, request, gmail_factory, ask=None, progress=None):
    """preview: read and classify recent mails, change nothing."""
    return _run("preview", cfg, request, gmail_factory, ask, progress)


def read_estimate(cfg, request, gmail_factory, ask=None, progress=None):
    """estimate: how many mails, batches and how long."""
    return _run("estimate", cfg, request, gmail_factory, ask, progress)


def read_search(cfg, request, gmail_factory, ask=None, progress=None):
    """search: Gmail's search, changes nothing."""
    return _run("search", cfg, request, gmail_factory, ask, progress)


def read_to_delete(cfg, request, gmail_factory, ask=None, progress=None):
    """to_delete: how many mails carry the "To delete" label."""
    return _run("to_delete", cfg, request, gmail_factory, ask, progress)


# memory_: Mail Adagio's own memory ---------------------------------------------------------
def memory_status(cfg, request, gmail_factory, ask=None, progress=None):
    """status: where the sorting stands."""
    return _run("status", cfg, request, gmail_factory, ask, progress)


def memory_journal(cfg, request, gmail_factory, ask=None, progress=None):
    """journal: lines of the journal."""
    return _run("journal", cfg, request, gmail_factory, ask, progress)


def memory_rules(cfg, request, gmail_factory, ask=None, progress=None):
    """rules: the sender rules and what was learned."""
    return _run("rules", cfg, request, gmail_factory, ask, progress)


def memory_proposals(cfg, request, gmail_factory, ask=None, progress=None):
    """proposals: rules proposed after repeated corrections."""
    return _run("proposals", cfg, request, gmail_factory, ask, progress)


def memory_senders(cfg, request, gmail_factory, ask=None, progress=None):
    """senders: the senders of the last scan."""
    return _run("senders", cfg, request, gmail_factory, ask, progress)


def memory_discard(cfg, request, gmail_factory, ask=None, progress=None, raison=""):
    """discard: drop the plan waiting for approval (nothing changes in Gmail)."""
    return _run("discard", cfg, request, gmail_factory, ask, progress, raison)


def memory_keep(cfg, request, gmail_factory, ask=None, progress=None, raison=""):
    """keep: untick "To delete" on a mail of the plan."""
    return _run("keep", cfg, request, gmail_factory, ask, progress, raison)


def memory_keywords(cfg, request, gmail_factory, ask=None, progress=None, raison=""):
    """keywords: read the keywords, or change one theme's keywords."""
    return _run("keywords", cfg, request, gmail_factory, ask, progress, raison)


def memory_correct(cfg, request, gmail_factory, ask=None, progress=None, raison=""):
    """correct: correct a mail of the plan waiting for approval."""
    return _run("correct", cfg, request, gmail_factory, ask, progress, raison)


def memory_add_rule(cfg, request, gmail_factory, ask=None, progress=None, raison=""):
    """add_rule: a sender or domain rule."""
    return _run("add_rule", cfg, request, gmail_factory, ask, progress, raison)


def memory_delete_rule(cfg, request, gmail_factory, ask=None, progress=None, raison=""):
    """delete_rule: delete a rule."""
    return _run("delete_rule", cfg, request, gmail_factory, ask, progress, raison)


def memory_ignore_proposal(cfg, request, gmail_factory, ask=None, progress=None, raison=""):
    """ignore_proposal: never propose this rule again."""
    return _run("ignore_proposal", cfg, request, gmail_factory, ask, progress, raison)


def memory_sender_state(cfg, request, gmail_factory, ask=None, progress=None, raison=""):
    """sender_state: remember a sender as opened or hidden."""
    return _run("sender_state", cfg, request, gmail_factory, ask, progress, raison)


def memory_learn_gmail(cfg, request, gmail_factory, ask=None, progress=None, raison=""):
    """learn_gmail: read the labels changed in Gmail and learn from them (Gmail is only read)."""
    return _run("learn_gmail", cfg, request, gmail_factory, ask, progress, raison)


def memory_scan(cfg, request, gmail_factory, ask=None, progress=None, raison=""):
    """scan: count the mails of each sender (headers only), kept in memory."""
    return _run("scan", cfg, request, gmail_factory, ask, progress, raison)


def memory_suggest(cfg, request, gmail_factory, ask=None, progress=None, raison=""):
    """suggest: the model proposes a rule for the biggest senders, kept in memory."""
    return _run("suggest", cfg, request, gmail_factory, ask, progress, raison)


def memory_why(cfg, request, gmail_factory, ask=None, progress=None, raison=""):
    """why: the model says why it sorted a mail this way (the sentence is kept in memory)."""
    return _run("why", cfg, request, gmail_factory, ask, progress, raison)


# action_: changes Gmail, can be undone -------------------------------------------------------
def action_plan(cfg, request, gmail_factory, ask=None, progress=None, raison=""):
    """plan: analyse the next batch; with act_alone, apply it (labels, out of the Inbox)."""
    _reason("plan", raison)
    return _run("plan", cfg, request, gmail_factory, ask, progress, raison)


def action_apply(cfg, request, gmail_factory, ask=None, progress=None, raison=""):
    """apply: carry out the plan waiting for approval."""
    _reason("apply", raison)
    return _run("apply", cfg, request, gmail_factory, ask, progress, raison)


def action_undo(cfg, request, gmail_factory, ask=None, progress=None, raison=""):
    """undo: put the last applied batch or relabel back as it was."""
    _reason("undo", raison)
    return _run("undo", cfg, request, gmail_factory, ask, progress, raison)


def action_relabel(cfg, request, gmail_factory, ask=None, progress=None, raison=""):
    """relabel: another theme and/or status for given mails (undone by undo)."""
    _reason("relabel", raison)
    return _run("relabel", cfg, request, gmail_factory, ask, progress, raison)


def action_rename_theme(cfg, request, gmail_factory, ask=None, progress=None, raison=""):
    """rename_theme: rename a theme and its Gmail label (undone by renaming back)."""
    _reason("rename_theme", raison)
    return _run("rename_theme", cfg, request, gmail_factory, ask, progress, raison)


TOOLS = {f.__name__.split("_", 1)[1]: f for f in (
    read_check, read_preview, read_estimate, read_search, read_to_delete,
    memory_status, memory_journal, memory_rules, memory_proposals, memory_senders, memory_discard, memory_keep,
    memory_keywords, memory_correct, memory_add_rule, memory_delete_rule, memory_ignore_proposal,
    memory_sender_state, memory_learn_gmail, memory_scan, memory_suggest, memory_why,
    action_plan, action_apply, action_undo, action_relabel, action_rename_theme)}
WITH_REASON = {name for name, f in TOOLS.items() if "raison" in f.__code__.co_varnames}


def run(action, cfg, request, gmail_factory, ask=None, progress=None, raison=""):
    """Run the tool for this action (one of skills.AGENT_ACTIONS)."""
    tool = TOOLS[action]
    if action in WITH_REASON:
        return tool(cfg, request, gmail_factory, ask, progress, raison=raison)
    return tool(cfg, request, gmail_factory, ask, progress)
