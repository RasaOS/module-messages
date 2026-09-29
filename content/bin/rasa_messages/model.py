# -*- coding: utf-8 -*-
"""model.py — what a messages ledger is: its paths, its kinds, its record types, its config.

Everything the CLI and the validator share about rasa.module.messages lives
here, so neither re-derives it. The family conventions live in records.py.

The message KIND is the load-bearing axis of this module. The kind table
below is the same law content/schemas/message.schema.json expresses as
allOf + if/then blocks; the two are kept in agreement and checked against
each other.
"""

import datetime as _dt
import os
import re
from collections import OrderedDict

from . import records as R

MODULE = "messages"
ELEMENT = "rasa.module.messages"
VERSION = "0.1.0"
ROOT = "messages"
CLI = "messages"
CHECK = "check-messages"
CONFIG_NAME = "messages.config.yml"
CONFIG_SCHEMA_VALUE = "rasa.module.messages/1"

PREFIX_TYPE = {"THR": "thread", "MSG": "message"}
HERE = os.path.dirname(os.path.abspath(__file__))

ATTACH_DIR = "attachments"
DEFAULT_STALE_AFTER = 7

STATUSES = ("draft", "sent", "received", "discarded")
LIVE = ("sent", "received")                   # it happened; the message is frozen
UNSENT = ("draft", "discarded")               # it never happened; no `at`
OUTCOMES = ("answered", "missed", "declined", "busy", "failed", "voicemail", "blocked")
TRANSCRIPTS = ("machine", "human")
ROLES = ("file", "image", "audio", "video", "recording", "scan", "page")
EXTRA_KEYS = ("service", "outcome", "duration", "transcript")
#: the only status changes there are; everything else is frozen or terminal
TRANSITIONS = (("draft", "sent"), ("draft", "discarded"))

RX_THREAD_DIR = re.compile(r"^(THR-[0-9]{4,})(?:-([a-z0-9]+(?:-[a-z0-9]+)*))?$")
RX_RECORD_STEM = re.compile(r"^(THR|MSG)-[0-9]{4,}(?:-[a-z0-9]+(?:-[a-z0-9]+)*)?$")
RX_STATUS_NOTE = re.compile(r"^status=(draft|sent|received|discarded)\b")

# A text message can come from a short code or an alphanumeric sender name
# (a bank, a pharmacy). Those are real addresses a phone shows, so the text
# kinds accept them as well as numbers and family ids.
_RX_SENDER = re.compile(r"^(?=.*[A-Za-z])[A-Za-z0-9][A-Za-z0-9 ._&-]{1,10}$")


def _fmt_text_address(v):
    return R.FORMATS["phone-or-ref"](v) or bool(_RX_SENDER.match(v))


R.register_format("phone-or-sender-or-ref", _fmt_text_address,
                  label="a phone number, a sender name (2-11 letters and digits) or a family id")
R.register_format("phone-or-email-or-sender-or-ref",
                  lambda v: _fmt_text_address(v) or R.FORMATS["email-or-ref"](v),
                  label="a phone number, an email address, a sender name or a family id")
R.register_format("phone-or-email", lambda v: R.FORMATS["phone"](v) or R.FORMATS["email"](v),
                  label="a phone number or an email address")
R.register_format("text", lambda v: bool(v.strip()), label="non-empty text")


# ---------------------------------------------------------------------------
# The kind registry — module vocabulary, fixed (each kind has its own law)
# ---------------------------------------------------------------------------

class Kind(object):
    """One kind of message and its field law."""

    def __init__(self, name, channel, address, composable, copies, roles, max_files, fields,
                 required, day_ok, blurb):
        self.name = name
        self.channel = channel            # the channel directory its threads live in
        self.address = address            # the records.py format every address must meet
        self.composable = composable      # may be drafted (draft / reply / mark-sent)
        self.copies = copies              # which of cc / bcc it carries
        self.roles = roles                # attachment roles it may carry (empty = none)
        self.max_files = max_files        # at most this many attachments (None = any)
        self.fields = fields              # which of service/outcome/duration/transcript it has
        self.required = required          # which of those it must carry
        self.day_ok = day_ok              # `at` may be a bare day (a letter arrives on a day)
        self.blurb = blurb


_MEDIA = ("file", "image", "audio", "video")

