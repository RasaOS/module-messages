# -*- coding: utf-8 -*-
"""records.py — the family core of the RasaOS personal-records family.

    rasa.module.notes · rasa.module.schedule · rasa.module.crm · rasa.module.messages

This file is BYTE-IDENTICAL in all four modules. Each module's own gate
(bin/check-manifest, check "family") proves that its copy hashes to the value
recorded in the sibling FAMILY file, and the four FAMILY files are compared
across the family before any of them ships. Edit it in all four in lockstep,
or not at all.

What lives here is everything the four modules agree on — the family
conventions F1..F15 of records-family/1, and the invariants F-01..F-27 that
enforce them:

    the records-yaml/1 frontmatter grammar (a strict subset of YAML 1.2)
    the serializer that writes it back canonically
    a JSON Schema (draft 2020-12 subset) evaluator — record law is data
    ids, slugs, dates, datetimes, durations, relative-date resolution
    the record file: frontmatter + body + H1 title
    the module root: scanning, locking, atomic writes, id allocation
    the history log (history.tsv) and the digest cache (.state/digests.tsv)
    family-wide reference resolution (frontmatter refs + [[wiki-links]])
    findings, reports, and the CLI conventions (actor, project root, exit codes)

Pure Python 3 standard library (3.9+). No network, no version control, no
third-party packages — a project with none of those runs every line of it.
"""

import datetime as _dt
import errno
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
from collections import OrderedDict

try:  # Python 3.9+
    import zoneinfo as _zoneinfo
except ImportError:  # pragma: no cover - the family requires 3.9, this is belt and braces
    _zoneinfo = None

FAMILY = "records-family/1"
CORE_VERSION = "1.0.1"

# ---------------------------------------------------------------------------
# The family registry
# ---------------------------------------------------------------------------

#: module name -> its root directory at the project root
FAMILY_ROOTS = OrderedDict([
    ("notes", "notes"),
    ("schedule", "schedule"),
    ("crm", "crm"),
    ("messages", "messages"),
    ("tasks", "tasks"),  # rasa.module.tasks — resolved read-only, never written
])

#: id prefix -> owning module
PREFIX_MODULE = OrderedDict([
    ("NOTE", "notes"),
    ("DEC", "notes"),
    ("EVT", "schedule"),
    ("CARD", "crm"),
    ("LEAD", "crm"),
    ("acct", "crm"),
    ("THR", "messages"),
    ("MSG", "messages"),
    ("TASK", "tasks"),
])

NUMBERED_PREFIXES = ("NOTE", "DEC", "EVT", "CARD", "LEAD", "THR", "MSG")
ID_WIDTH = 4

RX_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
RX_NUMBERED_ID = re.compile(r"^(NOTE|DEC|EVT|CARD|LEAD|THR|MSG)-([0-9]{4,})$")
RX_ACCT_ID = re.compile(r"^acct-([a-z0-9]+(?:-[a-z0-9]+)*)$")
RX_TASK_ID = re.compile(r"^TASK-(?:([A-Z][A-Z0-9.]{1,5})-)?([0-9]{1,6})([a-z])?$")
_FAMILY_REF_BODY = (r"(?:(?:NOTE|DEC|EVT|CARD|LEAD|THR|MSG)-[0-9]{4,}"
                    r"|acct-[a-z0-9]+(?:-[a-z0-9]+)*"
                    r"|TASK-(?:[A-Z][A-Z0-9.]{1,5}-)?[0-9]{1,6}[a-z]?)")
RX_FAMILY_REF = re.compile(r"^" + _FAMILY_REF_BODY + r"$")
RX_ACTOR = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")
RX_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
RX_DATETIME = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?"
    r"(Z|[+-]\d{2}:\d{2})$")
RX_PARTIAL_DATE = re.compile(r"^--(\d{2})-(\d{2})$")
RX_DURATION = re.compile(
    r"^([+-])?P(?:(\d+)W|(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?)$")
RX_KEY_LINE = re.compile(r"^(x-[a-z0-9][a-z0-9_-]*|[a-z][a-z0-9_]*):(?:[ \t](.*))?$")
RX_MAP_KEY = re.compile(r"^([a-z][a-z0-9_]*):(?:[ \t](.*))?$")
RX_WIKILINK = re.compile(r"\[\[([^\[\]|\n]+?)(?:\|([^\[\]\n]*))?\]\]")
RX_ACCT_MENTION = re.compile(r"(?<![\w@/])@(acct-[a-z0-9]+(?:-[a-z0-9]+)*)")

HISTORY_HEADER = ("at", "id", "verb", "from", "to", "actor", "note")
FILING_VERBS = ("new", "import")
RESERVED_NAMES = ("history.tsv", ".state", "_archive")
ARCHIVE_DIR = "_archive"
STATE_DIR = ".state"

ERROR, WARN, INFO = "ERROR", "WARN", "INFO"
_SEVERITY_ORDER = {ERROR: 0, WARN: 1, INFO: 2}

EXIT_OK, EXIT_REFUSED, EXIT_ENV = 0, 1, 2

DOC_BEGIN = "<!-- BEGIN records-family/1 -->"
DOC_END = "<!-- END records-family/1 -->"


# ---------------------------------------------------------------------------
# Exceptions — the CLI exit-code contract
# ---------------------------------------------------------------------------

class Refusal(Exception):
    """A precondition failed. Nothing was written. Exit 1."""


class EnvError(Exception):
    """The environment is unusable (no project, unreadable config, lock). Exit 2."""


# ---------------------------------------------------------------------------
# The clock — overridable for repeatable runs ($RASA_TODAY, $RASA_NOW)
# ---------------------------------------------------------------------------

def now_utc():
    """The current instant, aware, UTC, whole seconds. $RASA_NOW overrides."""
    forced = os.environ.get("RASA_NOW")
    if forced:
        dt = parse_datetime(forced)
        if dt is None:
            raise EnvError("RASA_NOW=%r is not an RFC 3339 datetime with an offset" % forced)
        return dt.astimezone(_dt.timezone.utc).replace(microsecond=0)
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def today():
    """Today's date. $RASA_TODAY overrides; else the date part of $RASA_NOW (UTC)."""
    forced = os.environ.get("RASA_TODAY")
    if forced:
        d = parse_date(forced)
        if d is None:
            raise EnvError("RASA_TODAY=%r is not a YYYY-MM-DD date" % forced)
        return d
    if os.environ.get("RASA_NOW"):
        return now_utc().date()
    return _dt.date.today()


def stamp(dt=None):
    """UTC history timestamp: YYYY-MM-DDTHH:MM:SSZ."""
    dt = dt or now_utc()
    return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Dates, datetimes, durations, timezones
# ---------------------------------------------------------------------------

def parse_date(text):
    m = RX_DATE.match(text or "")
    if not m:
        return None
    try:
        return _dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def parse_datetime(text):
    """RFC 3339 with an offset -> aware datetime; anything else (incl. naive) -> None."""
    m = RX_DATETIME.match(text or "")
    if not m:
        return None
    y, mo, d, hh, mm, ss, frac, off = m.groups()
    try:
        micro = int((frac or "0").ljust(6, "0")) if frac else 0
        if off == "Z":
            tz = _dt.timezone.utc
        else:
            sign = 1 if off[0] == "+" else -1
            oh, om = int(off[1:3]), int(off[4:6])
            if oh > 23 or om > 59:
                return None
            tz = _dt.timezone(sign * _dt.timedelta(hours=oh, minutes=om))
        return _dt.datetime(int(y), int(mo), int(d), int(hh), int(mm), int(ss or 0), micro, tz)
    except ValueError:
        return None


def parse_partial_date(text):
    """YYYY-MM-DD, or --MM-DD (vCard: a birthday with no year). Returns True/False."""
    if parse_date(text):
        return True
    m = RX_PARTIAL_DATE.match(text or "")
    if not m:
        return False
    try:
        _dt.date(2000, int(m.group(1)), int(m.group(2)))  # 2000 is a leap year
        return True
    except ValueError:
        return False


def format_datetime(dt):
    """Aware datetime -> RFC 3339, whole seconds, offset form (Z for UTC)."""
    if dt.tzinfo is None:
        raise ValueError("refusing to format a naive datetime")
    dt = dt.replace(microsecond=0)
    text = dt.isoformat()
    is_utc = dt.tzinfo is _dt.timezone.utc or getattr(dt.tzinfo, "key", None) in ("UTC", "Etc/UTC")
    if is_utc and text.endswith("+00:00"):
        text = text[:-6] + "Z"
    return text


def get_zone(name):
    """IANA name -> tzinfo, or None if it does not resolve."""
    if not name or _zoneinfo is None:
        return None
    if name in ("UTC", "Etc/UTC", "Z"):
        return _dt.timezone.utc
    try:
        return _zoneinfo.ZoneInfo(name)
    except Exception:  # ZoneInfoNotFoundError, ValueError on odd keys
        return None


def is_iana_tz(name):
    return bool(name) and get_zone(name) is not None and not name.startswith("/")


def parse_duration(text):
    """ISO 8601 duration (weeks, days, hours, minutes, seconds; signed) -> timedelta."""
    m = RX_DURATION.match(text or "")
    if not m or text.rstrip("+-") in ("P", "PT") or text.endswith("T"):
        return None
    sign, w, d, h, mi, s = m.groups()
    if not any((w, d, h, mi, s)):
        return None
    td = _dt.timedelta(weeks=int(w or 0), days=int(d or 0), hours=int(h or 0),
                       minutes=int(mi or 0), seconds=int(s or 0))
    return -td if sign == "-" else td


