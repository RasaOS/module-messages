# -*- coding: utf-8 -*-
"""cli.py — `messages`, the one way to change a messages ledger.

Every write verb is one act under the ledger lock: check preconditions,
plan every file it will touch, validate all of them, then write the records
atomically, append the history lines and refresh the content cache. A
refused command writes nothing (exit 1). The law is
.claude/messages-rules.md; this file executes it and does not redefine it.

The module RECORDS messages and keeps DRAFTS. It never sends, fetches or
syncs anything: `export-eml` hands a draft to your own mail program.

Write verbs
  draft --kind K --to WHO     a message you are writing     reply MSG      answer one
  mark-sent MSG               you sent the draft            discard MSG    you won't
  log --kind K --at WHEN      one that happened elsewhere   seen MSG|THR   read / heard
  import FILE.eml | --mbox F | --sms-xml F                  label THR +a -b
  about THR +REF              pin / unpin THR               rename THR "Title"
  archive / restore THR       touch ID
Read verbs
  threads  calls  voicemails  timeline  show  search  export-eml  history  where  check
"""

import argparse
import datetime as _dt
import os
import re
import sys
from collections import OrderedDict

from . import mail
from . import model as M
from . import records as R
from . import validate as V


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _out(a, human, data):
    if getattr(a, "json", False):
        R.emit_json(data)
    else:
        sys.stdout.write(human if human.endswith("\n") else human + "\n")


def _an(word):
    return ("an " if word[:1] in "aeiou" else "a ") + word


def _kind(name):
    k = M.KINDS.get(name or "")
    if k is None:
        raise R.Refusal("'%s' is not a kind of message (%s)" % (name, ", ".join(M.KINDS)))
    return k


def _addresses(ctx, fmt, values, flag, what="address"):
    """Addresses as given, each checked against the kind's form; CARD ids must resolve."""
    out, keys = [], set()
    index = None
    for v in values or []:
        v = (v or "").strip()
        if not M.address_ok(fmt, v):
            raise R.Refusal("%s %s is not %s" % (flag, v, R._FORMAT_LABEL.get(fmt, fmt)))
        if R.ref_prefix(v) == "CARD" and "crm" in R.mounted_modules(ctx.project):
            index = index if index is not None else R.family_index(ctx.project)
            if v not in index:
                raise R.Refusal("%s %s names a contact card that does not exist" % (flag, v))
        k = M.address_key(v)
        if k not in keys:
            keys.add(k)
            out.append(v)
    return out


def _check_refs(ctx, refs, what="about"):
    index = None
    mounted = R.mounted_modules(ctx.project)
    for ref in refs:
        if not R.is_family_ref(ref):
            raise R.Refusal("'%s' is not a family id (EVT-0001, CARD-0001, NOTE-0001, acct-x, "
                            "TASK-...)" % ref)
        if R.ref_module(ref) in mounted:
            index = index if index is not None else R.family_index(ctx.project)
            if ref not in index:
                raise R.Refusal("%s: %s names a record that does not exist" % (what, ref))
    return refs


def _labels(ctx, labels):
    allowed = ctx.declared("labels")
    for lab in labels:
        if not R.RX_SLUG.match(lab):
            raise R.Refusal("label '%s' must be a slug (lower-case words joined by '-')" % lab)
        if allowed is not None and lab not in allowed:
            raise R.Refusal("label '%s' is not declared (declared: %s)" % (lab, ", ".join(allowed)))
    return list(OrderedDict.fromkeys(labels))


def _account(ctx, acct):
    if not acct:
        return None
    if not R.RX_ACCT_ID.match(acct):
        raise R.Refusal("--account %s is not an account id (acct-<slug>)" % acct)
    _check_refs(ctx, [acct], "--account")
    return acct


def _service(ctx, kind, service, addresses):
    if "service" not in kind.fields:
        if service:
            raise R.Refusal("%s has no service (chat only)" % _an(kind.name))
        return None
    if not service:
        raise R.Refusal("a chat message needs --service (slack, whatsapp, signal, ...)")
    if not R.RX_SLUG.match(service):
        raise R.Refusal("--service %s must be a slug" % service)
    allowed = ctx.declared("services")
    if allowed is not None and service not in allowed:
        raise R.Refusal("service '%s' is not declared (declared: %s)" % (service,
                                                                        ", ".join(allowed)))
    for a in addresses:
        if R.FORMATS["chat-handle"](a) and not a.lower().startswith(service.lower() + ":"):
            raise R.Refusal("'%s' is not a %s handle (%s:<handle>)" % (a, service, service))
    return service


def _text(a):
    text = R.read_text_arg(getattr(a, "body", None), getattr(a, "body_file", None),
                           getattr(a, "stdin", False))
    return mail.clean_body(text) if text else ""


def _msg_body(text):
    return ("\n" + text.strip("\n") + "\n") if text and text.strip() else ""


