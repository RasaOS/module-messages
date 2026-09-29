# -*- coding: utf-8 -*-
"""mail.py — the file interop of rasa.module.messages.

In:   .eml files and mbox mailboxes (the standard library's `email` with
      policy.default, and `mailbox`), and the XML written by the phone app
      "SMS Backup & Restore" (text messages, picture messages, and the
      separate call-log file), read with `xml.etree.ElementTree`.
Out:  one email draft as an RFC 5322 message (email.message.EmailMessage),
      for the person to open and send from their own mail program.

Everything here reads or writes FILES the person hands over. Nothing opens a
connection, fetches, syncs or sends — that belongs to Connections, not to
this module.
"""

import base64
import datetime as _dt
import email
import email.utils
import hashlib
import mailbox
import mimetypes
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections import OrderedDict
from email import policy
from email.message import EmailMessage
from html.parser import HTMLParser

from . import model as M
from . import records as R


class Incoming(object):
    """One message read from a file, before it is filed."""

    def __init__(self, kind, source):
        self.kind = kind
        self.source = source          # where it came from, for the report
        self.status = None            # sent | received
        self.at = None                # RFC 3339 text
        self.from_ = None             # an address, or None = "me" (filled from the identity)
        self.to = []
        self.cc = []
        self.bcc = []
        self.names = {}               # address key -> display name as the file carried it
        self.subject = None
        self.body = ""
        self.external_id = None
        self.refs = []                # external ids this one answers (nearest first)
        self.attachments = []         # [(name, bytes, role)]
        self.outcome = None
        self.duration = None
        self.service = None

    def addresses(self):
        out = [self.from_] if self.from_ else []
        return out + list(self.to) + list(self.cc) + list(self.bcc)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

_RX_SUBJECT_PREFIX = re.compile(r"^\s*(?:(?:re|fwd?|aw|sv|antw|wg|tr)\s*(?:\[\d+\])?\s*:\s*)+",
                                re.I)


def normalize_subject(subject):
    """Display form of a subject: one line, Re:/Fwd: prefixes removed."""
    s = " ".join((subject or "").split())
    s = _RX_SUBJECT_PREFIX.sub("", s).strip()
    return s or "(no subject)"


_CONTROL = dict.fromkeys(c for c in range(32) if c not in (9, 10))


def clean_body(text):
    """LF only, no control characters, no trailing spaces, no blank edges."""
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n").translate(_CONTROL)
    lines = [ln.rstrip() for ln in t.split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def one_line(text):
    return " ".join((text or "").split())


def safe_name(name):
    """A file name that is safe on every system: ascii words, the extension kept."""
    base = os.path.basename((name or "").replace("\\", "/"))
    norm = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode("ascii")
    stem, ext = os.path.splitext(norm)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-.").lower()[:60]
    ext = re.sub(r"[^A-Za-z0-9.]+", "", ext)[:10]
    return (stem or "file") + ext.lower()


def role_for(kind, name, ctype=None):
    """The attachment role a file takes on a message of `kind`."""
    k = M.KINDS[kind]
    if not k.roles:
        raise R.Refusal("%s %s carries no attachments" % (
            "an" if kind[0] in "aeiou" else "a", kind))
    for preferred in ("recording", "audio", "scan", "page"):
        if k.roles[0] == preferred:
            return preferred
    ctype = ctype or (mimetypes.guess_type(name or "")[0] or "")
    main = ctype.split("/", 1)[0]
    guess = {"image": "image", "audio": "audio", "video": "video"}.get(main, "file")
    if guess in k.roles:
        return guess
    return "file" if "file" in k.roles else k.roles[0]


def attachment_path(thread_dir, msg_id, name, taken=()):
    """-> (relative path 'attachments/<MSG>-<name>', absolute path), never overwriting."""
    safe = safe_name(name)
    stem, ext = os.path.splitext(safe)
    n = 1
    while True:
        fn = "%s-%s%s" % (msg_id, stem if n == 1 else "%s-%d" % (stem, n), ext)
        rel = M.ATTACH_DIR + "/" + fn
        full = os.path.join(thread_dir, M.ATTACH_DIR, fn)
        if not os.path.exists(full) and rel not in taken:
            return rel, full
        n += 1


def write_bytes(full, data):
    """Write a file atomically (temp + replace), creating its directory."""
    d = os.path.dirname(full)
    os.makedirs(d, exist_ok=True)
    tmp = full + ".tmp-%d" % os.getpid()
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, full)


