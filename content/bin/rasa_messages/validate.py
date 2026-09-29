# -*- coding: utf-8 -*-
"""validate.py — check-messages: every invariant in .claude/messages-rules.md, mechanically.

Family invariants F-01..F-27 come from records.py; the messages invariants
M-01..M-16 are here. The kind law is the allOf blocks of
message.schema.json, each reported under the invariant it names
(x-invariant). Exit 0 = no errors (warnings allowed), 1 = errors,
2 = cannot run.
"""

import argparse
import os
from collections import OrderedDict

from . import model as M
from . import records as R

#: every messages invariant, and where it is enforced. The schema-borne ones
#: are the allOf blocks of message.schema.json, each tagged x-invariant.
INVARIANTS = OrderedDict([
    ("M-01", "records live only in thread directories; one thread record each"),
    ("M-02", "the channel is one of the five, and declared when channels are"),
    ("M-03", "a message's kind belongs to its thread's channel"),
    ("M-04", "the per-kind field law (schema blocks + chat handles on the service)"),
    ("M-05", "at exactly when sent/received; received has from; draft/sent has to (schema)"),
    ("M-06", "only kinds you compose are ever drafts (schema)"),
    ("M-07", "status moves only draft->sent / draft->discarded, as the log says"),
    ("M-08", "a message that happened is never reopened"),
    ("M-09", "in_reply_to: same thread, happened, earlier, not itself"),
    ("M-10", "external_id names one message"),
    ("M-11", "a draft / sent composed message is from a declared identity"),
    ("M-12", "attachments exist inside the thread's attachments/"),
    ("M-13", "a voicemail carries its audio (warning)"),
    ("M-14", "voicemail transcript provenance exactly when the body is not empty"),
    ("M-15", "seen only on received messages, never before at"),
    ("M-16", "participants suit the channel; the thread identity is declared"),
])
SCHEMA_BORNE = ("M-04", "M-05", "M-06", "M-11", "M-15")