def _read_files(kind, paths, role=None):
    """--attach / --audio files -> [(name, bytes, role)], refused when the kind carries none."""
    out = []
    for p in paths or []:
        if not kind.roles:
            raise R.Refusal("%s carries no attachments" % _an(kind.name))
        try:
            with open(p, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            raise R.Refusal("cannot read %s: %s" % (p, exc))
        r = role or mail.role_for(kind.name, p)
        if r not in kind.roles:
            raise R.Refusal("%s does not carry %s attachment (it carries: %s)" % (
                _an(kind.name), _an(r), ", ".join(kind.roles)))
        out.append((os.path.basename(p), data, r))
    if kind.max_files is not None and len(out) > kind.max_files:
        raise R.Refusal("%s carries at most %d attachment(s)" % (_an(kind.name), kind.max_files))
    return out


def _duration(text):
    t = (text or "").strip()
    td = R.parse_duration(t)
    if td is None:
        m = re.match(r"^(?:(\d+):)?(\d{1,2}):(\d{2})$", t)
        if m:
            td = _dt.timedelta(hours=int(m.group(1) or 0), minutes=int(m.group(2)),
                               seconds=int(m.group(3)))
    if td is None or td.total_seconds() < 0:
        raise R.Refusal("--duration %s is not a length of time (PT4M12S or 4:12)" % text)
    return R.format_duration(td)


def _people_label(ctx, participants):
    names = []
    for p in participants:
        who = p.get("who", "")
        names.append(p.get("name") or ctx.directory.name_for(who) or who)
    if not names:
        return "(no one)"
    label = ", ".join(names[:4])
    return label + (" +%d" % (len(names) - 4) if len(names) > 4 else "")


def _participants(addresses, names):
    out = []
    for a in addresses:
        d = OrderedDict([("who", a)])
        n = names.get(M.address_key(a)) if names else None
        if n:
            d["name"] = n
        out.append(d)
    return out


def _who(ctx, address, short=False):
    """An address for display: 'Jane Doe <jane@acme.com>' when crm knows it."""
    if not isinstance(address, str):
        return "?"
    d = ctx.directory
    cid = d.card_for(address)
    if R.ref_prefix(address) == "CARD":
        return "%s [%s]" % (d.cards[cid]["name"], address) if cid else address
    if cid:
        return d.cards[cid]["name"] if short else "%s <%s>" % (d.cards[cid]["name"], address)
    return address


def _participant(ctx, p):
    who = p.get("who", "?")
    if ctx.directory.card_for(who) or not p.get("name"):
        return _who(ctx, who)
    return "%s <%s>" % (p["name"], who)


def _first_line(text, width=60):
    for line in (text or "").split("\n"):
        if line.strip():
            s = line.strip()
            return s if len(s) <= width else s[:width - 1] + "…"
    return ""


def _bump(fm):
    fm["updated"] = R.today().isoformat()
    return fm


# ---------------------------------------------------------------------------
# the plan — one act, all or nothing
# ---------------------------------------------------------------------------

class TInfo(object):
    """A thread being written to in this act (new, or an existing one gaining people)."""

    def __init__(self, tid, channel, dirpath, fm, body, info, new, place):
        self.id, self.channel, self.dir = tid, channel, dirpath
        self.fm, self.body, self.info, self.new, self.place = fm, body, info, new, place
        self.path = os.path.join(dirpath, tid + ".md")
        self.keys = set(M.participant_set(p.get("who") for p in fm.get("participants") or []))
        self.added = []


class Plan(object):
    """Everything one command will write: validated in full before a byte is written."""

    def __init__(self, ctx, actor):
        self.ctx, self.actor = ctx, actor
        self.records, _ = ctx.scan()
        self.rows = ctx.history()
        self.threads = ctx.threads(self.records)
        on_disk = [r.id for r in self.records if r.id]
        self.next = {p: R.next_number(p, on_disk, self.rows) for p in ("THR", "MSG")}
        self.writes = OrderedDict()      # path -> (fm, body, schema, info)
        self.files = []                  # (full path, bytes)
        self.taken = set()
        self.lines = []
        self.touched = OrderedDict()     # thread id -> TInfo
        self.moves = []
        self.today = R.today().isoformat()
        self._finished = False
        self.ext = {}
        for t in self.threads.values():
            for m in t.messages:
                e = m.fm.get("external_id")
                if isinstance(e, str):
                    self.ext[e] = (m.id, t.id, m.fm.get("at"), m.fm.get("status"))

    def mint(self, prefix):
        n = self.next[prefix]
        self.next[prefix] = n + 1
        return R.format_id(prefix, n)

    def line(self, rid, verb, frm="-", to="-", note=""):
        self.lines.append((rid, verb, frm, to, self.actor, note))

    # -- threads -------------------------------------------------------------

    def tinfo(self, tid):
        if tid in self.touched:
            return self.touched[tid]
        t = self.threads[tid]
        fm = OrderedDict(t.rec.fm)
        if isinstance(fm.get("participants"), list):
            fm["participants"] = [OrderedDict(p) for p in fm["participants"]
                                  if isinstance(p, dict)]
        ti = TInfo(tid, t.channel, t.dir, fm, t.rec.body, t.rec.info, False, t.place.token)
        self.touched[tid] = ti
        return ti

    def open_thread(self, tid, channel):
        if not R.RX_NUMBERED_ID.match(tid or "") or R.ref_prefix(tid) != "THR":
            raise R.Refusal("--thread %s is not a thread id (THR-0001)" % tid)
        if tid in self.touched:
            ti = self.touched[tid]
        elif tid in self.threads:
            t = self.threads[tid]
            if t.archived:
                raise R.Refusal("%s is archived; restore it first (%s restore %s)" % (
                    tid, M.CLI, tid))
            ti = self.tinfo(tid)
        else:
            raise R.Refusal("no thread %s in %s/" % (tid, M.ROOT))
        if ti.channel != channel:
            raise R.Refusal("%s is a %s thread; this message belongs in %s" % (
                tid, ti.channel, channel))
        return ti

    def by_people(self, channel, keys):
        """The live thread in `channel` whose people are exactly `keys` (texts, chat, calls)."""
        if not keys:
            return None
        for ti in self.touched.values():
            if ti.new and ti.channel == channel and ti.keys == keys:
                return ti
        for t in self.threads.values():
            if t.channel == channel and not t.archived and t.participant_keys() == keys:
                return self.tinfo(t.id)
        return None

    def new_thread(self, channel, title, participants, verb="new", identity=None, labels=None,
                   account=None, about=None):
        tid = self.mint("THR")
        fm = OrderedDict([("id", tid), ("created", self.today), ("created_by", self.actor),
                          ("updated", self.today)])
        if participants:
            fm["participants"] = participants
        if identity:
            fm["identity"] = identity
        if labels:
            fm["labels"] = labels
        if account:
            fm["account"] = account
        if about:
            fm["about"] = list(OrderedDict.fromkeys(about))
        title = " ".join((title or "").split()) or "(untitled)"
        dirpath = self.ctx.abs("%s/%s-%s" % (channel, tid, R.slugify(title)))
        place = "%s/%s" % (channel, tid)
        ti = TInfo(tid, channel, dirpath, fm, R.new_body(title), None, True, place)
        self.touched[tid] = ti
        self.line(tid, verb, "-", place, "")
        return ti

    def add_people(self, ti, participants):
        for p in participants:
            k = M.address_key(p["who"])
            if k in ti.keys:
                continue
            ti.fm.setdefault("participants", []).append(p)
            ti.keys.add(k)
            if not ti.new:
                ti.added.append(p["who"])

    # -- messages ------------------------------------------------------------

    def add_message(self, ti, fm, body, files=(), verb="new"):
        atts = []
        for name, data, role in files:
            rel, full = mail.attachment_path(ti.dir, fm["id"], name, self.taken)
            self.taken.add(rel)
            self.files.append((full, data))
            atts.append(OrderedDict([("name", mail.one_line(name) or "file"), ("path", rel),
                                     ("role", role)]))
        if atts:
            fm["attachments"] = atts
        path = os.path.join(ti.dir, fm["id"] + ".md")
        self.writes[path] = (fm, body, self.ctx.schemas["message"], None)
        self.line(fm["id"], verb, "-", ti.place, "status=%s" % fm["status"])
        return path

    def update(self, rec, fm, verb, frm="-", to="-", note="", body=None):
        self.writes[rec.path] = (fm, rec.body if body is None else body,
                                 self.ctx.schema_for(rec), rec.info)
        self.line(rec.id, verb, frm, to, note)

    # -- doing it ------------------------------------------------------------

    def _finish(self):
        if self._finished:
            return
        self._finished = True
        schema = self.ctx.schemas["thread"]
        for ti in self.touched.values():
            if ti.new:
                self.writes[ti.path] = (ti.fm, ti.body, schema, None)
            elif ti.added:
                _bump(ti.fm)
                self.writes[ti.path] = (ti.fm, ti.body, schema, ti.info)
                self.line(ti.id, "participant", "-", "-", " ".join("+" + a for a in ti.added))
        for path, (fm, body, schema, info) in self.writes.items():
            problems = R.schema_errors(schema, fm)
            if problems:
                inv, where, msg = problems[0]
                raise R.Refusal("refusing to write %s: %s %s (%s)" % (
                    R.rel_posix(path, self.ctx.project), where or "(record)", msg, inv))

    def preview(self):
        self._finish()
        out = []
        for full, data in self.files:
            out.append("would copy %d bytes to %s" % (len(data),
                                                     R.rel_posix(full, self.ctx.project)))
        for path, (fm, body, schema, info) in self.writes.items():
            out.append("would write %s\n%s" % (R.rel_posix(path, self.ctx.project),
                                                M.render_record(fm, body, schema, info)))
        for rid, verb, frm, to, actor, note in self.lines:
            out.append("would log  %s %s %s -> %s %s" % (rid, verb, frm, to, note))
        return "\n".join(out)

    def apply(self):
        self._finish()
        for full, data in self.files:
            mail.write_bytes(full, data)
        for path, (fm, body, schema, info) in self.writes.items():
            R.atomic_write(path, M.render_record(fm, body, schema, info))
        if self.lines:
            self.ctx.log(self.lines)
        if self.writes:
            self.ctx.refresh_digests(list(self.writes))


def _identity_id_for(ctx, channel, addresses):
    keys = set(M.address_key(a) for a in addresses)
    for i in ctx.identities(channel):
        if M.address_key(i.get("address")) in keys:
            return i.get("id")
    only = ctx.identities(channel)
    return only[0].get("id") if len(only) == 1 else None


def _thread_for(ctx, plan, kind, thread_id, subject, others, names, identity_id,
                labels=None, account=None, about=None, verb="new"):
    """Find or open the thread a new message goes into."""
    participants = _participants(others, names)
    if thread_id:
        if subject:
            raise R.Refusal("--subject starts a new thread; --thread adds to an existing one")
        ti = plan.open_thread(thread_id, kind.channel)
        plan.add_people(ti, participants)
        return ti
    ti = None
    if not subject and kind.channel in ("text", "chat", "voice"):
        ti = plan.by_people(kind.channel, M.participant_set(others))
    if ti is not None:
        return ti
    if kind.channel == "email" and not subject:
        raise R.Refusal("an email needs --subject (it is the thread's title) or --thread THR")
    title = subject or _people_label(ctx, participants)
    return plan.new_thread(kind.channel, title, participants, verb=verb, identity=identity_id,
                           labels=labels, account=account, about=about)


def _finish_write(ctx, a, plan, human, data):
    if a.dry_run:
        _out(a, plan.preview(), dict(data, dry_run=True))
        return 0
    plan.apply()
    _out(a, human, data)
    return 0


# ---------------------------------------------------------------------------
# write verbs
# ---------------------------------------------------------------------------

def cmd_draft(ctx, a):
    ctx.require_root()
    cfg = ctx.usable_config()
    actor = R.resolve_actor(a.by, cfg)
    kind = _kind(a.kind)
    if not kind.composable:
        raise R.Refusal("%s is not composed here; record one that happened with `%s log --kind "
                        "%s`" % (_an(kind.name), M.CLI, kind.name))
    ctx.check_channel(kind.channel)
    ident = ctx.identity_for(kind.channel, a.as_ident, why="write", kind=kind)  # THE HARD-STOP
    to = _addresses(ctx, kind.address, a.to, "--to")
    if not to:
        raise R.Refusal("who is it to? pass --to (repeatable)")
    cc = _copies(ctx, kind, a.cc, "cc")
    bcc = _copies(ctx, kind, a.bcc, "bcc")
    service = _service(ctx, kind, a.service, to + cc + bcc)
    text = _text(a)
    files = _read_files(kind, a.attach)
    labels = _labels(ctx, a.label or [])
    about = _check_refs(ctx, a.about or [])
    account = _account(ctx, a.account)
    with ctx.lock():
        plan = Plan(ctx, actor)
        mine = ctx.identity_keys(kind.channel)
        others = M.counterparts(to + cc + bcc, mine)
        ti = _thread_for(ctx, plan, kind, a.thread, a.subject, others, {}, ident.get("id"),
                         labels, account, about)
        mid = plan.mint("MSG")
        fm = OrderedDict([("id", mid), ("created", plan.today), ("created_by", actor),
                          ("updated", plan.today), ("kind", kind.name), ("status", "draft"),
                          ("from", ident["address"]), ("to", to)])
        if cc:
            fm["cc"] = cc
        if bcc:
            fm["bcc"] = bcc
        if service:
            fm["service"] = service
        path = plan.add_message(ti, fm, _msg_body(text), files)
        return _finish_write(ctx, a, plan, "%s  draft  %s  (in %s)" % (
            mid, R.rel_posix(path, ctx.project), ti.id),
            {"id": mid, "thread": ti.id, "path": R.rel_posix(path, ctx.project)})


def _copies(ctx, kind, values, which):
    if not values:
        return []
    if which not in kind.copies:
        raise R.Refusal("%s has no %s" % (_an(kind.name), which))
    return _addresses(ctx, kind.address, values, "--" + which)


def cmd_reply(ctx, a):
    ctx.require_root()
    cfg = ctx.usable_config()
    actor = R.resolve_actor(a.by, cfg)
    text = _text(a)
    with ctx.lock():
        plan = Plan(ctx, actor)
        parent = _find_in(plan, a.id, "MSG")
        t = ctx.thread_of(parent, plan.threads)
        if t is None:
            raise R.Refusal("%s has no thread record beside it; run %s" % (parent.id, M.CHECK))
        if parent.fm.get("status") not in M.LIVE:
            raise R.Refusal("%s is %s; you reply to a message that happened" % (
                parent.id, _an(parent.fm.get("status") or "?")))
        pkind = M.KINDS.get(parent.fm.get("kind"))
        kind = _kind(a.kind) if a.kind else pkind
        if pkind is not None and not pkind.composable and not a.kind:
            raise R.Refusal("%s is %s; answer it by calling back, or write a text in its own "
                            "thread (%s draft --kind sms --to %s)" % (
                                parent.id, _an(pkind.name), M.CLI, parent.fm.get("from", "…")))
        if not kind.composable or kind.channel != t.channel:
            raise R.Refusal("a reply in %s (%s) is one of: %s" % (t.id, t.channel, ", ".join(
                k.name for k in M.kinds_in(t.channel) if k.composable) or "nothing"))
        if t.archived:
            raise R.Refusal("%s is archived; restore it first" % t.id)
        ctx.check_channel(kind.channel)
        wanted = a.as_ident or t.rec.fm.get("identity")
        if wanted and not ctx.identity_by_id(wanted) and not a.as_ident:
            wanted = None
        ident = ctx.identity_for(kind.channel, wanted, why="reply", kind=kind)  # THE HARD-STOP
        mine = ctx.identity_keys(kind.channel)
        pfrom = parent.fm.get("from")
        pto = list(parent.fm.get("to") or [])
        pcc = list(parent.fm.get("cc") or [])
        if "cc" not in kind.copies:
            # texts, chat, fax: a reply goes to the whole conversation
            to = M.counterparts(([pfrom] if pfrom else []) + pto + pcc, mine)
            cc = []
        else:
            first = [pfrom] if parent.fm.get("status") == "received" and pfrom else pto
            to = M.counterparts(first, mine)
            cc = []
            if a.all:
                rest = M.counterparts(([pfrom] if pfrom else []) + pto + pcc, mine)
                keys = set(M.address_key(x) for x in to)
                cc = [x for x in rest if M.address_key(x) not in keys]
        if not to:
            raise R.Refusal("nobody to reply to: every address on %s is one of your own "
                            "identities" % parent.id)
        for v in to + cc:
            if not M.address_ok(kind.address, v):
                raise R.Refusal("%s is not %s; reply with another kind (--kind)" % (
                    v, R._FORMAT_LABEL.get(kind.address)))
        files = _read_files(kind, a.attach)
        ti = plan.tinfo(t.id)
        plan.add_people(ti, _participants(M.counterparts(to + cc, mine), {}))
        mid = plan.mint("MSG")
        fm = OrderedDict([("id", mid), ("created", plan.today), ("created_by", actor),
                          ("updated", plan.today), ("kind", kind.name), ("status", "draft"),
                          ("from", ident["address"]), ("to", to)])
        if cc:
            fm["cc"] = cc
        if parent.fm.get("service") and "service" in kind.fields:
            fm["service"] = parent.fm["service"]
        fm["in_reply_to"] = parent.id
        path = plan.add_message(ti, fm, _msg_body(text), files)
        return _finish_write(ctx, a, plan, "%s  draft reply to %s  %s" % (
            mid, parent.id, R.rel_posix(path, ctx.project)),
            {"id": mid, "thread": t.id, "in_reply_to": parent.id,
             "path": R.rel_posix(path, ctx.project)})


def _find_in(plan, rid, prefix):
    if not R.RX_NUMBERED_ID.match(rid or "") or R.ref_prefix(rid) != prefix:
        raise R.Refusal("'%s' is not a %s id (%s-0001)" % (rid, M.PREFIX_TYPE[prefix], prefix))
    hits = [r for r in plan.records if r.id == rid]
    if not hits:
        raise R.Refusal("no record %s in %s/" % (rid, M.ROOT))
    if len(hits) > 1:
        raise R.Refusal("%s is used by %d files; run %s" % (rid, len(hits), M.CHECK))
    if not hits[0].parsed:
        raise R.Refusal("%s does not parse; run %s" % (rid, M.CHECK))
    return hits[0]


def _draft_of(plan, ctx, rid):
    rec = _find_in(plan, rid, "MSG")
    status = rec.fm.get("status")
    if status in M.LIVE:
        raise R.Refusal("%s was already %s; a message that happened is frozen" % (rec.id, status))
    if status == "discarded":
        raise R.Refusal("%s was discarded; a discarded draft stays discarded — draft a new one"
                        % rec.id)
    t = ctx.thread_of(rec, plan.threads)
    if t is None:
        raise R.Refusal("%s has no thread record beside it; run %s" % (rec.id, M.CHECK))
    if t.archived:
        raise R.Refusal("%s is in archived thread %s; restore it first" % (rec.id, t.id))
    return rec, t


def cmd_mark_sent(ctx, a):
    ctx.require_root()
    cfg = ctx.usable_config()
    actor = R.resolve_actor(a.by, cfg)
    with ctx.lock():
        plan = Plan(ctx, actor)
        rec, t = _draft_of(plan, ctx, a.id)
        kind = _kind(rec.fm.get("kind"))
        if not ctx.identities(kind.channel):
            ctx.identity_for(kind.channel, why="send")                     # THE HARD-STOP
        mine = ctx.identity_keys(kind.channel)
        if M.address_key(rec.fm.get("from")) not in mine:
            raise R.Refusal("HARD-STOP: %s is from %s, which is not a declared %s identity; "
                            "who you may write as is never guessed" % (
                                rec.id, rec.fm.get("from"), kind.channel))
        at = M.resolve_at(a.at or "now", cfg, day_ok=kind.day_ok)
        fm = OrderedDict(rec.fm)
        fm["status"] = "sent"
        fm["at"] = at
        _bump(fm)
        plan.update(rec, fm, "mark-sent", "status=draft", "status=sent", a.note or "")
        return _finish_write(ctx, a, plan, "%s  sent %s  (frozen from now on)" % (rec.id, at),
                             {"id": rec.id, "status": "sent", "at": at})


def cmd_discard(ctx, a):
    ctx.require_root()
    actor = R.resolve_actor(a.by, ctx.usable_config())
    with ctx.lock():
        plan = Plan(ctx, actor)
        rec, t = _draft_of(plan, ctx, a.id)
        fm = OrderedDict(rec.fm)
        fm["status"] = "discarded"
        _bump(fm)
        plan.update(rec, fm, "discard", "status=draft", "status=discarded", a.note or "")
        return _finish_write(ctx, a, plan, "%s  discarded (kept, never sent)" % rec.id,
                             {"id": rec.id, "status": "discarded"})


def cmd_log(ctx, a):
    """Record a message that happened elsewhere: the call you took, the letter that came."""
    ctx.require_root()
    cfg = ctx.usable_config()
    actor = R.resolve_actor(a.by, cfg)
    kind = _kind(a.kind)
    ctx.check_channel(kind.channel)
    status = "sent" if a.sent else "received"
    at = M.resolve_at(a.at, cfg, day_ok=kind.day_ok)
    ident = None
    if status == "sent":
        if kind.composable:
            ident = ctx.identity_for(kind.channel, a.as_ident, why="record a message sent",
                                     kind=kind)                              # THE HARD-STOP
        else:
            ident = ctx.identity_optional(kind.channel, a.as_ident, kind=kind)
        frm = ident["address"] if ident else None
        if a.from_ and (not frm or M.address_key(a.from_) != M.address_key(frm)):
            raise R.Refusal("--sent records a message from you; --from %s is not your %s identity"
                            % (a.from_, kind.channel))
        to = _addresses(ctx, kind.address, a.to, "--to")
        if not to:
            raise R.Refusal("who was it to? pass --to")
    else:
        if not a.from_:
            raise R.Refusal("who was it from? pass --from (or --sent if it was you)")
        frm = _addresses(ctx, kind.address, [a.from_], "--from")[0]
        to = _addresses(ctx, kind.address, a.to, "--to")
        ident = ctx.identity_optional(kind.channel, a.as_ident, kind=kind)
    cc = _copies(ctx, kind, a.cc, "cc")
    bcc = _copies(ctx, kind, a.bcc, "bcc")
    service = _service(ctx, kind, a.service, ([frm] if frm else []) + to + cc + bcc)
    text = _text(a)
    extra = OrderedDict()
    if a.outcome:
        if "outcome" not in kind.fields:
            raise R.Refusal("%s has no outcome (calls only)" % _an(kind.name))
        if a.outcome not in M.OUTCOMES:
            raise R.Refusal("--outcome %s is not one of: %s" % (a.outcome, ", ".join(M.OUTCOMES)))
        extra["outcome"] = a.outcome
    elif "outcome" in kind.required:
        raise R.Refusal("how did the call go? pass --outcome (%s)" % ", ".join(M.OUTCOMES))
    if a.duration:
        if "duration" not in kind.fields:
            raise R.Refusal("%s has no duration (calls and voicemails)" % _an(kind.name))
        extra["duration"] = _duration(a.duration)
    elif extra.get("outcome") == "answered":
        raise R.Refusal("an answered call has a length: pass --duration (PT4M12S or 4:12)")
    if kind.name == "voicemail":
        if text and not a.transcript:
            raise R.Refusal("the body of a voicemail is its transcript: say who wrote it "
                            "(--transcript machine | human)")
        if a.transcript and not text:
            raise R.Refusal("--transcript describes a transcript; give it as the body")
    if a.transcript:
        if "transcript" not in kind.fields:
            raise R.Refusal("%s has no transcript (voicemails only)" % _an(kind.name))
        extra["transcript"] = a.transcript
    files = []
    if a.audio:
        if kind.name not in ("voicemail", "call"):
            raise R.Refusal("--audio is a voicemail's audio or a call's recording")
        files += _read_files(kind, [a.audio], "audio" if kind.name == "voicemail" else "recording")
    files += _read_files(kind, a.attach)
    if kind.max_files is not None and len(files) > kind.max_files:
        raise R.Refusal("%s carries at most %d attachment(s)" % (_an(kind.name), kind.max_files))
    labels = _labels(ctx, a.label or [])
    about = _check_refs(ctx, a.about or [])
    account = _account(ctx, a.account)
    with ctx.lock():
        plan = Plan(ctx, actor)
        if a.external_id and a.external_id in plan.ext:
            raise R.Refusal("external id %s is already %s" % (a.external_id,
                                                               plan.ext[a.external_id][0]))
        mine = ctx.identity_keys(kind.channel)
        others = M.counterparts(([frm] if frm else []) + to + cc + bcc, mine)
        names = {}
        if a.name and others:
            names[M.address_key(others[0])] = a.name
        ident_id = ident.get("id") if ident else _identity_id_for(
            ctx, kind.channel, ([frm] if frm else []) + to + cc)
        ti = _thread_for(ctx, plan, kind, a.thread, a.subject, others, names, ident_id,
                         labels, account, about)
        mid = plan.mint("MSG")
        fm = OrderedDict([("id", mid), ("created", plan.today), ("created_by", actor),
                          ("updated", plan.today), ("kind", kind.name), ("status", status),
                          ("at", at)])
        if frm:
            fm["from"] = frm
        if to:
            fm["to"] = to
        if cc:
            fm["cc"] = cc
        if bcc:
            fm["bcc"] = bcc
        if service:
            fm["service"] = service
        fm.update(extra)
        if a.reply_to:
            parent = _find_in(plan, a.reply_to, "MSG")
            pt = ctx.thread_of(parent, plan.threads)
            if pt is None or pt.id != ti.id:
                raise R.Refusal("--reply-to %s is not in %s" % (a.reply_to, ti.id))
            pi, ci = M.at_instant(parent.fm.get("at")), M.at_instant(at)
            if parent.fm.get("status") not in M.LIVE or (pi and ci and pi > ci):
                raise R.Refusal("--reply-to %s: a message answers an earlier one that happened"
                                % a.reply_to)
            fm["in_reply_to"] = parent.id
        if a.external_id:
            fm["external_id"] = a.external_id
        path = plan.add_message(ti, fm, _msg_body(text), files)
        note = ""
        if kind.name == "voicemail" and not a.audio:
            note = "  (no audio attached: check-messages will say so)"
        return _finish_write(ctx, a, plan, "%s  %s %s  %s%s" % (
            mid, status, kind.name, R.rel_posix(path, ctx.project), note),
            {"id": mid, "thread": ti.id, "status": status,
             "path": R.rel_posix(path, ctx.project)})


def cmd_seen(ctx, a):
    ctx.require_root()
    actor = R.resolve_actor(a.by, ctx.usable_config())
    with ctx.lock():
        plan = Plan(ctx, actor)
        if R.ref_prefix(a.id) == "THR":
            if a.id not in plan.threads:
                raise R.Refusal("no thread %s in %s/" % (a.id, M.ROOT))
            targets = plan.threads[a.id].ordered()
        else:
            targets = [_find_in(plan, a.id, "MSG")]
        now = R.now_utc()
        stamp = R.format_datetime(now)
        done = []
        for m in targets:
            if m.fm.get("status") != "received" or "seen" in m.fm:
                continue
            inst = M.at_instant(m.fm.get("at"))
            if inst is not None and inst > now:
                continue
            fm = OrderedDict(m.fm)
            fm["seen"] = stamp
            _bump(fm)
            plan.update(m, fm, "seen")
            done.append(m.id)
        if not done:
            raise R.Refusal("nothing to mark: %s has no unseen received message" % a.id)
        return _finish_write(ctx, a, plan, "seen: %s" % ", ".join(done),
                             {"seen": done, "at": stamp})


def cmd_import(ctx, a):
    ctx.require_root()
    cfg = ctx.usable_config()
    actor = R.resolve_actor(a.by, cfg)
    sources = sum(bool(x) for x in (a.files, a.mbox, a.sms_xml))
    if sources != 1:
        raise R.Refusal("import one kind of file at a time: FILE.eml …, --mbox FILE, or "
                        "--sms-xml FILE")
    mine_email = ctx.identity_keys("email")
    if a.files:
        items, notes = mail.read_eml_files(a.files, mine_email)
    elif a.mbox:
        items, notes = mail.read_mbox(a.mbox, mine_email)
    else:
        items, notes = mail.read_sms_xml(a.sms_xml)
    items.sort(key=lambda i: M.at_instant(i.at) or _dt.datetime.max.replace(
        tzinfo=_dt.timezone.utc))
    with ctx.lock():
        plan = Plan(ctx, actor)
        imported, dupes, new_threads, used_threads = [], 0, 0, OrderedDict()
        for item in items:
            if item.external_id in plan.ext:
                dupes += 1
                continue
            kind = M.KINDS[item.kind]
            ctx.check_channel(kind.channel)
            mine = ctx.identity_keys(kind.channel)
            frm = item.from_
            to = list(item.to)
            if frm is None:                          # one you sent, from a file that omits you
                if kind.composable:
                    ident = ctx.identity_for(kind.channel, a.as_ident, kind=kind,
                                             why="record messages sent")      # THE HARD-STOP
                else:
                    ident = ctx.identity_optional(kind.channel, a.as_ident, kind=kind)
                frm = ident["address"] if ident else None
            elif item.status == "sent" and kind.composable and M.address_key(frm) not in mine:
                raise R.Refusal("HARD-STOP: %s is marked sent but %s is not a declared %s "
                                "identity" % (item.source, frm, kind.channel))
            if item.status == "received" and not to and kind.channel in ("text", "voice"):
                ident = ctx.identity_optional(kind.channel, a.as_ident, kind=kind)
                if ident:
                    to = [ident["address"]]
            others = M.counterparts(([frm] if frm else []) + to + item.cc + item.bcc, mine)
            ident_id = _identity_id_for(ctx, kind.channel, ([frm] if frm else []) + to + item.cc)
            ti, parent = None, None
            if kind.channel == "email":
                for ref in item.refs:
                    if ref in plan.ext:
                        pid, tid, pat, pstatus = plan.ext[ref]
                        if tid in plan.threads and plan.threads[tid].archived:
                            continue
                        if tid not in plan.threads and tid not in plan.touched:
                            continue
                        ti = plan.tinfo(tid)
                        pi, ci = M.at_instant(pat), M.at_instant(item.at)
                        if pstatus in M.LIVE and pi and ci and pi <= ci:
                            parent = pid
                        break
            if ti is None:
                subject = item.subject if kind.channel == "email" else None
                ti = _thread_for(ctx, plan, kind, None, subject, others, item.names, ident_id,
                                 verb="import")
                if ti.new and ti.id not in used_threads:
                    new_threads += 1
            else:
                plan.add_people(ti, _participants(others, item.names))
            used_threads[ti.id] = True
            mid = plan.mint("MSG")
            fm = OrderedDict([("id", mid), ("created", plan.today), ("created_by", actor),
                              ("updated", plan.today), ("kind", kind.name),
                              ("status", item.status), ("at", item.at)])
            if frm:
                fm["from"] = frm
            if to:
                fm["to"] = to
            if item.cc:
                fm["cc"] = list(item.cc)
            if item.bcc:
                fm["bcc"] = list(item.bcc)
            if item.outcome:
                fm["outcome"] = item.outcome
            if item.duration:
                fm["duration"] = item.duration
            if parent:
                fm["in_reply_to"] = parent
            fm["external_id"] = item.external_id
            plan.add_message(ti, fm, _msg_body(item.body), item.attachments, verb="import")
            plan.ext[item.external_id] = (mid, ti.id, item.at, item.status)
            imported.append(mid)
        data = OrderedDict([("imported", len(imported)), ("threads", len(used_threads)),
                            ("new_threads", new_threads), ("already_imported", dupes),
                            ("notes", notes), ("ids", imported)])
        human = "imported %d message(s) into %d thread(s) (%d new); %d already imported" % (
            len(imported), len(used_threads), new_threads, dupes)
        if notes:
            human += "\n" + "\n".join("  note: " + n for n in notes)
        if not imported:
            _out(a, human, data)
            return 0
        return _finish_write(ctx, a, plan, human, data)


def take_flags(a, tokens):
    """Pull --by/--dry-run/--json/--root out of a REMAINDER list, so '+a -b --by x' works."""
    rest, i = [], 0
    while i < len(tokens):
        t = tokens[i]
        if t in ("--by", "--root") and i + 1 < len(tokens):
            setattr(a, t[2:], tokens[i + 1])
            i += 2
            continue
        if t == "--dry-run":
            a.dry_run = True
        elif t == "--json":
            a.json = True
        elif t.startswith("--"):
            raise R.Refusal("unknown option %s" % t)
        else:
            rest.append(t)
        i += 1
    if not rest:
        raise R.Refusal("give at least one change: +add -remove")
    return rest


def _thread_rec(plan, tid):
    rec = _find_in(plan, tid, "THR")
    return rec


def _edit_thread_list(ctx, a, key, validate, verb):
    a.changes = take_flags(a, a.changes)
    ctx.require_root()
    actor = R.resolve_actor(a.by, ctx.usable_config())
    add, rem = R.split_edits(a.changes)
    validate(add)
    with ctx.lock():
        plan = Plan(ctx, actor)
        rec = _thread_rec(plan, a.id)
        cur = list(rec.fm.get(key) or []) if isinstance(rec.fm.get(key), list) else []
        missing = [r for r in rem if r not in cur]
        if missing:
            raise R.Refusal("%s has no %s %s" % (rec.id, key, ", ".join(missing)))
        new = [v for v in cur if v not in rem] + [v for v in add if v not in cur]
        if new == cur:
            raise R.Refusal("nothing changes")
        fm = OrderedDict(rec.fm)
        if new:
            fm[key] = new
        else:
            fm.pop(key, None)
        _bump(fm)
        plan.update(rec, fm, verb, note=" ".join(a.changes))
        return _finish_write(ctx, a, plan, "%s  %s: %s" % (rec.id, key, ", ".join(new) or "(none)"),
                             {"id": rec.id, key: new})


def cmd_label(ctx, a):
    return _edit_thread_list(ctx, a, "labels", lambda add: _labels(ctx, add), "label")


def cmd_about(ctx, a):
    return _edit_thread_list(ctx, a, "about", lambda add: _check_refs(ctx, add), "about")


def cmd_pin(ctx, a, on=True):
    ctx.require_root()
    actor = R.resolve_actor(a.by, ctx.usable_config())
    with ctx.lock():
        plan = Plan(ctx, actor)
        rec = _thread_rec(plan, a.id)
        if (rec.fm.get("pinned") == "true") == on:
            raise R.Refusal("%s is already %s" % (rec.id, "pinned" if on else "unpinned"))
        fm = OrderedDict(rec.fm)
        if on:
            fm["pinned"] = True
        else:
            fm.pop("pinned", None)
        _bump(fm)
        plan.update(rec, fm, "pin" if on else "unpin")
        return _finish_write(ctx, a, plan, "%s  %s" % (rec.id, "pinned" if on else "unpinned"),
                             {"id": rec.id, "pinned": on})


def cmd_rename(ctx, a):
    ctx.require_root()
    actor = R.resolve_actor(a.by, ctx.usable_config())
    title = " ".join((a.title or "").split())
    if not title:
        raise R.Refusal("a title cannot be empty")
    with ctx.lock():
        plan = Plan(ctx, actor)
        rec = _thread_rec(plan, a.id)
        if rec.h1 is None:
            raise R.Refusal("%s has no H1 to rename; run %s" % (rec.id, M.CHECK))
        lines = rec.body.split("\n")
        idx = rec.h1_lineno - rec.body_lineno
        old = lines[idx][2:].strip()
        lines[idx] = "# " + title
        fm = _bump(OrderedDict(rec.fm))
        old_dir = os.path.dirname(rec.path)
        new_dir = os.path.join(os.path.dirname(old_dir), "%s-%s" % (rec.id, R.slugify(title)))
        if a.dry_run:
            _out(a, "would retitle %s: %r -> %r (%s)" % (rec.id, old, title,
                                                         os.path.basename(new_dir)),
                 {"dry_run": True})
            return 0
        plan.update(rec, fm, "rename", note=old + " -> " + title, body="\n".join(lines))
        plan.apply()
        if new_dir != old_dir:
            R.move_path(old_dir, new_dir)
            ctx.refresh_digests([os.path.join(new_dir, rec.name)])
    _out(a, "%s  %s" % (rec.id, title), {"id": rec.id, "title": title})
    return 0


def cmd_archive(ctx, a, restore=False):
    ctx.require_root()
    actor = R.resolve_actor(a.by, ctx.usable_config())
    with ctx.lock():
        plan = Plan(ctx, actor)
        rec = _thread_rec(plan, a.id)
        t = plan.threads.get(rec.id)
        if t is None:
            raise R.Refusal("%s is not in a thread directory; run %s" % (rec.id, M.CHECK))
        if restore != t.archived:
            raise R.Refusal("%s is %s" % (rec.id,
                                          "not archived" if restore else "already archived"))
        if restore:
            ctx.check_channel(t.channel)
        drafts = [m.id for m in t.messages if m.fm.get("status") == "draft"]
        if drafts and not restore:
            raise R.Refusal("%s has an open draft (%s); send it (mark-sent) or discard it first"
                            % (rec.id, ", ".join(drafts)))
        old_place = t.place.token
        dirname = os.path.basename(t.dir)
        if restore:
            dst = ctx.abs("%s/%s" % (t.channel, dirname))
            new_place = "%s/%s" % (t.channel, t.id)
        else:
            dst = ctx.abs("%s/%s/%s" % (R.ARCHIVE_DIR, t.channel, dirname))
            new_place = "%s/%s/%s" % (R.ARCHIVE_DIR, t.channel, t.id)
        verb = "restore" if restore else "archive"
        ids = [t.id] + [m.id for m in t.ordered()]
        if a.dry_run:
            _out(a, "would %s %s (%d record(s)): %s -> %s" % (verb, t.id, len(ids), old_place,
                                                              new_place), {"dry_run": True})
            return 0
        R.move_path(t.dir, dst)
        ctx.log([(rid, verb, old_place, new_place, actor, a.note or "") for rid in ids])
        R.prune_empty_dirs(os.path.dirname(t.dir), ctx.root)
    _out(a, "%s  %s -> %s  (%d record(s))" % (t.id, old_place, new_place, len(ids)),
         {"id": t.id, "to": new_place, "records": ids})
    return 0


def cmd_touch(ctx, a):
    ctx.require_root()
    actor = R.resolve_actor(a.by, ctx.usable_config())
    with ctx.lock():
        rec = ctx.find(a.id)
        schema = ctx.schema_for(rec)
        errs = R.schema_errors(schema, rec.fm)
        if errs:
            raise R.Refusal("%s does not validate (%s %s); fix it first, then touch" % (
                rec.id, errs[0][1] or "(record)", errs[0][2]))
        today = R.today().isoformat()
        if a.dry_run:
            _out(a, "would stamp %s updated=%s" % (rec.id, today), {"dry_run": True})
            return 0
        # Touch keeps the hand edit byte-for-byte: only the updated line changes.
        lines = rec.text.split("\n")
        ln = rec.line_of("updated")
        lines[ln - 1] = "updated: %s" % today
        R.atomic_write(rec.path, "\n".join(lines))
        ctx.refresh_digest(rec)
        ctx.log([(rec.id, "touch", "-", "-", actor, a.note or "")])
    _out(a, "%s  updated %s" % (rec.id, today), {"id": rec.id, "updated": today})
    return 0


# ---------------------------------------------------------------------------
# read verbs
# ---------------------------------------------------------------------------

def _all_threads(ctx, a):
    threads = ctx.threads()
    out = []
    for t in threads.values():
        if getattr(a, "archived", False):
            if not t.archived:
                continue
        elif not getattr(a, "all", False) and t.archived:
            continue
        out.append(t)
    return out


def _with_keys(ctx, who):
    if not who:
        return None
    if R.is_family_ref(who) and R.ref_prefix(who) != "CARD":
        raise R.Refusal("--with names a person: an address or a CARD id")
    return ctx.directory.keys_for(who)


def _involves(msg, keys):
    return any(M.address_key(x) in keys for x in M.addresses_of(msg))


def _flags(st):
    f = []
    if st["needs_reply"]:
        f.append("needs-reply")
    if st["unseen"]:
        f.append("unseen:%d" % len(st["unseen"]))
    if st["waiting"]:
        f.append("waiting")
    if st["drafts"]:
        f.append("drafts:%d" % len(st["drafts"]))
    return " ".join(f)


def cmd_threads(ctx, a):
    ctx.require_root()
    keys = _with_keys(ctx, a.with_)
    rows = []
    for t in _all_threads(ctx, a):
        st = M.thread_state(t, ctx.stale_after)
        if a.channel and t.channel != a.channel:
            continue
        if a.kind and not any(m.fm.get("kind") == a.kind for m in t.messages):
            continue
        if a.label and a.label not in (t.rec.fm.get("labels") or []):
            continue
        if a.needs_reply and not st["needs_reply"]:
            continue
        if a.unseen and not st["unseen"]:
            continue
        if a.waiting and not st["waiting"]:
            continue
        if a.drafts and not st["drafts"]:
            continue
        if keys is not None and not (t.participant_keys() & keys or
                                     any(_involves(m, keys) for m in t.messages)):
            continue
        rows.append((t, st))
    rows.sort(key=lambda ts: M.sort_key(ts[1]["last"]) if ts[1]["last"] else (-1, "", 0),
              reverse=True)
    rows.sort(key=lambda ts: ts[0].rec.fm.get("pinned") != "true")
    if a.json:
        R.emit_json([OrderedDict([
            ("id", t.id), ("channel", t.channel), ("title", t.title),
            ("with", [p.get("who") for p in t.participants()]),
            ("last", st["last"].fm.get("at") if st["last"] else None),
            ("messages", len(t.messages)), ("needs_reply", st["needs_reply"]),
            ("unseen", [m.id for m in st["unseen"]]), ("waiting", st["waiting"]),
            ("drafts", [m.id for m in st["drafts"]]), ("archived", t.archived),
            ("path", ctx.rel_of(t.rec).rsplit("/", 1)[0])]) for t, st in rows])
        return 0
    if not rows:
        print("(no threads)")
        return 0
    R.table([[t.id, t.channel, ("* " if t.rec.fm.get("pinned") == "true" else "") + t.title,
              _people_label(ctx, t.participants()),
              (st["last"].fm.get("at") or "")[:16] if st["last"] else "-", len(t.messages),
              _flags(st)] for t, st in rows],
            ["id", "channel", "title", "with", "last", "n", "state"])
    return 0


def _messages(ctx, a, kinds):
    out = []
    for t in _all_threads(ctx, a):
        for m in t.messages:
            if m.fm.get("kind") in kinds:
                out.append((m, t))
    out.sort(key=lambda mt: M.sort_key(mt[0]), reverse=True)
    return out


def _other(ctx, m):
    if m.fm.get("status") == "received":
        return _who(ctx, m.fm.get("from"), short=True)
    return ", ".join(_who(ctx, x, short=True) for x in m.fm.get("to") or [])


def cmd_calls(ctx, a):
    ctx.require_root()
    rows = [(m, t) for m, t in _messages(ctx, a, ("call",))
            if not a.missed or (m.fm.get("status") == "received" and
                                m.fm.get("outcome") in ("missed", "voicemail"))]
    if a.json:
        R.emit_json([OrderedDict([("id", m.id), ("thread", t.id), ("at", m.fm.get("at")),
                                  ("direction",
                                   "in" if m.fm.get("status") == "received" else "out"),
                                  ("with", m.fm.get("from") if m.fm.get("status") == "received"
                                   else m.fm.get("to")),
                                  ("outcome", m.fm.get("outcome")),
                                  ("duration", m.fm.get("duration"))]) for m, t in rows])
        return 0
    if not rows:
        print("(no %scalls)" % ("missed " if a.missed else ""))
        return 0
    R.table([[m.id, (m.fm.get("at") or "")[:16],
              "in" if m.fm.get("status") == "received" else "out", _other(ctx, m),
              m.fm.get("outcome", ""), m.fm.get("duration", ""), t.id] for m, t in rows],
            ["id", "at", "dir", "with", "outcome", "length", "thread"])
    return 0


def cmd_voicemails(ctx, a):
    ctx.require_root()
    rows = [(m, t) for m, t in _messages(ctx, a, ("voicemail",))
            if not a.unheard or (m.fm.get("status") == "received" and "seen" not in m.fm)]
    if a.json:
        R.emit_json([OrderedDict([("id", m.id), ("thread", t.id), ("at", m.fm.get("at")),
                                  ("from", m.fm.get("from")), ("duration", m.fm.get("duration")),
                                  ("heard", "seen" in m.fm),
                                  ("audio", [x.get("path") for x in m.fm.get("attachments") or []
                                             if isinstance(x, dict)]),
                                  ("transcript", m.body.strip() or None)]) for m, t in rows])
        return 0
    if not rows:
        print("(no %svoicemails)" % ("unheard " if a.unheard else ""))
        return 0
    R.table([[m.id, (m.fm.get("at") or "")[:16], _who(ctx, m.fm.get("from"), short=True),
              m.fm.get("duration", ""), "yes" if "seen" in m.fm else "no",
              _first_line(m.body, 50)] for m, t in rows],
            ["id", "at", "from", "length", "heard", "transcript"])
    return 0


def cmd_timeline(ctx, a):
    ctx.require_root()
    keys = _with_keys(ctx, a.with_)
    rows = []
    for t in _all_threads(ctx, argparse.Namespace(all=True, archived=False)):
        for m in t.messages:
            if m.fm.get("status") == "discarded":
                continue
            if _involves(m, keys) or (t.participant_keys() & keys and
                                      m.fm.get("status") in ("sent", "received", "draft")):
                rows.append((m, t))
    rows.sort(key=lambda mt: M.sort_key(mt[0]))
    if a.json:
        R.emit_json([OrderedDict([("id", m.id), ("thread", t.id), ("channel", t.channel),
                                  ("kind", m.fm.get("kind")), ("status", m.fm.get("status")),
                                  ("at", m.fm.get("at")), ("from", m.fm.get("from")),
                                  ("to", m.fm.get("to")), ("summary", _summary(m, t))])
                     for m, t in rows])
        return 0
    if not rows:
        print("(nothing with %s)" % a.with_)
        return 0
    R.table([[(m.fm.get("at") or "(draft)")[:16], m.id, m.fm.get("kind"),
              {"received": "in", "sent": "out", "draft": "draft"}.get(m.fm.get("status"), "?"),
              t.channel, _summary(m, t)] for m, t in rows],
            ["at", "id", "kind", "dir", "channel", "summary"])
    return 0


def _summary(m, t):
    kind = m.fm.get("kind")
    if kind == "call":
        s = "call, %s" % m.fm.get("outcome", "?")
        if m.fm.get("duration"):
            s += ", " + m.fm["duration"]
        return s
    if kind in ("email", "letter"):
        return "%s — %s" % (t.title, _first_line(m.body, 40)) if m.body.strip() else t.title
    return _first_line(m.body) or "(%s)" % kind


def _render_message(ctx, m, out):
    st = m.fm.get("status")
    head = "── %s  %s · %s" % (m.id, m.fm.get("kind"), st)
    if m.fm.get("at"):
        head += " · %s" % m.fm["at"]
    if m.fm.get("seen"):
        head += " · seen %s" % m.fm["seen"]
    out.append(head)
    if m.fm.get("from"):
        out.append("   from  %s" % _who(ctx, m.fm["from"]))
    for key in ("to", "cc", "bcc"):
        if m.fm.get(key):
            out.append("   %-5s %s" % (key, ", ".join(_who(ctx, x) for x in m.fm[key])))
    for key in ("service", "outcome", "duration", "transcript", "in_reply_to"):
        if m.fm.get(key):
            out.append("   %-5s %s" % ({"in_reply_to": "re", "transcript": "text by"}.get(key, key)
                                       [:7], m.fm[key]))
    for att in m.fm.get("attachments") or []:
        if isinstance(att, dict):
            out.append("   file  %s  (%s, %s)" % (att.get("name"), att.get("path"),
                                                  att.get("role")))
    body = (m.body or "").strip("\n")
    if body.strip():
        out.append("")
        out.extend("   " + ln if ln else "" for ln in body.split("\n"))
    out.append("")


def cmd_show(ctx, a):
    ctx.require_root()
    rec = ctx.find(a.id)
    threads = ctx.threads()
    t = threads.get(rec.id) if rec.prefix == "THR" else ctx.thread_of(rec, threads)
    if a.json:
        if rec.prefix == "THR" and t is not None:
            d = M.summary(rec)
            d["state"] = OrderedDict((k, (v.id if hasattr(v, "id") else
                                          [x.id for x in v] if isinstance(v, list) else v))
                                     for k, v in M.thread_state(t, ctx.stale_after).items())
            d["messages"] = [OrderedDict(list(M.summary(m, t).items()) + [("body", m.body)])
                             for m in t.ordered()]
        else:
            d = M.summary(rec, t)
            d["body"] = rec.body
        R.emit_json(d)
        return 0
    out = []
    if rec.prefix == "THR" and t is not None:
        st = M.thread_state(t, ctx.stale_after)
        out.append("%s  %s   (%s · %s)" % (t.id, t.title, t.channel, ctx.rel_of(t.rec).rsplit(
            "/", 1)[0]))
        if t.participants():
            out.append("with: %s" % ", ".join(_participant(ctx, p) for p in t.participants()))
        for key in ("identity", "labels", "account", "about"):
            v = t.rec.fm.get(key)
            if v:
                out.append("%s: %s" % (key, ", ".join(v) if isinstance(v, list) else v))
        if _flags(st):
            out.append("state: %s" % _flags(st))
        out.append("")
        for m in t.ordered():
            _render_message(ctx, m, out)
    else:
        out.append("%s  (in %s: %s)" % (ctx.rel_of(rec), t.id if t else "?", t.title if t else "?"))
        out.append("")
        _render_message(ctx, rec, out)
    sys.stdout.write("\n".join(out).rstrip("\n") + "\n")
    return 0


def cmd_where(ctx, a):
    ctx.require_root()
    rec = ctx.find(a.id)
    _out(a, ctx.rel_of(rec), {"id": rec.id, "path": ctx.rel_of(rec)})
    return 0


def cmd_search(ctx, a):
    ctx.require_root()
    q = " ".join(a.text).lower()
    if not q.strip():
        raise R.Refusal("search for what?")
    hits = []
    for t in _all_threads(ctx, a):
        if a.channel and t.channel != a.channel:
            continue
        if q in t.title.lower() or any(q in (p.get("name") or "").lower() or
                                       q in (p.get("who") or "").lower()
                                       for p in t.participants()):
            hits.append((t.rec, t, t.title))
        for m in t.ordered():
            snippet = None
            if any(q in x.lower() for x in M.addresses_of(m)):
                snippet = ", ".join(M.addresses_of(m))
            for line in m.body.split("\n"):
                if q in line.lower():
                    snippet = line.strip()
                    break
            if snippet is not None:
                hits.append((m, t, snippet))
    if a.json:
        R.emit_json([OrderedDict([("id", r.id), ("thread", t.id), ("path", ctx.rel_of(r)),
                                  ("match", s)]) for r, t, s in hits])
        return 0
    if not hits:
        print("(nothing matches %r)" % q)
        return 0
    R.table([[r.id, t.id, t.channel, (s or "")[:70]] for r, t, s in hits],
            ["id", "thread", "channel", "match"])
    return 0


def cmd_export_eml(ctx, a):
    """Write an email draft as an RFC 5322 message on stdout. This module never sends it."""
    ctx.require_root()
    rec = ctx.find(a.id, ("MSG",))
    if rec.fm.get("kind") != "email":
        raise R.Refusal("%s is %s; only an email draft exports as .eml" % (
            rec.id, _an(rec.fm.get("kind") or "?")))
    if rec.fm.get("status") != "draft":
        raise R.Refusal("%s is %s; export-eml hands a DRAFT to your mail program" % (
            rec.id, rec.fm.get("status")))
    threads = ctx.threads()
    t = ctx.thread_of(rec, threads)
    if t is None:
        raise R.Refusal("%s has no thread record beside it; run %s" % (rec.id, M.CHECK))
    by_id = {m.id: m for m in t.messages}
    data = mail.eml_bytes(ctx, rec, t, by_id)
    out = getattr(sys.stdout, "buffer", None)
    if out is None:
        sys.stdout.write(data.decode("utf-8", "replace"))
    else:
        sys.stdout.flush()
        out.write(data)
        out.flush()
    return 0


def cmd_history(ctx, a):
    ctx.require_root()
    rows, problems, exists = R.load_history(ctx.history_path)
    if a.id:
        rows = [r for r in rows if r.id == a.id]
    if a.json:
        R.emit_json([OrderedDict([("at", r.at), ("id", r.id), ("verb", r.verb), ("from", r.frm),
                                  ("to", r.to), ("actor", r.actor), ("note", r.note)])
                     for r in rows])
        return 0
    R.table([[r.at, r.id, r.verb, r.frm, r.to, r.actor, r.note] for r in rows],
            ["at", "id", "verb", "from", "to", "actor", "note"])
    return 0


def cmd_check(ctx, a):
    report = V.run(ctx, fix=a.fix)
    report.render(as_json=a.json, quiet=a.quiet)
    return report.exit_code()


# ---------------------------------------------------------------------------
# the parser
# ---------------------------------------------------------------------------

def make_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", help="project root (default: the project that owns this tool)")
    common.add_argument("--json", action="store_true", help="machine-readable output")
    writer = argparse.ArgumentParser(add_help=False)
    writer.add_argument("--by", help="who is doing this (else $RASA_ACTOR, else default_actor)")
    writer.add_argument("--dry-run", action="store_true",
                        help="show what would happen; write nothing")

    ap = argparse.ArgumentParser(prog=M.CLI, description="rasa.module.messages %s — every kind "
                                 "of message as records: email, texts, chat, calls, voicemail, "
                                 "letters, faxes. Records and drafts; never sends. The law: "
                                 ".claude/messages-rules.md" % M.VERSION)
    ap.add_argument("--version", action="version", version="%s %s (%s, core %s)" % (
        M.ELEMENT, M.VERSION, R.FAMILY, R.CORE_VERSION))
    sub = ap.add_subparsers(dest="verb", metavar="<verb>")

    def body_args(p):
        p.add_argument("--body", help="the text")
        p.add_argument("--body-file", help="read the text from a file")
        p.add_argument("--stdin", action="store_true", help="read the text from stdin")

    def thread_args(p):
        g = p.add_mutually_exclusive_group()
        g.add_argument("--thread", metavar="THR", help="add to this thread")
        g.add_argument("--subject", help="start a new thread with this title (email: the subject)")
        p.add_argument("--label", action="append", help="a label for a new thread (repeatable)")
        p.add_argument("--about", action="append", metavar="REF",
                       help="a family record a new thread is about (repeatable)")
        p.add_argument("--account", metavar="acct-x", help="the account a new thread belongs to")

    p = sub.add_parser("draft", parents=[common, writer],
                       help="write a draft (never sent by this tool)")
    p.add_argument("--kind", required=True,
                   help="email, sms, mms, imessage, rcs, chat, letter, fax")
    p.add_argument("--to", action="append", metavar="WHO",
                   help="an address or CARD id (repeatable)")
    p.add_argument("--cc", action="append", metavar="WHO")
    p.add_argument("--bcc", action="append", metavar="WHO")
    p.add_argument("--service", help="chat: slack, whatsapp, signal, ...")
    p.add_argument("--as", dest="as_ident", metavar="IDENTITY", help="which declared identity")
    p.add_argument("--attach", action="append", metavar="FILE",
                   help="a file to attach (repeatable)")
    thread_args(p)
    body_args(p)
    p.set_defaults(fn=cmd_draft)

    p = sub.add_parser("reply", parents=[common, writer], help="draft a reply to a message")
    p.add_argument("id", metavar="MSG")
    p.add_argument("--all", action="store_true",
                   help="email/letter: everyone on it, not just the sender")
    p.add_argument("--kind", help="another kind in the same channel (sms -> mms)")
    p.add_argument("--as", dest="as_ident", metavar="IDENTITY")
    p.add_argument("--attach", action="append", metavar="FILE")
    body_args(p)
    p.set_defaults(fn=cmd_reply)

    p = sub.add_parser("mark-sent", parents=[common, writer],
                       help="record that you sent a draft (it is frozen from then on)")
    p.add_argument("id", metavar="MSG")
    p.add_argument("--at", metavar="WHEN", help="when it went (default: now)")
    p.add_argument("--note")
    p.set_defaults(fn=cmd_mark_sent)

    p = sub.add_parser("discard", parents=[common, writer], help="discard a draft (it is kept)")
    p.add_argument("id", metavar="MSG")
    p.add_argument("--note")
    p.set_defaults(fn=cmd_discard)

    p = sub.add_parser("log", parents=[common, writer],
                       help="record a message that happened elsewhere "
                            "(a call, a voicemail, a letter)")
    p.add_argument("--kind", required=True, help=", ".join(M.KINDS))
    p.add_argument("--at", required=True, metavar="WHEN",
                   help="when: RFC 3339 with an offset, 'now', or a wall-clock time "
                        "with a timezone")
    p.add_argument("--from", dest="from_", metavar="WHO", help="who it came from")
    p.add_argument("--sent", action="store_true", help="it was from you")
    p.add_argument("--to", action="append", metavar="WHO")
    p.add_argument("--cc", action="append", metavar="WHO")
    p.add_argument("--bcc", action="append", metavar="WHO")
    p.add_argument("--name", help="the other person's name as they gave it")
    p.add_argument("--service")
    p.add_argument("--as", dest="as_ident", metavar="IDENTITY")
    p.add_argument("--outcome", help="calls: " + ", ".join(M.OUTCOMES))
    p.add_argument("--duration", help="calls and voicemails: PT4M12S or 4:12")
    p.add_argument("--transcript", choices=M.TRANSCRIPTS,
                   help="voicemails: who wrote the transcript you give as the body")
    p.add_argument("--audio", metavar="FILE", help="the voicemail's audio (or a call recording)")
    p.add_argument("--attach", action="append", metavar="FILE")
    p.add_argument("--reply-to", metavar="MSG", help="the earlier message this answers")
    p.add_argument("--external-id", help="the id it carried elsewhere (dedupes imports)")
    thread_args(p)
    body_args(p)
    p.set_defaults(fn=cmd_log)

    p = sub.add_parser("seen", parents=[common, writer], help="mark received messages read / heard")
    p.add_argument("id", metavar="MSG|THR")
    p.set_defaults(fn=cmd_seen)

    p = sub.add_parser("import", parents=[common, writer],
                       help="import .eml files, an mbox, or an SMS Backup & Restore XML file")
    p.add_argument("files", nargs="*", metavar="FILE.eml")
    p.add_argument("--mbox", metavar="FILE")
    p.add_argument("--sms-xml", metavar="FILE",
                   help="texts or the call log, as written by SMS Backup & Restore")
    p.add_argument("--as", dest="as_ident", metavar="IDENTITY",
                   help="the identity your sent texts and calls were from")
    p.set_defaults(fn=cmd_import)

    p = sub.add_parser("label", parents=[common, writer], help="add/remove thread labels: +a -b")
    p.add_argument("id", metavar="THR")
    p.add_argument("changes", nargs=argparse.REMAINDER)
    p.set_defaults(fn=cmd_label)

    p = sub.add_parser("about", parents=[common, writer], help="add/remove what a thread is about")
    p.add_argument("id", metavar="THR")
    p.add_argument("changes", nargs=argparse.REMAINDER)
    p.set_defaults(fn=cmd_about)

    p = sub.add_parser("pin", parents=[common, writer], help="pin a thread")
    p.add_argument("id", metavar="THR")
    p.set_defaults(fn=lambda c, a: cmd_pin(c, a, True))
    p = sub.add_parser("unpin", parents=[common, writer], help="unpin a thread")
    p.add_argument("id", metavar="THR")
    p.set_defaults(fn=lambda c, a: cmd_pin(c, a, False))

    p = sub.add_parser("rename", parents=[common, writer], help="retitle a thread (H1 + directory)")
    p.add_argument("id", metavar="THR")
    p.add_argument("title")
    p.set_defaults(fn=cmd_rename)

    for name, restore in (("archive", False), ("restore", True)):
        p = sub.add_parser(name, parents=[common, writer], help="%s a whole thread" % name)
        p.add_argument("id", metavar="THR")
        p.add_argument("--note", help="why (goes in the history lines)")
        p.set_defaults(fn=(lambda r: (lambda c, a: cmd_archive(c, a, r)))(restore))

    p = sub.add_parser("touch", parents=[common, writer],
                       help="record a hand edit (stamps updated)")
    p.add_argument("id")
    p.add_argument("--note")
    p.set_defaults(fn=cmd_touch)

    def scope(p):
        g = p.add_mutually_exclusive_group()
        g.add_argument("--archived", action="store_true", help="only archived threads")
        g.add_argument("--all", action="store_true", help="include archived threads")

    p = sub.add_parser("threads", parents=[common], help="list conversations and what they need")
    p.add_argument("--needs-reply", action="store_true")
    p.add_argument("--unseen", action="store_true")
    p.add_argument("--waiting", action="store_true",
                   help="sent, unanswered, older than stale_after")
    p.add_argument("--drafts", action="store_true")
    p.add_argument("--label")
    p.add_argument("--with", dest="with_", metavar="REF|ADDRESS")
    p.add_argument("--channel", choices=M.CHANNELS)
    p.add_argument("--kind", choices=list(M.KINDS))
    scope(p)
    p.set_defaults(fn=cmd_threads)

    p = sub.add_parser("calls", parents=[common], help="the call log")
    p.add_argument("--missed", action="store_true", help="incoming calls nobody picked up")
    scope(p)
    p.set_defaults(fn=cmd_calls)

    p = sub.add_parser("voicemails", parents=[common], help="voicemails")
    p.add_argument("--unheard", action="store_true")
    scope(p)
    p.set_defaults(fn=cmd_voicemails)

    p = sub.add_parser("timeline", parents=[common],
                       help="everything with one person: every kind, every channel, in order")
    p.add_argument("--with", dest="with_", required=True, metavar="REF|ADDRESS")
    p.set_defaults(fn=cmd_timeline)

    for name, fn, h in (("show", cmd_show, "render a thread or a message"),
                        ("where", cmd_where, "print its path")):
        p = sub.add_parser(name, parents=[common], help=h)
        p.add_argument("id", metavar="THR|MSG")
        p.set_defaults(fn=fn)

    p = sub.add_parser("search", parents=[common], help="search titles, people and message text")
    p.add_argument("text", nargs="+")
    p.add_argument("--channel", choices=M.CHANNELS)
    scope(p)
    p.set_defaults(fn=cmd_search)

    p = sub.add_parser("export-eml", parents=[common],
                       help="write an email draft as .eml on stdout, for your own mail program")
    p.add_argument("id", metavar="MSG")
    p.set_defaults(fn=cmd_export_eml)

    p = sub.add_parser("history", parents=[common], help="the history log")
    p.add_argument("id", nargs="?")
    p.set_defaults(fn=cmd_history)

    p = sub.add_parser("check", parents=[common], help="run the validator (same as check-messages)")
    p.add_argument("--fix", action="store_true")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(fn=cmd_check)
    return ap


def main(argv=None, bin_dir=None):
    def _main(argv):
        ap = make_parser()
        a = ap.parse_args(argv)
        if not getattr(a, "fn", None):
            ap.print_help()
            return R.EXIT_REFUSED
        ctx = M.Ctx(getattr(a, "root", None), bin_dir)
        return a.fn(ctx, a)
    return R.run_main(_main, argv)
