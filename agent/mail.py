"""Gmail over IMAP: read mails, and (step 3) add labels and move mails out of the Inbox.

Never deletes: the only writes are adding/removing Gmail labels (\\Inbox is a label too).
"""
import base64
import email
import html as htmllib
import imaplib
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from email import policy
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

FETCH_BYTES = 30000  # first 30 KB of each mail: enough for the text, skips big attachments


class MailError(Exception):
    def __init__(self, message="", code=""):
        super().__init__(message)
        self.code = code  # gmail_login | offline | "" : lets the page show the right help


LOGIN_HELP = ("Gmail refused the login for {address}. Check these 3 things: "
              "1) IMAP is on: in Gmail, gear > See all settings > Forwarding and POP/IMAP > Enable IMAP. "
              "2) You use an app password (16 letters), not your Google password. Create one at "
              "https://myaccount.google.com/apppasswords (it needs 2-Step Verification). "
              "3) Paste it in Mail Adagio > Settings.")


@dataclass
class Mail:
    uid: int                # IMAP id inside the mailbox
    gm_msgid: str           # Gmail's permanent id for the message
    date: str               # ISO date, "" if unknown
    sender: str
    subject: str
    labels: list = field(default_factory=list)  # labels already on the mail
    body: str = ""          # plain text extract, kept in memory only
    unsubscribe: str = ""   # unsubscribe link (List-Unsubscribe header, else a link in the mail)


@dataclass
class Header:
    """What the sender scan reads: headers only, no content."""
    uid: int
    gm_msgid: str
    date: str
    sender: str
    unsubscribe: str = ""