def run(ctx, fix=False):
    report = R.Report(M.CHECK)
    if not os.path.isdir(ctx.root):
        report.error("F-15", "there is no %s/ directory here (%s)" % (M.ROOT, ctx.project),
                     fix="install rasa.module.messages with its bin/init, or pass --root")
        return report
    today = R.today()

    # ---- config ---------------------------------------------------------
    cfg_rel = ctx.rel_root(M.CONFIG_NAME)
    for inv, line, msg in ctx.config_problems:
        report.error(inv, msg, where=cfg_rel, line=line)
    cfg = ctx.config if not ctx.config_problems else {}
    actors = R.declared(cfg, "actors")
    index = R.family_index(ctx.project)
    mounted = R.mounted_modules(ctx.project)
    check_config(ctx, cfg, report, index, mounted)

    # ---- the files ------------------------------------------------------
    records, strays = ctx.scan()
    report.records = len(records)
    if strays:
        report.info("F-27", "not records, left alone: %s" % ", ".join(
            ctx.rel_root(s) for s in strays), where=M.ROOT)

    usable = []
    for rec in records:
        where = ctx.rel_of(rec)
        schema = ctx.schema_for(rec)
        if schema is None:
            report.error("F-03", "%s is not a messages record type (THR, MSG)" % rec.stem,
                         where=where)
            continue
        if not R.check_record_common(rec, M.base_schema(schema), report, where, actors=actors,
                                     today_=today):
            continue
        if rec.prefix not in M.PREFIX_TYPE:
            continue
        for block in schema.get("allOf") or []:
            inv = block.get("x-invariant")
            inv = inv if inv in SCHEMA_BORNE else "F-26"
            for _inv, path, msg in R.schema_errors(block, rec.fm):
                key = path.split(".")[0].split("[")[0] if path else ""
                report.error(inv, "%s %s" % (path or "(record)", msg), where=where,
                             line=rec.line_of(key) if key else None,
                             fix="%s show %s  (the kind law is in .claude/messages-rules.md §2)"
                                 % (M.CLI, rec.id))
        usable.append(rec)
        for key, ref in R.frontmatter_refs(rec, schema):
            R.check_ref(ref, index, mounted, report, where, rec.line_of(key), key=key)
        for ref, line in R.body_refs(rec):
            R.check_ref(ref, index, mounted, report, where, line, body=True)

    R.check_unique_ids(usable, report, ctx.rel_of)
    broken = set(r.dir for r in records if r not in usable and r.stem.startswith("THR-"))
    threads = check_layout(ctx, usable, report, cfg, broken)
    for t in threads.values():
        check_thread(ctx, t, report, cfg)
        for m in t.messages:
            check_message(ctx, m, t, report, cfg)
        check_replies(ctx, t, report)
        check_attachment_dir(ctx, t, report)
    check_external_ids(ctx, [r for r in usable if r.prefix == "MSG"], report)

    # ---- history ----------------------------------------------------------
    rows, problems, exists = R.load_history(ctx.history_path)
    hrel = ctx.rel_root("history.tsv")
    for inv, line, msg in problems:
        report.error(inv, msg, where=hrel, line=line)
    fixes = []
    R.check_history_common(usable, rows, exists, report,
                           module_ids_ok=lambda rid: R.ref_prefix(rid) in M.PREFIX_TYPE,
                           place_of=M.place_of, rel_of=ctx.rel_of, history_rel=hrel,
                           cli=M.CLI, fixes=fixes,
                           present_ids=R.ids_on_disk(records))
    for row in rows:
        if R.ref_prefix(row.id) not in M.PREFIX_TYPE:
            report.error("F-13", "%s does not belong to messages" % row.id, where=hrel,
                         line=row.lineno)
        if actors is not None and row.actor not in actors and row.actor != "unknown":
            report.error("F-22", "actor '%s' is not declared" % row.actor, where=hrel,
                         line=row.lineno)
    if exists:
        check_lifecycle(ctx, [r for r in usable if r.prefix == "MSG"], rows, report, hrel)

    # ---- digests ----------------------------------------------------------
    table, had_table = R.load_digests(ctx.root)
    schemas = {r.id: ctx.schema_for(r) for r in usable}
    for r in usable:
        prev = table.get(r.id)
        if r.prefix == "MSG" and prev and prev.get("frozen") and not M.is_frozen(r):
            report.error("M-08", "%s was %s and is now '%s'; a message that happened is never "
                                 "reopened" % (r.id, "sent or received", r.fm.get("status")),
                         where=ctx.rel_of(r), line=r.line_of("status"),
                         fix="restore its status; to write again, draft a new message "
                             "(%s reply %s)" % (M.CLI, r.id))
    fresh = R.check_digests(usable, table, report, ctx.rel_of, M.CLI,
                            is_frozen=M.is_frozen,
                            mutable_keys_of=lambda r: M.mutable_keys(r, schemas.get(r.id)),
                            today_=today)

    # ---- --fix: the mechanical subset, then re-check ------------------------
    if fix and fixes:
        with ctx.lock():
            entries = []
            seen = set()
            for _kind, rec in fixes:
                if rec.id in seen:
                    continue
                seen.add(rec.id)
                place = M.place_of(rec) or R.place_token(rec.dir)
                entries.append((rec.id, "reconcile", "-", place, "unknown", "reconciled"))
            if not exists:
                entries.sort(key=lambda e: (e[0][:3] != "THR", e[0]))
            ctx.log(entries)
        report.notes.append("--fix appended %d reconcile line(s) to %s" % (len(entries), hrel))
        return run(ctx, fix=False)

    if report.ok:
        if fresh != table or not had_table:
            R.save_digests(ctx.root, fresh)
            if not had_table and usable:
                report.notes.append("seeded the content cache (%s/.state/digests.tsv)" % M.ROOT)
    return report


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

def check_config(ctx, cfg, report, index, mounted):
    where = ctx.rel_root(M.CONFIG_NAME)
    idents = cfg.get("identities") if isinstance(cfg.get("identities"), list) else []
    seen_ids, seen_addr = set(), {}
    channels = R.declared(cfg, "channels")
    for i in idents:
        if not isinstance(i, dict):
            continue
        iid = i.get("id")
        if iid in seen_ids:
            report.error("F-20", "identity id '%s' is declared twice" % iid, where=where)
        seen_ids.add(iid)
        key = (i.get("channel"), M.address_key(i.get("address")))
        if key in seen_addr:
            report.error("F-20", "identities '%s' and '%s' are the same %s address" % (
                seen_addr[key], iid, i.get("channel")), where=where)
        seen_addr[key] = iid
        if channels is not None and i.get("channel") not in channels:
            R.check_declared(i.get("channel"), channels, report, "identity channel", where, None)
        if isinstance(i.get("card"), str):
            R.check_ref(i["card"], index, mounted, report, where, None, key="identities.card")


# ---------------------------------------------------------------------------
# layout — M-01, M-02
# ---------------------------------------------------------------------------