KINDS = OrderedDict((k.name, k) for k in (
    Kind("email", "email", "email-or-ref", True, ("cc", "bcc"), _MEDIA, None, (), (), False,
         "an email; the thread's H1 is the subject"),
    Kind("sms", "text", "phone-or-sender-or-ref", True, (), (), 0, (), (), False,
         "a plain text message; no attachments"),
    Kind("mms", "text", "phone-or-sender-or-ref", True, (), _MEDIA, None, (), (), False,
         "a text message carrying media"),
    Kind("imessage", "text", "phone-or-email-or-ref", True, (), _MEDIA, None, (), (), False,
         "a message over a phone number or an email address"),
    Kind("rcs", "text", "phone-or-sender-or-ref", True, (), _MEDIA, None, (), (), False,
         "a rich text message"),
    Kind("chat", "chat", "chat-handle-or-ref", True, (), _MEDIA, None, ("service",), ("service",),
         False, "a message on a chat service, addressed <service>:<handle>"),
    Kind("call", "voice", "phone-or-ref", False, (), ("recording",), 1, ("outcome", "duration"),
         ("outcome",), False, "a phone call; the body is call notes"),
    Kind("voicemail", "voice", "phone-or-ref", False, (), ("audio",), 1,
         ("duration", "transcript"), (), False,
         "a voicemail; the body is its transcript, the audio an attachment"),
    Kind("letter", "post", "text-or-ref", True, ("cc",), ("scan", "image", "file"), None, (), (),
         True, "a letter on paper; scans as attachments"),
    Kind("fax", "post", "phone-or-ref", True, (), ("page", "file"), None, (), (), False,
         "a fax; pages as attachments"),
))

CHANNELS = ("email", "text", "chat", "voice", "post")
#: what an address must look like on a thread in that channel (its participants)
CHANNEL_ADDRESS = OrderedDict([
    ("email", "email-or-ref"),
    ("text", "phone-or-email-or-sender-or-ref"),
    ("chat", "chat-handle-or-ref"),
    ("voice", "phone-or-ref"),
    ("post", "text-or-ref"),
])
#: an identity's own address (never a family id: it is the literal address you write from)
IDENTITY_ADDRESS = OrderedDict([
    ("email", "email"),
    ("text", "phone-or-email"),
    ("chat", "chat-handle"),
    ("voice", "phone"),
    ("post", "text"),
])

COMPOSABLE = tuple(k for k, v in KINDS.items() if v.composable)
RECORD_ONLY = tuple(k for k, v in KINDS.items() if not v.composable)
RX_NONPERSON_REF = re.compile(r"^(?:(?:NOTE|DEC|EVT|LEAD|THR|MSG)-[0-9]{4,}"
                              r"|acct-[a-z0-9]+(?:-[a-z0-9]+)*|TASK-\S+)$")


def kinds_in(channel):
    return [k for k in KINDS.values() if k.channel == channel]


def address_ok(fmt, value):
    """An address in the form `fmt` names, and never a record that is not a person."""
    if not isinstance(value, str) or not value.strip():
        return False
    if RX_NONPERSON_REF.match(value):
        return False
    return bool(R.FORMATS[fmt](value))


# ---------------------------------------------------------------------------
# Addresses — stored literally, compared by key
# ---------------------------------------------------------------------------

_RX_EXT = re.compile(r"(?i)\s*(?:x|ext\.?)\s*\d+$")


def address_key(value):
    """A comparison key for an address. The stored address is never changed.

    Email addresses compare case-insensitively; phone numbers by their digits
    (the last ten when there are at least ten, so +1 555 010 0100 and
    (555) 010-0100 are one number); chat handles case-insensitively; family
    ids exactly; anything else (a postal address) by its words.
    """
    v = (value or "").strip()
    if R.is_family_ref(v):
        return v
    low = v.lower()
    if low.startswith("mailto:"):
        v, low = v[7:], low[7:]
    if low.startswith("tel:"):
        v, low = v[4:], low[4:]
    if R.FORMATS["email"](v):
        return "mail:" + low
    if R.FORMATS["phone"](v):
        digits = re.sub(r"\D", "", _RX_EXT.sub("", v))
        return "tel:" + (digits[-10:] if len(digits) >= 10 else digits)
    if R.FORMATS["chat-handle"](low):
        return "chat:" + low
    return "text:" + " ".join(low.split())


