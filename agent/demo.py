"""Made-up mails to try the interface without a Gmail account (`python adagio.py ui --demo`)."""
import json
import re
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime

from .mail import Gmail, utf7_decode, utf7_encode

# (sender, subject, text, theme, status, day of October 2026)
SAMPLES = [
    ("Northbank <alerts@northbank.example>", "Unusual sign-in to your account",
     "We noticed a sign-in from a new device.", "Finance", "Important", 24),
    ("City Hall <noreply@city.example>", "Your residence permit appointment",
     "Please confirm your appointment on 3 November.", "Admin", "Important", 24),
    ("Dr Martin's office <rdv@clinic.example>", "Reminder: appointment Tuesday 10:30",
     "Your appointment is confirmed.", "Health", "To Do", 23),
    ("Aerolis <booking@aerolis.example>", "Your flight to Lisbon: check-in is open",
     "Check in now for flight AE 412.", "Travel", "To Do", 23),
    ("Jobs Digest <jobs@careers.example>", "Senior Data Analyst at Northwind",
     "A new job matches your alert.", "Job Search", "Info", 24),
    ("Jobs Digest <jobs@careers.example>", "AI Architect at Contoso",
     "A new job matches your alert.", "Job Search", "Info", 23),
    ("Recruiter Anna <anna@talent.example>", "Your application: next steps",
     "Could you share your availability for an interview?", "Job Search", "To Do", 22),
    ("Shoply <orders@shoply.example>", "Your order has shipped",
     "Parcel arriving Monday.", "Shopping & Deliveries", "Info", 22),
    ("Power & Co <billing@power.example>", "Your October invoice: 64.20 EUR",
     "Payment due on 5 November.", "Finance", "To Do", 21),
    ("Weekly AI Notes <hello@ainotes.example>", "This week: small models, big wins",
     "Five links worth your time.", "Newsletters", "Info", 21),
    ("PizzaNow <promo@pizzanow.example>", "-30% tonight only",
     "Order before 9 pm.", "Promotions", "Info", 21),
    ("GameStore <noreply@games.example>", "A game on your wishlist is on sale",
     "Space Builder -75%.", "Promotions", "Info", 20),
    ("Mum <mum@family.example>", "Sunday lunch?",
     "Are you coming on Sunday? Bring dessert.", "Family & Friends", "To Do", 20),
    ("LearnLang <hello@learnlang.example>", "You are on a 30-day streak",
     "Keep going!", "Social & Notifications", "Info", 19),
]


def _raw(sender, subject, text, when):
    m = EmailMessage()
    m["From"], m["Subject"], m["To"] = sender, subject, "you@gmail.com"
    m["Date"] = format_datetime(when)
    m.set_content(text)
    return m.as_bytes()


class FakeIMAP:
    """A pretend Gmail kept in memory: labels can be added and removed, nothing is real.

    Mails have one id in the Inbox and another in All Mail, like in Gmail.
    """
    COPIES = 10  # SAMPLES repeated, one week apart: 140 mails, enough to see batches
    mails = None  # all-mail uid -> {"gm", "labels", "raw", "date"}

    @classmethod
    def reset(cls, copies=None):
        cls.created, cls.commands = set(), []
        cls.mails, start = {}, datetime(2026, 10, 24, 9, 0, tzinfo=timezone.utc)
        n = 0
        for week in range(copies or cls.COPIES):
            for sender, subject, text, _t, _s, day in SAMPLES:
                n += 1
                when = start - timedelta(days=24 - day + 7 * week)
                cls.mails[5000 + 10_000 - n] = {"gm": str(1879950646965534236 + n), "labels": {"\\Inbox"},
                                                "raw": _raw(sender, subject, text, when), "date": when}

    def __init__(self, host):
        if FakeIMAP.mails is None:
            FakeIMAP.reset()
        self.folder = "INBOX"

    def login(self, *a):
        pass

    def logout(self):
        pass

    def list(self, *a):
        names = {label for m in self.mails.values() for label in m["labels"] if not label.startswith("\\")}
        lines = [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\All) "/" "[Gmail]/All Mail"']
        lines += [f'(\\HasNoChildren) "/" "{utf7_encode(n)}"'.encode() for n in sorted(names | self.created)]
        return "OK", lines

    created = set()

    def create(self, name):
        FakeIMAP.commands.append(("CREATE", name))
        FakeIMAP.created.add(utf7_decode(name.strip('"')))
        return "OK", [b"Success"]

    def select(self, mailbox, readonly=False):
        self.folder = utf7_decode(mailbox.strip('"'))
        return "OK", [str(len(self.visible())).encode()]

    def visible(self):
        """{uid in this folder: mail}"""
        if self.folder == "INBOX":
            return {u - 5000: m for u, m in self.mails.items() if "\\Inbox" in m["labels"]}
        return dict(self.mails)

    commands = []  # every IMAP command received, so tests can check nothing is ever deleted

    def uid(self, command, *args):
        FakeIMAP.commands.append((command,) + args)
        box = self.visible()
        if command == "SEARCH":
            if "X-GM-MSGID" in args:
                hits = [u for u, m in box.items() if m["gm"] == args[-1]]
            elif "SINCE" in args:
                since = datetime.strptime(args[-1], "%d-%b-%Y").replace(tzinfo=timezone.utc)
                hits = [u for u, m in box.items() if m["date"] >= since]
            else:
                hits = list(box)
            return "OK", [" ".join(map(str, sorted(hits))).encode()]
        uids = [int(x) for x in args[0].split(",")]
        if command == "STORE":
            op, value = args[1], args[2]
            labels = {utf7_decode(q or b) for q, b in re.findall(r'"([^"]*)"|(\\?[^\s()"]+)', value)}
            for u in uids:
                if u in box:
                    box[u]["labels"] = box[u]["labels"] | labels if op[0] == "+" else box[u]["labels"] - labels
            return "OK", []
        out = []
        for i, u in enumerate(uids, 1):
            m = box.get(u)
            if not m:
                continue
            labels = " ".join(f'"{x}"' if x.startswith("\\") else f'"{utf7_encode(x)}"'
                              for x in sorted(m["labels"])).replace("\\", "\\\\")
            head = f"{i} (X-GM-MSGID {m['gm']} X-GM-LABELS ({labels}) UID {u}"
            if "BODY" in args[1]:
                out += [(f"{head} BODY[]<0> {{{len(m['raw'])}}}".encode(), m["raw"]), b")"]
            else:
                out.append(f"{head})".encode())
        return "OK", out


def gmail_factory(address, password, host):
    return Gmail(address, password, host, imap_class=FakeIMAP)


DELAY = 0.15


def ask(system, user, schema):
    time.sleep(DELAY)  # feel of a real model
    for sender, subject, _text, theme, status, _day in SAMPLES:
        if subject in user:
            return json.dumps({"theme": theme, "status": status})
    return "{}"