class Gmail:
    """Small wrapper around imaplib for Gmail."""

    def __init__(self, address, app_password, host="imap.gmail.com", imap_class=imaplib.IMAP4_SSL):
        self.address, self.password, self.host = address, app_password, host
        self.imap_class = imap_class
        self.imap = None

    def __enter__(self):
        try:
            self.imap = self.imap_class(self.host)
            self.imap.login(self.address, self.password)
        except imaplib.IMAP4.error as e:
            raise MailError(LOGIN_HELP.format(address=self.address), "gmail_login") from e
        except OSError as e:
            raise MailError(f"Cannot reach Gmail ({self.host}). Check your internet connection. ({e})",
                            "offline") from e
        return self

    def __exit__(self, *exc):
        try:
            self.imap.logout()
        except Exception:
            pass

    def open(self, mailbox="INBOX", readonly=True):
        """Open a mailbox (read-only unless asked) and return its number of mails."""
        status, data = self.imap.select(quote(mailbox), readonly=readonly)
        if status != "OK":
            raise MailError(f"Cannot open mailbox '{mailbox}'")
        return int(data[0])

    def recent_uids(self, limit=None, since_days=None, max_uid=None):
        """Mail ids, newest first (only up to max_uid if given: ignores mails that arrived later)."""
        criteria = ["ALL"]
        if since_days:
            day = date.today() - timedelta(days=since_days)
            criteria = ["SINCE", day.strftime("%d-%b-%Y")]
        status, data = self.imap.uid("SEARCH", None, *criteria)
        if status != "OK":
            raise MailError("Search failed")
        uids = [int(u) for u in data[0].split() if not max_uid or int(u) <= max_uid]
        uids.sort(reverse=True)
        return uids[:limit] if limit else uids

    def fetch(self, uids, body_chars=1500):
        """Fetch the given mails (headers, existing labels, start of the text)."""
        mails = []
        for chunk in chunks(uids, 50):
            uid_set = ",".join(str(u) for u in chunk)
            status, data = self.imap.uid(
                "FETCH", uid_set, f"(X-GM-MSGID X-GM-LABELS BODY.PEEK[]<0.{FETCH_BYTES}>)")
            if status != "OK":
                raise MailError("Fetch failed")
            for item in data:
                if isinstance(item, tuple):
                    mails.append(parse_fetch(item[0], item[1], body_chars))
        mails.sort(key=lambda m: m.uid, reverse=True)
        return mails

    def headers(self, uids):
        """From, Date and List-Unsubscribe of many mails, fast: the mail content is not downloaded."""
        out = []
        for chunk in chunks(list(uids), 500):
            status, data = self.imap.uid("FETCH", ",".join(map(str, chunk)),
                                         "(X-GM-MSGID BODY.PEEK[HEADER.FIELDS (FROM DATE LIST-UNSUBSCRIBE)])")
            if status != "OK":
                raise MailError("Fetch failed")
            for item in data:
                if not isinstance(item, tuple):
                    continue
                meta = item[0].decode("utf-8", "replace")
                uid, msgid = re.search(r"UID (\d+)", meta), re.search(r"X-GM-MSGID (\d+)", meta)
                msg = email.message_from_bytes(item[1], policy=policy.default)
                if uid:
                    out.append(Header(int(uid.group(1)), msgid.group(1) if msgid else "",
                                      parse_date(msg.get("Date")), clean(str(msg.get("From", ""))),
                                      unsubscribe_header(str(msg.get("List-Unsubscribe", "")))))
        return out

    # --- step 3: writing labels -------------------------------------------------
    def folders(self):
        """{name: flags} of every folder and label."""
        status, data = self.imap.list()
        if status != "OK":
            raise MailError("Cannot list the labels")
        out = {}
        for line in data:
            m = re.match(rb'\((.*?)\) "(?:[^"]*)" (.*)$', line or b"")
            if m:
                name = m.group(2).decode().strip()
                name = name[1:-1].replace('\\"', '"').replace("\\\\", "\\") if name.startswith('"') else name
                out[utf7_decode(name)] = m.group(1).decode()
        return out

    def all_mail(self, folders=None):
        """Name of the "All Mail" folder (it depends on the Gmail language)."""
        for name, flags in (folders or self.folders()).items():
            if "\\All" in flags.split():
                return name
        return "[Gmail]/All Mail"

    def special(self, flag, default, folders=None):
        """Name of a special folder ("\\Trash"...), which depends on the Gmail language."""
        for name, flags in (folders or self.folders()).items():
            if flag in flags.split():
                return name
        return default

    def to_trash(self, uids):
        """Move to Gmail's Trash (Gmail empties it after 30 days). Never a permanent delete."""
        self.change_labels(uids, add=["\\Trash"])

    def copy(self, uids, label):
        """Copy to a label: out of the Trash, a mail goes back with this label."""
        for chunk in chunks(sorted(uids), 200):
            status, _ = self.imap.uid("COPY", ",".join(map(str, chunk)), quote(label))
            if status != "OK":
                raise MailError(f"Gmail refused to copy mails to '{label}'")

    def create_label(self, name):
        status, data = self.imap.create(quote(name))
        if status != "OK" and b"ALREADYEXISTS" not in b" ".join(d or b"" for d in data).upper():
            raise MailError(f"Cannot create the label '{name}'")

    def rename_label(self, old, new):
        """Gmail keeps the mails and the colour of a renamed label."""
        status, data = self.imap.rename(quote(old), quote(new))
        if status != "OK":
            raise MailError(f"Cannot rename the label '{old}' to '{new}'")

    def find(self, gm_msgids):
        """{gm_msgid: uid} in the open folder (open All Mail to find a mail wherever it is)."""
        found = {}
        for gm in gm_msgids:
            status, data = self.imap.uid("SEARCH", None, "X-GM-MSGID", str(gm))
            ids = data[0].split() if status == "OK" and data and data[0] else []
            if ids:
                found[str(gm)] = int(ids[-1])
        return found

    def meta(self, uids):
        """{uid: (gm_msgid, [labels])} for mails of the open folder (\\Inbox included). No content."""
        out = {}
        for chunk in chunks(list(uids), 500):
            status, data = self.imap.uid("FETCH", ",".join(map(str, chunk)), "(X-GM-MSGID X-GM-LABELS)")
            if status != "OK":
                raise MailError("Fetch failed")
            for item in data:
                line = (item[0] if isinstance(item, tuple) else item or b"").decode("utf-8", "replace")
                uid = re.search(r"UID (\d+)", line)
                msgid = re.search(r"X-GM-MSGID (\d+)", line)
                labels = re.search(r"X-GM-LABELS \((.*?)\)(?= UID| X-GM|\s*\)?\s*$)", line)
                if uid:
                    out[int(uid.group(1))] = (msgid.group(1) if msgid else "",
                                              parse_labels(labels.group(1)) if labels else [])
        return out

    def unread(self, uids):
        """The mails among `uids` (open folder) that are not read yet."""
        out = set()
        for chunk in chunks(sorted(uids), 500):
            status, data = self.imap.uid("FETCH", ",".join(map(str, chunk)), "(FLAGS)")
            if status != "OK":
                raise MailError("Fetch failed")
            for item in data:
                line = (item[0] if isinstance(item, tuple) else item or b"").decode("utf-8", "replace")
                uid, flags = re.search(r"UID (\d+)", line), re.search(r"FLAGS \((.*?)\)", line)
                if uid and flags is not None and "\\Seen" not in flags.group(1).split():
                    out.add(int(uid.group(1)))
        return out

    def mark_read(self, uids, read=True):
        for chunk in chunks(sorted(uids), 200):
            status, _ = self.imap.uid("STORE", ",".join(map(str, chunk)), "+FLAGS" if read else "-FLAGS", "(\\Seen)")
            if status != "OK":
                raise MailError("Gmail refused to mark mails as read")

    def change_labels(self, uids, add=(), remove=()):
        """Add or remove labels on mails of the open folder. "\\Inbox" removed = archived."""
        for op, labels in (("+X-GM-LABELS", add), ("-X-GM-LABELS", remove)):
            if not labels or not uids:
                continue
            value = "(" + " ".join(label if label.startswith("\\") else quote(label)
                                   for label in labels) + ")"
            for chunk in chunks(sorted(uids), 200):
                status, _ = self.imap.uid("STORE", ",".join(map(str, chunk)), op, value)
                if status != "OK":
                    raise MailError(f"Gmail refused to change labels ({op} {value})")