def participant_set(addresses):
    return frozenset(address_key(a) for a in addresses if a)


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------

def zone_name(cfg):
    """The zone wall-clock input is read in: config `timezone`, else an IANA $TZ, else None."""
    tz = cfg.get("timezone") if isinstance(cfg.get("timezone"), str) else None
    if tz and R.is_iana_tz(tz):
        return tz
    env = os.environ.get("TZ", "").lstrip(":")
    if env and "/" in env and R.is_iana_tz(env):
        return env
    return None


def resolve_at(expr, cfg, day_ok=False):
    """When a message happened, resolved at capture. -> RFC 3339 text (or a day for letters).

    An RFC 3339 datetime keeps the offset it was given (the offset is part of
    the fact). 'now' is the current instant in UTC. A wall-clock time needs a
    zone: config `timezone`, or an IANA $TZ. A bare day is accepted only for a
    kind whose `at` may be a day (a letter).
    """
    e = (expr or "").strip()
    if not e:
        raise R.Refusal("when did it happen? pass --at (RFC 3339 with an offset, 'now', or "
                        "'YYYY-MM-DD HH:MM' with a timezone declared)")
    dt = R.parse_datetime(e)
    if dt is None and e.lower() == "now":
        dt = R.now_utc()
    if dt is None and day_ok and " " not in e and ":" not in e:
        day = R.resolve_day(e)
        if day > R.today():
            raise R.Refusal("%s is in the future; a message is recorded once it has happened"
                            % day.isoformat())
        return day.isoformat()
    if dt is None:
        tz = zone_name(cfg)
        if tz is None:
            raise R.Refusal("cannot read %r without a timezone: give it with an offset "
                            "(2026-09-28T14:00:00-04:00), say 'now', or declare `timezone` in "
                            "%s/%s" % (expr, ROOT, CONFIG_NAME))
        dt = R.resolve_when(e, tz)
    if dt > R.now_utc() + _dt.timedelta(minutes=1):
        raise R.Refusal("%s is in the future; a message is recorded once it has happened"
                        % R.format_datetime(dt))
    return R.format_datetime(dt)


def at_instant(value):
    """`at` text -> an aware UTC datetime (a bare day is its start, UTC), or None."""
    if not isinstance(value, str):
        return None
    dt = R.parse_datetime(value)
    if dt is not None:
        return dt.astimezone(_dt.timezone.utc)
    d = R.parse_date(value)
    if d is not None:
        return _dt.datetime(d.year, d.month, d.day, tzinfo=_dt.timezone.utc)
    return None


def sort_key(msg):
    """Chronological order: things that happened by `at`; drafts after them, by id."""
    inst = at_instant(msg.fm.get("at"))
    parsed = R.parse_id(msg.id) if msg.id else None
    num = parsed[1] if parsed and isinstance(parsed[1], int) else 0
    if inst is None:
        return (1, "", num)
    return (0, inst.strftime("%Y-%m-%dT%H:%M:%S"), num)


def render_record(fm, body, schema=None, info=None):
    """The record's text, exactly as the tool writes it (records-family/1 core >= 1.0.1)."""
    return R.render_record(fm, body, schema, info)


def schema_dir():
    """Installed: .claude/schemas/messages/. Element source: content/schemas/."""
    for cand in (os.path.join(HERE, "..", "..", "schemas", MODULE),
                 os.path.join(HERE, "..", "..", "schemas")):
        if os.path.exists(os.path.join(cand, "message.schema.json")):
            return os.path.normpath(cand)
    raise R.EnvError("cannot find the messages schemas next to %s (expected "
                     ".claude/schemas/messages/message.schema.json)" % HERE)


# ---------------------------------------------------------------------------
# Where a record is
# ---------------------------------------------------------------------------

