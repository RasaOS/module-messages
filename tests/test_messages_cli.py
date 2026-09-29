# -*- coding: utf-8 -*-
"""End-to-end checks for the messages tool and check-messages.

Every scenario runs the real programs in a scratch project, with the clock
and the actor pinned through $RASA_TODAY / $RASA_NOW / $RASA_ACTOR.
Run:  python3 -B -m unittest discover -s tests
"""

import base64
import email
import json
import mailbox
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from email import policy

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
ELEMENT = os.path.dirname(HERE)
BIN = os.path.join(ELEMENT, "content", "bin")
MESSAGES = os.path.join(BIN, "messages")
CHECK = os.path.join(BIN, "check-messages")
TODAY = "2026-09-28"
NOW = TODAY + "T12:00:00Z"

sys.path.insert(0, BIN)
from rasa_messages import model as M  # noqa: E402
from rasa_messages import records as R  # noqa: E402

IDENTITIES = (
    "identities:\n"
    "  - {id: home, channel: email, address: me@example.org}\n"
    "  - {id: cell, channel: text, address: +1 555 010 0100}\n"
    "  - {id: phone, channel: voice, address: +1 555 010 0100}\n"
    "  - {id: slack-me, channel: chat, address: \"slack:U0ME\"}\n"
    "  - {id: home-post, channel: post, address: \"Me, 1 Main St, Springfield\"}\n")

JANE = "jane@acme.com"
JANE_CELL = "+1 555 010 0200"
BOB_CELL = "+1 555 010 0300"
ME = "me@example.org"
ME_CELL = "+1 555 010 0100"


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.proj = os.path.join(self.tmp, "proj")
        os.makedirs(os.path.join(self.proj, ".claude"))
        for ch in M.CHANNELS:
            os.makedirs(os.path.join(self.proj, "messages", ch))
        with open(self.path("history.tsv"), "w") as fh:
            fh.write("at\tid\tverb\tfrom\tto\tactor\tnote\n")
        self.env = dict(os.environ)
        for k in list(self.env):
            if k.startswith("RASA_") or k == "TZ":
                del self.env[k]
        self.env.update({"RASA_PROJECT_ROOT": self.proj, "RASA_ACTOR": "chazz",
                         "RASA_TODAY": TODAY, "RASA_NOW": NOW, "PYTHONDONTWRITEBYTECODE": "1"})

    def tearDown(self):
        shutil.rmtree(self.tmp)

    # -- running --------------------------------------------------------------

    def run_(self, prog, *args, expect=0, env=None, raw=False):
        e = dict(self.env)
        e.update(env or {})
        p = subprocess.run([sys.executable, "-B", prog] + list(args), capture_output=True,
                           text=not raw, env=e)
        if expect is not None:
            self.assertEqual(p.returncode, expect, "%s %s\nstdout:%s\nstderr:%s" % (
                os.path.basename(prog), " ".join(args), p.stdout, p.stderr))
        return p

    def m(self, *args, **kw):
        return self.run_(MESSAGES, *args, **kw)

    def mj(self, *args, **kw):
        return json.loads(self.m(*(list(args) + ["--json"]), **kw).stdout)

    def check(self, *args, **kw):
        return self.run_(CHECK, *args, **kw)

    def report(self, env=None):
        return json.loads(self.run_(CHECK, "--json", expect=None, env=env).stdout)

    def invariants(self, env=None, severity=("ERROR", "WARN")):
        return sorted(set(f["invariant"] for f in self.report(env)["findings"]
                          if f["severity"] in severity))

    def errors(self, env=None):
        return self.invariants(env, ("ERROR",))

    # -- files ----------------------------------------------------------------

    def path(self, *parts):
        return os.path.join(self.proj, "messages", *parts)

    def find(self, rid):
        for d, _, files in os.walk(self.path()):
            if rid + ".md" in files:
                return os.path.join(d, rid + ".md")
        raise AssertionError("no file for %s" % rid)

    def read(self, rid):
        with open(self.find(rid)) as fh:
            return fh.read()

    def write(self, path, text):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)

    def edit(self, rid, old, new):
        p = self.find(rid)
        text = self.read(rid)
        self.assertIn(old, text)
        self.write(p, text.replace(old, new, 1))

    def config(self, text=""):
        self.write(self.path("messages.config.yml"), "schema: rasa.module.messages/1\n" + text)

    def history(self):
        with open(self.path("history.tsv")) as fh:
            return [l.split("\t") for l in fh.read().strip().split("\n")[1:]]

    def scratch(self, name, data):
        p = os.path.join(self.tmp, name)
        with open(p, "wb" if isinstance(data, bytes) else "w") as fh:
            fh.write(data)
        return p

    def later(self, day="2026-10-10"):
        return {"RASA_TODAY": day, "RASA_NOW": day + "T12:00:00Z"}

    def jane_email(self):
        """A received email from Jane (MSG-0001 in THR-0001)."""
        self.m("log", "--kind", "email", "--from", JANE, "--to", ME, "--subject",
               "Lease renewal", "--at", "2026-09-20T09:14:00-04:00", "--body",
               "Your lease is up. Renew?")


# ---------------------------------------------------------------------------
# walkthroughs
# ---------------------------------------------------------------------------

