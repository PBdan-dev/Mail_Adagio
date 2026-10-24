"""Made-up mails to try the interface without a Gmail account (`python adagio.py ui --demo`)."""
import json
import time
from email.message import EmailMessage

from .mail import Gmail

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


def _raw(sender, subject, text, day):
    m = EmailMessage()
    m["From"], m["Subject"], m["To"] = sender, subject, "you@gmail.com"
    m["Date"] = f"{['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][(day + 2) % 7]}, {day} Oct 2026 09:00:00 +0200"
    m.set_content(text)
    return m.as_bytes()


class FakeIMAP:
    def __init__(self, host):
        pass

    def login(self, *a):
        pass

    def select(self, mailbox, readonly=False):
        return "OK", [b"1284"]

    def uid(self, command, *args):
        if command == "SEARCH":
            return "OK", [" ".join(str(1000 + i) for i in range(len(SAMPLES))).encode()]
        out = []
        for u in (int(x) for x in args[0].split(",")):
            sender, subject, text, *_rest, day = SAMPLES[u - 1000]
            raw = _raw(sender, subject, text, day)
            meta = f"1 (X-GM-MSGID {1879950646965534236 + u} X-GM-LABELS () UID {u} BODY[]<0> {{{len(raw)}}}"
            out += [(meta.encode(), raw), b")"]
        return "OK", out

    def logout(self):
        pass


def gmail_factory(address, password, host):
    return Gmail(address, password, host, imap_class=FakeIMAP)


def ask(system, user, schema):
    time.sleep(0.15)  # feel of a real model
    for sender, subject, _text, theme, status, _day in SAMPLES:
        if subject in user:
            return json.dumps({"theme": theme, "status": status})
    return "{}"
