"""What the agent learns from your corrections, kept in a local SQLite file (adagio.db).

- Rules: "mails from this sender (or this domain) go to this theme and status".
  They are applied before the model: no model call, always the same answer.
- Examples: your last corrections, shown to the model so it handles similar mails the same way.

Only the sender and the subject are stored, never the content of a mail.
"""
import re
import sqlite3
from email.utils import parseaddr
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS rules (
    id      INTEGER PRIMARY KEY,
    kind    TEXT NOT NULL CHECK (kind IN ('sender', 'domain')),
    value   TEXT NOT NULL,
    theme   TEXT NOT NULL,
    status  TEXT NOT NULL,
    created TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (kind, value)
);
CREATE TABLE IF NOT EXISTS examples (
    id      INTEGER PRIMARY KEY,
    sender  TEXT NOT NULL,
    subject TEXT NOT NULL,
    theme   TEXT NOT NULL,
    status  TEXT NOT NULL,
    created TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS ignored (     -- rule proposals you said no to
    sender  TEXT NOT NULL,
    theme   TEXT NOT NULL,
    status  TEXT NOT NULL,
    UNIQUE (sender, theme, status)
);
"""

PROPOSE_AFTER = 2  # corrections of the same sender to the same theme and status: propose a rule


def address(sender):
    """'Shop <News@Shop.com>' -> 'news@shop.com'"""
    return parseaddr(sender or "")[1].strip().lower()


def domain(sender):
    addr = address(sender)
    return addr.split("@", 1)[1] if "@" in addr else ""


class Memory:
    def __init__(self, path):
        self.path = Path(path)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    # rules ------------------------------------------------------------------
    def add_rule(self, kind, value, theme, status):
        value = value.strip().lower()
        if kind not in ("sender", "domain") or not re.fullmatch(r"[^\s]+", value or ""):
            raise ValueError("A rule needs a sender address or a domain")
        with self.db:
            self.db.execute(
                "INSERT INTO rules (kind, value, theme, status) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (kind, value) DO UPDATE SET theme = excluded.theme, status = excluded.status",
                (kind, value, theme, status))

    def rules(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM rules ORDER BY created DESC, id DESC")]

    def delete_rule(self, rule_id):
        with self.db:
            self.db.execute("DELETE FROM rules WHERE id = ?", (int(rule_id),))

    def match(self, sender, themes, statuses):
        """The rule for this sender: exact address first, then its domain or a parent domain.

        Rules whose theme or status no longer exists in the config are ignored."""
        addr, dom = address(sender), domain(sender)
        if not addr:
            return None
        candidates = [("sender", addr)] + [("domain", ".".join(dom.split(".")[i:]))
                                           for i in range(max(dom.count("."), 1))]
        for kind, value in candidates:
            row = self.db.execute("SELECT * FROM rules WHERE kind = ? AND value = ?", (kind, value)).fetchone()
            if row and row["theme"] in themes and row["status"] in statuses:
                return dict(row)
        return None

    # examples ---------------------------------------------------------------
    def add_example(self, sender, subject, theme, status):
        with self.db:
            self.db.execute("INSERT INTO examples (sender, subject, theme, status) VALUES (?, ?, ?, ?)",
                            (address(sender) or sender, subject[:200], theme, status))

    # rule proposals: the same correction several times for one sender ----------
    def proposals(self, themes, statuses):
        """[{sender, theme, status, count}] that would make a good rule, most corrections first."""
        rows = self.db.execute(
            "SELECT sender, theme, status, COUNT(*) AS count, MAX(id) AS last FROM examples "
            "GROUP BY sender, theme, status HAVING COUNT(*) >= ? ORDER BY count DESC, last DESC",
            (PROPOSE_AFTER,)).fetchall()
        out = []
        for r in rows:
            r = dict(r)
            latest = self.db.execute("SELECT theme, status FROM examples WHERE sender = ? ORDER BY id DESC LIMIT 1",
                                     (r["sender"],)).fetchone()
            rule = self.match(r["sender"], themes, statuses)
            if ("@" not in r["sender"] or r["theme"] not in themes or r["status"] not in statuses
                    or (latest["theme"], latest["status"]) != (r["theme"], r["status"])  # you changed your mind
                    or (rule and (rule["theme"], rule["status"]) == (r["theme"], r["status"]))
                    or self.db.execute("SELECT 1 FROM ignored WHERE sender = ? AND theme = ? AND status = ?",
                                       (r["sender"], r["theme"], r["status"])).fetchone()):
                continue
            out.append({k: r[k] for k in ("sender", "theme", "status", "count")})
        return out

    def ignore(self, sender, theme, status):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO ignored (sender, theme, status) VALUES (?, ?, ?)",
                            (address(sender) or sender, theme, status))

    def examples(self, n=8):
        rows = self.db.execute("SELECT * FROM examples ORDER BY id DESC LIMIT ?", (n,))
        return [dict(r) for r in rows][::-1]