class WalkthroughTests(Base):

    def test_email_draft_reply_export_send_freeze(self):
        self.config(IDENTITIES)
        self.jane_email()
        t = self.mj("threads")
        self.assertEqual(len(t), 1)
        self.assertTrue(t[0]["needs_reply"])
        self.assertEqual(t[0]["unseen"], ["MSG-0001"])
        # reply: a draft to the sender, in the same thread, answering MSG-0001
        out = self.m("reply", "MSG-0001", "--body", "Yes, please renew for a year.").stdout
        self.assertIn("MSG-0002  draft reply to MSG-0001", out)
        draft = self.read("MSG-0002")
        self.assertIn("status: draft\nfrom: me@example.org\nto: [jane@acme.com]\n"
                      "in_reply_to: MSG-0001", draft)
        self.assertNotIn("\nat:", draft)
        self.assertEqual(self.mj("threads", "--drafts")[0]["drafts"], ["MSG-0002"])
        # export: an RFC 5322 message for your own mail program; nothing is sent
        raw = self.m("export-eml", "MSG-0002", raw=True).stdout
        self.assertIn(b"\r\n", raw)
        e = email.message_from_bytes(raw, policy=policy.default)
        self.assertEqual(str(e["Subject"]), "Re: Lease renewal")
        self.assertEqual(str(e["To"]), JANE)
        self.assertEqual(str(e["From"]), ME)
        self.assertEqual(str(e["X-Unsent"]), "1")
        self.assertIn("renew for a year", e.get_body().get_content())
        # you sent it from your own program: record it
        self.m("mark-sent", "MSG-0002", "--at", "2026-09-21T10:00:00-04:00")
        sent = self.read("MSG-0002")
        self.assertIn("status: sent\nat: 2026-09-21T10:00:00-04:00", sent)
        self.m("mark-sent", "MSG-0002", expect=1)
        self.m("discard", "MSG-0002", expect=1)
        self.m("export-eml", "MSG-0002", expect=1)
        self.m("seen", "THR-0001")
        self.assertIn("seen: 2026-09-28T12:00:00Z", self.read("MSG-0001"))
        self.m("seen", "MSG-0001", expect=1)          # nothing left to mark
        t = self.mj("threads")[0]
        self.assertFalse(t["needs_reply"])
        self.assertEqual(t["unseen"], [])
        self.assertEqual(self.errors(), [])
        hist = self.mj("history")
        self.assertEqual([(h["id"], h["verb"], h["from"], h["to"]) for h in hist], [
            ("THR-0001", "new", "-", "email/THR-0001"),
            ("MSG-0001", "new", "-", "email/THR-0001"),
            ("MSG-0002", "new", "-", "email/THR-0001"),
            ("MSG-0002", "mark-sent", "status=draft", "status=sent"),
            ("MSG-0001", "seen", "-", "-")])
        self.assertEqual([h["note"] for h in hist[1:3]], ["status=received", "status=draft"])
        # frozen: a hand edit to a sent message is F-19, even after a touch
        self.edit("MSG-0002", "renew for a year", "renew for two years")
        self.assertIn("F-19", self.errors())
        self.m("touch", "MSG-0002", env=self.later())
        self.assertIn("F-19", self.errors(env=self.later()))

    def test_every_kind(self):
        """Every kind: recorded as received, and — when you can compose it — drafted and sent."""
        self.config(IDENTITIES + "  - {id: fax, channel: post, address: +1 555 010 0999}\n")
        audio = self.scratch("vm.m4a", b"audio")
        scan = self.scratch("letter.pdf", b"%PDF")
        received = {
            "email": ["--from", JANE, "--subject", "Hello"],
            "sms": ["--from", JANE_CELL, "--body", "hi"],
            "mms": ["--from", JANE_CELL, "--attach", self.scratch("p.jpg", b"jpg")],
            "imessage": ["--from", JANE, "--body", "hi over email"],
            "rcs": ["--from", BOB_CELL, "--body", "rich"],
            "chat": ["--from", "slack:U0JANE", "--service", "slack", "--body", "ping"],
            "call": ["--from", JANE_CELL, "--outcome", "answered", "--duration", "4:12"],
            "voicemail": ["--from", JANE_CELL, "--audio", audio, "--transcript", "human",
                          "--body", "call me"],
            "letter": ["--from", "Acme Property Mgmt, 9 Elm St", "--subject", "Rent notice",
                       "--attach", scan],
            "fax": ["--from", "+1 555 010 0777", "--subject", "Signed form"],
        }
        drafts = {
            "email": ["--to", JANE, "--subject", "Hello back"],
            "sms": ["--to", JANE_CELL],
            "mms": ["--to", JANE_CELL, "--attach", self.scratch("q.png", b"png")],
            "imessage": ["--to", JANE],
            "rcs": ["--to", BOB_CELL],
            "chat": ["--to", "slack:U0JANE", "--service", "slack"],
            "letter": ["--to", "Acme Property Mgmt, 9 Elm St", "--as", "home-post"],
            "fax": ["--to", "+1 555 010 0777", "--subject", "Reply form"],
        }
        self.assertEqual(sorted(received), sorted(M.KINDS))
        self.assertEqual(sorted(drafts), sorted(M.COMPOSABLE))
        at = "2026-09-25T10:00:00Z"
        for kind, args in received.items():
            if kind == "letter":
                at_ = "2026-09-25"                          # a letter arrives on a day
            else:
                at_ = at
            self.m("log", "--kind", kind, "--at", at_, *args)
        for kind in M.RECORD_ONLY:
            p = self.m("draft", "--kind", kind, "--to", JANE_CELL, expect=1)
            self.assertIn("log --kind %s" % kind, p.stderr)
        for kind, args in drafts.items():
            out = self.mj("draft", "--kind", kind, *args, "--body", "draft of a " + kind)
            self.m("mark-sent", out["id"])
        found = {}
        for d, _, files in os.walk(self.path()):
            for f in files:
                if f.startswith("MSG-") and f.endswith(".md"):
                    text = open(os.path.join(d, f)).read()
                    kind = [l for l in text.split("\n") if l.startswith("kind: ")][0][6:]
                    found.setdefault(kind, set()).add(os.path.relpath(d, self.path()).split("/")[0])
        for kind, k in M.KINDS.items():
            self.assertEqual(found[kind], {k.channel}, kind)
        self.assertIn("at: 2026-09-25\n", open(self.find("MSG-0009")).read())   # the letter
        self.assertIn("duration: PT4M12S", self.read("MSG-0007"))
        self.assertEqual(self.errors(), [])
        self.assertEqual(self.invariants(), [])

    def test_voicemail_audio_and_transcript(self):
        self.config(IDENTITIES)
        audio = self.scratch("Voicemail 12.m4a", b"\x00\x01audio")
        out = self.mj("log", "--kind", "voicemail", "--from", JANE_CELL, "--name", "Jane Doe",
                      "--at", "2026-09-27T15:00:00Z", "--duration", "PT38S", "--audio", audio,
                      "--transcript", "machine", "--body", "Hi, it's Jane. Call me back.")
        rec = self.read(out["id"])
        self.assertIn("attachments:\n  - {name: Voicemail 12.m4a, path: attachments/"
                      "MSG-0001-voicemail-12.m4a, role: audio}", rec)
        self.assertIn("transcript: machine", rec)
        copied = os.path.join(os.path.dirname(self.find("MSG-0001")), "attachments",
                              "MSG-0001-voicemail-12.m4a")
        with open(copied, "rb") as fh:
            self.assertEqual(fh.read(), b"\x00\x01audio")
        self.assertIn("Jane Doe", self.read("THR-0001"))
        vms = self.mj("voicemails", "--unheard")
        self.assertEqual([v["id"] for v in vms], ["MSG-0001"])
        self.assertEqual(vms[0]["transcript"], "Hi, it's Jane. Call me back.")
        self.m("seen", "MSG-0001")
        self.assertEqual(self.mj("voicemails", "--unheard"), [])
        self.assertEqual(len(self.mj("voicemails")), 1)
        # the transcript law, at the door
        p = self.m("log", "--kind", "voicemail", "--from", JANE_CELL, "--at", "now",
                   "--body", "text with no provenance", expect=1)
        self.assertIn("--transcript", p.stderr)
        self.m("log", "--kind", "voicemail", "--from", JANE_CELL, "--at", "now",
               "--transcript", "human", expect=1)
        self.m("log", "--kind", "sms", "--from", JANE_CELL, "--at", "now", "--audio", audio,
               expect=1)
        # a voicemail with no audio is recorded, and the validator warns
        out = self.m("log", "--kind", "voicemail", "--from", JANE_CELL, "--at", "now").stdout
        self.assertIn("no audio attached", out)
        self.assertEqual(self.errors(), [])
        self.assertEqual(self.invariants(), ["M-13"])

    def test_call_outcomes(self):
        self.config(IDENTITIES)
        p = self.m("log", "--kind", "call", "--from", JANE_CELL, "--at", "now", expect=1)
        self.assertIn("--outcome", p.stderr)
        p = self.m("log", "--kind", "call", "--from", JANE_CELL, "--at", "now", "--outcome",
                   "answered", expect=1)
        self.assertIn("--duration", p.stderr)
        self.m("log", "--kind", "call", "--from", JANE_CELL, "--at", "now", "--outcome", "picked",
               expect=1)
        self.m("log", "--kind", "call", "--from", JANE_CELL, "--at", "2026-09-27T09:00:00Z",
               "--outcome", "answered", "--duration", "1:02:03", "--body", "Talked about rent.")
        self.m("log", "--kind", "call", "--from", JANE_CELL, "--at", "2026-09-27T18:00:00Z",
               "--outcome", "missed")
        self.m("log", "--kind", "call", "--sent", "--to", JANE_CELL, "--at",
               "2026-09-27T18:05:00Z", "--outcome", "busy")
        self.assertIn("duration: PT1H2M3S", self.read("MSG-0001"))
        self.assertIn("from: +1 555 010 0100", self.read("MSG-0003"))     # your voice identity
        calls = self.mj("calls")
        self.assertEqual([(c["id"], c["direction"], c["outcome"]) for c in calls], [
            ("MSG-0003", "out", "busy"), ("MSG-0002", "in", "missed"),
            ("MSG-0001", "in", "answered")])
        self.assertEqual([c["id"] for c in self.mj("calls", "--missed")], ["MSG-0002"])
        # one voice thread per number; an answered call last is not "needs reply"
        self.assertEqual(len(self.mj("threads")), 1)
        self.assertEqual(self.errors(), [])

    def test_texts_thread_by_people(self):
        self.config(IDENTITIES)
        self.m("log", "--kind", "sms", "--from", JANE_CELL, "--at", "2026-09-27T08:00:00Z",
               "--body", "Running late")
        # a draft to the same number (written differently) joins the same conversation
        self.m("draft", "--kind", "sms", "--to", "(555) 010-0200", "--body", "No problem")
        self.assertEqual(len(self.mj("threads")), 1)
        # a group text is its own conversation; a reply goes to everyone in it
        self.m("log", "--kind", "mms", "--from", JANE_CELL, "--to", BOB_CELL, "--to", ME_CELL,
               "--at", "2026-09-27T09:00:00Z", "--body", "Dinner?")
        threads = self.mj("threads")
        self.assertEqual(len(threads), 2)
        group = [t for t in threads if len(t["with"]) == 2][0]
        self.assertEqual(sorted(group["with"]), sorted([JANE_CELL, BOB_CELL]))
        out = self.mj("reply", "MSG-0003", "--kind", "sms", "--body", "Yes!")
        rec = self.read(out["id"])
        self.assertIn("to: [+1 555 010 0200, +1 555 010 0300]", rec)
        self.assertIn("from: +1 555 010 0100", rec)
        self.assertIn("in_reply_to: MSG-0003", rec)
        self.assertEqual(out["thread"], group["id"])
        # --subject always starts a new thread
        self.m("draft", "--kind", "sms", "--to", JANE_CELL, "--subject", "Birthday plan")
        self.assertEqual(len(self.mj("threads")), 3)
        self.assertEqual(self.errors(), [])

    def test_email_reply_all_and_cards(self):
        self.config(IDENTITIES)
        self.m("log", "--kind", "email", "--from", JANE, "--to", ME, "--to", "bob@acme.com",
               "--cc", "dana@acme.com", "--subject", "Offsite", "--at", "2026-09-26T10:00:00Z")
        out = self.mj("reply", "MSG-0001", "--all", "--body", "Count me in.")
        rec = self.read(out["id"])
        self.assertIn("to: [jane@acme.com]\ncc: [bob@acme.com, dana@acme.com]", rec)
        plain = self.mj("reply", "MSG-0001", "--body", "Just you.")
        self.assertNotIn("cc:", self.read(plain["id"]))
        self.assertEqual(self.errors(), [])

    def test_discard_archive_restore(self):
        self.config(IDENTITIES)
        self.jane_email()
        self.m("reply", "MSG-0001", "--body", "draft one")
        p = self.m("archive", "THR-0001", expect=1)
        self.assertIn("open draft", p.stderr)
        self.m("discard", "MSG-0002")
        self.assertIn("status: discarded", self.read("MSG-0002"))
        self.m("discard", "MSG-0002", expect=1)
        self.m("mark-sent", "MSG-0002", expect=1)
        self.assertEqual(self.mj("threads", "--drafts"), [])
        self.m("archive", "THR-0001", "--note", "done")
        self.assertTrue(os.path.isdir(self.path("_archive", "email", "THR-0001-lease-renewal")))
        self.assertEqual(self.mj("threads"), [])
        self.assertEqual(len(self.mj("threads", "--archived")), 1)
        self.m("reply", "MSG-0001", "--body", "x", expect=1)            # archived: restore first
        self.m("log", "--kind", "email", "--from", JANE, "--thread", "THR-0001", "--at", "now",
               expect=1)
        self.m("restore", "THR-0001")
        self.assertTrue(os.path.isdir(self.path("email", "THR-0001-lease-renewal")))
        moves = [(h[1], h[2], h[3], h[4]) for h in self.history() if h[2] in ("archive", "restore")]
        self.assertEqual(moves, [
            ("THR-0001", "archive", "email/THR-0001", "_archive/email/THR-0001"),
            ("MSG-0001", "archive", "email/THR-0001", "_archive/email/THR-0001"),
            ("MSG-0002", "archive", "email/THR-0001", "_archive/email/THR-0001"),
            ("THR-0001", "restore", "_archive/email/THR-0001", "email/THR-0001"),
            ("MSG-0001", "restore", "_archive/email/THR-0001", "email/THR-0001"),
            ("MSG-0002", "restore", "_archive/email/THR-0001", "email/THR-0001")])
        self.assertEqual(self.errors(), [])

    def test_label_pin_about_rename_touch(self):
        self.config(IDENTITIES + "labels: [home, urgent]\n")
        self.jane_email()
        self.m("label", "THR-0001", "+home", "+urgent")
        self.m("label", "THR-0001", "+nope", expect=1)
        self.m("label", "THR-0001", "-urgent", "--by", "claude")
        self.assertIn("labels: [home]", self.read("THR-0001"))
        self.assertEqual(len(self.mj("threads", "--label", "home")), 1)
        self.m("pin", "THR-0001")
        self.m("pin", "THR-0001", expect=1)
        self.m("about", "THR-0001", "+EVT-0001")        # schedule not mounted: unverifiable, legal
        os.makedirs(os.path.join(self.proj, "notes", "inbox"))
        self.m("about", "THR-0001", "+NOTE-0009", expect=1)   # notes mounted: must resolve
        self.m("rename", "THR-0001", "Lease renewal 2027")
        self.assertTrue(os.path.isfile(self.path("email", "THR-0001-lease-renewal-2027",
                                                 "THR-0001.md")))
        self.assertIn("# Lease renewal 2027", self.read("THR-0001"))
        self.m("unpin", "THR-0001")
        # a hand edit of a thread, then touch
        self.check()
        self.edit("THR-0001", "# Lease renewal 2027", "# Lease renewal 2027\n\nKeep the PDF.")
        self.assertEqual(self.errors(env=self.later()), ["F-18"])
        self.m("touch", "THR-0001", env=self.later())
        self.assertEqual(self.errors(env=self.later()), [])

    def test_derived_views(self):
        self.config(IDENTITIES + "stale_after: 3\n")
        self.jane_email()                                              # THR-0001: needs reply
        self.m("log", "--kind", "email", "--sent", "--to", "bob@acme.com", "--subject", "Quote",
               "--at", "2026-09-20T10:00:00Z")                         # THR-0002: waiting
        self.m("log", "--kind", "sms", "--from", BOB_CELL, "--at", "2026-09-27T10:00:00Z",
               "--body", "ok")                                         # THR-0003: unseen
        self.m("seen", "MSG-0003")
        self.m("draft", "--kind", "sms", "--to", BOB_CELL, "--body", "thanks")   # draft there
        ids = lambda *f: [t["id"] for t in self.mj("threads", *f)]
        self.assertEqual(ids("--needs-reply"), ["THR-0003", "THR-0001"])
        self.assertEqual(ids("--unseen"), ["THR-0001"])
        self.assertEqual(ids("--waiting"), ["THR-0002"])
        self.assertEqual(ids("--drafts"), ["THR-0003"])
        self.assertEqual(ids("--channel", "text"), ["THR-0003"])
        self.assertEqual(ids("--kind", "email"), ["THR-0001", "THR-0002"])
        self.assertEqual(ids("--with", "BOB@acme.com"), ["THR-0002"])
        # waiting is about time: not yet stale two days after
        self.assertEqual([t["id"] for t in self.mj("threads", "--waiting",
                                                    env=self.later("2026-09-22"))], [])
        self.assertIn("needs-reply", self.m("threads").stdout)
        self.assertIn("state: needs-reply unseen:1", self.m("show", "THR-0001").stdout)

    def test_timeline_across_channels_and_crm_cards(self):
        self.config(IDENTITIES)
        self.write(os.path.join(self.proj, "crm", "contacts", "CARD-0001-jane-doe.md"),
                   "---\nid: CARD-0001\ncreated: 2026-09-01\ncreated_by: chazz\n"
                   "updated: 2026-09-01\nemail:\n  - {label: work, value: jane@acme.com, "
                   "pref: true}\nphone:\n  - {label: cell, value: \"+15550100200\"}\n"
                   "handle:\n  - {service: slack, value: U0JANE}\n---\n\n# Jane Doe\n")
        self.jane_email()
        self.m("log", "--kind", "sms", "--from", JANE_CELL, "--at", "2026-09-21T08:00:00Z",
               "--body", "Got your email")
        self.m("log", "--kind", "call", "--from", "555-010-0200", "--at", "2026-09-22T08:00:00Z",
               "--outcome", "missed")
        self.m("log", "--kind", "chat", "--from", "slack:U0JANE", "--service", "slack",
               "--at", "2026-09-23T08:00:00Z", "--body", "ping")
        self.m("log", "--kind", "sms", "--from", BOB_CELL, "--at", "2026-09-24T08:00:00Z",
               "--body", "not Jane")
        tl = self.mj("timeline", "--with", "CARD-0001")
        self.assertEqual([(x["id"], x["channel"]) for x in tl], [
            ("MSG-0001", "email"), ("MSG-0002", "text"), ("MSG-0003", "voice"),
            ("MSG-0004", "chat")])
        # by an address the card knows: the same person, every channel
        self.assertEqual([x["id"] for x in self.mj("timeline", "--with", "+1 (555) 010-0200")],
                         ["MSG-0001", "MSG-0002", "MSG-0003", "MSG-0004"])
        # an address crm does not know: just that address
        self.assertEqual([x["id"] for x in self.mj("timeline", "--with", BOB_CELL)], ["MSG-0005"])
        self.assertIn("Jane Doe <jane@acme.com>", self.m("show", "THR-0001").stdout)
        self.m("timeline", "--with", "NOTE-0001", expect=1)
        # a CARD id where no address is typed: resolved for export
        out = self.mj("draft", "--kind", "email", "--to", "CARD-0001", "--subject", "Hi")
        e = email.message_from_bytes(self.m("export-eml", out["id"], raw=True).stdout,
                                     policy=policy.default)
        self.assertEqual(str(e["To"]), "Jane Doe <jane@acme.com>")
        self.m("draft", "--kind", "email", "--to", "CARD-0404", "--subject", "x", expect=1)
        self.assertEqual(self.errors(), [])

    def test_wall_clock_times_need_a_zone(self):
        self.config(IDENTITIES)
        p = self.m("log", "--kind", "sms", "--from", JANE_CELL, "--at", "2026-09-27 14:00",
                   expect=1)
        self.assertIn("timezone", p.stderr)
        self.m("log", "--kind", "sms", "--from", JANE_CELL, "--at", "2026-09-27", expect=1)
        self.m("log", "--kind", "sms", "--from", JANE_CELL, "--at", "2026-09-30T10:00:00Z",
               expect=1)                                                 # the future
        self.config(IDENTITIES + "timezone: America/New_York\n")
        self.m("log", "--kind", "sms", "--from", JANE_CELL, "--at", "2026-09-27 14:00")
        self.assertIn("at: 2026-09-27T14:00:00-04:00", self.read("MSG-0001"))
        self.m("log", "--kind", "letter", "--from", "Bank", "--at", "yesterday")
        self.assertIn("at: 2026-09-27\n", self.read("MSG-0002"))

    def test_dry_run_writes_nothing(self):
        self.config(IDENTITIES)
        before = sorted(os.listdir(self.path("email")))
        out = self.m("draft", "--kind", "email", "--to", JANE, "--subject", "Ghost",
                     "--dry-run").stdout
        self.assertIn("would write", out)
        self.m("log", "--kind", "sms", "--from", JANE_CELL, "--at", "now", "--dry-run")
        self.m("import", self.scratch("a.eml", EML_1), "--dry-run")
        self.assertEqual(sorted(os.listdir(self.path("email"))), before)
        self.assertEqual(os.listdir(self.path("text")), [])
        self.assertEqual(self.history(), [])

    def test_ids_are_never_reissued(self):
        self.config(IDENTITIES)
        self.m("draft", "--kind", "sms", "--to", JANE_CELL, "--body", "one")
        shutil.rmtree(os.path.dirname(self.find("MSG-0001")))
        out = self.mj("draft", "--kind", "sms", "--to", JANE_CELL, "--body", "two")
        self.assertEqual((out["id"], out["thread"]), ("MSG-0002", "THR-0002"))
        self.assertEqual(self.errors(), [])
        self.assertEqual(self.invariants(), ["F-17"])          # gone files: a warning