class Place(object):
    """What a record's directory says: archived?, channel, thread directory."""

    def __init__(self, rel_dir):
        parts = [p for p in rel_dir.split("/") if p] if rel_dir else []
        self.archived = bool(parts) and parts[0] == R.ARCHIVE_DIR
        if self.archived:
            parts = parts[1:]
        self.parts = parts
        self.channel = parts[0] if parts else None
        self.thread_dir = parts[1] if len(parts) >= 2 else None
        m = RX_THREAD_DIR.match(self.thread_dir or "")
        self.thread_id = m.group(1) if m else None
        self.problem = None
        if not rel_dir:
            self.problem = ("a record lives inside a thread directory "
                            "(<channel>/THR-nnnn-<slug>/), not at the top of %s/" % ROOT)
        elif len(parts) != 2:
            self.problem = ("a record lives directly inside a thread directory "
                            "(<channel>/THR-nnnn-<slug>/), not in %s/%s/" % (ROOT, rel_dir))
        elif self.thread_id is None:
            self.problem = ("'%s' is not a thread directory (THR-nnnn or THR-nnnn-<slug>)"
                            % self.thread_dir)

    @property
    def ok(self):
        return self.problem is None and self.channel in CHANNELS

    @property
    def token(self):
        """The history place: the thread directory named by its id alone (the slug is cosmetic)."""
        base = "%s/%s" % (self.channel, self.thread_id)
        return (R.ARCHIVE_DIR + "/" + base) if self.archived else base


def place_of(rec):
    p = Place(rec.dir)
    return p.token if p.ok else None


def in_attachments(rel):
    """True for anything under a thread's attachments/ directory (never a record)."""
    return ATTACH_DIR in rel.split("/")[:-1]


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------

class Thread(object):
    """A thread record and the messages beside it."""

    def __init__(self, rec):
        self.rec = rec
        self.messages = []

    @property
    def id(self):
        return self.rec.id

    @property
    def dir(self):
        return os.path.dirname(self.rec.path)

    @property
    def place(self):
        return Place(self.rec.dir)

    @property
    def channel(self):
        return self.place.channel

    @property
    def archived(self):
        return self.place.archived

    @property
    def title(self):
        return self.rec.h1 or "(untitled)"

    def participants(self):
        v = self.rec.fm.get("participants")
        return [p for p in v if isinstance(p, dict)] if isinstance(v, list) else []

    def participant_keys(self):
        return participant_set(p.get("who") for p in self.participants())

    def live(self):
        """Messages that happened (sent or received), in order."""
        return sorted([m for m in self.messages if m.fm.get("status") in LIVE], key=sort_key)

    def ordered(self):
        return sorted(self.messages, key=sort_key)