def stable_id(prefix, *parts):
    h = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()
    return "%s:%s" % (prefix, h[:24])


def epoch_ms(text):
    """Milliseconds since 1970 -> RFC 3339 UTC text, or None."""
    try:
        ms = int(str(text).strip())
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    dt = _dt.datetime.fromtimestamp(ms / 1000.0, tz=_dt.timezone.utc).replace(microsecond=0)
    return R.format_datetime(dt)


# ---------------------------------------------------------------------------
# HTML to text (for an email with no plain-text part)
# ---------------------------------------------------------------------------

class _Text(HTMLParser):
    BLOCK = {"p", "div", "tr", "table", "ul", "ol", "blockquote", "section", "article",
             "header", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "hr"}
    SKIP = {"script", "style", "head", "title"}

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.out = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "br":
            self.out.append("\n")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag in ("td", "th"):
            self.out.append(" ")
        elif tag in self.BLOCK:
            self.out.append("\n\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.out.append("\n\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(re.sub(r"[ \t\r\n]+", " ", data))


def html_to_text(markup):
    p = _Text()
    try:
        p.feed(markup or "")
        p.close()
    except Exception:  # a malformed page still yields whatever text was read
        pass
    text = "".join(p.out)
    lines = [" ".join(ln.split()) for ln in text.split("\n")]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---------------------------------------------------------------------------
# .eml and mbox
# ---------------------------------------------------------------------------

def _addresses(msg, header):
    out = []
    for h in msg.get_all(header) or []:
        pairs = []
        try:
            for a in getattr(h, "addresses", ()) or ():
                pairs.append((a.display_name or "", a.addr_spec or ""))
        except Exception:  # a defective header: fall back to the plain parser
            pairs = []
        if not pairs:
            pairs = email.utils.getaddresses([str(h)])
        for name, addr in pairs:
            addr = (addr or "").strip()
            if addr:
                out.append((one_line(name), addr))
    return out


def _date(msg):
    h = msg.get("Date")
    if h is None:
        return None
    dt = getattr(h, "datetime", None)
    if dt is None:
        try:
            dt = email.utils.parsedate_to_datetime(str(h))
        except (TypeError, ValueError, IndexError):
            return None
    if dt is None:
        return None
    if dt.tzinfo is None:          # RFC 5322 "-0000": the time is UTC, the local zone unknown
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.replace(microsecond=0)


def _body(msg):
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
    except Exception:
        part = None
    if part is None:
        return ""
    try:
        content = part.get_content()
    except Exception:
        payload = part.get_payload(decode=True) or b""
        content = payload.decode("utf-8", "replace")
    if isinstance(content, bytes):
        content = content.decode("utf-8", "replace")
    if part.get_content_subtype() == "html":
        content = html_to_text(content)
    return clean_body(content)


def _files(msg):
    out = []
    try:
        parts = list(msg.iter_attachments())
    except Exception:
        parts = []
    for part in parts:
        ctype = part.get_content_type()
        name = part.get_filename()
        data = None
        if ctype == "message/rfc822":
            try:
                data = bytes(part.get_content())
            except Exception:
                data = None
            name = name or "message.eml"
        if data is None:
            data = part.get_payload(decode=True)
        if data is None:
            data = part.as_bytes()
        if not name:
            name = "attachment" + (mimetypes.guess_extension(ctype) or ".bin")
        out.append((one_line(name), data, ctype))
    return out


def _ids(text):
    found = re.findall(r"<([^<>\s]+)>", text or "")
    return found or [t for t in (text or "").split() if t]


def read_eml(raw, source, mine):
    """One RFC 5322 message -> (Incoming, None) or (None, why it was not read).

    `mine` is the set of address keys of the declared email identities: a
    message from one of them is one you sent; anything else was received.
    """
    msg = email.message_from_bytes(raw, policy=policy.default)
    froms = [(n, a) for n, a in _addresses(msg, "From") if R.FORMATS["email"](a)]
    if not froms:
        return None, "%s: no readable From address" % source
    at = _date(msg)
    if at is None:
        return None, "%s: no readable Date" % source
    item = Incoming("email", source)
    item.at = R.format_datetime(at)
    name, sender = froms[0]
    item.from_ = sender
    item.status = "sent" if M.address_key(sender) in mine else "received"
    dropped = 0
    for header, target in (("To", item.to), ("Cc", item.cc), ("Bcc", item.bcc)):
        for n, a in _addresses(msg, header):
            if not R.FORMATS["email"](a):
                dropped += 1
                continue
            if M.address_key(a) not in set(M.address_key(x) for x in target):
                target.append(a)
            if n:
                item.names.setdefault(M.address_key(a), n)
    if name:
        item.names.setdefault(M.address_key(sender), name)
    item.subject = normalize_subject(str(msg.get("Subject") or ""))
    item.body = _body(msg)
    mid = _ids(str(msg.get("Message-ID") or ""))
    item.external_id = mid[0] if mid else stable_id("eml", hashlib.sha256(raw).hexdigest())
    refs = _ids(str(msg.get("In-Reply-To") or ""))
    refs += list(reversed(_ids(str(msg.get("References") or ""))))
    item.refs = list(OrderedDict.fromkeys(r for r in refs if r != item.external_id))
    for fname, data, ctype in _files(msg):
        item.attachments.append((fname, data, role_for("email", fname, ctype)))
    if item.status == "sent" and not item.to:
        return None, "%s: sent, but no readable recipient" % source
    return item, ("%s: %d unreadable address(es) left out" % (source, dropped)) if dropped else None


def read_eml_files(paths, mine):
    items, notes = [], []
    for p in paths:
        try:
            with open(p, "rb") as fh:
                raw = fh.read()
        except OSError as exc:
            raise R.Refusal("cannot read %s: %s" % (p, exc))
        item, why = read_eml(raw, os.path.basename(p), mine)
        if item:
            items.append(item)
        if why:
            notes.append(why)
    return items, notes


def read_mbox(path, mine):
    if not os.path.isfile(path):
        raise R.Refusal("no mailbox file at %s" % path)
    items, notes = [], []
    box = mailbox.mbox(path, create=False)
    try:
        for n, key in enumerate(box.iterkeys(), start=1):
            item, why = read_eml(box.get_bytes(key), "%s #%d" % (os.path.basename(path), n),
                                 mine)
            if item:
                items.append(item)
            if why:
                notes.append(why)
    finally:
        box.close()
    return items, notes


# ---------------------------------------------------------------------------
# SMS Backup & Restore XML — texts, picture messages, and the call log
# ---------------------------------------------------------------------------

_NULLS = ("", "null", "(unknown)", "unknown")
_WITHHELD = ("", "-1", "-2", "-3", "unknown", "private", "withheld", "anonymous")
#: picture-message address roles: 137 from, 151 to, 130 cc, 129 bcc
_MMS_FROM, _MMS_TO = ("137",), ("151", "130", "129")
#: call-log type -> (status, outcome)
_CALL_TYPES = {"1": ("received", "answered"), "2": ("sent", None), "3": ("received", "missed"),
               "4": ("received", "voicemail"), "5": ("received", "declined"),
               "6": ("received", "blocked")}


def _attr(el, name):
    v = el.get(name)
    return None if v is None or v.strip().lower() in ("null",) else v


def _name(el):
    v = (el.get("contact_name") or "").strip()
    return None if v.lower() in _NULLS else v


def _split(addr):
    return [a.strip() for a in (addr or "").split("~") if a.strip()]


def read_sms_xml(path):
    """-> (items, notes). Reads a text-message backup or a call-log backup, whichever it is."""
    if not os.path.isfile(path):
        raise R.Refusal("no file at %s" % path)
    items, notes = [], []
    skipped = OrderedDict()
    origin = os.path.basename(path)
    try:
        for _event, el in ET.iterparse(path, events=("end",)):
            if el.tag == "sms":
                _sms(el, origin, items, skipped)
                el.clear()
            elif el.tag == "mms":
                _mms(el, origin, items, skipped)
                el.clear()
            elif el.tag == "call":
                _call(el, origin, items, skipped)
                el.clear()
    except ET.ParseError as exc:
        raise R.Refusal("%s is not readable XML (%s)" % (path, exc))
    for why, n in skipped.items():
        notes.append("%s: %d left out — %s" % (origin, n, why))
    return items, notes


def _skip(skipped, why):
    skipped[why] = skipped.get(why, 0) + 1


def _sms(el, origin, items, skipped):
    status = {"1": "received", "2": "sent"}.get(el.get("type"))
    if status is None:
        return _skip(skipped, "texts that are drafts, queued or failed (only sent and received "
                              "are messages)")
    at = epoch_ms(el.get("date"))
    addrs = _split(el.get("address"))
    if not at or not addrs or not all(M.address_ok("phone-or-sender-or-ref", a) for a in addrs):
        return _skip(skipped, "texts with no readable date or address")
    item = Incoming("sms", origin)
    item.status, item.at = status, at
    body = el.get("body")
    item.body = clean_body("" if body is None or body == "null" else body)
    if status == "received":
        item.from_ = addrs[0]
    else:
        item.to = addrs
    name = _name(el)
    if name and "," not in name and len(addrs) == 1:
        item.names[M.address_key(addrs[0])] = name
    item.external_id = stable_id("sms", el.get("date"), el.get("type"),
                                 ",".join(sorted(M.address_key(a) for a in addrs)), item.body)
    items.append(item)


def _mms(el, origin, items, skipped):
    status = {"1": "received", "2": "sent"}.get(el.get("msg_box"))
    if status is None:
        return _skip(skipped, "picture messages that are drafts or unsent")
    at = epoch_ms(el.get("date"))
    if not at:
        return _skip(skipped, "picture messages with no readable date")
    frm, to = [], []
    for a in el.iter("addr"):
        v = (a.get("address") or "").strip()
        if not v or not M.address_ok("phone-or-sender-or-ref", v):
            continue
        if a.get("type") in _MMS_FROM:
            frm.append(v)
        elif a.get("type") in _MMS_TO:
            to.append(v)
    if not frm and not to:
        parts = _split(el.get("address"))
        if status == "received":
            frm, to = parts[:1], parts[1:]
        else:
            to = parts
    to = [a for a in to if M.address_ok("phone-or-sender-or-ref", a)]
    if (status == "received" and not frm) or (status == "sent" and not to):
        return _skip(skipped, "picture messages with no readable address")
    item = Incoming("mms", origin)
    item.status, item.at = status, at
    if status == "received":
        item.from_, item.to = frm[0], [a for a in to if M.address_key(a) != M.address_key(frm[0])]
    else:
        item.to = to
    texts, names = [], []
    for part in el.iter("part"):
        ctype = (part.get("ct") or "").lower()
        if ctype == "application/smil":
            continue
        if ctype.startswith("text/plain"):
            t = _attr(part, "text")
            if t:
                texts.append(t)
            continue
        data = part.get("data")
        if not data:
            continue
        try:
            blob = base64.b64decode(data)
        except (ValueError, TypeError):
            _skip(skipped, "picture-message parts whose media would not decode")
            continue
        fname = _attr(part, "fn") or _attr(part, "name") or _attr(part, "cl") or \
            ("part" + (mimetypes.guess_extension(ctype) or ".bin"))
        names.append(fname)
        item.attachments.append((one_line(fname), blob, role_for("mms", fname, ctype)))
    item.body = clean_body("\n".join(texts))
    name = _name(el)
    # a group message's contact_name lists everyone ("Jane Doe, Bob"): it names
    # nobody in particular, so it is kept only for a one-to-one message
    if name and "," not in name and len(_split(el.get("address"))) == 1:
        other = item.from_ if status == "received" else item.to[0]
        item.names[M.address_key(other)] = name
    item.external_id = stable_id("mms", el.get("date"), el.get("msg_box"),
                                 ",".join(sorted(M.address_key(a) for a in item.addresses())),
                                 item.body, ",".join(names))
    items.append(item)


def _call(el, origin, items, skipped):
    kind = _CALL_TYPES.get(el.get("type"))
    if kind is None:
        return _skip(skipped, "call-log entries of a type this module does not know")
    number = (el.get("number") or "").strip()
    if number.lower() in _WITHHELD or not M.address_ok("phone-or-ref", number):
        return _skip(skipped, "calls from a withheld or unreadable number (no address to record)")
    at = epoch_ms(el.get("date"))
    if not at:
        return _skip(skipped, "calls with no readable date")
    try:
        seconds = max(0, int(el.get("duration") or 0))
    except ValueError:
        seconds = 0
    status, outcome = kind
    if outcome is None:                       # an outgoing call: did anyone pick up?
        outcome = "answered" if seconds > 0 else "missed"
    item = Incoming("call", origin)
    item.status, item.at, item.outcome = status, at, outcome
    if outcome == "answered" or seconds > 0:
        item.duration = R.format_duration(_dt.timedelta(seconds=seconds))
    if status == "received":
        item.from_ = number
    else:
        item.to = [number]
    name = _name(el)
    if name:
        item.names[M.address_key(number)] = name
    item.external_id = stable_id("call", el.get("date"), el.get("type"), M.address_key(number),
                                 seconds)
    items.append(item)


# ---------------------------------------------------------------------------
# Out: an email draft as an RFC 5322 message
# ---------------------------------------------------------------------------

def eml_bytes(ctx, msg, thread, by_id):
    """The draft as a message your own mail program opens and sends (adding Date, Message-ID)."""
    d = ctx.directory

    def header_addr(v):
        if R.ref_prefix(v) == "CARD":
            e = d.email_for(v)
            if not e:
                raise R.Refusal("%s has no email address this project knows (crm %s); give the "
                                "address instead" % (v, "is not mounted" if not d.mounted else
                                                     "card has none"))
            return email.utils.formataddr((d.cards[v]["name"], e))
        name = d.name_for(v)
        return email.utils.formataddr((name, v)) if name else v

    m = EmailMessage()
    m["From"] = header_addr(msg.fm["from"])
    m["To"] = ", ".join(header_addr(v) for v in msg.fm.get("to") or [])
    for key, header in (("cc", "Cc"), ("bcc", "Bcc")):
        if msg.fm.get(key):
            m[header] = ", ".join(header_addr(v) for v in msg.fm[key])
    subject = thread.title if thread.title != "(no subject)" else ""
    chain, cur, guard = [], msg.fm.get("in_reply_to"), 0
    while cur and cur in by_id and guard < 200:
        parent = by_id[cur]
        if isinstance(parent.fm.get("external_id"), str):
            chain.append(parent.fm["external_id"])
        cur, guard = parent.fm.get("in_reply_to"), guard + 1
    if msg.fm.get("in_reply_to") and not re.match(r"^\s*re\s*:", subject, re.I):
        subject = "Re: " + subject if subject else "Re:"
    m["Subject"] = subject
    if chain:
        m["In-Reply-To"] = "<%s>" % chain[0]
        m["References"] = " ".join("<%s>" % e for e in reversed(chain))
    m["X-Unsent"] = "1"
    m.set_content((msg.body or "").strip("\n") + "\n")
    for att in msg.fm.get("attachments") or []:
        if not isinstance(att, dict):
            continue
        full = os.path.join(thread.dir, *att.get("path", "").split("/"))
        try:
            with open(full, "rb") as fh:
                data = fh.read()
        except OSError:
            raise R.Refusal("attachment %s is missing; run %s" % (att.get("path"), M.CHECK))
        ctype = mimetypes.guess_type(att.get("name") or "")[0] or "application/octet-stream"
        main, sub = ctype.split("/", 1)
        if main == "text":
            m.add_attachment(data.decode("utf-8", "replace"), subtype=sub,
                             filename=att.get("name"))
        else:
            m.add_attachment(data, maintype=main, subtype=sub, filename=att.get("name"))
    return m.as_bytes(policy=policy.SMTP)
