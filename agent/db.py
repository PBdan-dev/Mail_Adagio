"""The agent's own database (memoire/adagio.db): every read and write of it goes through the memory_
functions below, so the code that touches the memory is easy to find and to check."""
import sqlite3
from pathlib import Path

READ_WORDS = ("SELECT", "PRAGMA", "WITH")


class Db:
    db = None

    def memory_open(self, path, schema):
        """Open (or create) the database file and its tables."""
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(schema)

    def memory_read(self, sql, *args):
        """Rows of a SELECT, as dicts. Refuses anything that would write."""
        if not sql.lstrip().upper().startswith(READ_WORDS):
            raise ValueError("memory_read only reads")
        return [dict(r) for r in self.db.execute(sql, args)]

    def memory_write(self, *statements):
        """Run (sql, args) statements in one transaction. Returns the id of the last row inserted."""
        last = None
        with self.db:
            for sql, args in statements:
                last = self.db.execute(sql, tuple(args)).lastrowid
        return last

    def memory_write_many(self, sql, rows):
        """The same statement for many rows, in one transaction."""
        with self.db:
            self.db.executemany(sql, [tuple(r) for r in rows])

    def memory_close(self):
        if self.db is not None:
            self.db.close()

    # short forms for reading
    def one(self, sql, *args):
        rows = self.memory_read(sql, *args)
        return rows[0] if rows else None

    def all(self, sql, *args):
        return self.memory_read(sql, *args)

    def close(self):
        self.memory_close()