class Ctx(object):
    """One project's messages ledger."""

    def __init__(self, project_root=None, bin_dir=None):
        self.project = R.find_project_root(project_root, bin_dir)
        self.root = os.path.join(self.project, ROOT)
        self.history_path = os.path.join(self.root, "history.tsv")
        self.config_path = os.path.join(self.root, CONFIG_NAME)
        sd = schema_dir()
        self.schemas = {
            "thread": R.load_json(os.path.join(sd, "thread.schema.json")),
            "message": R.load_json(os.path.join(sd, "message.schema.json")),
            "config": R.load_json(os.path.join(sd, "config.schema.json")),
        }
        self.config, self.config_problems, self.config_exists = R.load_config(
            self.config_path, self.schemas["config"], self.rel_root(CONFIG_NAME))
        self._directory = None

    # -- paths -------------------------------------------------------------

    def rel_root(self, rel):
        return ROOT + "/" + rel if rel else ROOT

    def rel_of(self, rec):
        return self.rel_root(rec.rel)

    def abs(self, rel):
        return os.path.join(self.root, *rel.split("/"))

    def require_root(self):
        if not os.path.isdir(self.root):
            raise R.EnvError("no messages ledger at %s — install rasa.module.messages (bin/init) "
                             "or pass --root" % self.root)

    # -- config ------------------------------------------------------------

    def usable_config(self):
        if self.config_problems:
            first = self.config_problems[0]
            raise R.EnvError("%s is not valid (line %s: %s); run %s" % (
                self.rel_root(CONFIG_NAME), first[1], first[2], CHECK))
        return self.config

    def declared(self, key):
        return R.declared(self.config, key)

    def identities(self, channel=None):
        v = self.config.get("identities")
        out = [i for i in v if isinstance(i, dict)] if isinstance(v, list) else []
        return [i for i in out if channel is None or i.get("channel") == channel]

    def identity_keys(self, channel=None):
        return set(address_key(i.get("address")) for i in self.identities(channel)
                   if i.get("address"))

    def identity_by_id(self, ident_id):
        for i in self.identities():
            if i.get("id") == ident_id:
                return i
        return None

    def identity_for(self, channel, wanted=None, why="write", kind=None):
        """THE HARD-STOP: the identity a message on `channel` is written as. Never guessed.

        With `kind`, only identities whose address suits that kind are candidates
        (the post channel holds letters, addressed on paper, and faxes, by number).
        """
        mine = self.identities(channel)
        if not mine:
            raise R.Refusal(
                "HARD-STOP: no identity is declared for the %s channel. Who you may %s as is the "
                "one thing this module refuses to guess: declare it under `identities` in %s "
                "(see .claude/messages-gate.md), e.g.  identities:  - {id: my-%s, channel: %s, "
                "address: <your address>}" % (channel, why, self.rel_root(CONFIG_NAME), channel,
                                               channel))
        fits = [i for i in mine if kind is None or address_ok(kind.address, i.get("address"))]
        if wanted:
            for i in mine:
                if i.get("id") == wanted:
                    if i not in fits:
                        raise R.Refusal("identity %s (%s) is not %s address" % (
                            wanted, i.get("address"), "a " + kind.name))
                    return i
            raise R.Refusal("--as %s is not a declared %s identity (declared: %s)" % (
                wanted, channel, ", ".join(i.get("id", "?") for i in mine)))
        if not fits:
            raise R.Refusal("HARD-STOP: none of the declared %s identities (%s) is an address "
                            "%s can come from; declare one" % (
                                channel, ", ".join(i.get("id", "?") for i in mine),
                                "a " + kind.name))
        if len(fits) > 1:
            raise R.Refusal("several %s identities are declared (%s); say which with --as" % (
                channel, ", ".join(i.get("id", "?") for i in fits)))
        return fits[0]

    def identity_optional(self, channel, wanted=None, kind=None):
        """An identity when one is unambiguous, else None (for kinds with no hard-stop)."""
        if wanted:
            return self.identity_for(channel, wanted, kind=kind)
        fits = [i for i in self.identities(channel)
                if kind is None or address_ok(kind.address, i.get("address"))]
        return fits[0] if len(fits) == 1 else None

    @property
    def stale_after(self):
        v = self.config.get("stale_after")
        return int(v) if isinstance(v, str) and v.isdigit() and int(v) > 0 else \
            DEFAULT_STALE_AFTER

    def check_channel(self, channel):
        if channel not in CHANNELS:
            raise R.Refusal("'%s' is not a channel (%s)" % (channel, ", ".join(CHANNELS)))
        allowed = self.declared("channels")
        if allowed is not None and channel not in allowed:
            raise R.Refusal("the %s channel is not declared in %s (declared: %s)" % (
                channel, self.rel_root(CONFIG_NAME), ", ".join(allowed)))
        return channel

    # -- people (crm, read at view time) -----------------------------------

    @property
    def directory(self):
        if self._directory is None:
            self._directory = Directory(self.project)
        return self._directory

    # -- records -----------------------------------------------------------

    @staticmethod
    def is_record_name(stem, rel):
        return bool(RX_RECORD_STEM.match(stem)) and not in_attachments(rel)

    def scan(self):
        found, strays = R.scan_root(self.root, self.is_record_name)
        strays = [s for s in strays if not in_attachments(s)]
        return [R.read_record(p, rel) for p, rel in found], strays

    def schema_for(self, rec):
        t = PREFIX_TYPE.get(rec.prefix or "")
        if t is None:
            m = RX_RECORD_STEM.match(rec.stem)
            t = PREFIX_TYPE.get(m.group(1)) if m else None
        return self.schemas.get(t) if t else None

    def threads(self, records=None):
        """-> OrderedDict thread id -> Thread (messages attached by directory)."""
        if records is None:
            records, _ = self.scan()
        out = OrderedDict()
        by_dir = {}
        for r in records:
            if r.prefix == "THR" and r.parsed and r.id and r.id not in out:
                t = Thread(r)
                out[r.id] = t
                by_dir.setdefault(r.dir, t)
        for r in records:
            if r.prefix == "MSG" and r.parsed and r.dir in by_dir:
                by_dir[r.dir].messages.append(r)
        return out

    @staticmethod
    def thread_of(msg, threads):
        for t in threads.values():
            if t.rec.dir == msg.dir:
                return t
        return None

    def find(self, rid, prefixes=("THR", "MSG")):
        """-> the Record for an id, or Refusal."""
        if not R.RX_NUMBERED_ID.match(rid or "") or R.ref_prefix(rid) not in prefixes:
            raise R.Refusal("'%s' is not a %s id (%s)" % (
                rid, " or ".join(PREFIX_TYPE[p] for p in prefixes),
                ", ".join("%s-0001" % p for p in prefixes)))
        records, _ = self.scan()
        hits = [r for r in records if r.id == rid]
        if not hits:
            raise R.Refusal("no record %s in %s/" % (rid, ROOT))
        if len(hits) > 1:
            raise R.Refusal("%s is used by %d files; run %s" % (rid, len(hits), CHECK))
        if not hits[0].parsed:
            raise R.Refusal("%s does not parse; run %s" % (rid, CHECK))
        return hits[0]

    # -- writing -----------------------------------------------------------

    def lock(self):
        return R.Lock(self.root)

    def history(self):
        rows, _, _ = R.load_history(self.history_path)
        return rows

    def write(self, abs_path, fm, body, schema, info=None):
        """Render, self-validate, and atomically write one record. -> text."""
        text = R.verify_roundtrip(render_record(fm, body, schema, info), fm)
        problems = R.schema_errors(schema, fm)
        if problems:
            inv, path, msg = problems[0]
            raise R.Refusal("refusing to write an invalid record: %s %s (%s)" % (
                path or "(record)", msg, inv))
        R.atomic_write(abs_path, text)
        return text

    def refresh_digests(self, paths):
        """Re-read records just written and store their digests in one pass."""
        table, _ = R.load_digests(self.root)
        for p in paths:
            rec = R.read_record(p, R.rel_posix(p, self.root))
            if not rec.id:
                continue
            table[rec.id] = R.digest_entry(rec, is_frozen(rec),
                                           mutable_keys(rec, self.schema_for(rec)),
                                           table.get(rec.id))
        R.save_digests(self.root, table)

    def refresh_digest(self, rec_or_path):
        path = rec_or_path.path if isinstance(rec_or_path, R.Record) else rec_or_path
        self.refresh_digests([path])

    def log(self, entries):
        return R.append_history(self.history_path, entries)


