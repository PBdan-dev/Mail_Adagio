"""Who writes to you: one row per sender, filled by a fast scan of the mail headers (no AI).

Used for two things:
- Subscriptions: senders with an unsubscribe link. You click, the link opens; nothing is automatic.
- Rule suggestions: for the senders who write the most, a few of their mails are classified and a
  ready-made rule is proposed ("340 mails from LinkedIn -> Job Search / Info?").

Kept in adagio.db. Only the address, the name, counts and the unsubscribe link are stored.
"""
import json
import sqlite3
from collections import Counter

from .batches import now
from .memory import address, domain

SCHEMA = """
CREATE TABLE IF NOT EXISTS senders (
    address     TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    domain      TEXT NOT NULL DEFAULT '',
    mails       INTEGER NOT NULL DEFAULT 0,
    last_date   TEXT NOT NULL DEFAULT '',
    unsubscribe TEXT NOT NULL DEFAULT '',
    samples     TEXT NOT NULL DEFAULT '[]',   -- Inbox ids of a few recent mails, for suggestions
    state       TEXT NOT NULL DEFAULT '',     -- '' | opened (unsubscribe link opened) | hidden
    state_at    TEXT NOT NULL DEFAULT '',
    theme       TEXT NOT NULL DEFAULT '',     -- suggested rule
    status      TEXT NOT NULL DEFAULT '',
    votes       TEXT NOT NULL DEFAULT ''      -- e.g. "3 of 3 mails"
);
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY, created TEXT NOT NULL, mails INTEGER NOT NULL, mailbox TEXT NOT NULL
);
"""


def display_name(sender):
    name = sender.split("<")[0].strip().strip('"').strip()
    return name or address(sender)


def aggregate(headers, samples=3):
    """Headers (newest first) -> {address: row}."""
    rows = {}
    for h in headers:
        addr = address(h.sender)
        if not addr:
            continue
        r = rows.setdefault(addr, {"address": addr, "name": display_name(h.sender), "domain": domain(h.sender),
                                   "mails": 0, "last_date": "", "unsubscribe": "", "samples": []})
        r["mails"] += 1
        r["last_date"] = max(r["last_date"], h.date)
        if h.unsubscribe and not r["unsubscribe"]:
            r["unsubscribe"] = h.unsubscribe
        if len(r["samples"]) < samples:
            r["samples"].append(h.uid)
    return rows


class Senders:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def save_scan(self, rows, mails, mailbox):
        """Replace the counts with the new scan; keep what you did (opened, hidden) and suggestions."""
        with self.db:
            self.db.execute("UPDATE senders SET mails = 0, samples = '[]'")
            for r in rows.values():
                self.db.execute(
                    "INSERT INTO senders (address, name, domain, mails, last_date, unsubscribe, samples) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (address) DO UPDATE SET name = excluded.name, "
                    "mails = excluded.mails, last_date = excluded.last_date, samples = excluded.samples, "
                    "unsubscribe = CASE WHEN excluded.unsubscribe != '' THEN excluded.unsubscribe "
                    "ELSE senders.unsubscribe END",
                    (r["address"], r["name"], r["domain"], r["mails"], r["last_date"], r["unsubscribe"],
                     json.dumps(r["samples"])))
            self.db.execute("INSERT INTO scans (created, mails, mailbox) VALUES (?, ?, ?)", (now(), mails, mailbox))

    def last_scan(self):
        row = self.db.execute("SELECT * FROM scans ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def all(self):
        rows = [dict(r) for r in self.db.execute(
            "SELECT * FROM senders WHERE mails > 0 ORDER BY mails DESC, last_date DESC")]
        for r in rows:
            r["samples"] = json.loads(r["samples"])
        return rows

    def get(self, addr):
        row = self.db.execute("SELECT * FROM senders WHERE address = ?", (addr,)).fetchone()
        if not row:
            return None
        r = dict(row)
        r["samples"] = json.loads(r["samples"])
        return r

    def set_state(self, addr, state):
        if state not in ("", "opened", "hidden"):
            raise ValueError("state must be '', opened or hidden")
        with self.db:
            self.db.execute("UPDATE senders SET state = ?, state_at = ? WHERE address = ?", (state, now(), addr))

    def set_link(self, addr, link):
        with self.db:
            self.db.execute("UPDATE senders SET unsubscribe = ? WHERE address = ? AND unsubscribe = ''",
                            (link, addr))

    def suggest(self, addr, theme, status, votes):
        with self.db:
            self.db.execute("UPDATE senders SET theme = ?, status = ?, votes = ? WHERE address = ?",
                            (theme, status, votes, addr))


def majority(results):
    """Most frequent (theme, status) among a sender's sampled mails, and how many agreed."""
    pairs = Counter((r.theme, r.status) for r in results if r.ok)
    if not pairs:
        return None
    (theme, status), n = pairs.most_common(1)[0]
    return theme, status, f"{n} of {len(results)} mails"