# ---------------------------------------------------------------------------
# imports
# ---------------------------------------------------------------------------

EML_1 = """\
Message-ID: <lease-1@acme.com>
Date: Sun, 20 Sep 2026 09:14:00 -0400
From: Jane Doe <jane@acme.com>
To: Me <me@example.org>
Subject: Lease renewal
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="XX"

--XX
Content-Type: text/plain; charset=utf-8

Your lease is up at the end of October.
Renew?
--XX
Content-Type: application/pdf
Content-Disposition: attachment; filename="lease 2027.pdf"
Content-Transfer-Encoding: base64

JVBERi0xLjQK
--XX--
"""

EML_2 = """\
Message-ID: <reply-2@example.org>
In-Reply-To: <lease-1@acme.com>
References: <lease-1@acme.com>
Date: Mon, 21 Sep 2026 08:00:00 -0400
From: me@example.org
To: jane@acme.com
Subject: Re: Lease renewal
Content-Type: text/plain; charset=utf-8

Yes, one more year.
"""

EML_3 = """\
Message-ID: <lease-3@acme.com>
References: <lease-1@acme.com> <reply-2@example.org>
Date: Mon, 21 Sep 2026 16:30:00 +0100
From: Jane Doe <jane@acme.com>
To: me@example.org
Cc: Dana <dana@acme.com>
Subject: RE: Re: Lease renewal
Content-Type: text/html; charset=utf-8

<html><head><style>p{color:red}</style></head><body><p>Great &amp; done.</p><p>Sign<br>here</p></body></html>
"""