def is_frozen(rec, schema=None):
    """A message is frozen once it happened: status sent or received."""
    return rec.prefix == "MSG" and rec.fm.get("status") in LIVE


def mutable_keys(rec, schema):
    return list((schema or {}).get("x-mutable-when-frozen") or [])


def base_schema(schema):
    """The record schema without its allOf law blocks (those are reported under M- ids)."""
    return OrderedDict((k, v) for k, v in schema.items() if k != "allOf")


def filing_status(rows):
    """The status a message was filed with (the `status=` note on its filing line), or None."""
    if not rows:
        return None
    m = RX_STATUS_NOTE.match(rows[0].note or "")
    return m.group(1) if m else None


def logged_status(rows):
    """The status the history says a message has now, or None when it never said."""
    return R.last_status(rows, "status") or filing_status(rows)


# ---------------------------------------------------------------------------
# People — crm cards, resolved at VIEW time, read-only
# ---------------------------------------------------------------------------

class Directory(object):
    """The address book of rasa.module.crm, when it is mounted — read, never written.

    Addresses on messages are stored literally (the address used is the
    fact). This resolves them to contact cards for display, timelines and
    exports: every `email`, `phone` and `handle` on each active card in
    crm/contacts/.
    """

    def __init__(self, project):
        self.cards = OrderedDict()
        self.by_key = {}
        self.mounted = os.path.isdir(os.path.join(project, "crm"))
        root = os.path.join(project, "crm", "contacts")
        if not os.path.isdir(root):
            return
        for fn in sorted(os.listdir(root)):
            if not fn.endswith(".md") or not re.match(r"^CARD-[0-9]{4,}(?:-|\.md$)", fn):
                continue
            rec = R.read_record(os.path.join(root, fn), "contacts/" + fn)
            if not rec.parsed or not rec.id or R.ref_prefix(rec.id) != "CARD":
                continue
            card = {"id": rec.id,
                    "name": rec.h1 or _formatted_name(rec.fm.get("name")) or rec.id,
                    "emails": _values(rec.fm.get("email")), "phones": _values(rec.fm.get("phone")),
                    "handles": _handles(rec.fm.get("handle"))}
            card["keys"] = set([rec.id] + [address_key(v) for v in
                                           card["emails"] + card["phones"] + card["handles"]])
            self.cards[rec.id] = card
            for k in card["keys"]:
                self.by_key.setdefault(k, rec.id)

    def card_for(self, address):
        """-> the card id an address belongs to, or None."""
        if not address:
            return None
        if R.ref_prefix(address) == "CARD":
            return address if address in self.cards else None
        return self.by_key.get(address_key(address))

    def name_for(self, address):
        cid = self.card_for(address)
        return self.cards[cid]["name"] if cid else None

    def keys_for(self, ref_or_address):
        """Every key that means this person: the card and all its addresses, or the address."""
        cid = self.card_for(ref_or_address)
        if cid:
            return set(self.cards[cid]["keys"])
        return {address_key(ref_or_address)}

    def email_for(self, card_id):
        c = self.cards.get(card_id)
        return c["emails"][0] if c and c["emails"] else None