def format_duration(td):
    """timedelta -> ISO 8601 duration (e.g. -PT15M, P1D, PT1H30M)."""
    total = int(td.total_seconds())
    sign = "-" if total < 0 else ""
    total = abs(total)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    out = sign + "P"
    if days:
        out += "%dD" % days
    if hours or minutes or seconds:
        out += "T"
        if hours:
            out += "%dH" % hours
        if minutes:
            out += "%dM" % minutes
        if seconds:
            out += "%dS" % seconds
    if out in ("P", "-P"):
        out = "PT0S"
    return out


_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_RX_REL = re.compile(r"^([+-])(\d+)([dw])$")


def resolve_day(expr, base=None):
    """Resolve a day expression AT CAPTURE into an absolute date.

    Accepts YYYY-MM-DD, today, tomorrow, yesterday, +Nd/-Nd, +Nw/-Nw, and a
    weekday name (mon..sun, or the full name) meaning the next such day
    strictly after today. Returns a date or raises Refusal.
    """
    base = base or today()
    e = (expr or "").strip().lower()
    d = parse_date(e)
    if d:
        return d
    if e == "today":
        return base
    if e == "tomorrow":
        return base + _dt.timedelta(days=1)
    if e == "yesterday":
        return base - _dt.timedelta(days=1)
    m = _RX_REL.match(e)
    if m:
        n = int(m.group(2)) * (7 if m.group(3) == "w" else 1)
        return base + _dt.timedelta(days=n if m.group(1) == "+" else -n)
    for idx, name in enumerate(_WEEKDAYS):
        if e == name or (len(e) > 3 and e.startswith(name) and
                         e in ("monday", "tuesday", "wednesday", "thursday",
                               "friday", "saturday", "sunday")):
            ahead = (idx - base.weekday()) % 7 or 7
            return base + _dt.timedelta(days=ahead)
    raise Refusal("cannot read %r as a day; use YYYY-MM-DD, today, tomorrow, "
                  "+3d, -1w, or a weekday name" % expr)


_RX_CLOCK = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$")


def localize(naive, zone, tzname=""):
    """Attach a zone to a naive wall-clock time, refusing times that do not exist."""
    aware = naive.replace(tzinfo=zone)
    back = aware.astimezone(_dt.timezone.utc).astimezone(zone)
    if back.replace(tzinfo=None) != naive:
        raise Refusal("%s does not exist in %s (a daylight-saving gap); pick another time"
                      % (naive.strftime("%Y-%m-%d %H:%M"), tzname or "that timezone"))
    return aware


def resolve_when(expr, tzname, base=None):
    """Resolve a moment AT CAPTURE into an aware datetime in `tzname`.

    Accepts an RFC 3339 datetime with an offset (converted into tzname),
    'YYYY-MM-DD HH:MM' / 'YYYY-MM-DDTHH:MM' (wall clock in tzname),
    '<day expr> <clock>' (tomorrow 14:00, fri 9am), or 'now'.
    """
    zone = get_zone(tzname)
    if zone is None:
        raise Refusal("timezone %r does not resolve (IANA names, e.g. America/New_York)"
                      % tzname)
    e = (expr or "").strip()
    dt = parse_datetime(e)
    if dt:
        return dt.astimezone(zone)
    if e.lower() == "now":
        return now_utc().astimezone(zone)
    parts = e.replace("T", " ", 1).split(None, 1) if RX_DATE.match(e[:10] or "") \
        else e.split(None, 1)
    if len(parts) == 2:
        day = resolve_day(parts[0])
        m = _RX_CLOCK.match(parts[1].strip().lower())
        if not m:
            raise Refusal("cannot read the time %r; use HH:MM, 9am, 2:30pm" % parts[1])
        hh, mm, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if ampm:
            if not 1 <= hh <= 12:
                raise Refusal("%r is not a 12-hour clock time" % parts[1])
            hh = hh % 12 + (12 if ampm == "pm" else 0)
        if hh > 23 or mm > 59:
            raise Refusal("%r is not a time of day" % parts[1])
        naive = _dt.datetime(day.year, day.month, day.day, hh, mm)
        return localize(naive, zone, tzname)
    raise Refusal("cannot read %r as a moment; use 'YYYY-MM-DD HH:MM', "
                  "'tomorrow 14:00', 'fri 9am', or RFC 3339 with an offset" % expr)


# ---------------------------------------------------------------------------
# Slugs and ids
# ---------------------------------------------------------------------------

def slugify(text, maxlen=60):
    """Title -> file slug: ascii, lower-case words joined by '-', <= maxlen."""
    norm = unicodedata.normalize("NFKD", text or "")
    ascii_text = norm.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    if len(slug) > maxlen:
        cut = slug[:maxlen]
        slug = cut.rsplit("-", 1)[0] if "-" in cut else cut
    return slug or "untitled"


def format_id(prefix, number):
    if prefix not in NUMBERED_PREFIXES:
        raise ValueError("not a numbered prefix: %s" % prefix)
    return "%s-%0*d" % (prefix, ID_WIDTH, number)


def parse_id(text):
    """-> (prefix, number) for numbered ids, ('acct', slug) for accounts,
    ('TASK', text) for task ids, or None."""
    m = RX_NUMBERED_ID.match(text or "")
    if m:
        return m.group(1), int(m.group(2))
    m = RX_ACCT_ID.match(text or "")
    if m:
        return "acct", m.group(1)
    if RX_TASK_ID.match(text or ""):
        return "TASK", text
    return None


def ref_prefix(ref):
    parsed = parse_id(ref)
    return parsed[0] if parsed else None


def ref_module(ref):
    p = ref_prefix(ref)
    return PREFIX_MODULE.get(p) if p else None


def is_family_ref(text):
    return bool(RX_FAMILY_REF.match(text or ""))


# ---------------------------------------------------------------------------
# records-yaml/1 — the grammar
# ---------------------------------------------------------------------------

_PLAIN_BAD_FIRST = set("[]{},#&*!|>'\"%@`")
_FLOW_CHARS = set(",[]{}")


def _parse_quoted(text, lineno):
    """text starts with a quote. -> (value, rest, error)."""
    q = text[0]
    out = []
    i = 1
    while i < len(text):
        c = text[i]
        if q == "'":
            if c == "'":
                if i + 1 < len(text) and text[i + 1] == "'":
                    out.append("'")
                    i += 2
                    continue
                return "".join(out), text[i + 1:], None
            out.append(c)
            i += 1
            continue
        # double-quoted
        if c == "\\":
            if i + 1 >= len(text):
                return None, "", (lineno, "a backslash ends the quoted value")
            n = text[i + 1]
            if n in ('"', "\\", "/"):
                out.append(n)
                i += 2
                continue
            if n == "t":
                out.append("\t")
                i += 2
                continue
            if n == "u" and re.match(r"^[0-9a-fA-F]{4}$", text[i + 2:i + 6] or ""):
                out.append(chr(int(text[i + 2:i + 6], 16)))
                i += 6
                continue
            return None, "", (lineno, "unsupported escape \\%s in a quoted value "
                                      "(allowed: \\\" \\\\ \\/ \\t \\uXXXX)" % n)
        if c == '"':
            return "".join(out), text[i + 1:], None
        out.append(c)
        i += 1
    return None, "", (lineno, "an unterminated %s-quoted value" %
                      ("single" if q == "'" else "double"))


def parse_scalar(text, lineno, flow=False):
    """One scalar in block (flow=False) or flow (flow=True) context -> (value, error)."""
    t = text.strip()
    if t == "":
        return None, (lineno, "an empty value; to say nothing, omit the key")
    if t[0] in ("'", '"'):
        value, rest, err = _parse_quoted(t, lineno)
        if err:
            return None, err
        if rest.strip():
            return None, (lineno, "text after a closing quote: %r" % rest.strip())
        return value, None
    if t in ("null", "Null", "NULL", "~"):
        return None, (lineno, "'%s' is forbidden; to say nothing, omit the key" % t)
    if t[0] in "|>":
        return None, (lineno, "block scalars (| and >) are forbidden; use one line")
    if t[0] in _PLAIN_BAD_FIRST:
        return None, (lineno, "a value starting with %r must be double-quoted" % t[0])
    if t[0] in "-?:" and (len(t) == 1 or t[1] in " \t"):
        return None, (lineno, "a value starting with %r and a space must be quoted" % t[0])
    if ": " in t or t.endswith(":"):
        return None, (lineno, "a plain value may not contain ': ' or end with ':'; quote it")
    if " #" in t or "\t#" in t:
        return None, (lineno, "' #' inside a plain value is ambiguous (YAML reads a comment); "
                              "quote the value or move the comment to its own line")
    if flow and any(c in _FLOW_CHARS for c in t):
        return None, (lineno, "',', '[', ']', '{' or '}' inside a flow collection must be quoted")
    if flow and t[0] in "?:":
        return None, (lineno, "inside a flow collection a value starting with %r must be quoted"
                      % t[0])
    return t, None


_RX_QUOTE_OPENS = re.compile(r"^\s*(?:[a-z][a-z0-9_]*:\s+)?$")