def check_layout(ctx, usable, report, cfg, broken=()):
    """Every record in a thread directory of a declared channel; one thread record per directory.

    `broken` holds directories whose thread record exists but did not parse:
    that is already reported (F-05 and friends), so its messages are not
    also called orphans.
    """
    channels = R.declared(cfg, "channels")
    by_dir = OrderedDict()
    placed = []
    for rec in usable:
        where = ctx.rel_of(rec)
        place = M.Place(rec.dir)
        if place.problem:
            report.error("M-01", place.problem, where=where,
                         fix="%s check --fix cannot move it; put it in its thread directory, "
                             "then run %s check --fix" % (M.CLI, M.CLI))
            continue
        if place.channel not in M.CHANNELS:
            report.error("M-02", "'%s' is not a channel (%s)" % (place.channel,
                                                                ", ".join(M.CHANNELS)),
                         where=where)
            continue
        if channels is not None and place.channel not in channels:
            report.error("M-02", "the %s channel is not declared in %s (declared: %s)" % (
                place.channel, ctx.rel_root(M.CONFIG_NAME), ", ".join(channels)), where=where)
        by_dir.setdefault(rec.dir, []).append(rec)
        placed.append(rec)
    for d, recs in by_dir.items():
        place = M.Place(d)
        heads = [r for r in recs if r.prefix == "THR"]
        if not heads and d in broken:
            continue
        if not heads:
            for r in recs:
                report.error("M-01", "%s has no thread record beside it (%s/%s/%s.md)" % (
                    r.id, M.ROOT, d, place.thread_id), where=ctx.rel_of(r))
            continue
        if len(heads) > 1:
            report.error("M-01", "%s/%s holds %d thread records (%s); a thread directory holds "
                                 "exactly one" % (M.ROOT, d, len(heads),
                                                  ", ".join(h.id for h in heads)),
                         where=ctx.rel_of(heads[0]))
        for h in heads:
            if h.id != place.thread_id:
                report.error("M-01", "thread %s sits in %s's directory (%s)" % (
                    h.id, place.thread_id, d), where=ctx.rel_of(h))
            elif h.name != h.id + ".md":
                report.error("M-01", "the thread record is named %s.md inside its directory "
                                     "(the slug belongs to the directory)" % h.id,
                             where=ctx.rel_of(h))
    return ctx.threads(placed)


# ---------------------------------------------------------------------------
# threads — M-16, F-21
# ---------------------------------------------------------------------------

def check_thread(ctx, t, report, cfg):
    rec, where = t.rec, ctx.rel_of(t.rec)
    channel = t.channel
    fmt = M.CHANNEL_ADDRESS.get(channel)
    mine = M.address_key
    ident_keys = set(mine(i.get("address")) for i in _identities(cfg, channel))
    seen = set()
    for p in t.participants():
        who = p.get("who")
        if not isinstance(who, str):
            continue
        if fmt and not M.address_ok(fmt, who):
            report.error("M-16", "participant '%s' is not %s — the address form of the %s "
                                 "channel" % (who, R._FORMAT_LABEL.get(fmt, fmt), channel),
                         where=where, line=rec.line_of("participants"))
        k = mine(who)
        if k in seen:
            report.error("M-16", "participant '%s' is listed twice" % who, where=where,
                         line=rec.line_of("participants"))
        seen.add(k)
        if k in ident_keys:
            report.warn("M-16", "participant '%s' is one of your own identities; participants "
                                "are the other people" % who, where=where,
                        line=rec.line_of("participants"))
    ident = rec.fm.get("identity")
    if isinstance(ident, str):
        declared = [i for i in _identities(cfg) if i.get("id") == ident]
        if not declared:
            report.error("M-16", "identity '%s' is not declared in %s" % (
                ident, ctx.rel_root(M.CONFIG_NAME)), where=where, line=rec.line_of("identity"))
        elif declared[0].get("channel") != channel:
            report.error("M-16", "identity '%s' is a %s identity; this thread is in %s" % (
                ident, declared[0].get("channel"), channel), where=where,
                line=rec.line_of("identity"))
    labels = rec.fm.get("labels")
    if isinstance(labels, list):
        allowed = R.declared(cfg, "labels")
        for lab in labels:
            R.check_declared(lab, allowed, report, "label", where, rec.line_of("labels"),
                             fix="%s label %s -%s" % (M.CLI, rec.id, lab))


def _identities(cfg, channel=None):
    v = cfg.get("identities")
    out = [i for i in v if isinstance(i, dict)] if isinstance(v, list) else []
    return [i for i in out if channel is None or i.get("channel") == channel]


