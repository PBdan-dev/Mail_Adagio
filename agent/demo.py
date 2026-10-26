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


BULK = {"Newsletters", "Promotions", "Job Search", "Social & Notifications", "Shopping & Deliveries"}


# more to read in the reader window, for a few made-up mails
LONGER = {
    "Sunday lunch?": ("Hi love,\n\nAre you coming on Sunday? Bring dessert.\n\nYour aunt and Paul will be there too, "
                      "around 12:30. Tell me if you want me to pick you up at the station.\n\nLove,\nMum"),
    "Your application: next steps": ("Hello,\n\nThank you for your application for the Data Analyst role. Could you "
                                     "share your availability for an interview?\n\nWe have slots on Tuesday and "
                                     "Thursday afternoon, 30 minutes by video.\n\nBest regards,\nAnna\nTalent team"),
    "Your October invoice: 64.20 EUR": ("Hello,\n\nYour October invoice is ready: 64.20 EUR.\nPayment due on 5 "
                                        "November, by direct debit.\n\nThe invoice is attached as a PDF.\n\nPower & Co"),
}
CC = {"Sunday lunch?": "Paul <paul@family.example>, Aunt Rose <rose@family.example>",
      "Your application: next steps": "Hiring team <hiring@talent.example>"}
NEWSLETTER_HTML = """<html><body style="font-family:Georgia,serif;margin:0;padding:24px;background:#fbfaf7;color:#222">
<h1 style="font-size:22px;margin:0 0 6px">This week: small models, big wins</h1>
<p style="color:#666;margin:0 0 18px">Five links worth your time.</p>
<img src="https://ainotes.example/banner.png" alt="Banner" width="560" height="120" style="display:block;background:#ddd">
<ol><li><a href="https://ainotes.example/1">Running a 9B model on one GPU</a></li>
<li><a href="https://ainotes.example/2">Why JSON schemas make small models reliable</a></li>
<li><a href="https://ainotes.example/3">Benchmarks you can trust</a></li></ol>
<p style="font-size:12px;color:#888">You get this because you subscribed. <a href="https://ainotes.example/unsubscribe">Unsubscribe</a></p>
</body></html>"""


def _raw(sender, subject, text, when, theme=""):
    m = EmailMessage()
    m["From"], m["Subject"], m["To"] = sender, subject, "you@gmail.com"
    if subject in CC:
        m["Cc"] = CC[subject]
    m["Date"] = format_datetime(when)
    if theme in BULK and "talent" not in sender:  # mailing lists carry an unsubscribe link
        site = sender.split("@")[1].rstrip(">")
        m["List-Unsubscribe"] = f"<mailto:leave@{site}>, <https://{site}/unsubscribe?u=demo>"
    m.set_content(LONGER.get(subject, text))
    if theme == "Newsletters":
        m.add_alternative(NEWSLETTER_HTML, subtype="html")
    if "invoice" in subject:
        m.add_attachment(b"%PDF-1.4 made-up invoice " * 2000, maintype="application", subtype="pdf",
                         filename="invoice-october.pdf")
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
            for sender, subject, text, theme, _s, day in SAMPLES:
                n += 1
                when = start - timedelta(days=24 - day + 7 * week)
                cls.mails[5000 + 10_000 - n] = {"gm": str(1879950646965534236 + n), "labels": {"\\Inbox"},
                                                "raw": _raw(sender, subject, text, when, theme), "date": when,
                                                "seen": n % 3 == 0}

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
        lines = [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\All) "/" "[Gmail]/All Mail"',
                 b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Trash"']
        lines += [f'(\\HasNoChildren) "/" "{utf7_encode(n)}"'.encode() for n in sorted(names | self.created)]
        return "OK", lines

    created = set()

    def create(self, name):
        FakeIMAP.commands.append(("CREATE", name))
        FakeIMAP.created.add(utf7_decode(name.strip('"')))
        return "OK", [b"Success"]

    def rename(self, old, new):
        FakeIMAP.commands.append(("RENAME", old, new))
        old, new = utf7_decode(old.strip('"')), utf7_decode(new.strip('"'))
        for m in self.mails.values():
            if old in m["labels"]:
                m["labels"] = (m["labels"] - {old}) | {new}
        if old in FakeIMAP.created:
            FakeIMAP.created = (FakeIMAP.created - {old}) | {new}
        return "OK", [b"Success"]

    def select(self, mailbox, readonly=False):
        self.folder = utf7_decode(mailbox.strip('"'))
        return "OK", [str(len(self.visible())).encode()]

    def visible(self):
        """{uid in this folder: mail}"""
        live = {u: m for u, m in self.mails.items() if "\\Trash" not in m["labels"]}
        if self.folder == "INBOX":
            return {u - 5000: m for u, m in live.items() if "\\Inbox" in m["labels"]}
        if self.folder == "[Gmail]/Trash":
            return {u: m for u, m in self.mails.items() if "\\Trash" in m["labels"]}
        if self.folder == "[Gmail]/All Mail":
            return live
        return {u: m for u, m in live.items() if self.folder in m["labels"]}  # a label

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
        if command == "COPY":  # out of the Trash, back with this label
            label = utf7_decode(args[1].strip('"'))
            for u in uids:
                if u in box:
                    box[u]["labels"] = (box[u]["labels"] - {"\\Trash"}) | {label}
            return "OK", []
        if command == "STORE" and "FLAGS" in args[1]:
            for u in uids:
                if u in box:
                    box[u]["seen"] = args[1][0] == "+"
            return "OK", []
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
            if args[1] == "(FLAGS)":
                flags = "\\Seen" if m["seen"] else ""
                out.append(f"{i} (FLAGS ({flags}) UID {u})".encode())
                continue
            head = f"{i} (X-GM-MSGID {m['gm']} X-GM-LABELS ({labels}) UID {u}"
            if "BODY" in args[1]:
                out += [(f"{head} BODY[]<0> {{{len(m['raw'])}}}".encode(), m["raw"]), b")"]
            else:
                out.append(f"{head})".encode())
        return "OK", out


def gmail_factory(address, password, host):
    return Gmail(address, password, host, imap_class=FakeIMAP)


DELAY = 0.15


UNSURE = {  # subject: (reason, confidence) for the mails the made-up model is not sure about
    "You are on a 30-day streak": ("A learning app reminder: could be a notification or a newsletter, "
                                   "nothing to act on.", "low"),
    "A game on your wishlist is on sale": ("A sale on a game you follow: a promotion, but it could also "
                                           "be shopping.", "low"),
}


def ask(system, user, schema):
    time.sleep(DELAY)  # feel of a real model
    for sender, subject, _text, theme, status, _day in SAMPLES:
        if subject in user:
            reason, confidence = UNSURE.get(subject, (f"The sender and the subject point to {theme}.", "high"))
            if "reason" in schema.get("properties", {}):
                return json.dumps({"reason": reason})
            return json.dumps({"theme": theme, "status": status, "confidence": confidence,
                               "to_delete": theme == "Promotions"})
    return "{}"