def parse_fetch(meta, raw, body_chars):
    meta = meta.decode("utf-8", "replace")
    uid = int(re.search(r"UID (\d+)", meta).group(1))
    msgid = re.search(r"X-GM-MSGID (\d+)", meta)
    labels = re.search(r"X-GM-LABELS \((.*?)\)(?= UID| BODY| X-GM|\s*$)", meta)
    msg = email.message_from_bytes(raw, policy=policy.default)
    return Mail(
        uid=uid,
        gm_msgid=msgid.group(1) if msgid else "",
        date=parse_date(msg.get("Date")),
        sender=clean(str(msg.get("From", ""))),
        subject=clean(str(msg.get("Subject", ""))),
        labels=parse_labels(labels.group(1)) if labels else [],
        body=extract_text(msg)[:body_chars],
        unsubscribe=unsubscribe_header(str(msg.get("List-Unsubscribe", ""))) or unsubscribe_link(msg),
    )


def unsubscribe_header(value):
    """'<mailto:x@y.com>, <https://y.com/u?id=1>' -> the web link if any, else the mailto."""
    links = re.findall(r"<\s*([^>\s]+)\s*>", value or "")
    web = [x for x in links if x.lower().startswith("https://") or x.lower().startswith("http://")]
    mail = [x for x in links if x.lower().startswith("mailto:")]
    return (web or mail or [""])[0]


UNSUB_WORDS = re.compile(r"unsubscribe|d[ée]sabonn|d[ée]sinscri|se d[ée]sinscrire|opt[ -]?out|abmelden|darse de baja",
                         re.I)


def unsubscribe_link(msg):
    """The unsubscribe link written in the mail itself (when there is no List-Unsubscribe header)."""
    for part in msg.walk():
        if part.get_content_type() != "text/html" or part.is_attachment():
            continue
        html = part_text(part)
        for m in re.finditer(r'<a\b[^>]*href\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I | re.S):
            href = htmllib.unescape(m.group(1).strip())
            text = htmllib.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))
            if href.lower().startswith(("https://", "http://")) and (UNSUB_WORDS.search(text) or UNSUB_WORDS.search(href)):
                return href
    return ""


def parse_labels(text):
    """'"\\\\Important" Finance "My &AOk-t&AOk-"' -> ['\\Important', 'Finance', 'My été']"""
    found = re.findall(r'"((?:[^"\\]|\\.)*)"|(\S+)', text)
    labels = [(q.replace('\\\\', '\\').replace('\\"', '"') if q else bare) for q, bare in found]
    return [utf7_decode(label) for label in labels]


def parse_date(value):
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except Exception:
        return ""


def extract_text(msg):
    """Plain text of a mail: the text/plain part if any, else the HTML part without tags."""
    plain = html = None
    for part in msg.walk():
        kind = part.get_content_type()
        if part.is_attachment() or kind not in ("text/plain", "text/html"):
            continue
        content = part_text(part)
        if kind == "text/plain" and plain is None:
            plain = content
        elif kind == "text/html" and html is None:
            html = content
    text = plain if plain and plain.strip() else strip_html(html or "")
    return clean(text)


def part_text(part):
    try:
        return part.get_content()
    except Exception:  # truncated or badly encoded part: decode what we can
        payload = part.get_payload(decode=True) or b""
        return payload.decode(part.get_content_charset() or "utf-8", "replace")


class _TextOnly(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        self.skip += tag in ("script", "style", "head")

    def handle_endtag(self, tag):
        self.skip -= tag in ("script", "style", "head") and self.skip > 0

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def strip_html(html):
    parser = _TextOnly()
    try:
        parser.feed(html)
    except Exception:
        pass
    return " ".join(parser.parts)


def clean(text):
    return re.sub(r"\s+", " ", text or "").strip()


def chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def quote(mailbox):
    return '"' + utf7_encode(mailbox).replace("\\", "\\\\").replace('"', '\\"') + '"'


# IMAP writes non-ASCII folder and label names in "modified UTF-7" (RFC 3501).
def utf7_decode(text):
    def repl(m):
        chunk = m.group(1)
        if not chunk:
            return "&"
        chunk = chunk.replace(",", "/")
        chunk += "=" * (-len(chunk) % 4)
        return base64.b64decode(chunk).decode("utf-16-be")
    return re.sub(r"&([^-]*)-", repl, text)


def utf7_encode(text):
    out, buf = [], []

    def flush():
        if buf:
            raw = "".join(buf).encode("utf-16-be")
            out.append("&" + base64.b64encode(raw).decode().rstrip("=").replace("/", ",") + "-")
            buf.clear()
    for ch in text:
        if 0x20 <= ord(ch) <= 0x7E:
            flush()
            out.append("&-" if ch == "&" else ch)
        else:
            buf.append(ch)
    flush()
    return "".join(out)