# ---------------------------------------------------------------------------
# messages — M-03, M-04 (beyond the schema), M-11, M-12, M-13, M-14, M-15
# ---------------------------------------------------------------------------

def check_message(ctx, m, t, report, cfg):
    where = ctx.rel_of(m)
    kind = M.KINDS.get(m.fm.get("kind"))
    status = m.fm.get("status")
    if kind is None:
        return  # the schema already said so (F-26)
    # M-03 the kind belongs to the thread's channel
    if kind.channel != t.channel:
        report.error("M-03", "a %s belongs in a %s thread; %s is in %s" % (
            kind.name, kind.channel, t.id, t.channel), where=where, line=m.line_of("kind"))
    # M-04 chat: every handle is on the message's service
    service = m.fm.get("service")
    if kind.name == "chat" and isinstance(service, str):
        R.check_declared(service, R.declared(cfg, "services"), report, "service", where,
                         m.line_of("service"))
        for a in M.addresses_of(m):
            if R.FORMATS["chat-handle"](a) and not a.lower().startswith(service.lower() + ":"):
                report.error("M-04", "'%s' is not a %s handle (%s:<handle>)" % (a, service,
                                                                               service),
                             where=where, line=m.line_of("service"))
    # M-11 a draft or a sent message you composed is from a declared identity of its channel
    frm = m.fm.get("from")
    if kind.composable and status in ("draft", "sent") and isinstance(frm, str):
        mine = [i for i in _identities(cfg, kind.channel)]
        if not mine:
            report.error("M-11", "%s is %s as you, but no %s identity is declared" % (
                m.id, "drafted" if status == "draft" else "sent", kind.channel), where=where,
                line=m.line_of("from"), fix="declare it under identities in %s" %
                ctx.rel_root(M.CONFIG_NAME))
        elif M.address_key(frm) not in set(M.address_key(i.get("address")) for i in mine):
            report.error("M-11", "from '%s' is not a declared %s identity (%s)" % (
                frm, kind.channel, ", ".join(i.get("address", "?") for i in mine)),
                where=where, line=m.line_of("from"))
    # M-12 attachments exist and stay inside the thread
    atts = m.fm.get("attachments")
    has_audio = False
    if isinstance(atts, list):
        root = os.path.realpath(t.dir)
        for a in atts:
            if not isinstance(a, dict) or not isinstance(a.get("path"), str):
                continue
            rel = a["path"]
            full = os.path.realpath(os.path.join(t.dir, *rel.split("/")))
            if not rel.startswith(M.ATTACH_DIR + "/") or \
                    not full.startswith(os.path.join(root, M.ATTACH_DIR) + os.sep):
                report.error("M-12", "attachment '%s' is outside the thread's %s/ directory" % (
                    rel, M.ATTACH_DIR), where=where, line=m.line_of("attachments"))
            elif not os.path.isfile(full):
                report.error("M-12", "attachment '%s' is missing" % rel, where=where,
                             line=m.line_of("attachments"),
                             fix="put the file back at %s/%s/%s" % (M.ROOT, t.rec.dir, rel))
            if a.get("role") == "audio":
                has_audio = True
    # M-13 / M-14 the voicemail
    if kind.name == "voicemail":
        if not has_audio:
            report.warn("M-13", "voicemail %s has no audio attachment; the recording is the "
                                "message" % m.id, where=where)
        body = (m.body or "").strip()
        if body and "transcript" not in m.fm:
            report.error("M-14", "the body is a transcript; say who wrote it (transcript: "
                                 "machine | human)", where=where)
        if not body and "transcript" in m.fm:
            report.error("M-14", "transcript: %s, but the body is empty" % m.fm["transcript"],
                         where=where, line=m.line_of("transcript"))
    # M-15 seen is never before the message happened
    seen = M.at_instant(m.fm.get("seen"))
    at = M.at_instant(m.fm.get("at"))
    if seen and at and seen < at and R.parse_datetime(m.fm.get("at") or ""):
        report.error("M-15", "seen %s is before the message arrived (%s)" % (
            m.fm["seen"], m.fm["at"]), where=where, line=m.line_of("seen"))