def _split_flow(inner, lineno):
    """Split the inside of [..] or {..} on top-level commas. -> (parts, error)."""
    parts, buf, i, quote = [], [], 0, None
    while i < len(inner):
        c = inner[i]
        if quote:
            buf.append(c)
            if quote == '"' and c == "\\" and i + 1 < len(inner):
                buf.append(inner[i + 1])
                i += 2
                continue
            if c == quote:
                if quote == "'" and i + 1 < len(inner) and inner[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if c in ("'", '"') and _RX_QUOTE_OPENS.match("".join(buf)):
            # a quote opens a value at the start of an item, or right after the
            # 'key: ' of a flow-map entry (core 1.0.1: before, only the former,
            # so a quoted map value holding ',' could be written but not read)
            quote = c
            buf.append(c)
            i += 1
            continue
        if c in "[]{}":
            return None, (lineno, "collections may not nest inside a flow collection")
        if c == ",":
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    if quote:
        return None, (lineno, "an unterminated quote inside a flow collection")
    parts.append("".join(buf))
    if len(parts) == 1 and parts[0].strip() == "":
        return [], None
    for p in parts:
        if p.strip() == "":
            return None, (lineno, "an empty item in a flow collection (a stray or trailing comma)")
    return parts, None


def parse_flow_list(text, lineno):
    t = text.strip()
    if not (t.startswith("[") and t.endswith("]")):
        return None, (lineno, "a flow list must open with '[' and close with ']' on one line")
    parts, err = _split_flow(t[1:-1], lineno)
    if err:
        return None, err
    out = []
    for p in parts:
        v, err = parse_scalar(p, lineno, flow=True)
        if err:
            return None, err
        out.append(v)
    return out, None


def parse_flow_map(text, lineno):
    t = text.strip()
    if not (t.startswith("{") and t.endswith("}")):
        return None, (lineno, "a flow map must open with '{' and close with '}' on one line")
    parts, err = _split_flow(t[1:-1], lineno)
    if err:
        return None, err
    if not parts:
        return None, (lineno, "an empty map {}; to say nothing, omit the key")
    out = OrderedDict()
    for p in parts:
        m = RX_MAP_KEY.match(p.strip())
        if not m or m.group(2) is None:
            return None, (lineno, "a map entry must read 'key: value' with a lower-case key: %r"
                          % p.strip())
        k = m.group(1)
        if k in out:
            return None, (lineno, "duplicate key '%s' inside a map" % k)
        v, err = parse_scalar(m.group(2), lineno, flow=True)
        if err:
            return None, err
        out[k] = v
    return out, None


def parse_value(raw, lineno):
    t = raw.strip()
    if t.startswith("["):
        return parse_flow_list(t, lineno)
    if t.startswith("{"):
        return parse_flow_map(t, lineno)
    if t.endswith("]") or t.endswith("}"):
        return None, (lineno, "a value ending in ']' or '}' must be a whole flow collection")
    if re.match(r"^[&*][A-Za-z0-9_]", t):
        return None, (lineno, "anchors and aliases are forbidden")
    if t.startswith("!"):
        return None, (lineno, "YAML tags are forbidden")
    return parse_scalar(t, lineno, flow=False)


def parse_mapping(lines, first_lineno=1):
    """Parse a block of records-yaml/1 lines.

    Returns (values, info, errors):
      values  OrderedDict key -> str | [str] | {str: str} | [str | {str: str}]
      info    {"lines": key -> lineno, "raw": key -> [source lines]}
      errors  [(lineno, message)] — any error makes the whole mapping unusable
    """
    values = OrderedDict()
    key_lines = {}
    raw = {}
    errors = []
    block_key = None
    for offset, line in enumerate(lines):
        lineno = first_lineno + offset
        if line.endswith("\r"):
            line = line[:-1]
        stripped = line.strip()
        if stripped == "":
            continue
        if stripped.startswith("#"):
            if line[:1] in (" ", "\t") and block_key is None:
                errors.append((lineno, "an indented comment outside a list; comments start "
                                       "at column 1"))
            continue
        if line.startswith("  - ") or line.rstrip() == "  -":
            if block_key is None:
                errors.append((lineno, "a list item with no 'key:' line above it"))
                continue
            item_text = line[4:] if len(line) > 4 else ""
            if item_text.strip().startswith("{"):
                v, err = parse_flow_map(item_text, lineno)
            elif item_text.strip().startswith("["):
                v, err = None, (lineno, "a list item may not itself be a list")
            else:
                v, err = parse_scalar(item_text, lineno, flow=False)
            if err:
                errors.append(err)
                continue
            values[block_key].append(v)
            raw[block_key].append(line)
            continue
        if line[:1] in (" ", "\t"):
            errors.append((lineno, "only list items ('  - ' two spaces, dash, space) may be "
                                   "indented; no other nesting"))
            continue
        if stripped in ("---", "..."):
            errors.append((lineno, "a document marker may not appear inside the frontmatter"))
            continue
        m = RX_KEY_LINE.match(line)
        if not m:
            errors.append((lineno, "not a legal 'key: value' line (keys are lower-case, "
                                   "a-z 0-9 _, or x-<name>)"))
            block_key = None
            continue
        key, rawval = m.group(1), m.group(2)
        if key in values:
            errors.append((lineno, "duplicate key '%s' (first on line %d); last-wins is never "
                                   "applied" % (key, key_lines[key])))
            block_key = None
            continue
        key_lines[key] = lineno
        raw[key] = [line]
        if rawval is None or rawval.strip() == "":
            values[key] = []
            block_key = key
            continue
        block_key = None
        v, err = parse_value(rawval, lineno)
        if err:
            errors.append(err)
            values[key] = None
            continue
        values[key] = v
    for key, v in values.items():
        if v == [] and len(raw.get(key, [])) == 1 and not raw[key][0].rstrip().endswith("[]"):
            errors.append((key_lines[key], "'%s:' has no value and no list items; to say "
                                           "nothing, omit the key" % key))
    if errors:
        return values, {"lines": key_lines, "raw": raw}, errors
    return values, {"lines": key_lines, "raw": raw}, []


def split_frontmatter(text):
    """-> (fm_lines, fm_first_lineno, body, body_first_lineno, error)."""
    lines = text.split("\n")

    def fence(s):
        return (s[:-1] if s.endswith("\r") else s) == "---"

    if not lines or not fence(lines[0]):
        return None, 0, text, 1, (1, "line 1 must be exactly '---' (the frontmatter opens)")
    for idx in range(1, len(lines)):
        if fence(lines[idx]):
            body = "\n".join(lines[idx + 1:])
            return lines[1:idx], 2, body, idx + 2, None
    return None, 0, text, 1, (1, "the frontmatter is never closed by a line that is exactly '---'")


# ---------------------------------------------------------------------------
# records-yaml/1 — the serializer
# ---------------------------------------------------------------------------

_RX_YAMLISH_NUMBER = re.compile(
    r"^(?:[-+]?(?:\d[\d_]*)?\.?\d[\d_]*(?:[eE][-+]?\d+)?|[-+]?\.(?:inf|Inf|INF)|"
    r"\.(?:nan|NaN|NAN)|0x[0-9a-fA-F_]+|0o[0-7_]+|0b[01_]+|[-+]?\d+(?::[0-5]?\d)+)$")
_YAMLISH_WORDS = {"true", "false", "yes", "no", "on", "off", "null", "y", "n", "~"}


def needs_quote(s, flow=False, typed=False):
    if s == "" or s != s.strip():
        return True
    if s[0] in _PLAIN_BAD_FIRST:
        return True
    if s[0] in "-?:" and (len(s) == 1 or s[1] in " \t"):
        return True
    if ": " in s or s.endswith(":") or " #" in s or "\t#" in s or "\t" in s:
        return True
    if flow and (any(c in _FLOW_CHARS for c in s) or s[0] in "?:"):
        return True
    if s.endswith("]") or s.endswith("}"):
        return True
    if not typed and (s.lower() in _YAMLISH_WORDS or _RX_YAMLISH_NUMBER.match(s)):
        return True
    return False


def quote(s):
    out = s.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t")
    return '"' + out + '"'


def dump_scalar(value, flow=False, typed=False):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if not isinstance(value, str):
        raise TypeError("cannot serialize %r" % (value,))
    if "\n" in value or "\r" in value:
        raise ValueError("values are single-line; a newline cannot be stored in frontmatter")
    return quote(value) if needs_quote(value, flow=flow, typed=typed) else value


def _scalar_is_typed(node, value):
    """True when the schema node says this value is a bare boolean/integer/number."""
    if not isinstance(node, dict) or not isinstance(value, str):
        return False
    types = node.get("type")
    types = types if isinstance(types, list) else [types]
    if "boolean" in types and value in ("true", "false"):
        return True
    if "integer" in types and re.match(r"^-?\d+$", value):
        return True
    if "number" in types and re.match(r"^-?\d+(\.\d+)?$", value):
        return True
    return False


def _prop(schema, key):
    if not isinstance(schema, dict):
        return {}
    return (schema.get("properties") or {}).get(key) or {}


def _map_order(node, mapping):
    items = (node or {}).get("items") if isinstance(node, dict) else None
    order = []
    if isinstance(items, dict):
        order = items.get("x-key-order") or list((items.get("properties") or {}).keys())
    elif isinstance(node, dict) and node.get("type") == "object":
        order = node.get("x-key-order") or list((node.get("properties") or {}).keys())
    keys = [k for k in order if k in mapping] + [k for k in mapping if k not in order]
    return keys


def _dump_map(mapping, node):
    item_schema = node.get("items") if isinstance(node, dict) and node.get("type") == "array" \
        else node
    parts = []
    for k in _map_order(node, mapping):
        v = mapping[k]
        typed = _scalar_is_typed(_prop(item_schema, k), v)
        parts.append("%s: %s" % (k, dump_scalar(v, flow=True, typed=typed)))
    return "{" + ", ".join(parts) + "}"


def dump_frontmatter(fm, schema=None, info=None):
    """Canonical frontmatter block (with both '---' fences), ending in a newline.

    Keys follow the schema's x-key-order; unknown non-x keys after them; x-
    keys last, re-emitted from their original source lines when available.
    """
    schema = schema or {}
    order = list(schema.get("x-key-order") or (schema.get("properties") or {}).keys())
    raw = (info or {}).get("raw") or {}
    keys = [k for k in order if k in fm]
    keys += [k for k in fm if k not in order and not k.startswith("x-")]
    keys += [k for k in fm if k.startswith("x-") and k not in keys]
    out = ["---"]
    for key in keys:
        value = fm[key]
        node = _prop(schema, key)
        if key.startswith("x-") and key in raw:
            out.extend(raw[key])
            continue
        if isinstance(value, (list, tuple)):
            if value and any(isinstance(v, dict) for v in value):
                out.append("%s:" % key)
                items_node = node.get("items") if isinstance(node, dict) else {}
                for v in value:
                    if isinstance(v, dict):
                        out.append("  - " + _dump_map(v, node))
                    else:
                        typed = _scalar_is_typed(items_node, v)
                        out.append("  - " + dump_scalar(v, flow=False, typed=typed))
            else:
                items_node = node.get("items") if isinstance(node, dict) else {}
                out.append("%s: [%s]" % (key, ", ".join(
                    dump_scalar(v, flow=True, typed=_scalar_is_typed(items_node, v))
                    for v in value)))
        elif isinstance(value, dict):
            out.append("%s: %s" % (key, _dump_map(value, node)))
        else:
            out.append("%s: %s" % (key, dump_scalar(value, flow=False,
                                                      typed=_scalar_is_typed(node, value)
                                                      or isinstance(value, bool))))
    out.append("---")
    return "\n".join(out) + "\n"


def render_record(fm, body, schema=None, info=None):
    """Frontmatter + body. The body is kept verbatim; the file ends with one newline."""
    text = dump_frontmatter(fm, schema, info) + body
    if not text.endswith("\n"):
        text += "\n"
    return text


def _plain(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return OrderedDict((k, _plain(x)) for k, x in v.items())
    return v


def verify_roundtrip(text, fm):
    """Refuse a rendered record that would not read back as exactly `fm`.

    The last guard before a write: whatever the serializer does, the file on
    disk must parse, under this grammar, to the values the tool meant.
    """
    fm_lines, first, _body, _bf, err = split_frontmatter(text)
    if err:
        raise Refusal("internal: the rendered record has no frontmatter (%s)" % err[1])
    values, _info, errors = parse_mapping(fm_lines, first)
    if errors:
        raise Refusal("internal: the rendered record does not parse (line %s: %s)" % errors[0])
    want = json.dumps(_plain(fm), sort_keys=True)
    got = json.dumps(values, sort_keys=True)
    if want != got:
        raise Refusal("internal: the rendered record would not read back as written; "
                      "nothing was written")
    return text


def new_body(title=None, text=None):
    """A fresh record body: blank line, '# title', blank line, text."""
    parts = []
    if title is not None:
        if "\n" in title:
            raise Refusal("a title is one line")
        parts.append("# %s" % title.strip())
    if text:
        parts.append(text.rstrip("\n"))
    return "\n" + "\n\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# JSON Schema subset evaluator — record law is data
# ---------------------------------------------------------------------------

def _fmt_email(v):
    return bool(re.match(r"^[^@\s<>(),;:\"]+@[^@\s<>(),;:\"]+\.[^@\s<>(),;:\"]+$", v))


def _fmt_phone(v):
    if not re.match(r"^\+?[\d(][\d\s().-]*\d(?:\s*(?:x|ext\.?)\s*\d+)?$", v):
        return False
    return len(re.sub(r"\D", "", v)) >= 3


def _fmt_chat(v):
    return bool(re.match(r"^[a-z0-9][a-z0-9._-]*:\S+$", v))


FORMATS = {
    "date": lambda v: parse_date(v) is not None,
    "date-time": lambda v: parse_datetime(v) is not None,
    "date-or-date-time": lambda v: parse_date(v) is not None or parse_datetime(v) is not None,
    "partial-date": parse_partial_date,
    "email": _fmt_email,
    "uri": lambda v: bool(re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:\S+$", v)),
    "iana-tz": is_iana_tz,
    "duration": lambda v: parse_duration(v) is not None,
    "actor": lambda v: v == "unknown" or bool(RX_ACTOR.match(v)),
    "slug": lambda v: bool(RX_SLUG.match(v)),
    "family-ref": is_family_ref,
    "currency": lambda v: bool(re.match(r"^[A-Z]{3}$", v)),
    "decimal": lambda v: bool(re.match(r"^-?\d+(?:\.\d+)?$", v)),
    "phone": _fmt_phone,
    "color": lambda v: bool(re.match(r"^(?:#[0-9a-fA-F]{6}|[a-z]+)$", v)),
    "chat-handle": _fmt_chat,
    "email-or-ref": lambda v: is_family_ref(v) or _fmt_email(v),
    "phone-or-ref": lambda v: is_family_ref(v) or _fmt_phone(v),
    "phone-or-email-or-ref": lambda v: is_family_ref(v) or _fmt_phone(v) or _fmt_email(v),
    "chat-handle-or-ref": lambda v: is_family_ref(v) or _fmt_chat(v),
    "text-or-ref": lambda v: bool(v.strip()),
    "hhmm-range": lambda v: bool(re.match(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-4]):[0-5]\d$", v)),
}

_FORMAT_LABEL = {
    "date": "a real YYYY-MM-DD date",
    "date-time": "an RFC 3339 datetime WITH an offset (e.g. 2026-09-28T14:00:00-04:00)",
    "date-or-date-time": "a YYYY-MM-DD date or an RFC 3339 datetime with an offset",
    "partial-date": "YYYY-MM-DD or --MM-DD",
    "email": "an email address",
    "uri": "a URI (scheme:rest)",
    "iana-tz": "an IANA timezone name (e.g. America/New_York)",
    "duration": "an ISO 8601 duration (e.g. PT15M, -P1D, PT1H30M)",
    "actor": "an actor handle (^[a-z0-9][a-z0-9._-]{0,31}$ or unknown)",
    "slug": "a slug (lower-case words joined by '-')",
    "family-ref": "a family id (NOTE-0001, DEC-0001, EVT-0001, CARD-0001, LEAD-0001, "
                  "acct-<slug>, THR-0001, MSG-0001, TASK-…)",
    "currency": "an ISO 4217 code (USD, EUR, …)",
    "decimal": "a decimal number (1200 or 1200.50)",
    "phone": "a phone number (+1 555 010 0100)",
    "color": "#rrggbb or a colour name",
    "chat-handle": "<service>:<handle>",
    "email-or-ref": "an email address or a family id",
    "phone-or-ref": "a phone number or a family id",
    "phone-or-email-or-ref": "a phone number, an email address or a family id",
    "chat-handle-or-ref": "<service>:<handle> or a family id",
    "text-or-ref": "non-empty text or a family id",
    "hhmm-range": "HH:MM-HH:MM",
}

#: format -> the family invariant a failure is reported under
FORMAT_INVARIANT = {"date-time": "F-24", "date-or-date-time": "F-24", "iana-tz": "F-25"}


def register_format(name, check, label=None, invariant=None):
    """Modules add formats (e.g. schedule's 'rrule') before validating."""
    FORMATS[name] = check
    if label:
        _FORMAT_LABEL[name] = label
    if invariant:
        FORMAT_INVARIANT[name] = invariant


def _type_ok(t, v):
    if t == "string":
        return isinstance(v, str)
    if t == "boolean":
        return isinstance(v, str) and v in ("true", "false") or isinstance(v, bool)
    if t == "integer":
        return isinstance(v, str) and bool(re.match(r"^-?\d+$", v)) or \
            (isinstance(v, int) and not isinstance(v, bool))
    if t == "number":
        return isinstance(v, str) and bool(re.match(r"^-?\d+(?:\.\d+)?$", v))
    if t == "array":
        return isinstance(v, list)
    if t == "object":
        return isinstance(v, dict)
    return False


def _canon(v):
    return json.dumps(v, sort_keys=True)


def schema_errors(schema, value, path=""):
    """Evaluate `value` against `schema`. -> [(invariant, path, message)]."""
    errs = []
    if not isinstance(schema, dict) or schema is True:
        return errs
    if isinstance(value, bool):          # the tools pass bare booleans; the parser yields text
        value = "true" if value else "false"
    elif isinstance(value, int):
        value = str(value)
    elif isinstance(value, list):
        value = [("true" if v else "false") if isinstance(v, bool) else
                 (str(v) if isinstance(v, int) else v) for v in value]
    where = path or "(record)"
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_type_ok(x, value) for x in types):
            human = {"string": "text", "boolean": "true or false", "integer": "a whole number",
                     "number": "a number", "array": "a list", "object": "a map {k: v}"}
            errs.append(("F-26", where, "must be %s" % " or ".join(human.get(x, x) for x in types)))
            return errs
    if "const" in schema and _canon(value) != _canon(schema["const"]):
        errs.append(("F-26", where, "must be %r" % (schema["const"],)))
    if "enum" in schema and value not in schema["enum"]:
        errs.append(("F-26", where, "%r is not one of: %s" % (
            value, ", ".join(str(x) for x in schema["enum"]))))
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errs.append(("F-26", where, "shorter than %d characters" % schema["minLength"]))
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errs.append(("F-26", where, "longer than %d characters" % schema["maxLength"]))
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errs.append(("F-26", where, "%r does not match %s" % (value, schema["pattern"])))
        fmt = schema.get("format")
        if fmt:
            check = FORMATS.get(fmt)
            if check is None:
                errs.append(("F-26", where, "the schema names an unknown format %r" % fmt))
            elif not check(value):
                errs.append((FORMAT_INVARIANT.get(fmt, "F-26"), where, "%r is not %s" % (
                    value, _FORMAT_LABEL.get(fmt, fmt))))
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errs.append(("F-26", where, "needs at least %d item(s)" % schema["minItems"]))
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errs.append(("F-26", where, "allows at most %d item(s)" % schema["maxItems"]))
        if schema.get("uniqueItems"):
            seen = set()
            for v in value:
                c = _canon(v)
                if c in seen:
                    errs.append(("F-26", where, "lists %s more than once" % (v,)))
                seen.add(c)
        items = schema.get("items")
        if isinstance(items, dict):
            for idx, v in enumerate(value):
                errs.extend(schema_errors(items, v, "%s[%d]" % (path, idx)))
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        for req in schema.get("required") or []:
            if req not in value:
                errs.append(("F-26", "%s.%s" % (path, req) if path else req,
                             "is required"))
        pattern_props = schema.get("patternProperties") or {}
        for k, v in value.items():
            sub = "%s.%s" % (path, k) if path else k
            if k in props:
                errs.extend(schema_errors(props[k], v, sub))
                continue
            matched = False
            for pat, psch in pattern_props.items():
                if re.search(pat, k):
                    matched = True
                    errs.extend(schema_errors(psch, v, sub))
            if not matched and schema.get("additionalProperties") is False:
                errs.append(("F-06" if not path else "F-26", sub,
                             "is not a key this record type defines (extensions go under x-)"))
    for sub in schema.get("allOf") or []:
        errs.extend(schema_errors(sub, value, path))
    if "anyOf" in schema:
        if not any(not schema_errors(s, value, path) for s in schema["anyOf"]):
            msg = schema.get("x-anyOf-message") or "matches none of the allowed shapes"
            errs.append(("F-26", where, msg))
    if "not" in schema and not schema_errors(schema["not"], value, path):
        errs.append(("F-26", where, schema.get("x-not-message") or "has a forbidden shape"))
    if "if" in schema:
        if not schema_errors(schema["if"], value, path):
            if "then" in schema:
                errs.extend(schema_errors(schema["then"], value, path))
        elif "else" in schema:
            errs.extend(schema_errors(schema["else"], value, path))
    return errs


def load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh, object_pairs_hook=OrderedDict)
    except (OSError, ValueError) as exc:
        raise EnvError("cannot load %s: %s" % (path, exc))


# ---------------------------------------------------------------------------
# The record file
# ---------------------------------------------------------------------------

def fence_map(lines):
    inside, marks = False, []
    for line in lines:
        s = line.strip()
        if s.startswith("```") or s.startswith("~~~"):
            marks.append(True)
            inside = not inside
            continue
        marks.append(inside)
    return marks


class Record(object):
    """One record file. `rel` is POSIX, relative to the module root."""

    def __init__(self, path, rel):
        self.path = path
        self.rel = rel
        self.dir = rel.rsplit("/", 1)[0] if "/" in rel else ""
        self.name = rel.rsplit("/", 1)[-1]
        self.stem = self.name[:-3] if self.name.endswith(".md") else self.name
        self.text = ""
        self.raw_bytes = b""
        self.fm = OrderedDict()
        self.info = {"lines": {}, "raw": {}}
        self.body = ""
        self.body_lineno = 1
        self.problems = []          # [(invariant, lineno, message)] — file-level
        self.parsed = False
        self.h1 = None              # the title
        self.h1_lineno = None
        self.h1_count = 0
        self.h1_first = False

    @property
    def id(self):
        v = self.fm.get("id")
        return v if isinstance(v, str) else None

    @property
    def prefix(self):
        return ref_prefix(self.id) if self.id else None

    @property
    def archived(self):
        return self.rel.startswith(ARCHIVE_DIR + "/") or ("/" + ARCHIVE_DIR + "/") in self.rel

    def line_of(self, key):
        return self.info.get("lines", {}).get(key)

    def digest(self):
        return hashlib.sha256(self.raw_bytes).hexdigest()


def read_record(path, rel):
    rec = Record(path, rel)
    try:
        with open(path, "rb") as fh:
            rec.raw_bytes = fh.read()
    except OSError as exc:
        rec.problems.append(("F-01", None, "cannot read the file: %s" % exc))
        return rec
    try:
        text = rec.raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        rec.problems.append(("F-01", None, "not valid UTF-8 (%s)" % exc.reason))
        return rec
    if text.startswith("\ufeff"):
        rec.problems.append(("F-01", 1, "the file starts with a byte-order mark; remove it"))
        text = text[1:]
    if "\r\n" in text:
        rec.problems.append(("F-01", None, "CRLF line endings; the family is LF only"))
        text = text.replace("\r\n", "\n")
    if not text.endswith("\n"):
        rec.problems.append(("F-01", None, "the file does not end with a newline"))
    rec.text = text
    fm_lines, first, body, body_first, err = split_frontmatter(text)
    if err:
        rec.problems.append(("F-05", err[0], err[1]))
        rec.body = text
        return rec
    values, info, errors = parse_mapping(fm_lines, first)
    rec.fm, rec.info = values, info
    for lineno, msg in errors:
        inv = "F-07" if msg.startswith("duplicate key") else "F-05"
        rec.problems.append((inv, lineno, msg))
    rec.parsed = not errors
    rec.body = body
    rec.body_lineno = body_first
    body_lines = body.split("\n")
    marks = fence_map(body_lines)
    first_content = None
    for idx, line in enumerate(body_lines):
        if marks[idx]:
            if first_content is None and line.strip():
                first_content = idx
            continue
        if first_content is None and line.strip():
            first_content = idx
        if line.startswith("# ") and line[2:].strip():
            rec.h1_count += 1
            if rec.h1 is None:
                rec.h1 = line[2:].strip()
                rec.h1_lineno = body_first + idx
                rec.h1_first = first_content == idx
    return rec


def body_refs(rec):
    """Family references in the body: [[ID]], [[ID|label]], @acct-<slug>. -> [(ref, lineno)]."""
    out = []
    lines = rec.body.split("\n")
    marks = fence_map(lines)
    for idx, line in enumerate(lines):
        if marks[idx]:
            continue
        for m in RX_WIKILINK.finditer(line):
            target = m.group(1).strip()
            if is_family_ref(target):
                out.append((target, rec.body_lineno + idx))
        for m in RX_ACCT_MENTION.finditer(line):
            out.append((m.group(1), rec.body_lineno + idx))
    return out


def frontmatter_refs(rec, schema):
    """Family references named by the schema's x-refs: {key: [prefixes]} or
    {key: {"field": subkey, "prefixes": [...]}} for lists of maps. -> [(key, ref)]."""
    out = []
    spec = (schema or {}).get("x-refs") or {}
    for key, rule in spec.items():
        if key not in rec.fm:
            continue
        value = rec.fm[key]
        field = rule.get("field") if isinstance(rule, dict) else None
        candidates = []
        if isinstance(value, str):
            candidates = [value]
        elif isinstance(value, list):
            for v in value:
                if isinstance(v, dict) and field:
                    if isinstance(v.get(field), str):
                        candidates.append(v[field])
                elif isinstance(v, str):
                    candidates.append(v)
        elif isinstance(value, dict) and field and isinstance(value.get(field), str):
            candidates = [value[field]]
        for c in candidates:
            if is_family_ref(c):
                out.append((key, c))
    return out


# ---------------------------------------------------------------------------
# Findings and reports
# ---------------------------------------------------------------------------

class Finding(object):
    __slots__ = ("severity", "inv", "where", "line", "msg", "fix", "fixable")

    def __init__(self, severity, inv, msg, where=None, line=None, fix=None, fixable=False):
        self.severity, self.inv, self.msg = severity, inv, msg
        self.where, self.line, self.fix, self.fixable = where, line, fix, fixable

    def as_dict(self):
        return OrderedDict([("severity", self.severity), ("invariant", self.inv),
                            ("file", self.where), ("line", self.line),
                            ("message", self.msg), ("fix", self.fix),
                            ("fixable", self.fixable)])


class Report(object):
    def __init__(self, tool):
        self.tool = tool
        self.findings = []
        self.records = 0
        self.notes = []  # free-form informational lines (counts, seeded caches)

    def add(self, severity, inv, msg, where=None, line=None, fix=None, fixable=False):
        self.findings.append(Finding(severity, inv, msg, where, line, fix, fixable))

    def error(self, inv, msg, **kw):
        self.add(ERROR, inv, msg, **kw)

    def warn(self, inv, msg, **kw):
        self.add(WARN, inv, msg, **kw)

    def info(self, inv, msg, **kw):
        self.add(INFO, inv, msg, **kw)

    def count(self, severity):
        return sum(1 for f in self.findings if f.severity == severity)

    @property
    def ok(self):
        return self.count(ERROR) == 0

    def sorted(self):
        return sorted(self.findings, key=lambda f: (_SEVERITY_ORDER[f.severity],
                                                    f.where or "", f.line or 0, f.inv))

    def render(self, as_json=False, quiet=False, stream=None):
        stream = stream or sys.stdout
        if as_json:
            json.dump(OrderedDict([
                ("tool", self.tool), ("family", FAMILY), ("ok", self.ok),
                ("records", self.records),
                ("counts", OrderedDict([("error", self.count(ERROR)),
                                        ("warn", self.count(WARN)),
                                        ("info", self.count(INFO))])),
                ("notes", self.notes),
                ("findings", [f.as_dict() for f in self.sorted()]),
            ]), stream, indent=2)
            stream.write("\n")
            return
        for f in self.sorted():
            if quiet and f.severity != ERROR:
                continue
            loc = f.where or "-"
            if f.line:
                loc += ":%d" % f.line
            stream.write("%-5s %s  %s  %s\n" % (f.severity, f.inv, loc, f.msg))
            if f.fix:
                stream.write("            fix: %s\n" % f.fix)
        if not quiet:
            for n in self.notes:
                stream.write("note  %s\n" % n)
        stream.write("%s: %d error(s), %d warning(s), %d info — %d record(s)%s\n" % (
            self.tool, self.count(ERROR), self.count(WARN), self.count(INFO), self.records,
            "" if self.ok else "  ✗"))

    def exit_code(self):
        return EXIT_OK if self.ok else EXIT_REFUSED


# ---------------------------------------------------------------------------
# Project, module root, scanning
# ---------------------------------------------------------------------------

def find_project_root(explicit=None, bin_dir=None):
    """--root > $RASA_PROJECT_ROOT > the project owning an installed .claude/bin >
    walk up from cwd to a dir holding .claude/ > cwd."""
    if explicit:
        return os.path.abspath(explicit)
    env = os.environ.get("RASA_PROJECT_ROOT")
    if env:
        return os.path.abspath(env)
    if bin_dir:
        b = os.path.abspath(bin_dir)
        parent = os.path.dirname(b)
        if os.path.basename(b) == "bin" and os.path.basename(parent) == ".claude":
            return os.path.dirname(parent)
    cur = os.path.abspath(os.getcwd())
    while True:
        if os.path.isdir(os.path.join(cur, ".claude")):
            return cur
        up = os.path.dirname(cur)
        if up == cur:
            return os.path.abspath(os.getcwd())
        cur = up


def rel_posix(path, start):
    return os.path.relpath(path, start).replace(os.sep, "/")


def scan_root(root, is_record_name):
    """Walk a module root. -> (records [(abs, rel)], strays [rel]).

    Skips .state/ and any dot-directory. A .md file whose name is not a record
    name is a stray (F-27, listed once as info, never touched). .keep files and
    the family's reserved files are neither.
    """
    records, strays = [], []
    if not os.path.isdir(root):
        return records, strays
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            rel = rel_posix(full, root)
            if fn.startswith(".") or rel in ("history.tsv",) or rel.endswith(".config.yml"):
                continue
            if fn.endswith(".md") and is_record_name(fn[:-3], rel):
                records.append((full, rel))
            elif fn.endswith(".md"):
                strays.append(rel)
    return records, strays


def ensure_state_dir(root):
    state = os.path.join(root, STATE_DIR)
    os.makedirs(state, exist_ok=True)
    gi = os.path.join(state, ".gitignore")
    if not os.path.exists(gi):
        with open(gi, "w", encoding="utf-8") as fh:
            fh.write("# machine cache for the records family; regenerable, not project content\n*\n")
    return state


class Lock(object):
    """Exclusive O_EXCL lock at <root>/.state/lock. Stale after 120 s or a dead pid."""

    def __init__(self, root, timeout=10.0):
        self.root = root
        self.timeout = timeout
        self.path = None

    def __enter__(self):
        state = ensure_state_dir(self.root)
        self.path = os.path.join(state, "lock")
        deadline = time.time() + self.timeout
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                with os.fdopen(fd, "w") as fh:
                    fh.write("%d %d\n" % (os.getpid(), int(time.time())))
                return self
            except OSError as exc:
                if exc.errno != errno.EEXIST:
                    raise EnvError("cannot create the lock %s: %s" % (self.path, exc))
                if self._stale():
                    try:
                        os.unlink(self.path)
                    except OSError:
                        pass
                    continue
                if time.time() > deadline:
                    raise EnvError("the ledger is locked (%s); another write is in progress"
                                   % self.path)
                time.sleep(0.05)

    def _stale(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                pid_s, ts_s = (fh.read().split() + ["0", "0"])[:2]
            pid, ts = int(pid_s), int(ts_s)
        except (OSError, ValueError):
            return True
        if time.time() - ts > 120:
            return True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        except OSError:
            return True
        return False

    def __exit__(self, *exc):
        try:
            os.unlink(self.path)
        except OSError:
            pass
        return False


def atomic_write(path, text):
    """Write text to path via a temp file in the same directory + os.replace."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def move_path(source, target):
    """Move a file or directory; refuse to overwrite anything."""
    if os.path.exists(target):
        raise Refusal("refusing to overwrite %s" % target)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    os.rename(source, target)


def prune_empty_dirs(start, stop):
    """Remove now-empty directories from start up to (not including) stop."""
    cur = os.path.abspath(start)
    stop = os.path.abspath(stop)
    while cur.startswith(stop + os.sep) and cur != stop:
        try:
            entries = [e for e in os.listdir(cur) if e != ".keep"]
            if entries:
                return
            if os.path.exists(os.path.join(cur, ".keep")):
                return
            os.rmdir(cur)
        except OSError:
            return
        cur = os.path.dirname(cur)


# ---------------------------------------------------------------------------
# The history log
# ---------------------------------------------------------------------------

class HistoryRow(object):
    __slots__ = ("at", "id", "verb", "frm", "to", "actor", "note", "lineno")

    def __init__(self, at, rid, verb, frm, to, actor, note, lineno=None):
        self.at, self.id, self.verb, self.frm, self.to = at, rid, verb, frm, to
        self.actor, self.note, self.lineno = actor, note, lineno


def _clean_field(v):
    v = "" if v is None else str(v)
    return re.sub(r"[\t\r\n]+", " ", v).strip()


def load_history(path, rel="history.tsv"):
    """-> (rows, problems [(invariant, lineno, message)], exists)."""
    rows, problems = [], []
    if not os.path.exists(path):
        return rows, problems, False
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().split("\n")
    except (OSError, UnicodeDecodeError) as exc:
        return rows, [("F-13", None, "cannot read the history log: %s" % exc)], True
    if lines and lines[-1] == "":
        lines = lines[:-1]
    if not lines:
        return rows, [("F-13", 1, "the history log is empty; line 1 must be the header")], True
    if tuple(lines[0].split("\t")) != HISTORY_HEADER:
        problems.append(("F-13", 1, "line 1 must be the header: %s" % "\\t".join(HISTORY_HEADER)))
    last = None
    for idx, line in enumerate(lines[1:], start=2):
        if line.strip() == "":
            problems.append(("F-13", idx, "a blank line in the history log"))
            continue
        cols = line.split("\t")
        if len(cols) != 7:
            problems.append(("F-13", idx, "%d tab-separated fields; exactly 7 are required"
                             % len(cols)))
            continue
        at, rid, verb, frm, to, actor, note = cols
        if not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", at) or parse_datetime(at) is None:
            problems.append(("F-13", idx, "'%s' is not a UTC YYYY-MM-DDTHH:MM:SSZ stamp" % at))
            continue
        if last and at < last:
            problems.append(("F-14", idx, "time runs backwards (%s after %s); the log is "
                                          "append-only" % (at, last)))
        last = at
        if not is_family_ref(rid):
            problems.append(("F-13", idx, "'%s' is not a family id" % rid))
        if not re.match(r"^[a-z][a-z-]*$", verb):
            problems.append(("F-13", idx, "'%s' is not a verb" % verb))
        if actor != "unknown" and not RX_ACTOR.match(actor):
            problems.append(("F-22", idx, "'%s' is not a legal actor handle" % actor))
        rows.append(HistoryRow(at, rid, verb, frm, to, actor, note, idx))
    return rows, problems, True


def last_history_at(path):
    if not os.path.exists(path):
        return None
    last = None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            col = line.split("\t", 1)[0]
            if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", col):
                last = col
    return last


def append_history(path, entries):
    """Append [(id, verb, from, to, actor, note)] rows; create the header if needed.

    The stamp never runs backwards: if the clock reads earlier than the last
    line, the last line's stamp is reused (and the log stays ordered).
    """
    at = stamp()
    prev = last_history_at(path)
    if prev and at < prev:
        at = prev
    new_file = not os.path.exists(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a+", encoding="utf-8", newline="\n") as fh:
        if new_file:
            fh.write("\t".join(HISTORY_HEADER) + "\n")
        else:
            fh.seek(0, os.SEEK_END)
            if fh.tell() > 0:
                fh.seek(fh.tell() - 1)
                if fh.read(1) != "\n":
                    fh.write("\n")
        for rid, verb, frm, to, actor, note in entries:
            fh.write("\t".join([at, _clean_field(rid), _clean_field(verb),
                                _clean_field(frm) or "-", _clean_field(to) or "-",
                                _clean_field(actor) or "unknown", _clean_field(note)]) + "\n")
    return at


def history_by_id(rows):
    out = OrderedDict()
    for r in rows:
        out.setdefault(r.id, []).append(r)
    return out


def is_status_token(tok):
    return "=" in (tok or "")


def last_place(rows):
    """The `to` of the last row whose `to` is a place (not '-', not key=value)."""
    place = None
    for r in rows:
        if r.to and r.to != "-" and not is_status_token(r.to):
            place = r.to
    return place


def last_status(rows, key):
    val = None
    for r in rows:
        if r.to and r.to.startswith(key + "="):
            val = r.to.split("=", 1)[1]
    return val


def place_token(dirrel):
    return dirrel if dirrel else "."


# ---------------------------------------------------------------------------
# The digest cache — makes `updated` a fact and frozen records frozen
# ---------------------------------------------------------------------------

DIGEST_HEADER = ("id", "sha256", "updated", "frozen_sha256")


def load_digests(root):
    path = os.path.join(root, STATE_DIR, "digests.tsv")
    out = {}
    if not os.path.exists(path):
        return out, False
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh.read().split("\n")[1:]:
                cols = line.split("\t")
                if len(cols) == 4 and cols[0]:
                    out[cols[0]] = {"sha": cols[1], "updated": cols[2], "frozen": cols[3]}
    except (OSError, UnicodeDecodeError):
        return {}, False
    return out, True


def save_digests(root, table):
    ensure_state_dir(root)
    lines = ["\t".join(DIGEST_HEADER)]
    for rid in sorted(table):
        e = table[rid]
        lines.append("\t".join([rid, e.get("sha", ""), e.get("updated", ""),
                                e.get("frozen", "") or ""]))
    atomic_write(os.path.join(root, STATE_DIR, "digests.tsv"), "\n".join(lines) + "\n")


def frozen_digest(fm, body, mutable_keys):
    """Content hash that ignores formatting and the keys a frozen record may change."""
    keep = OrderedDict((k, v) for k, v in fm.items() if k not in set(mutable_keys))
    payload = json.dumps(keep, sort_keys=True, ensure_ascii=False) + "\n" + body.strip() + "\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def digest_entry(rec, frozen=False, mutable_keys=(), previous=None):
    """The cache row for a record as it is now. A frozen hash, once recorded, is kept."""
    frozen_sha = ""
    if frozen:
        if previous and previous.get("frozen"):
            frozen_sha = previous["frozen"]
        else:
            frozen_sha = frozen_digest(rec.fm, rec.body, mutable_keys)
    upd = rec.fm.get("updated")
    return {"sha": rec.digest(), "updated": upd if isinstance(upd, str) else "",
            "frozen": frozen_sha}


# ---------------------------------------------------------------------------
# Family-wide resolution
# ---------------------------------------------------------------------------

_RX_FILE_ID = re.compile(r"^((?:NOTE|DEC|EVT|CARD|LEAD|THR|MSG)-[0-9]{4,})(?:-[a-z0-9]+(?:-[a-z0-9]+)*)?$")
_RX_TASK_FILE = re.compile(r"^(TASK-(?:[A-Z][A-Z0-9.]{1,5}-)?[0-9]{1,6}[a-z]?)(?:-[a-z0-9]+(?:-[a-z0-9]+)*)?$")


def mounted_modules(project_root):
    return [m for m, d in FAMILY_ROOTS.items() if os.path.isdir(os.path.join(project_root, d))]


def family_index(project_root):
    """Cheap, filename-level index of every record in every mounted family module.

    -> {id: {"module", "path" (project-relative), "archived"}}
    """
    index = {}
    for module in mounted_modules(project_root):
        root = os.path.join(project_root, FAMILY_ROOTS[module])
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in filenames:
                if not fn.endswith(".md"):
                    continue
                stem = fn[:-3]
                full = os.path.join(dirpath, fn)
                rel = rel_posix(full, project_root)
                rid = None
                if module == "tasks":
                    m = _RX_TASK_FILE.match(stem)
                    rid = m.group(1) if m else None
                else:
                    m = _RX_FILE_ID.match(stem)
                    if m:
                        rid = m.group(1)
                    elif module == "crm" and RX_ACCT_ID.match(stem) and \
                            rel_posix(dirpath, root).split("/")[0] == "accounts":
                        rid = stem
                if rid and rid not in index:
                    index[rid] = {"module": module, "path": rel,
                                  "archived": ("/" + ARCHIVE_DIR + "/") in ("/" + rel)}
    return index


def find_backlinks(project_root, rid, exclude_path=None):
    """Every line in every mounted family module that names `rid`.

    -> [(module, project-relative path, lineno, line)]. Matches the bare id as
    a whole token, so frontmatter refs and [[wiki-links]] are both found.
    """
    rx = re.compile(r"(?<![A-Za-z0-9-])" + re.escape(rid) + r"(?![A-Za-z0-9-])")
    hits = []
    skip = os.path.abspath(exclude_path) if exclude_path else None
    for module in mounted_modules(project_root):
        root = os.path.join(project_root, FAMILY_ROOTS[module])
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for fn in sorted(filenames):
                if not fn.endswith(".md"):
                    continue
                full = os.path.join(dirpath, fn)
                if skip and os.path.abspath(full) == skip:
                    continue
                try:
                    with open(full, encoding="utf-8") as fh:
                        lines = fh.read().split("\n")
                except (OSError, UnicodeDecodeError):
                    continue
                for idx, line in enumerate(lines, start=1):
                    if rx.search(line) and not line.startswith("id:"):
                        hits.append((module, rel_posix(full, project_root), idx, line.strip()))
    return hits


def check_ref(ref, index, mounted, report, where, line, key=None, body=False, own_module=None):
    """F-10 / F-11 / F-12 for one reference."""
    module = ref_module(ref)
    if module is None:
        return True
    label = ("the wiki-link [[%s]]" % ref) if body else ("'%s: %s'" % (key, ref) if key else ref)
    if module not in mounted:
        report.info("F-12", "%s points into %s, which is not mounted here; unverifiable"
                    % (label, module), where=where, line=line)
        return True
    if ref not in index:
        if body:
            report.warn("F-11", "%s names a record that does not exist" % label,
                        where=where, line=line)
        else:
            report.error("F-10", "%s names a record that does not exist" % label,
                         where=where, line=line)
        return False
    return True


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path, schema, rel):
    """-> (config dict, problems [(invariant, lineno, message)], exists)."""
    if not os.path.exists(path):
        return OrderedDict(), [], False
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        return OrderedDict(), [("F-20", None, "cannot read %s: %s" % (rel, exc))], True
    values, info, errors = parse_mapping(text.split("\n"), 1)
    problems = [("F-20", ln, msg) for ln, msg in errors]
    if errors:
        return values, problems, True
    for inv, path_, msg in schema_errors(schema, values):
        key = path_.split(".")[0].split("[")[0]
        problems.append(("F-20", info["lines"].get(key), "%s %s" % (path_, msg)))
    return values, problems, True


def declared(config, key):
    """Declare to constrain: None when free (absent or []), else the declared set."""
    v = config.get(key)
    if isinstance(v, list) and v:
        out = []
        for item in v:
            if isinstance(item, dict):
                if "id" in item:
                    out.append(item["id"])
            else:
                out.append(item)
        return out
    return None


def check_declared(value, allowed, report, what, where, line, fix=None):
    """F-21 — one value against a declare-to-constrain list."""
    if allowed is None or value in allowed:
        return True
    report.error("F-21", "%s %r is not declared (declared: %s)" % (what, value, ", ".join(allowed)),
                 where=where, line=line, fix=fix)
    return False


# ---------------------------------------------------------------------------
# Common record checks — F-01..F-09, F-22, F-23, F-26
# ---------------------------------------------------------------------------

def check_record_common(rec, schema, report, where, actors=None, today_=None):
    """The checks every family record gets. Returns False if the record is unusable."""
    for inv, line, msg in rec.problems:
        report.error(inv, msg, where=where, line=line)
    if rec.text == "" and rec.problems:
        return False
    if not rec.parsed:
        return False
    today_ = today_ or today()
    rid = rec.id
    want_prefix = schema.get("x-id-prefix")
    if not rid:
        report.error("F-09", "'id' is missing", where=where)
    else:
        parsed = parse_id(rid)
        if parsed is None or parsed[0] != want_prefix:
            report.error("F-09", "'%s' is not a %s id" % (rid, want_prefix), where=where,
                         line=rec.line_of("id"))
        else:
            # F-02: filename agrees with the id
            title_rule = schema.get("x-title", "required")
            if title_rule == "none":
                if rec.stem != rid:
                    report.error("F-02", "the filename must be '%s.md'" % rid, where=where)
            else:
                if rec.stem != rid and not rec.stem.startswith(rid + "-"):
                    report.error("F-02", "the filename must be '%s-<slug>.md'" % rid, where=where)
                elif rec.stem != rid:
                    slug = rec.stem[len(rid) + 1:]
                    if not RX_SLUG.match(slug) or len(slug) > 60:
                        report.error("F-02", "the slug '%s' must be lower-case words joined by "
                                             "'-', at most 60 characters" % slug, where=where)
    # F-04: the title
    if schema.get("x-title", "required") == "required":
        if rec.h1_count == 0:
            report.error("F-04", "the body has no '# <title>' line; the H1 is the title",
                         where=where, line=rec.body_lineno)
        elif rec.h1_count > 1:
            report.error("F-04", "the body has %d '# ' lines; exactly one H1 (the title)"
                         % rec.h1_count, where=where)
        elif not rec.h1_first:
            report.error("F-04", "the H1 must be the first non-blank line of the body",
                         where=where, line=rec.h1_lineno)
    # F-26 / F-06 / F-24 / F-25: field law from the schema
    for inv, path, msg in schema_errors(schema, rec.fm):
        key = path.split(".")[0].split("[")[0]
        report.error(inv, "%s %s" % (path, msg), where=where, line=rec.line_of(key))
    # F-22: attribution
    cb = rec.fm.get("created_by")
    if isinstance(cb, str) and actors is not None and cb != "unknown" and cb not in actors:
        report.error("F-22", "created_by '%s' is not a declared actor (%s)" % (cb, ", ".join(actors)),
                     where=where, line=rec.line_of("created_by"))
    # F-23: bookkeeping dates
    created = parse_date(rec.fm.get("created")) if isinstance(rec.fm.get("created"), str) else None
    updated = parse_date(rec.fm.get("updated")) if isinstance(rec.fm.get("updated"), str) else None
    if created and created > today_:
        report.error("F-23", "created %s is in the future (today is %s)" % (created, today_),
                     where=where, line=rec.line_of("created"))
    if updated and updated > today_:
        report.error("F-23", "updated %s is in the future (today is %s)" % (updated, today_),
                     where=where, line=rec.line_of("updated"))
    if created and updated and updated < created:
        report.error("F-23", "updated %s is earlier than created %s" % (updated, created),
                     where=where, line=rec.line_of("updated"))
    return True


def ids_on_disk(records):
    """Every id a scanned file claims: its frontmatter id, else the id its name starts with."""
    out = set()
    for r in records:
        rid = r.id
        if not rid:
            m = _RX_FILE_ID.match(r.stem) or _RX_TASK_FILE.match(r.stem)
            rid = m.group(1) if m else (r.stem if RX_ACCT_ID.match(r.stem) else None)
        if rid:
            out.add(rid)
    return out


def check_unique_ids(records, report, rel_of):
    """F-08."""
    seen = OrderedDict()
    for r in records:
        if r.id:
            seen.setdefault(r.id, []).append(r)
    for rid, rs in seen.items():
        if len(rs) > 1:
            report.error("F-08", "id %s is used by %d files: %s" % (
                rid, len(rs), ", ".join(rel_of(r) for r in rs)), where=rel_of(rs[0]))
    return seen


def check_history_common(records, rows, exists, report, module_ids_ok, place_of, rel_of,
                         history_rel, cli, fixes=None, present_ids=None):
    """F-15, F-16, F-17 (F-13/F-14 come from load_history).

    present_ids: every id on disk, including files too broken to validate —
    so a file that fails to parse is reported for what it is, not as gone.
    """
    if not exists:
        if records:
            report.error("F-15", "%s is absent; %d record(s) have no filing line"
                         % (history_rel, len(records)), where=history_rel,
                         fix="%s check --fix  (re-creates the log with 'reconcile' lines)" % cli,
                         fixable=True)
            if fixes is not None:
                for r in records:
                    if r.id:
                        fixes.append(("reconcile", r))
        return
    by_id = history_by_id(rows)
    on_disk = set()
    for r in records:
        if not r.id:
            continue
        on_disk.add(r.id)
        hist = by_id.get(r.id)
        where = rel_of(r)
        if not hist:
            report.error("F-15", "%s is on disk but was never filed in %s" % (r.id, history_rel),
                         where=where, fix="%s check --fix" % cli, fixable=True)
            if fixes is not None:
                fixes.append(("reconcile", r))
            continue
        if hist[0].verb not in FILING_VERBS and hist[0].verb != "reconcile":
            report.error("F-15", "the first history line for %s is '%s'; it must be a filing "
                                 "verb (%s)" % (r.id, hist[0].verb, "/".join(FILING_VERBS)),
                         where=history_rel, line=hist[0].lineno)
        want = place_of(r)
        if want is None:
            continue
        got = last_place(hist)
        if got is not None and got != want:
            report.error("F-16", "%s is at '%s' but its last history line says '%s' (moved by "
                                 "hand?)" % (r.id, want, got), where=where,
                         fix="%s check --fix  (appends a 'reconcile' line)" % cli, fixable=True)
            if fixes is not None:
                fixes.append(("reconcile", r))
    if present_ids is not None:
        on_disk |= set(present_ids)
    for rid, hist in by_id.items():
        if rid not in on_disk and module_ids_ok(rid):
            report.warn("F-17", "%s was filed (line %s) but its file is gone; ids are never "
                                "reissued" % (rid, hist[0].lineno), where=history_rel)


def check_digests(records, table, report, rel_of, cli, is_frozen, mutable_keys_of, today_=None):
    """F-18, F-19. Returns the refreshed table (to save only after a clean run).

    F-18 fires only when `updated` is older than today: if it already reads
    today, the edit happened today and the date is already true.
    """
    today_s = (today_ or today()).isoformat()
    fresh = dict(table)
    for r in records:
        rid = r.id
        if not rid or not r.parsed:
            continue
        prev = table.get(rid)
        frozen = is_frozen(r)
        if prev:
            upd = r.fm.get("updated") if isinstance(r.fm.get("updated"), str) else ""
            if prev["sha"] != r.digest() and upd == prev["updated"] and upd < today_s:
                report.error("F-18", "%s changed since it was last recorded but 'updated' is still "
                                     "%s" % (rid, upd), where=rel_of(r),
                             fix="%s touch %s" % (cli, rid))
            if frozen and prev.get("frozen"):
                now = frozen_digest(r.fm, r.body, mutable_keys_of(r))
                if now != prev["frozen"]:
                    report.error("F-19", "%s is frozen and its content changed; frozen records are "
                                         "superseded or replaced, never rewritten" % rid,
                                 where=rel_of(r),
                                 fix="restore the original content (see version history), "
                                     "then record the change as a new record")
        fresh[rid] = digest_entry(r, frozen, mutable_keys_of(r), prev)
    return fresh


# ---------------------------------------------------------------------------
# CLI conventions
# ---------------------------------------------------------------------------

def resolve_actor(by, config):
    """--by > $RASA_ACTOR > config default_actor > refuse. Attribution is never invented."""
    actor = by or os.environ.get("RASA_ACTOR") or config.get("default_actor")
    if not actor:
        raise Refusal("who is doing this? pass --by <handle>, set RASA_ACTOR, or declare "
                      "default_actor in the module config")
    if actor != "unknown" and not RX_ACTOR.match(actor):
        raise Refusal("'%s' is not a legal actor handle (^[a-z0-9][a-z0-9._-]{0,31}$)" % actor)
    allowed = declared(config, "actors")
    if allowed is not None and actor not in allowed and actor != "unknown":
        raise Refusal("'%s' is not a declared actor (%s)" % (actor, ", ".join(allowed)))
    return actor


def next_number(prefix, ids_on_disk, history_rows):
    """max(number on disk ∪ ever filed) + 1 — the history is the retirement ledger."""
    top = 0
    for rid in list(ids_on_disk) + [r.id for r in history_rows]:
        p = parse_id(rid)
        if p and p[0] == prefix:
            top = max(top, p[1])
    return top + 1


def emit_json(obj, stream=None):
    stream = stream or sys.stdout
    json.dump(obj, stream, indent=2, ensure_ascii=False)
    stream.write("\n")


def table(rows, headers, stream=None):
    """Plain aligned text table."""
    stream = stream or sys.stdout
    rows = [[("" if c is None else str(c)) for c in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in rows:
        for i, c in enumerate(row):
            widths[i] = max(widths[i], len(c))
    fmt = "  ".join("%%-%ds" % w for w in widths)
    stream.write((fmt % tuple(headers)).rstrip() + "\n")
    stream.write((fmt % tuple("-" * w for w in widths)).rstrip() + "\n")
    for row in rows:
        stream.write((fmt % tuple(row)).rstrip() + "\n")


def split_edits(tokens):
    """['+a', '-b', 'c'] -> (add [a, c], remove [b])."""
    add, rem = [], []
    for t in tokens:
        if t.startswith("-") and len(t) > 1:
            rem.append(t[1:])
        elif t.startswith("+") and len(t) > 1:
            add.append(t[1:])
        else:
            add.append(t)
    return add, rem


def read_text_arg(text=None, path=None, use_stdin=False):
    if sum(bool(x) for x in (text, path, use_stdin)) > 1:
        raise Refusal("give the body once: --body, --body-file, or --stdin")
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                return fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            raise Refusal("cannot read %s: %s" % (path, exc))
    if use_stdin:
        return sys.stdin.read()
    return text


def run_main(fn, argv=None):
    """Wrap a CLI entry point in the exit-code contract."""
    try:
        code = fn(argv)
        return EXIT_OK if code is None else code
    except Refusal as exc:
        sys.stderr.write("✗ %s\n" % exc)
        return EXIT_REFUSED
    except EnvError as exc:
        sys.stderr.write("✗ %s\n" % exc)
        return EXIT_ENV
    except KeyboardInterrupt:
        sys.stderr.write("✗ interrupted\n")
        return EXIT_ENV


# ---------------------------------------------------------------------------
# Release-gate helpers (used by each Element's bin/check-manifest)
# ---------------------------------------------------------------------------

def doc_block(text):
    """The family block of a rules file (between the BEGIN/END markers), or None."""
    b = text.find(DOC_BEGIN)
    e = text.find(DOC_END)
    if b < 0 or e < 0 or e < b:
        return None
    return text[b:e + len(DOC_END)]


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