def _values(v):
    out = []
    if isinstance(v, list):
        items = sorted(v, key=lambda x: not (isinstance(x, dict) and x.get("pref") == "true"))
        for x in items:
            if isinstance(x, dict) and isinstance(x.get("value"), str):
                out.append(x["value"])
            elif isinstance(x, str):
                out.append(x)
    elif isinstance(v, str):
        out.append(v)
    return out


def _handles(v):
    out = []
    if isinstance(v, list):
        for x in v:
            if isinstance(x, dict) and x.get("service") and x.get("value"):
                out.append("%s:%s" % (x["service"], x["value"]))
            elif isinstance(x, str):
                out.append(x)
    return out


def _formatted_name(v):
    if isinstance(v, dict):
        return " ".join(v[k] for k in ("prefix", "given", "additional", "family", "suffix")
                        if isinstance(v.get(k), str)) or None
    return v if isinstance(v, str) else None


# ---------------------------------------------------------------------------
# Derived views — computed, never stored
# ---------------------------------------------------------------------------

def thread_state(thread, stale_after, now=None):
    """What a thread needs, derived from its messages at the moment of asking.

    needs_reply  the last message that happened was received
    waiting      the last message that happened was sent, longer ago than stale_after days
                 (neither, when it was an answered call: that conversation was had)
    unseen       received messages with no `seen` stamp
    drafts       open drafts
    """
    now = now or R.now_utc()
    live = thread.live()
    last = live[-1] if live else None
    had = bool(last) and last.fm.get("kind") == "call" and last.fm.get("outcome") == "answered"
    needs_reply = bool(last) and last.fm.get("status") == "received" and not had
    waiting = False
    if last is not None and last.fm.get("status") == "sent" and not had:
        inst = at_instant(last.fm.get("at"))
        waiting = inst is not None and (now - inst) > _dt.timedelta(days=stale_after)
    unseen = [m for m in thread.ordered()
              if m.fm.get("status") == "received" and "seen" not in m.fm]
    drafts = [m for m in thread.ordered() if m.fm.get("status") == "draft"]
    return OrderedDict([("last", last), ("needs_reply", needs_reply), ("waiting", waiting),
                        ("unseen", unseen), ("drafts", drafts)])


def addresses_of(msg):
    out = []
    if isinstance(msg.fm.get("from"), str):
        out.append(msg.fm["from"])
    for key in ("to", "cc", "bcc"):
        v = msg.fm.get(key)
        if isinstance(v, list):
            out.extend(x for x in v if isinstance(x, str))
    return out


def counterparts(addresses, mine):
    """The other people: every address that is not one of my identity keys, first form kept."""
    seen, out = set(), []
    for a in addresses:
        k = address_key(a)
        if k in mine or k in seen:
            continue
        seen.add(k)
        out.append(a)
    return out


def summary(rec, thread=None):
    """A JSON-ready view of a record."""
    d = OrderedDict([("id", rec.id), ("type", PREFIX_TYPE.get(rec.prefix or "", "?")),
                     ("path", ROOT + "/" + rec.rel)])
    place = Place(rec.dir)
    if rec.prefix == "THR":
        d["title"] = rec.h1
    d["channel"] = place.channel
    d["archived"] = place.archived
    if thread is not None:
        d["thread"] = thread.id
    d["frontmatter"] = rec.fm
    return d