def check_replies(ctx, t, report):
    """M-09: in_reply_to names an earlier message of the same thread that happened."""
    ids = {m.id: m for m in t.messages}
    for m in t.messages:
        parent_id = m.fm.get("in_reply_to")
        if not isinstance(parent_id, str):
            continue
        where = ctx.rel_of(m)
        if parent_id == m.id:
            report.error("M-09", "a message cannot answer itself", where=where,
                         line=m.line_of("in_reply_to"))
            continue
        parent = ids.get(parent_id)
        if parent is None:
            report.error("M-09", "in_reply_to %s is not in this thread (%s)" % (parent_id, t.id),
                         where=where, line=m.line_of("in_reply_to"))
            continue
        if parent.fm.get("status") not in M.LIVE:
            report.error("M-09", "in_reply_to %s, which never happened (%s)" % (
                parent_id, parent.fm.get("status")), where=where, line=m.line_of("in_reply_to"))
            continue
        mine, theirs = M.at_instant(m.fm.get("at")), M.at_instant(parent.fm.get("at"))
        if mine and theirs and theirs > mine:
            report.error("M-09", "in_reply_to %s, which happened later (%s after %s)" % (
                parent_id, parent.fm.get("at"), m.fm.get("at")), where=where,
                line=m.line_of("in_reply_to"))


def check_attachment_dir(ctx, t, report):
    """M-12 (warning): every file in attachments/ belongs to a message."""
    adir = os.path.join(t.dir, M.ATTACH_DIR)
    if not os.path.isdir(adir):
        return
    named = set()
    for m in t.messages:
        for a in m.fm.get("attachments") or []:
            if isinstance(a, dict) and isinstance(a.get("path"), str):
                named.add(a["path"])
    for fn in sorted(os.listdir(adir)):
        if fn.startswith("."):
            continue
        rel = M.ATTACH_DIR + "/" + fn
        if rel not in named:
            report.warn("M-12", "%s is not attached to any message" % rel,
                        where=ctx.rel_root(t.rec.dir + "/" + rel))


def check_external_ids(ctx, msgs, report):
    """M-10: an external id names one message."""
    seen = OrderedDict()
    for m in msgs:
        e = m.fm.get("external_id")
        if isinstance(e, str):
            seen.setdefault(e, []).append(m)
    for e, ms in seen.items():
        if len(ms) > 1:
            report.error("M-10", "external_id %s is on %d messages (%s); it is the dedupe key" % (
                e, len(ms), ", ".join(x.id for x in ms)), where=ctx.rel_of(ms[1]),
                line=ms[1].line_of("external_id"))


# ---------------------------------------------------------------------------
# lifecycle — M-07
# ---------------------------------------------------------------------------

def check_lifecycle(ctx, msgs, rows, report, hrel):
    """Status moves only draft -> sent / draft -> discarded, and the log agrees with the file."""
    by_id = R.history_by_id(rows)
    for m in msgs:
        hist = by_id.get(m.id) or []
        where = ctx.rel_of(m)
        for r in hist:
            if not (R.is_status_token(r.frm) or R.is_status_token(r.to)):
                continue
            move = (r.frm.split("=", 1)[-1], r.to.split("=", 1)[-1])
            if not (r.frm.startswith("status=") and r.to.startswith("status=")) or \
                    move not in M.TRANSITIONS:
                report.error("M-07", "%s: the log moves it %s -> %s; a message only goes "
                                     "draft -> sent or draft -> discarded" % (m.id, r.frm, r.to),
                             where=hrel, line=r.lineno)
        filed = M.filing_status(hist)
        if filed == "discarded":
            report.error("M-07", "%s was filed as discarded; only a draft is ever discarded"
                         % m.id, where=hrel, line=hist[0].lineno if hist else None)
        logged = M.logged_status(hist)
        status = m.fm.get("status")
        if logged and status in M.STATUSES and logged != status:
            report.error("M-07", "%s is '%s' but the log says '%s' (changed by hand?)" % (
                m.id, status, logged), where=where, line=m.line_of("status"),
                fix="restore status: %s, then use %s mark-sent / discard" % (logged, M.CLI))


def main(argv=None, bin_dir=None):
    def _main(argv):
        ap = argparse.ArgumentParser(prog=M.CHECK, description="Validate the messages ledger "
                                     "against .claude/messages-rules.md (records-family/1).")
        ap.add_argument("--root", help="project root (default: the project that owns this tool)")
        ap.add_argument("--fix", action="store_true",
                        help="repair the mechanical subset (history reconcile lines); never "
                             "invents a person, a date or a status")
        ap.add_argument("--json", action="store_true", help="machine-readable report")
        ap.add_argument("--quiet", action="store_true", help="errors only")
        a = ap.parse_args(argv)
        ctx = M.Ctx(a.root, bin_dir)
        report = run(ctx, fix=a.fix)
        report.render(as_json=a.json, quiet=a.quiet)
        return report.exit_code()
    return R.run_main(_main, argv)
