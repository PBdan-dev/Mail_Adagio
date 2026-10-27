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
    archived   INTEGER NOT NULL DEFAULT 0, -- 1 = the agent moved it out of the Inbox
    marked_read INTEGER NOT NULL DEFAULT 0, -- 1 = it was unread and the agent marked it as read
    to_delete  INTEGER NOT NULL DEFAULT 0, -- 1 = proposed for deletion (label "To delete")
    confidence TEXT NOT NULL DEFAULT '',  -- high | medium | low, from the model ('' = a rule decided)
    to_check   INTEGER NOT NULL DEFAULT 0, -- 1 = the model was not sure: label "To check"
    reason     TEXT NOT NULL DEFAULT '',  -- one sentence from the model, only for the mails to check
    all_uid    INTEGER NOT NULL DEFAULT 0, -- id in All Mail, to read its labels again later (0 = unknown)
    removed    TEXT NOT NULL DEFAULT '[]' -- labels the agent removed (relabel), put back by undo
);
CREATE TABLE IF NOT EXISTS trash (
    gm_msgid TEXT NOT NULL,
    trash_id INTEGER NOT NULL,           -- one id per click on "Move to Trash"
    created  TEXT NOT NULL,
    restored INTEGER NOT NULL DEFAULT 0
);
"""


NEW_COLUMNS = [("marked_read", "INTEGER NOT NULL DEFAULT 0"), ("to_delete", "INTEGER NOT NULL DEFAULT 0"),
               ("confidence", "TEXT NOT NULL DEFAULT ''"), ("to_check", "INTEGER NOT NULL DEFAULT 0"),
               ("reason", "TEXT NOT NULL DEFAULT ''"), ("all_uid", "INTEGER NOT NULL DEFAULT 0"),
               ("removed", "TEXT NOT NULL DEFAULT '[]'")]


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        columns = {r["name"] for r in self.db.execute("PRAGMA table_info(mails)")}
        for column, kind in NEW_COLUMNS:  # adagio.db made by an older version
            if column not in columns:
                with self.db:
                    self.db.execute(f"ALTER TABLE mails ADD COLUMN {column} {kind}")

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

    def last_model(self):
        """Name of the model that sorted the last batch ('' if none yet)."""
        row = self.one("SELECT model FROM batches WHERE model != '' ORDER BY id DESC LIMIT 1")
        return row["model"] if row else ""

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
                    "theme, status, classified, rule, error, state, to_delete, confidence, to_check, reason) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned', ?, ?, ?, ?)",
                    (r["gm_msgid"], cur.lastrowid, r["uid"], r["date"], r["sender"], r["subject"][:300],
                     r["theme"], r["status"], int(r["classified"]), r.get("rule", ""), r.get("error", ""),
                     int(bool(r.get("to_delete"))), r.get("confidence", ""), int(bool(r.get("to_check"))),
                     r.get("reason", "")[:300]))
        return cur.lastrowid

    def add_relabel(self, rows):
        """Mails given another theme or status on request (agent mode): one applied batch, outside any
        sort job, so undo can put their labels back. rows: [{gm_msgid, uid, date, sender, subject, theme,
        status, added, removed, archived}]"""
        with self.db:
            cur = self.db.execute("INSERT INTO batches (job_id, state, model, created, applied) "
                                  "VALUES (0, 'applied', 'relabel', ?, ?)", (now(), now()))
            for r in rows:
                self.db.execute(
                    "INSERT OR REPLACE INTO mails (gm_msgid, batch_id, uid, date, sender, subject, theme, status, "
                    "classified, corrected, state, added, removed, archived, all_uid) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 'applied', ?, ?, ?, ?)",
                    (r["gm_msgid"], cur.lastrowid, r["uid"], r["date"], r["sender"], r["subject"][:300],
                     r["theme"], r["status"], json.dumps(r["added"]), json.dumps(r["removed"]),
                     int(r["archived"]), r["uid"]))
        return cur.lastrowid

    def mails(self, batch_id):
        rows = self.all("SELECT * FROM mails WHERE batch_id = ? ORDER BY date DESC, uid DESC", batch_id)
        for r in rows:
            r["added"] = json.loads(r["added"])
            r["removed"] = json.loads(r.get("removed") or "[]")
            r["classified"], r["corrected"], r["archived"] = (bool(r["classified"]), bool(r["corrected"]),
                                                              bool(r["archived"]))
            r["marked_read"], r["to_delete"] = bool(r["marked_read"]), bool(r["to_delete"])
            r["to_check"] = bool(r["to_check"])
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
                            "corrected = 1, rule = '', to_delete = 0, to_check = 0 WHERE gm_msgid = ? AND state = 'planned'",
                            (theme, status, str(gm_msgid)))

    def apply_rule(self, gm_msgid, theme, status, rule):
        with self.db:
            self.db.execute("UPDATE mails SET theme = ?, status = ?, classified = 1, error = '', "
                            "rule = ?, to_delete = 0, to_check = 0 WHERE gm_msgid = ? AND state = 'planned' AND corrected = 0",
                            (theme, status, rule, gm_msgid))

    def mark(self, gm_msgid, state, added=None, archived=None, read=False, all_uid=0):
        with self.db:
            if added is None:
                self.db.execute("UPDATE mails SET state = ? WHERE gm_msgid = ?", (state, gm_msgid))
            else:
                self.db.execute("UPDATE mails SET state = ?, added = ?, archived = ?, marked_read = ?, all_uid = ? "
                                "WHERE gm_msgid = ?", (state, json.dumps(added), int(archived), int(read),
                                                       int(all_uid or 0), gm_msgid))

    def mail(self, gm_msgid):
        row = self.one("SELECT * FROM mails WHERE gm_msgid = ?", str(gm_msgid))
        if row:
            row["added"] = json.loads(row["added"])
        return row

    def set_reason(self, gm_msgid, reason):
        with self.db:
            self.db.execute("UPDATE mails SET reason = ? WHERE gm_msgid = ?", (reason[:300], str(gm_msgid)))

    def set_all_uid(self, gm_msgid, all_uid):
        with self.db:
            self.db.execute("UPDATE mails SET all_uid = ? WHERE gm_msgid = ?", (int(all_uid), str(gm_msgid)))

    def applied(self, n=2000):
        """The mails the agent labelled, most recent batches first (to read their labels again)."""
        rows = self.all("SELECT * FROM mails WHERE state = 'applied' ORDER BY batch_id DESC, uid DESC LIMIT ?", n)
        for r in rows:
            r["added"] = json.loads(r["added"])
        return rows

    def changed_in_gmail(self, gm_msgid, theme, status, checked):
        """You changed the theme or status of a sorted mail in Gmail: keep the new one."""
        with self.db:
            self.db.execute("UPDATE mails SET theme = ?, status = ?, corrected = 1, rule = '', "
                            "to_check = CASE WHEN ? THEN 0 ELSE to_check END WHERE gm_msgid = ?",
                            (theme, status, int(checked), str(gm_msgid)))

    def checked(self, gm_msgid):
        """You removed the label "To check" in Gmail: the mail is checked."""
        with self.db:
            self.db.execute("UPDATE mails SET to_check = 0 WHERE gm_msgid = ?", (str(gm_msgid),))

    def keep(self, gm_msgid):
        """You un-ticked "To delete" on a mail of the plan."""
        with self.db:
            self.db.execute("UPDATE mails SET to_delete = 0 WHERE gm_msgid = ? AND state = 'planned'", (str(gm_msgid),))

    # trash: only when you click "Move to Trash" ---------------------------------
    def add_trash(self, gm_msgids):
        row = self.one("SELECT COALESCE(MAX(trash_id), 0) + 1 AS n FROM trash")
        with self.db:
            self.db.executemany("INSERT INTO trash (gm_msgid, trash_id, created) VALUES (?, ?, ?)",
                                [(str(g), row["n"], now()) for g in gm_msgids])
        return row["n"]

    def last_trash(self):
        row = self.one("SELECT trash_id, MIN(created) AS created, COUNT(*) AS n FROM trash WHERE restored = 0 "
                       "GROUP BY trash_id ORDER BY trash_id DESC LIMIT 1")
        if not row:
            return None
        row["gm_msgids"] = [r["gm_msgid"] for r in self.all("SELECT gm_msgid FROM trash WHERE trash_id = ? "
                                                            "AND restored = 0", row["trash_id"])]
        return row

    def restored(self, trash_id):
        with self.db:
            self.db.execute("UPDATE trash SET restored = 1 WHERE trash_id = ?", (trash_id,))

    def rename_labels(self, renamed):
        """Labels renamed in Gmail: keep the undo list right."""
        with self.db:
            for row in self.db.execute("SELECT gm_msgid, added FROM mails WHERE added != '[]'").fetchall():
                added = json.loads(row["added"])
                new = [renamed.get(x, x) for x in added]
                if new != added:
                    self.db.execute("UPDATE mails SET added = ? WHERE gm_msgid = ?", (json.dumps(new), row["gm_msgid"]))

    def seconds_per_call(self, model):
        """Average time of one model call for this model, from past batches (None if unknown)."""
        row = self.one("SELECT SUM(seconds) AS s, SUM(calls) AS c FROM batches WHERE model = ? "
                       "AND calls > 0", model)
        return row["s"] / row["c"] if row and row["c"] else None