EML_5 = """\
Message-ID: <lease-5@acme.com>
In-Reply-To: <lease-3@acme.com>
Date: Wed, 23 Sep 2026 10:00:00 -0400
From: Jane Doe <jane@acme.com>
To: me@example.org
Cc: Eve <eve@acme.com>
Subject: Re: Lease renewal
Content-Type: text/plain

Eve will countersign.
"""

EML_4 = """\
Message-ID: <promo@shop.example>
Date: Tue, 22 Sep 2026 07:00:00 +0000
From: Shop <news@shop.example>
To: me@example.org
Subject: Fwd: Sale
Content-Type: text/plain

50% off.
"""


def sms_xml():
    img = base64.b64encode(b"\x89PNGfake").decode("ascii")
    return """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<smses count="6">
  <sms protocol="0" address="+15550100200" date="1790000000000" type="1" subject="null" body="Hi from Jane" read="1" status="-1" contact_name="Jane Doe" />
  <sms protocol="0" address="+15550100200" date="1790000060000" type="2" subject="null" body="Hi Jane" read="1" status="-1" contact_name="Jane Doe" />
  <sms protocol="0" address="AMAZON" date="1790000120000" type="1" subject="null" body="Your parcel shipped" read="1" status="-1" contact_name="(Unknown)" />
  <sms protocol="0" address="+15550100200" date="1790000180000" type="3" subject="null" body="unsent draft" read="1" status="-1" contact_name="Jane Doe" />
  <mms date="1790000240000" msg_box="1" address="+15550100200~+15550100300~+15550100100" ct_t="application/vnd.wap.multipart.related" contact_name="Jane Doe, Bob">
    <parts>
      <part seq="-1" ct="application/smil" name="null" chset="null" cd="null" fn="null" cid="&lt;smil&gt;" cl="smil.xml" text="&lt;smil&gt;&lt;/smil&gt;" />
      <part seq="0" ct="image/png" name="beach.png" chset="null" cd="null" fn="null" cid="null" cl="beach.png" data="%s" />
      <part seq="0" ct="text/plain" name="null" chset="106" cd="null" fn="null" cid="null" cl="txt000.txt" text="Look at this" />
    </parts>
    <addrs>
      <addr address="+15550100200" type="137" charset="106" />
      <addr address="+15550100300" type="151" charset="106" />
      <addr address="+15550100100" type="151" charset="106" />
    </addrs>
  </mms>
  <mms date="1790000300000" msg_box="2" address="+15550100200~+15550100300" ct_t="application/vnd.wap.multipart.related" contact_name="Jane Doe, Bob">
    <parts>
      <part seq="0" ct="text/plain" name="null" chset="106" cd="null" fn="null" cid="null" cl="txt000.txt" text="Lovely!" />
    </parts>
    <addrs>
      <addr address="insert-address-token" type="137" charset="106" />
      <addr address="+15550100200" type="151" charset="106" />
      <addr address="+15550100300" type="151" charset="106" />
    </addrs>
  </mms>
</smses>
""" % img


CALLS_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<calls count="8">
  <call number="+15550100200" duration="125" date="1790000000000" type="1" presentation="1" contact_name="Jane Doe" />
  <call number="+15550100200" duration="0" date="1790000100000" type="2" presentation="1" contact_name="Jane Doe" />
  <call number="+15550100200" duration="61" date="1790000200000" type="2" presentation="1" contact_name="Jane Doe" />
  <call number="+15550100300" duration="0" date="1790000300000" type="3" presentation="1" contact_name="Bob" />
  <call number="+15550100300" duration="0" date="1790000400000" type="4" presentation="1" contact_name="Bob" />
  <call number="+15550100400" duration="0" date="1790000500000" type="5" presentation="1" contact_name="(Unknown)" />
  <call number="+15550100500" duration="0" date="1790000600000" type="6" presentation="1" contact_name="null" />
  <call number="-2" duration="0" date="1790000700000" type="3" presentation="2" contact_name="(Unknown)" />
