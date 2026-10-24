"""Progress of the sorting, kept in adagio.db so a stopped run resumes where it was.

- job:     what you asked to sort (newest N mails, last N days, or the whole mailbox).
- batches: one plan of up to `batch_size` mails: pending -> applied -> undone (or discarded).
- mails:   one row per mail: Gmail id, date, sender, subject, theme, status, and what was done
           to it in Gmail (labels added, moved out of the Inbox) so it can be undone.

The content of a mail is never stored.
"""
import json
import sqlite3
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id       INTEGER PRIMARY KEY,
    kind     TEXT NOT NULL,              -- count | days | all
    value    INTEGER NOT NULL DEFAULT 0,
    max_uid  INTEGER NOT NULL,           -- mails that arrive later wait for the next job
    total    INTEGER NOT NULL,
    created  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batches (
    id       INTEGER PRIMARY KEY,
    job_id   INTEGER NOT NULL,
    state    TEXT NOT NULL,              -- pending | applied | undone | discarded
    model    TEXT NOT NULL DEFAULT '',
    seconds  REAL NOT NULL DEFAULT 0,    -- time spent classifying, for the estimates
    calls    INTEGER NOT NULL DEFAULT 0, -- model calls (mails sorted by a rule need none)
    created  TEXT NOT NULL,
    applied  TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS mails (
    gm_msgid   TEXT PRIMARY KEY,
    batch_id   INTEGER NOT NULL,
    uid        INTEGER NOT NULL,
    date       TEXT NOT NULL DEFAULT '',
    sender     TEXT NOT NULL DEFAULT '',
    subject    TEXT NOT NULL DEFAULT '',
    theme      TEXT NOT NULL,
    status     TEXT NOT NULL,
    classified INTEGER NOT NULL DEFAULT 1,
    rule       TEXT NOT NULL DEFAULT '',
    error      TEXT NOT NULL DEFAULT '',
    corrected  INTEGER NOT NULL DEFAULT 0,
    state      TEXT NOT NULL,            -- planned | applied | skipped | undone
    added      TEXT NOT NULL DEFAULT '[]', -- labels the agent added (JSON list)
    archived   INTEGER NOT NULL DEFAULT 0  -- 1 = the agent moved it out of the Inbox
);
"""


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def one(self, sql, *args):
        row = self.db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def all(self, sql, *args):
        return [dict(r) for r in self.db.execute(sql, args)]

    # job ----------------------------------------------------------------------
    def job(self):
        return self.one("SELECT * FROM jobs ORDER BY id DESC LIMIT 1")

    def new_job(self, kind, value, max_uid, total):
        with self.db:
            cur = self.db.execute("INSERT INTO jobs (kind, value, max_uid, total, created) "
                                  "VALUES (?, ?, ?, ?, ?)", (kind, value, max_uid, total, now()))
        return self.one("SELECT * FROM jobs WHERE id = ?", cur.lastrowid)

    def done_in_job(self, job_id):
        """Mails of this job that are finished (applied or left as they are)."""
        row = self.one("SELECT COUNT(*) AS n FROM mails m JOIN batches b ON b.id = m.batch_id "
                       "WHERE b.job_id = ? AND m.state IN ('applied', 'skipped')", job_id)
        return row["n"]

    def batches_in_job(self, job_id):
        return self.one("SELECT COUNT(*) AS n FROM batches WHERE job_id = ? AND state = 'applied'",
                        job_id)["n"]

    def handled(self):
        """Gmail ids that must not be proposed again."""
        return {r["gm_msgid"] for r in self.db.execute(
            "SELECT gm_msgid FROM mails WHERE state IN ('applied', 'skipped', 'planned')")}

    # batches ------------------------------------------------------------------
    def pending(self):
        return self.one("SELECT * FROM batches WHERE state = 'pending' ORDER BY id DESC LIMIT 1")

    def last_applied(self):
        return self.one("SELECT * FROM batches WHERE state = 'applied' ORDER BY id DESC LIMIT 1")

    def add_batch(self, job_id, model, seconds, calls, rows):
        with self.db:
            cur = self.db.execute("INSERT INTO batches (job_id, state, model, seconds, calls, created) "
                                  "VALUES (?, 'pending', ?, ?, ?, ?)",
                                  (job_id, model, seconds, calls, now()))
            for r in rows:
                self.db.execute(
                    "INSERT OR REPLACE INTO mails (gm_msgid, batch_id, uid, date, sender, subject, "
                    "theme, status, classified, rule, error, state) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned')",
                    (r["gm_msgid"], cur.lastrowid, r["uid"], r["date"], r["sender"], r["subject"][:300],
                     r["theme"], r["status"], int(r["classified"]), r.get("rule", ""), r.get("error", "")))
        return cur.lastrowid

    def mails(self, batch_id):
        rows = self.all("SELECT * FROM mails WHERE batch_id = ? ORDER BY date DESC, uid DESC", batch_id)
        for r in rows:
            r["added"] = json.loads(r["added"])
            r["classified"], r["corrected"], r["archived"] = (bool(r["classified"]), bool(r["corrected"]),
                                                              bool(r["archived"]))
        return rows

    def set_state(self, batch_id, state):
        with self.db:
            self.db.execute("UPDATE batches SET state = ?, applied = CASE WHEN ? = 'applied' "
                            "THEN ? ELSE applied END WHERE id = ?", (state, state, now(), batch_id))

    def discard(self, batch_id):
        """Forget a plan: its mails will be analysed again in a later batch."""
        with self.db:
            self.db.execute("DELETE FROM mails WHERE batch_id = ? AND state = 'planned'", (batch_id,))
            self.db.execute("UPDATE batches SET state = 'discarded' WHERE id = ?", (batch_id,))

    def release_planned(self, batch_id):
        with self.db:
            self.db.execute("DELETE FROM mails WHERE batch_id = ? AND state = 'planned'", (batch_id,))

    # mails --------------------------------------------------------------------
    def correct(self, gm_msgid, theme, status):
        with self.db:
            self.db.execute("UPDATE mails SET theme = ?, status = ?, classified = 1, error = '', "
                            "corrected = 1, rule = '' WHERE gm_msgid = ? AND state = 'planned'",
                            (theme, status, str(gm_msgid)))

    def apply_rule(self, gm_msgid, theme, status, rule):
        with self.db:
            self.db.execute("UPDATE mails SET theme = ?, status = ?, classified = 1, error = '', "
                            "rule = ? WHERE gm_msgid = ? AND state = 'planned' AND corrected = 0",
                            (theme, status, rule, gm_msgid))

    def mark(self, gm_msgid, state, added=None, archived=None):
        with self.db:
            if added is None:
                self.db.execute("UPDATE mails SET state = ? WHERE gm_msgid = ?", (state, gm_msgid))
            else:
                self.db.execute("UPDATE mails SET state = ?, added = ?, archived = ? WHERE gm_msgid = ?",
                                (state, json.dumps(added), int(archived), gm_msgid))

    def seconds_per_call(self, model):
        """Average time of one model call for this model, from past batches (None if unknown)."""
        row = self.one("SELECT SUM(seconds) AS s, SUM(calls) AS c FROM batches WHERE model = ? "
                       "AND calls > 0", model)
        return row["s"] / row["c"] if row and row["c"] else None