</calls>
"""


class ImportTests(Base):

    def test_eml_threading_attachments_and_dedupe(self):
        self.config(IDENTITIES)
        files = [self.scratch(n, t) for n, t in (("3.eml", EML_3), ("1.eml", EML_1),
                                                  ("2.eml", EML_2), ("4.eml", EML_4))]
        out = self.mj("import", *files)
        self.assertEqual((out["imported"], out["threads"], out["new_threads"],
                          out["already_imported"]), (4, 2, 2, 0))
        # imported in date order, whatever order the files came in
        self.assertEqual(out["ids"], ["MSG-0001", "MSG-0002", "MSG-0003", "MSG-0004"])
        m1, m2, m3 = self.read("MSG-0001"), self.read("MSG-0002"), self.read("MSG-0003")
        self.assertIn("status: received\nat: 2026-09-20T09:14:00-04:00\nfrom: jane@acme.com", m1)
        self.assertIn("external_id: lease-1@acme.com", m1)
        self.assertIn("{name: lease 2027.pdf, path: attachments/MSG-0001-lease-2027.pdf, "
                      "role: file}", m1)
        self.assertIn("Your lease is up at the end of October.\nRenew?", m1)
        self.assertIn("status: sent", m2)                         # From is your identity
        self.assertIn("in_reply_to: MSG-0001", m2)
        self.assertIn("at: 2026-09-21T16:30:00+01:00", m3)        # the offset is kept
        self.assertIn("cc: [dana@acme.com]", m3)
        self.assertIn("in_reply_to: MSG-0002", m3)                # nearest reference first
        self.assertIn("Great & done.\n\nSign\nhere", m3)          # html, made plain
        self.assertNotIn("color", m3)
        thr = self.read("THR-0001")
        self.assertIn("# Lease renewal", thr)
        self.assertIn("{who: jane@acme.com, name: Jane Doe}", thr)
        self.assertIn("{who: dana@acme.com, name: Dana}", thr)
        self.assertIn("identity: home", thr)
        self.assertIn("# Sale", self.read("THR-0002"))
        pdf = os.path.join(os.path.dirname(self.find("MSG-0001")), "attachments",
                           "MSG-0001-lease-2027.pdf")
        with open(pdf, "rb") as fh:
            self.assertEqual(fh.read(), b"%PDF-1.4\n")
        # the same files again: nothing new
        again = self.mj("import", *files)
        self.assertEqual((again["imported"], again["already_imported"]), (0, 4))
        self.assertEqual(self.errors(), [])
        verbs = [(h[1], h[2]) for h in self.history()]
        self.assertIn(("THR-0001", "import"), verbs)
        self.assertNotIn(("THR-0001", "participant"), verbs)    # new in this act: born whole
        # a later message that brings someone new: the thread gains a participant, logged
        out = self.mj("import", self.scratch("5.eml", EML_5))
        self.assertEqual(out["ids"], ["MSG-0005"])
        self.assertIn("in_reply_to: MSG-0003", self.read("MSG-0005"))
        self.assertIn("{who: eve@acme.com, name: Eve}", self.read("THR-0001"))
        self.assertIn(("THR-0001", "participant"), [(h[1], h[2]) for h in self.history()])
        self.assertEqual(self.errors(), [])

    def test_mbox(self):
        self.config(IDENTITIES)
        box_path = os.path.join(self.tmp, "mail.mbox")
        box = mailbox.mbox(box_path)
        for text in (EML_2, EML_1, EML_4):
            box.add(email.message_from_string(text, policy=policy.compat32))
        box.flush()
        box.close()
        out = self.mj("import", "--mbox", box_path)
        self.assertEqual((out["imported"], out["threads"]), (3, 2))
        self.assertIn("in_reply_to: MSG-0001", self.read("MSG-0002"))
        self.assertEqual(self.mj("import", "--mbox", box_path)["already_imported"], 3)
        self.m("import", "--mbox", box_path, self.scratch("x.eml", EML_4), expect=1)
        self.assertEqual(self.errors(), [])

    def test_sms_backup_xml(self):
        self.config(IDENTITIES)
        path = self.scratch("sms-2026.xml", sms_xml())
        out = self.mj("import", "--sms-xml", path)
        self.assertEqual(out["imported"], 5)
        self.assertTrue(any("drafts, queued or failed" in n for n in out["notes"]))
        threads = self.mj("threads", "--channel", "text")
        people = sorted(tuple(sorted(t["with"])) for t in threads)
        self.assertEqual(people, [("+15550100200",), ("+15550100200", "+15550100300"),
                                  ("AMAZON",)])
        sent = self.read("MSG-0002")
        self.assertIn("status: sent\nat: 2026-09-21T14:14:20Z\nfrom: +1 555 010 0100\n"
                      "to: [\"+15550100200\"]", sent)
        self.assertRegex(self.read("MSG-0001"), r"external_id: sms:[0-9a-f]{24}\n")
        mms = self.read("MSG-0004")
        self.assertIn("kind: mms", mms)
        self.assertIn("from: \"+15550100200\"", mms)
        self.assertIn("role: image", mms)
        self.assertIn("Look at this", mms)
        img = os.path.join(os.path.dirname(self.find("MSG-0004")), "attachments",
                           "MSG-0004-beach.png")
        with open(img, "rb") as fh:
            self.assertEqual(fh.read(), b"\x89PNGfake")
        # the group reply went into the same group thread
        self.assertEqual(os.path.dirname(self.find("MSG-0005")),
                         os.path.dirname(self.find("MSG-0004")))
        self.assertIn("# Jane Doe", self.read("THR-0001"))
        # a group's contact_name ("Jane Doe, Bob") names nobody in particular
        group = self.read("THR-0003")
        self.assertNotIn("name:", group)
        self.assertIn("# +15550100200, +15550100300", group)
        self.assertEqual(self.mj("import", "--sms-xml", path)["already_imported"], 5)
        self.assertEqual(self.errors(), [])

    def test_call_log_xml(self):
        self.config(IDENTITIES)
        out = self.mj("import", "--sms-xml", self.scratch("calls.xml", CALLS_XML))
        self.assertEqual(out["imported"], 7)
        self.assertTrue(any("withheld" in n for n in out["notes"]))
        calls = {c["id"]: c for c in self.mj("calls")}
        got = sorted((c["direction"], c["outcome"], c["duration"]) for c in calls.values())
        self.assertEqual(got, sorted([
            ("in", "answered", "PT2M5S"), ("out", "missed", None), ("out", "answered", "PT1M1S"),
            ("in", "missed", None), ("in", "voicemail", None), ("in", "declined", None),
            ("in", "blocked", None)]))
        self.assertEqual(len(self.mj("calls", "--missed")), 2)
        self.assertEqual(len(self.mj("threads", "--channel", "voice")), 4)
        self.assertIn("at: 2026-09-21T14:13:20Z", self.read("MSG-0001"))
        # Jane's thread ends with an answered call you made: nobody owes anyone
        jane = [t for t in self.mj("threads", "--channel", "voice", env=self.later())
                if t["with"] == ["+15550100200"]][0]
        self.assertEqual((jane["needs_reply"], jane["waiting"]), (False, False))
        bob = [t for t in self.mj("threads") if t["with"] == ["+15550100300"]][0]
        self.assertTrue(bob["needs_reply"])                  # a missed call, then a voicemail
        self.assertEqual(self.errors(), [])

    def test_import_of_sent_texts_needs_an_identity(self):
        path = self.scratch("sms.xml", sms_xml())
        p = self.m("import", "--sms-xml", path, expect=1)
        self.assertIn("HARD-STOP", p.stderr)
        self.assertEqual(self.history(), [])
        self.assertEqual(os.listdir(self.path("text")), [])
        # an email file with no identities: everything is simply received
        self.m("import", self.scratch("2.eml", EML_2))
        self.assertIn("status: received", self.read("MSG-0001"))


# ---------------------------------------------------------------------------
# refusals
# ---------------------------------------------------------------------------

class RefusalTests(Base):

    def test_the_identity_hard_stop(self):
        for args in (("draft", "--kind", "email", "--to", JANE, "--subject", "x"),
                     ("draft", "--kind", "sms", "--to", JANE_CELL),
                     ("log", "--kind", "email", "--sent", "--to", JANE, "--subject", "x",
                      "--at", "now")):
            p = self.m(*args, expect=1)
            self.assertIn("HARD-STOP", p.stderr)
        self.jane_email()                                    # receiving needs no identity
        self.assertIn("HARD-STOP", self.m("reply", "MSG-0001", expect=1).stderr)
        self.m("log", "--kind", "call", "--sent", "--to", JANE_CELL, "--at", "now",
               "--outcome", "missed")                        # a call is not composed
        self.config(IDENTITIES)
        self.m("reply", "MSG-0001", "--body", "ok")
        self.config("identities:\n  - {id: cell, channel: text, address: +1 555 010 0100}\n")
        self.assertIn("HARD-STOP", self.m("mark-sent", "MSG-0003", expect=1).stderr)
        self.config("identities:\n  - {id: work, channel: email, address: me@work.example}\n")
        p = self.m("mark-sent", "MSG-0003", expect=1)
        self.assertIn("not a declared email identity", p.stderr)
        # two identities on a channel: say which
        self.config("identities:\n  - {id: a, channel: email, address: a@example.org}\n"
                    "  - {id: b, channel: email, address: b@example.org}\n")
        self.assertIn("--as", self.m("draft", "--kind", "email", "--to", JANE, "--subject", "x",
                                     expect=1).stderr)
        self.m("draft", "--kind", "email", "--to", JANE, "--subject", "x", "--as", "b")
        self.m("draft", "--kind", "email", "--to", JANE, "--subject", "x", "--as", "c", expect=1)
        # a fax is from a number: a postal identity will not do
        self.config("identities:\n  - {id: p, channel: post, address: \"Me, 1 Main St\"}\n")
        self.assertIn("HARD-STOP", self.m("draft", "--kind", "fax", "--to", "+1 555 010 0999",
                                          "--subject", "x", expect=1).stderr)

    def test_actor_is_never_invented(self):
        self.config(IDENTITIES)
        p = self.m("draft", "--kind", "sms", "--to", JANE_CELL, expect=1, env={"RASA_ACTOR": ""})
        self.assertIn("who is doing this", p.stderr)
        self.config(IDENTITIES + "default_actor: claude\nactors: [chazz, claude]\n")
        self.m("draft", "--kind", "sms", "--to", JANE_CELL, env={"RASA_ACTOR": ""})
        self.m("draft", "--kind", "sms", "--to", JANE_CELL, "--by", "zed", expect=1)

    def test_declared_vocabularies_are_closed(self):
        self.config("identities:\n  - {id: phone, channel: voice, address: +1 555 010 0100}\n"
                    "channels: [email, chat, voice]\nservices: [slack]\nlabels: [home]\n")
        self.m("log", "--kind", "sms", "--from", JANE_CELL, "--at", "now", expect=1)
        self.m("log", "--kind", "chat", "--from", "teams:jane", "--service", "teams", "--at",
               "now", expect=1)
        self.m("log", "--kind", "chat", "--from", "slack:U1", "--service", "slack", "--at", "now",
               "--label", "work", expect=1)
        self.m("log", "--kind", "chat", "--from", "slack:U1", "--service", "slack", "--at", "now",
               "--label", "home")
        self.m("import", "--sms-xml", self.scratch("c.xml", CALLS_XML))           # voice: fine
        self.m("import", "--sms-xml", self.scratch("s.xml", sms_xml()), expect=1)  # text: not
        self.assertEqual(self.errors(), [])

    def test_kind_law_at_the_door(self):
        self.config(IDENTITIES)
        cases = [
            ("draft", "--kind", "sms", "--to", JANE_CELL, "--cc", BOB_CELL),       # no cc
            ("draft", "--kind", "sms", "--to", JANE_CELL, "--attach", __file__),   # no files
            ("draft", "--kind", "chat", "--to", "slack:U1"),                        # service
            ("draft", "--kind", "chat", "--to", "teams:U1", "--service", "slack"),  # wrong service
            ("draft", "--kind", "email", "--to", JANE_CELL, "--subject", "x"),     # not email
            ("draft", "--kind", "email", "--to", JANE),                             # no subject
            ("draft", "--kind", "email", "--to", "NOTE-0001", "--subject", "x"),   # not a person
            ("draft", "--kind", "sms"),                                             # nobody
            ("draft", "--kind", "telegram", "--to", JANE_CELL),                     # no such kind
            ("log", "--kind", "sms", "--at", "now"),                                # from whom
            ("log", "--kind", "sms", "--from", JANE_CELL, "--at", "now", "--outcome", "missed"),
            ("log", "--kind", "email", "--from", JANE, "--at", "now", "--subject", "x",
             "--duration", "PT1M"),
            ("log", "--kind", "voicemail", "--from", JANE_CELL, "--at", "now", "--audio",
             __file__, "--attach", __file__),                                       # one file
            ("log", "--kind", "call", "--from", JANE_CELL, "--at", "now", "--outcome",
             "answered", "--duration", "soon"),
            ("log", "--kind", "sms", "--sent", "--from", JANE_CELL, "--to", BOB_CELL, "--at",
             "now"),                                                                # not you
        ]
        for args in cases:
            self.m(*args, expect=1)
        self.assertEqual(self.history(), [])

    def test_status_and_thread_refusals(self):
        self.config(IDENTITIES)
        self.jane_email()
        self.m("log", "--kind", "voicemail", "--from", JANE_CELL, "--at", "now")
        self.m("draft", "--kind", "sms", "--to", JANE_CELL, "--body", "x")
        self.m("mark-sent", "MSG-0001", expect=1)                  # received
        self.m("discard", "MSG-0001", expect=1)
        self.assertIn("calling back", self.m("reply", "MSG-0002", expect=1).stderr)
        self.m("reply", "MSG-0003", expect=1)                      # a draft never happened
        self.m("reply", "MSG-0001", "--kind", "sms", expect=1)     # wrong channel for the thread
        self.m("export-eml", "MSG-0003", expect=1)                 # not an email
        self.m("export-eml", "MSG-0001", expect=1)                 # not a draft
        self.m("draft", "--kind", "sms", "--to", JANE_CELL, "--thread", "THR-0001", expect=1)
        self.m("draft", "--kind", "email", "--to", JANE, "--thread", "THR-0404", expect=1)
        self.m("log", "--kind", "email", "--from", JANE, "--at", "now", "--subject", "x",
               "--external-id", "a@b", "--reply-to", "MSG-0001", expect=1)  # other thread
        self.m("log", "--kind", "email", "--from", JANE, "--at", "2026-09-19T00:00:00Z",
               "--thread", "THR-0001", "--reply-to", "MSG-0001", expect=1)  # earlier than it
        self.m("log", "--kind", "email", "--from", JANE, "--at", "now", "--thread", "THR-0001",
               "--reply-to", "MSG-0001", "--external-id", "x-1")
        self.m("log", "--kind", "email", "--from", JANE, "--at", "now", "--thread", "THR-0001",
               "--external-id", "x-1", expect=1)                   # dedupe key taken
        self.m("show", "MSG-0404", expect=1)
        self.m("show", "NOTE-0001", expect=1)
        self.m("seen", "MSG-0003", expect=1)                       # a draft is never "seen"
        self.m("label", "MSG-0001", "+x", expect=1)
        self.assertEqual(self.errors(), [])


# ---------------------------------------------------------------------------
# every invariant, caught under its own id
# ---------------------------------------------------------------------------

HEAD = "---\nid: %s\ncreated: 2026-09-28\ncreated_by: chazz\nupdated: 2026-09-28\n"


class InvariantTests(Base):

    def seed(self):
        self.config(IDENTITIES)
        self.jane_email()                                            # THR-0001 / MSG-0001
        self.m("reply", "MSG-0001", "--body", "draft")               # MSG-0002 draft
        self.m("log", "--kind", "sms", "--from", JANE_CELL, "--at", "2026-09-27T08:00:00Z",
               "--body", "hi")                                       # THR-0002 / MSG-0003
        self.check()                                                 # seeds the content cache
        self.thr1 = os.path.dirname(self.find("THR-0001"))
        self.thr2 = os.path.dirname(self.find("THR-0002"))

    def plant(self, where, name, fm, body="\n", inv=None, invs=None):
        p = os.path.join(where, name)
        self.write(p, HEAD % "-".join(name[:-3].split("-")[:2]) + fm + "---\n" + body)
        found = self.invariants(severity=("ERROR", "WARN", "INFO"))
        for i in ([inv] if inv else []) + list(invs or []):
            self.assertIn(i, found, "%s should raise %s, got %s" % (name, i, found))
        os.remove(p)
        return found

    def test_clean_seed(self):
        self.seed()
        self.assertEqual(self.invariants(), [])

    def test_m01_layout(self):
        self.seed()
        msg = "kind: sms\nstatus: received\nat: 2026-09-27T08:00:00Z\nfrom: \"+15550100200\"\n"
        self.plant(self.path("text"), "MSG-0050.md", msg, inv="M-01")          # no thread dir
        self.plant(self.path(), "MSG-0051.md", msg, inv="M-01")                # at the top
        os.makedirs(self.path("text", "THR-0060-lonely"))
        self.plant(self.path("text", "THR-0060-lonely"), "MSG-0052.md", msg, inv="M-01")
        self.plant(self.thr2, "THR-0061.md", "", "\n# Two\n", inv="M-01")     # second record
        os.makedirs(self.path("text", "THR-0062"))
        self.plant(self.path("text", "THR-0062"), "THR-0062-slug.md", "", "\n# X\n", inv="M-01")
        os.makedirs(self.path("text", "notes"))
        self.plant(self.path("text", "notes"), "MSG-0053.md", msg, inv="M-01")
        # a thread record that does not parse is its own finding; its messages are not orphans
        with open(self.find("THR-0002")) as fh:
            good = fh.read()
        self.write(self.find("THR-0002"), good.replace("participants:", "participants: [a b"))
        found = self.errors()
        self.assertIn("F-05", found)
        self.assertNotIn("M-01", found)
        self.write(self.find("THR-0002"), good)

    def test_m02_channels(self):
        self.seed()
        os.makedirs(self.path("fax", "THR-0070-x"))
        self.plant(self.path("fax", "THR-0070-x"), "THR-0070.md", "", "\n# X\n", inv="M-02")
        self.config(IDENTITIES + "channels: [email]\n")
        self.assertIn("M-02", self.errors())

    def test_m03_kind_in_its_channel(self):
        self.seed()
        self.plant(self.thr1, "MSG-0080.md", "kind: sms\nstatus: received\n"
                   "at: 2026-09-27T08:00:00Z\nfrom: \"+15550100200\"\n", inv="M-03")

    def test_m04_kind_law(self):
        self.seed()
        base = "status: received\nat: 2026-09-27T08:00:00Z\n"
        self.plant(self.thr2, "MSG-0090.md", "kind: sms\n" + base + "from: \"+15550100200\"\n"
                   "cc: [\"+15550100300\"]\n", inv="M-04")
        os.makedirs(self.path("chat", "THR-0091-c"))
        self.write(self.path("chat", "THR-0091-c", "THR-0091.md"), HEAD % "THR-0091" +
                   "---\n\n# C\n")
        self.plant(self.path("chat", "THR-0091-c"), "MSG-0092.md", "kind: chat\n" + base +
                   "from: slack:U1\n", inv="M-04")                           # no service
        self.plant(self.path("chat", "THR-0091-c"), "MSG-0093.md", "kind: chat\n" + base +
                   "from: teams:U1\nservice: slack\n", inv="M-04")           # wrong service
        self.plant(self.thr1, "MSG-0094.md", "kind: email\n" + base + "from: \"+15550100200\"\n",
                   inv="M-04")                                               # not an address
        self.plant(self.thr1, "MSG-0095.md", "kind: email\n" + base + "from: jane@acme.com\n"
                   "outcome: missed\n", inv="M-04")
        os.makedirs(self.path("voice", "THR-0096-v"))
        self.write(self.path("voice", "THR-0096-v", "THR-0096.md"), HEAD % "THR-0096" +
                   "---\n\n# V\n")
        self.plant(self.path("voice", "THR-0096-v"), "MSG-0097.md", "kind: call\n" + base +
                   "from: \"+15550100200\"\noutcome: answered\n", inv="M-04")  # no duration
        self.plant(self.thr1, "MSG-0098.md", "kind: email\nstatus: received\nat: 2026-09-27\n"
                   "from: jane@acme.com\n", inv="M-04")                      # only letters: a day

    def test_m05_status_fields(self):
        self.seed()
        self.plant(self.thr2, "MSG-0100.md", "kind: sms\nstatus: draft\n"
                   "at: 2026-09-27T08:00:00Z\nfrom: +1 555 010 0100\nto: [\"+15550100200\"]\n",
                   inv="M-05")
        self.plant(self.thr2, "MSG-0101.md", "kind: sms\nstatus: received\n"
                   "from: \"+15550100200\"\n", inv="M-05")
        self.plant(self.thr2, "MSG-0102.md", "kind: sms\nstatus: received\n"
                   "at: 2026-09-27T08:00:00Z\n", inv="M-05")

    def test_m06_only_composable_kinds_are_drafts(self):
        self.seed()
        os.makedirs(self.path("voice", "THR-0110-v"))
        self.write(self.path("voice", "THR-0110-v", "THR-0110.md"), HEAD % "THR-0110" +
                   "---\n\n# V\n")
        self.plant(self.path("voice", "THR-0110-v"), "MSG-0111.md", "kind: call\nstatus: draft\n"
                   "to: [\"+15550100200\"]\noutcome: missed\n", inv="M-06")

    def test_m07_status_lifecycle(self):
        self.seed()
        # draft -> sent by hand: the log never said so
        self.edit("MSG-0002", "status: draft\n", "status: sent\nat: 2026-09-28T10:00:00Z\n")
        self.assertIn("M-07", self.errors())
        self.edit("MSG-0002", "status: sent\nat: 2026-09-28T10:00:00Z\n", "status: draft\n")
        self.assertEqual(self.errors(), [])
        # a transition that does not exist, written into the log
        with open(self.path("history.tsv"), "a") as fh:
            fh.write("2026-09-28T12:00:00Z\tMSG-0003\tunsend\tstatus=received\tstatus=draft\t"
                     "chazz\t\n")
        self.assertIn("M-07", self.errors())

    def test_m07_filed_as_discarded(self):
        self.seed()
        p = os.path.join(self.thr2, "MSG-0120.md")
        self.write(p, HEAD % "MSG-0120" + "kind: sms\nstatus: discarded\n"
                   "from: +1 555 010 0100\nto: [\"+15550100200\"]\n---\n")
        with open(self.path("history.tsv"), "a") as fh:
            fh.write("2026-09-28T12:00:00Z\tMSG-0120\tnew\t-\ttext/THR-0002\tchazz\t"
                     "status=discarded\n")
        self.assertIn("M-07", self.errors())

    def test_m08_frozen_is_never_reopened(self):
        self.seed()
        self.edit("MSG-0001", "status: received\nat: 2026-09-20T09:14:00-04:00\n",
                  "status: draft\n")
        found = self.errors()
        self.assertIn("M-08", found)
        self.assertIn("M-07", found)

    def test_m09_in_reply_to(self):
        self.seed()
        body = "kind: email\nstatus: received\nat: 2026-09-21T08:00:00Z\nfrom: jane@acme.com\n"
        self.plant(self.thr1, "MSG-0130.md", body + "in_reply_to: MSG-0003\n", inv="M-09")
        self.plant(self.thr1, "MSG-0131.md", body + "in_reply_to: MSG-0131\n", inv="M-09")
        self.plant(self.thr1, "MSG-0132.md", body + "in_reply_to: MSG-0002\n", inv="M-09")
        self.plant(self.thr1, "MSG-0133.md", "kind: email\nstatus: received\n"
                   "at: 2026-09-19T08:00:00Z\nfrom: jane@acme.com\nin_reply_to: MSG-0001\n",
                   inv="M-09")                                                 # later parent

    def test_m10_external_ids_are_unique(self):
        self.seed()
        self.m("log", "--kind", "sms", "--from", JANE_CELL, "--at", "now", "--external-id", "k1")
        self.plant(self.thr2, "MSG-0140.md", "kind: sms\nstatus: received\n"
                   "at: 2026-09-27T08:00:00Z\nfrom: \"+15550100200\"\nexternal_id: k1\n",
                   inv="M-10")

    def test_m11_from_is_a_declared_identity(self):
        self.seed()
        self.edit("MSG-0002", "from: me@example.org", "from: someone@else.example")
        self.assertIn("M-11", self.errors())
        self.edit("MSG-0002", "from: someone@else.example", "from: me@example.org")
        self.config("identities:\n  - {id: cell, channel: text, address: +1 555 010 0100}\n")
        self.assertIn("M-11", self.errors())

    def test_m12_attachments(self):
        self.seed()
        att = "attachments:\n  - {name: a.pdf, path: attachments/MSG-0150-a.pdf, role: file}\n"
        body = "kind: email\nstatus: received\nat: 2026-09-21T08:00:00Z\nfrom: jane@acme.com\n"
        self.plant(self.thr1, "MSG-0150.md", body + att, inv="M-12")            # missing
        outside = os.path.join(self.tmp, "secret.txt")
        self.write(outside, "x")
        os.makedirs(os.path.join(self.thr1, "attachments"))
        os.symlink(outside, os.path.join(self.thr1, "attachments", "MSG-0151-a.pdf"))
        self.plant(self.thr1, "MSG-0151.md", body + att.replace("0150", "0151"),
                   inv="M-12")                                                # escapes
        os.remove(os.path.join(self.thr1, "attachments", "MSG-0151-a.pdf"))
        self.write(os.path.join(self.thr1, "attachments", "stray.bin"), "x")
        self.assertEqual(self.invariants(), ["M-12"])                          # orphan: a warning
        self.assertEqual(self.errors(), [])

    def test_m13_m14_voicemail(self):
        self.seed()
        os.makedirs(self.path("voice", "THR-0160-v"))
        self.write(self.path("voice", "THR-0160-v", "THR-0160.md"), HEAD % "THR-0160" +
                   "---\n\n# V\n")
        vm = "kind: voicemail\nstatus: received\nat: 2026-09-27T08:00:00Z\nfrom: \"+15550100200\"\n"
        where = self.path("voice", "THR-0160-v")
        self.plant(where, "MSG-0161.md", vm, inv="M-13")
        self.plant(where, "MSG-0162.md", vm, "\nCall me back.\n", inv="M-14")
        self.plant(where, "MSG-0163.md", vm + "transcript: machine\n", inv="M-14")

    def test_m15_seen(self):
        self.seed()
        self.plant(self.thr2, "MSG-0170.md", "kind: sms\nstatus: sent\nat: 2026-09-27T08:00:00Z\n"
                   "from: +1 555 010 0100\nto: [\"+15550100200\"]\nseen: 2026-09-27T09:00:00Z\n",
                   inv="M-15")
        self.plant(self.thr2, "MSG-0171.md", "kind: sms\nstatus: received\n"
                   "at: 2026-09-27T08:00:00Z\nfrom: \"+15550100200\"\n"
                   "seen: 2026-09-27T07:00:00Z\n", inv="M-15")

    def test_m16_threads(self):
        self.seed()
        self.edit("THR-0001", "participants:\n  - {who: jane@acme.com}\n",
                  "participants:\n  - {who: \"+15550100200\"}\n")
        self.assertIn("M-16", self.errors(env=self.later("2026-09-28")))
        self.edit("THR-0001", "participants:\n  - {who: \"+15550100200\"}\n",
                  "participants:\n  - {who: jane@acme.com}\n  - {who: JANE@acme.com}\n")
        self.assertIn("M-16", self.errors())
        self.edit("THR-0001", "  - {who: JANE@acme.com}\n", "")
        self.edit("THR-0001", "identity: home", "identity: cell")             # a text identity
        self.assertIn("M-16", self.errors())
        self.edit("THR-0001", "identity: cell", "identity: nobody")
        self.assertIn("M-16", self.errors())
        self.edit("THR-0001", "identity: nobody", "identity: home")
        self.edit("THR-0001", "  - {who: jane@acme.com}\n",
                  "  - {who: jane@acme.com}\n  - {who: me@example.org}\n")
        self.assertEqual(self.invariants(), ["M-16"])                          # yourself: a warning
        self.assertEqual(self.errors(), [])

    def test_family_invariants_through_this_module(self):
        self.seed()
        self.plant(self.thr1, "MSG-0180.md", "kind: email\nstatus: received\n"
                   "at: 2026-09-21T08:00:00\nfrom: jane@acme.com\n", inv="F-24")   # no offset
        self.plant(self.thr1, "MSG-0181.md", "kind: fax-ish\nstatus: received\n", inv="F-26")
        self.plant(self.thr1, "MSG-0182.md", "kind: email\nstatus: received\n"
                   "at: 2026-09-21T08:00:00Z\nfrom: CARD-0404\n", inv="F-12")  # crm not mounted
        os.makedirs(os.path.join(self.proj, "crm", "contacts"))
        self.plant(self.thr1, "MSG-0183.md", "kind: email\nstatus: received\n"
                   "at: 2026-09-21T08:00:00Z\nfrom: CARD-0404\n", inv="F-10")  # mounted: resolve
        self.plant(self.thr1, "MSG-0184.md", "kind: email\nstatus: received\n"
                   "at: 2026-09-21T08:00:00Z\nfrom: jane@acme.com\nsubject: x\n", inv="F-06")
        self.plant(self.thr1, "MSG-0185-slug.md", "kind: email\nstatus: received\n"
                   "at: 2026-09-21T08:00:00Z\nfrom: jane@acme.com\n", inv="F-02")

    def test_frozen_tamper_and_same_day(self):
        self.seed()
        self.edit("MSG-0001", "Renew?", "Renew now?")
        self.assertIn("F-19", self.errors())
        self.edit("MSG-0001", "Renew now?", "Renew?")
        self.assertEqual(self.errors(), [])
        # a draft is not frozen: a same-day hand edit is simply true
        self.edit("MSG-0002", "\n\ndraft\n", "\n\na better draft\n")
        self.assertEqual(self.errors(), [])

    def test_hand_moved_thread_and_fix(self):
        self.seed()
        os.makedirs(self.path("_archive", "text"))
        os.rename(self.thr2, self.path("_archive", "text", os.path.basename(self.thr2)))
        self.assertEqual(self.errors(), ["F-16"])
        self.check("--fix")
        self.assertEqual(self.errors(), [])
        fixed = [h for h in self.history() if h[2] == "reconcile"]
        self.assertEqual(sorted((h[1], h[4], h[5]) for h in fixed), [
            ("MSG-0003", "_archive/text/THR-0002", "unknown"),
            ("THR-0002", "_archive/text/THR-0002", "unknown")])

    def test_unfiled_record_and_fix(self):
        self.seed()
        self.write(os.path.join(self.thr2, "MSG-0190.md"), HEAD % "MSG-0190" +
                   "kind: sms\nstatus: received\nat: 2026-09-27T09:00:00Z\n"
                   "from: \"+15550100200\"\n---\n\nhand-made\n")
        self.assertEqual(self.errors(), ["F-15"])
        self.check("--fix")
        self.assertEqual(self.errors(), [])
        self.assertIn("MSG-0191", self.m("log", "--kind", "sms", "--from", JANE_CELL, "--at",
                                         "now").stdout)

    def test_bad_config(self):
        self.config("identities:\n  - {id: home, channel: email, address: \"+15550100100\"}\n")
        self.assertIn("F-20", self.errors())
        self.config("identities:\n  - {id: a, channel: email, address: a@x.org}\n"
                    "  - {id: a, channel: text, address: +1 555 010 0100}\n")
        self.assertIn("F-20", self.errors())
        self.config("channels: [email, pigeon]\n")
        self.assertIn("F-20", self.errors())
        self.config("stale_after: soon\n")
        self.assertIn("F-20", self.errors())
        self.config("bogus: 1\n")
        self.assertIn("F-20", self.errors())
        self.m("draft", "--kind", "sms", "--to", JANE_CELL, expect=2)   # it stops the writers


# ---------------------------------------------------------------------------
# the kind registry: schema and table say the same thing
# ---------------------------------------------------------------------------

class KindTableTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ELEMENT, "content", "schemas", "message.schema.json")) as fh:
            cls.schema = json.load(fh)

    def block(self, kind):
        hits = [b for b in self.schema["allOf"]
                if b.get("if", {}).get("properties", {}).get("kind", {}).get("const") == kind]
        self.assertEqual(len(hits), 1, kind)
        self.assertEqual(hits[0]["x-invariant"], "M-04")
        return hits[0]["then"]

    def test_the_registry_is_the_schema_enum(self):
        self.assertEqual(self.schema["properties"]["kind"]["enum"], list(M.KINDS))
        m06 = [b for b in self.schema["allOf"] if b.get("x-invariant") == "M-06"][0]
        self.assertEqual(m06["if"]["properties"]["kind"]["enum"], list(M.RECORD_ONLY))
        m11 = [b for b in self.schema["allOf"] if b.get("x-invariant") == "M-11"][0]
        self.assertEqual(m11["if"]["properties"]["kind"]["enum"], list(M.COMPOSABLE))
        self.assertEqual(set(M.CHANNELS), set(k.channel for k in M.KINDS.values()))

    def test_each_kind_block_matches_the_table(self):
        for name, k in M.KINDS.items():
            then = self.block(name)
            props = then["properties"]
            self.assertEqual(props["from"]["format"], k.address, name)
            self.assertEqual(props["to"]["items"]["format"], k.address, name)
            for c in ("cc", "bcc"):
                self.assertEqual(c in props, c in k.copies, (name, c))
            if k.roles:
                self.assertEqual(props["attachments"]["items"]["properties"]["role"]["enum"],
                                 list(k.roles), name)
                self.assertEqual(props["attachments"].get("maxItems"), k.max_files, name)
            self.assertEqual(sorted(then.get("required", [])), sorted(k.required), name)
            forbidden = set(b["not"]["required"][0] for b in then.get("allOf", []) if "not" in b)
            expect = set(x for x in ("cc", "bcc") if x not in k.copies)
            expect |= set(x for x in M.EXTRA_KEYS if x not in k.fields)
            if not k.roles:
                expect.add("attachments")
            self.assertEqual(forbidden, expect, name)
            self.assertEqual("at" not in props, k.day_ok, name)

    def good(self, k, status="received"):
        addr = {"email-or-ref": JANE, "phone-or-sender-or-ref": JANE_CELL,
                "phone-or-email-or-ref": JANE, "chat-handle-or-ref": "slack:U1",
                "phone-or-ref": JANE_CELL, "text-or-ref": "Acme, 9 Elm St"}[k.address]
        fm = {"id": "MSG-0001", "created": TODAY, "created_by": "chazz", "updated": TODAY,
              "kind": k.name, "status": status, "from": addr, "to": [addr]}
        if status in M.LIVE:
            fm["at"] = NOW
        if "service" in k.required:
            fm["service"] = "slack"
        if "outcome" in k.required:
            fm["outcome"] = "missed"
        return fm, addr

    def test_the_schema_behaves_as_the_table_says(self):
        extra_values = {"service": "slack", "outcome": "missed", "duration": "PT1M",
                        "transcript": "human", "cc": [JANE], "bcc": [JANE],
                        "attachments": [{"name": "a", "path": "attachments/a", "role": "file"}]}
        for name, k in M.KINDS.items():
            fm, addr = self.good(k)
            self.assertEqual(R.schema_errors(self.schema, fm), [], name)
            for key, value in extra_values.items():
                trial = dict(fm)
                trial[key] = value if key not in ("cc", "bcc") else [addr]
                if key == "attachments" and k.roles:
                    trial[key] = [dict(value[0], role=k.roles[0])]
                allowed = (key in k.fields) or (key in k.copies) or \
                    (key == "attachments" and bool(k.roles))
                self.assertEqual(R.schema_errors(self.schema, trial) == [], allowed,
                                 (name, key, R.schema_errors(self.schema, trial)))
            draft, _ = self.good(k, "draft")
            self.assertEqual(R.schema_errors(self.schema, draft) == [], k.composable, name)
            wrong = dict(fm, **{"from": "not an address at all" if k.address != "text-or-ref"
                                else "NOTE-0001"})
            self.assertNotEqual(R.schema_errors(self.schema, wrong), [], name)


# ---------------------------------------------------------------------------
# install smoke
# ---------------------------------------------------------------------------

class InstallSmokeTests(unittest.TestCase):
    """bin/init into a scratch project, then drive the INSTALLED tools with no env help."""

    def test_install_and_use(self):
        tmp = tempfile.mkdtemp()
        try:
            proj = os.path.join(tmp, "clinic")
            os.makedirs(proj)
            env = {k: v for k, v in os.environ.items() if not k.startswith("RASA_")}
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            env.pop("TZ", None)
            p = subprocess.run([os.path.join(ELEMENT, "bin", "init"), proj], capture_output=True,
                               text=True, env=env)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            for rel in (".claude/messages-rules.md", ".claude/messages-gate.md",
                        ".claude/schemas/messages/message.schema.json",
                        ".claude/schemas/messages/thread.schema.json",
                        ".claude/schemas/messages/config.schema.json",
                        ".claude/skills/message/SKILL.md", ".claude/skills/threads/SKILL.md",
                        ".claude/bin/messages", ".claude/bin/check-messages",
                        ".claude/bin/rasa_messages/records.py", ".claude/bin/rasa_messages/mail.py",
                        ".claude/bin/rasa_messages/FAMILY", "messages/messages.config.yml",
                        "messages/history.tsv", "messages/email/.keep", "messages/text/.keep",
                        "messages/chat/.keep", "messages/voice/.keep", "messages/post/.keep",
                        ".claude/rasa.lock.json"):
                self.assertTrue(os.path.exists(os.path.join(proj, rel)), rel)
            for gone in (".claude/skills/sync", ".claude/skills/promote", ".claude/skills/whoami",
                         ".claude/skills/inbox", ".claude/rasa-identity.md", "kit"):
                self.assertFalse(os.path.exists(os.path.join(proj, gone)), gone)
            with open(os.path.join(proj, ".claude", "rasa.lock.json")) as fh:
                json.load(fh)
            tool = os.path.join(proj, ".claude", "bin", "messages")
            chk = os.path.join(proj, ".claude", "bin", "check-messages")

            def run(*args):
                return subprocess.run([tool] + list(args), capture_output=True, text=True,
                                      cwd=tmp, env=env)
            # run from somewhere else entirely: the tool finds its own project
            r = run("log", "--kind", "voicemail", "--from", "+1 555 010 0200", "--at", "now",
                    "--by", "nurse1")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("messages/voice/THR-0001-1-555-010-0200/MSG-0001.md", r.stdout)
            r = run("draft", "--kind", "sms", "--to", "+1 555 010 0200", "--by", "nurse1")
            self.assertEqual(r.returncode, 1)
            self.assertIn("HARD-STOP", r.stderr)                 # the seeded config declares none
            r = subprocess.run([chk], capture_output=True, text=True, cwd=tmp, env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertFalse(any("__pycache__" in d for d, _, _ in
                                 os.walk(os.path.join(proj, ".claude"))))
            # re-init is non-destructive for everything the project owns
            with open(os.path.join(proj, "messages", "messages.config.yml"), "a") as fh:
                fh.write("default_actor: nurse1\n")
            p = subprocess.run([os.path.join(ELEMENT, "bin", "init"), proj], capture_output=True,
                               text=True, env=env)
            self.assertEqual(p.returncode, 0)
            with open(os.path.join(proj, "messages", "messages.config.yml")) as fh:
                self.assertIn("default_actor: nurse1", fh.read())
            with open(os.path.join(proj, "messages", "history.tsv")) as fh:
                self.assertEqual(len(fh.read().strip().split("\n")), 3)
            r = subprocess.run([chk], capture_output=True, text=True, cwd=tmp, env=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
