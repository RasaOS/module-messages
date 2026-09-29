# -*- coding: utf-8 -*-
"""Tests for records.py — the records-family/1 core.

This file is BYTE-IDENTICAL in all four family modules (it tests the shared
core, not the module). It locates the core under content/bin/rasa_*/.
Run:  python3 -B -m unittest discover -s tests
"""

import datetime as dt
import glob
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ELEMENT = os.path.dirname(HERE)
_CORE = glob.glob(os.path.join(ELEMENT, "content", "bin", "rasa_*", "records.py"))
assert len(_CORE) == 1, "expected exactly one family core under content/bin/rasa_*/"
_spec = importlib.util.spec_from_file_location("records_core_under_test", _CORE[0])
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)


def parse(text):
    return R.parse_mapping(text.split("\n"), 1)


class GrammarTests(unittest.TestCase):

    def test_scalars_lists_maps_blocks(self):
        v, info, errs = parse(
            "id: CARD-0042\n"
            "# a whole-line comment\n"
            "tags: [family, \"a, b\", 'it''s']\n"
            "name: {given: Jane, family: Doe}\n"
            "email:\n"
            "  - {label: work, value: jane@acme.com, pref: true}\n"
            "  - {label: home, value: \"j@example.org\"}\n"
            "empty: []\n"
            "x-legacy: \"77\"\n")
        self.assertEqual(errs, [])
        self.assertEqual(v["id"], "CARD-0042")
        self.assertEqual(v["tags"], ["family", "a, b", "it's"])
        self.assertEqual(v["name"], {"given": "Jane", "family": "Doe"})
        self.assertEqual(v["email"][0], {"label": "work", "value": "jane@acme.com", "pref": "true"})
        self.assertEqual(v["email"][1]["value"], "j@example.org")
        self.assertEqual(v["empty"], [])
        self.assertEqual(v["x-legacy"], "77")
        self.assertEqual(info["lines"]["email"], 5)

    def test_every_value_is_a_string(self):
        v, _, errs = parse("pinned: true\ncount: 5\nwhen: 2026-09-28\n")
        self.assertEqual(errs, [])
        self.assertEqual((v["pinned"], v["count"], v["when"]), ("true", "5", "2026-09-28"))

    def test_block_list_of_scalars(self):
        v, _, errs = parse("aliases:\n  - one\n  - \"two: three\"\n")
        self.assertEqual(errs, [])
        self.assertEqual(v["aliases"], ["one", "two: three"])

    def assertRejects(self, text, fragment):
        _, _, errs = parse(text)
        self.assertTrue(errs, "expected a grammar error for %r" % text)
        self.assertIn(fragment, " | ".join(m for _, m in errs))

    def test_rejections(self):
        self.assertRejects("a: null\n", "forbidden")
        self.assertRejects("a: ~\n", "forbidden")
        self.assertRejects("a: |\n", "block scalars")
        self.assertRejects("a: [x, [y]]\n", "nest")
        self.assertRejects("a: [x, ]\n", "empty item")
        self.assertRejects("a: b # comment\n", "' #'")
        self.assertRejects("a: b: c\n", "': '")
        self.assertRejects("a: \"open\n", "unterminated")
        self.assertRejects("a:\n", "no value")
        self.assertRejects("a: 1\na: 2\n", "duplicate key")
        self.assertRejects("  a: 1\n", "indented")
        self.assertRejects("A: 1\n", "not a legal")
        self.assertRejects("a: {}\n", "empty map")
        self.assertRejects("a: {k: v, k: w}\n", "duplicate key 'k'")
        self.assertRejects("a: @acct-x\n", "double-quoted")
        self.assertRejects("a: &anchor x\n", "anchors and aliases")
        self.assertRejects("  - x\n", "no 'key:'")
        self.assertRejects("a: x]\n", "whole flow collection")
        self.assertRejects("a: [x,y]\nb: \"bad \\q\"\n", "unsupported escape")

    def test_frontmatter_split(self):
        fm, first, body, bfirst, err = R.split_frontmatter("---\na: 1\n---\n\n# T\n")
        self.assertIsNone(err)
        self.assertEqual((fm, first, body, bfirst), (["a: 1"], 2, "\n# T\n", 4))
        self.assertIsNotNone(R.split_frontmatter("a: 1\n")[4])
        self.assertIsNotNone(R.split_frontmatter("---\na: 1\n")[4])


class SerializerTests(unittest.TestCase):
    SCHEMA = {
        "x-key-order": ["id", "pinned", "count", "phone", "tags", "email", "name"],
        "properties": {
            "id": {"type": "string"}, "pinned": {"type": "boolean"},
            "count": {"type": "integer"}, "phone": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "email": {"type": "array", "items": {
                "type": "object", "x-key-order": ["label", "value", "pref"],
                "properties": {"label": {"type": "string"}, "value": {"type": "string"},
                               "pref": {"type": "boolean"}}}},
            "name": {"type": "object", "x-key-order": ["given", "family"],
                     "properties": {"given": {"type": "string"},
                                    "family": {"type": "string"}}}}}

    def test_roundtrip_is_stable(self):
        src = ("---\nname: {family: Doe, given: Jane}\nid: CARD-0001\npinned: true\n"
               "count: 3\nphone: \"5550100\"\ntags: [a, \"b, c\", \"yes\"]\n"
               "email:\n  - {value: \"x@y.org\", pref: true, label: work}\n"
               "x-keep: [1, 2]\n---\n\n# Jane\n")
        fm_lines, first, body, _, _ = R.split_frontmatter(src)
        v, info, errs = R.parse_mapping(fm_lines, first)
        self.assertEqual(errs, [])
        out = R.render_record(v, body, self.SCHEMA, info)
        self.assertEqual(out.split("\n")[1:4], ["id: CARD-0001", "pinned: true", "count: 3"])
        self.assertIn('phone: "5550100"', out)            # numeric-looking text stays text
        self.assertIn('tags: [a, "b, c", "yes"]', out)    # flow chars + yaml words quoted
        self.assertIn("  - {label: work, value: x@y.org, pref: true}", out)
        self.assertIn("name: {given: Jane, family: Doe}", out)
        self.assertIn("x-keep: [1, 2]", out)              # x- keys re-emitted verbatim
        # and it parses back to the same values
        fm2, first2, body2, _, _ = R.split_frontmatter(out)
        v2, _, errs2 = R.parse_mapping(fm2, first2)
        self.assertEqual(errs2, [])
        self.assertEqual(json.dumps(v2, sort_keys=True), json.dumps(v, sort_keys=True))
        self.assertEqual(body2, body)

    def test_quoted_map_values_with_flow_characters(self):
        # core 1.0.1: found by the crm builder — "Suite 5, Floor 2" was written, not read back
        v, _, errs = parse('addr: {street: "Suite 5, Floor 2", code: "62701"}\n'
                           "items:\n  - {name: 'a, b [c] {d}', n: \"1\"}\n")
        self.assertEqual(errs, [])
        self.assertEqual(v["addr"], {"street": "Suite 5, Floor 2", "code": "62701"})
        self.assertEqual(v["items"], [{"name": "a, b [c] {d}", "n": "1"}])
        fm = {"id": "CARD-0001", "address": [{"label": "work", "street": "Suite 5, Floor 2"}],
              "pinned": True}
        text = R.render_record(fm, "\n# X\n", SerializerTests.SCHEMA)
        self.assertEqual(R.verify_roundtrip(text, fm), text)
        with self.assertRaises(R.Refusal):
            R.verify_roundtrip(text, dict(fm, id="CARD-0002"))

    def test_quoting_rules(self):
        q = R.needs_quote
        for s in ("", " x", "@acct-a", "#x", "- x", "a: b", "a #b", "true", "No", "12",
                  "1.5", "0x1F", "12:30", "x]"):
            self.assertTrue(q(s), s)
        for s in ("plain text", "jane@acme.com", "mailto:j@x.com", "2026-09-28",
                  "2026-09-28T14:00:00-04:00", "-PT15M", "+1 555 010 0100", "a-b", "09:00-17:00",
                  "http://x.org/a#b"):
            self.assertFalse(q(s), s)
        self.assertTrue(q("a,b", flow=True))
        self.assertFalse(q("a,b", flow=False))
        with self.assertRaises(ValueError):
            R.dump_scalar("two\nlines")


class TimeTests(unittest.TestCase):

    def test_dates_and_datetimes(self):
        self.assertEqual(R.parse_date("2026-02-28"), dt.date(2026, 2, 28))
        self.assertIsNone(R.parse_date("2026-02-30"))
        self.assertIsNotNone(R.parse_datetime("2026-09-28T14:00:00-04:00"))
        self.assertIsNotNone(R.parse_datetime("2026-09-28T18:00Z"))
        self.assertIsNone(R.parse_datetime("2026-09-28T14:00:00"))   # naive is refused
        self.assertTrue(R.parse_partial_date("--02-29"))
        self.assertFalse(R.parse_partial_date("--02-30"))
        self.assertEqual(R.format_datetime(dt.datetime(2026, 9, 28, 18, 0, tzinfo=dt.timezone.utc)),
                         "2026-09-28T18:00:00Z")

    def test_durations(self):
        self.assertEqual(R.parse_duration("-PT15M"), -dt.timedelta(minutes=15))
        self.assertEqual(R.parse_duration("P1W"), dt.timedelta(weeks=1))
        self.assertEqual(R.parse_duration("PT1H30M"), dt.timedelta(minutes=90))
        for bad in ("P", "PT", "P1Y", "15M", "PT1H30"):
            self.assertIsNone(R.parse_duration(bad), bad)
        for td in (dt.timedelta(minutes=-15), dt.timedelta(days=1, hours=2), dt.timedelta(0)):
            self.assertEqual(R.parse_duration(R.format_duration(td)), td)

    def test_relative_days(self):
        base = dt.date(2026, 9, 28)  # a Monday
        self.assertEqual(R.resolve_day("today", base), base)
        self.assertEqual(R.resolve_day("tomorrow", base), dt.date(2026, 9, 29))
        self.assertEqual(R.resolve_day("+3d", base), dt.date(2026, 10, 1))
        self.assertEqual(R.resolve_day("-1w", base), dt.date(2026, 9, 21))
        self.assertEqual(R.resolve_day("mon", base), dt.date(2026, 10, 5))  # strictly after
        self.assertEqual(R.resolve_day("friday", base), dt.date(2026, 10, 2))
        with self.assertRaises(R.Refusal):
            R.resolve_day("someday", base)

    def test_resolve_when_and_dst(self):
        os.environ["RASA_TODAY"] = "2026-09-28"
        try:
            w = R.resolve_when("tomorrow 2pm", "America/New_York")
            self.assertEqual(R.format_datetime(w), "2026-09-29T14:00:00-04:00")
            w = R.resolve_when("2026-12-01 09:30", "America/New_York")
            self.assertEqual(R.format_datetime(w), "2026-12-01T09:30:00-05:00")
            w = R.resolve_when("2026-09-28T18:00:00Z", "America/New_York")
            self.assertEqual(R.format_datetime(w), "2026-09-28T14:00:00-04:00")
            with self.assertRaises(R.Refusal):   # 02:30 does not exist on 2026-03-08 in New York
                R.resolve_when("2026-03-08 02:30", "America/New_York")
            with self.assertRaises(R.Refusal):
                R.resolve_when("tomorrow 14:00", "Mars/Olympus")
        finally:
            del os.environ["RASA_TODAY"]

    def test_clock_overrides(self):
        os.environ["RASA_NOW"] = "2026-09-28T23:30:00-04:00"
        try:
            self.assertEqual(R.stamp(), "2026-09-29T03:30:00Z")
            self.assertEqual(R.today(), dt.date(2026, 9, 29))
        finally:
            del os.environ["RASA_NOW"]


class IdTests(unittest.TestCase):

    def test_ids(self):
        self.assertEqual(R.format_id("NOTE", 7), "NOTE-0007")
        self.assertEqual(R.parse_id("NOTE-0007"), ("NOTE", 7))
        self.assertEqual(R.parse_id("NOTE-12345"), ("NOTE", 12345))
        self.assertIsNone(R.parse_id("NOTE-7"))
        self.assertEqual(R.parse_id("acct-acme-co"), ("acct", "acme-co"))
        self.assertEqual(R.parse_id("TASK-LIT-042"), ("TASK", "TASK-LIT-042"))
        self.assertEqual(R.ref_module("CARD-0001"), "crm")
        self.assertEqual(R.ref_module("MSG-0001"), "messages")
        self.assertFalse(R.is_family_ref("CON-0001"))   # contracts' prefix is not ours
        self.assertEqual(R.slugify("Ünïcode — Title: v2!"), "unicode-title-v2")
        self.assertEqual(R.slugify("!!!"), "untitled")
        self.assertLessEqual(len(R.slugify("word " * 40)), 60)


class SchemaTests(unittest.TestCase):
    S = {
        "type": "object", "additionalProperties": False,
        "patternProperties": {"^x-": {}},
        "required": ["id"],
        "properties": {
            "id": {"type": "string", "pattern": "^EVT-"},
            "status": {"enum": ["tentative", "confirmed", "cancelled"]},
            "pinned": {"type": "boolean", "const": "true"},
            "start": {"type": "string", "format": "date-time"},
            "tz": {"type": "string", "format": "iana-tz"},
            "tags": {"type": "array", "uniqueItems": True, "items": {"type": "string",
                                                                      "format": "slug"}},
            "attendees": {"type": "array", "items": {
                "type": "object", "required": ["who"], "additionalProperties": False,
                "properties": {"who": {"type": "string", "format": "email-or-ref"}}}},
        },
        "allOf": [{"if": {"properties": {"status": {"const": "cancelled"}},
                          "required": ["status"]},
                   "then": {"not": {"required": ["pinned"]},
                            "x-not-message": "a cancelled event cannot be pinned"}}],
    }

    def errs(self, value):
        return R.schema_errors(self.S, value)

    def test_valid(self):
        self.assertEqual(self.errs({"id": "EVT-0001", "status": "confirmed", "pinned": "true",
                                    "start": "2026-09-28T14:00:00-04:00",
                                    "tz": "America/New_York", "tags": ["a-b"],
                                    "attendees": [{"who": "CARD-0001"}, {"who": "a@b.co"}],
                                    "x-anything": "ok"}), [])

    def test_invalid(self):
        e = self.errs({"status": "done", "bogus": "1", "start": "2026-09-28T14:00:00",
                       "tz": "Nowhere/City", "tags": ["a", "a", "Bad"],
                       "attendees": [{"who": "not an address"}, {"x": "y"}]})
        invs = sorted(set(i for i, _, _ in e))
        self.assertIn("F-06", invs)   # unknown top-level key
        self.assertIn("F-24", invs)   # naive datetime
        self.assertIn("F-25", invs)   # bad tz
        self.assertIn("F-26", invs)
        text = " | ".join(p + " " + m for _, p, m in e)
        for frag in ("id is required", "'done' is not one of", "lists a more than once",
                     "attendees[0].who", "attendees[1].who is required"):
            self.assertIn(frag, text)

    def test_conditionals(self):
        e = self.errs({"id": "EVT-0001", "status": "cancelled", "pinned": "true"})
        self.assertEqual([m for _, _, m in e], ["a cancelled event cannot be pinned"])
        s = {"anyOf": [{"format": "email"}, {"format": "phone"}],
             "x-anyOf-message": "an email or a phone"}
        self.assertEqual(R.schema_errors(s, "+1 555 010 0100"), [])
        self.assertEqual(R.schema_errors(s, "nope")[0][2], "an email or a phone")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


class RecordAndStoreTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_read_record_and_h1(self):
        p = os.path.join(self.tmp, "NOTE-0001-x.md")
        write(p, "---\nid: NOTE-0001\n---\n\n# Title\n\n```\n# not a title\n```\nsee [[CARD-0002]] "
                 "and [[CARD-0003|Bob]] and @acct-acme and [[plain page]]\n")
        rec = R.read_record(p, "inbox/NOTE-0001-x.md")
        self.assertTrue(rec.parsed)
        self.assertEqual((rec.h1, rec.h1_count, rec.h1_first), ("Title", 1, True))
        self.assertEqual(rec.dir, "inbox")
        self.assertEqual([r for r, _ in R.body_refs(rec)], ["CARD-0002", "CARD-0003", "acct-acme"])

    def test_file_level_problems(self):
        p = os.path.join(self.tmp, "a.md")
        write(p, "---\r\nid: NOTE-0001\r\n---\r\n\r\n# T")
        rec = R.read_record(p, "a.md")
        msgs = " | ".join(m for _, _, m in rec.problems)
        self.assertIn("CRLF", msgs)
        self.assertIn("newline", msgs)

    def test_common_checks(self):
        schema = {"x-id-prefix": "NOTE", "x-title": "required", "type": "object",
                  "properties": {"id": {"type": "string"}, "created": {"type": "string"},
                                 "updated": {"type": "string"}, "created_by": {}},
                  "additionalProperties": False}
        p = os.path.join(self.tmp, "NOTE-0001-Bad_Slug.md")
        write(p, "---\nid: NOTE-0001\ncreated: 2026-09-28\nupdated: 2026-09-27\n"
                 "created_by: zed\n---\nintro\n# T\n# U\n")
        rec = R.read_record(p, "NOTE-0001-Bad_Slug.md")
        rep = R.Report("t")
        R.check_record_common(rec, schema, rep, "x", actors=["chazz"], today_=dt.date(2026, 9, 28))
        invs = sorted(f.inv for f in rep.findings)
        self.assertEqual(invs, ["F-02", "F-04", "F-22", "F-23"])

    def test_history_append_and_load(self):
        h = os.path.join(self.tmp, "history.tsv")
        os.environ["RASA_NOW"] = "2026-09-28T14:00:00Z"
        try:
            R.append_history(h, [("NOTE-0001", "new", "-", "inbox", "chazz", "tab\there")])
            os.environ["RASA_NOW"] = "2026-09-28T13:00:00Z"   # the clock went backwards
            R.append_history(h, [("NOTE-0001", "move", "inbox", "work", "chazz", "")])
        finally:
            del os.environ["RASA_NOW"]
        rows, problems, exists = R.load_history(h)
        self.assertTrue(exists)
        self.assertEqual(problems, [])
        self.assertEqual([r.at for r in rows], ["2026-09-28T14:00:00Z"] * 2)  # clamped
        self.assertEqual(rows[0].note, "tab here")
        self.assertEqual(R.last_place(rows), "work")
        with open(h, "a") as fh:
            fh.write("2026-01-01T00:00:00Z\tNOTE-0001\tmove\twork\tinbox\tchazz\t\n")
        _, problems, _ = R.load_history(h)
        self.assertEqual([p[0] for p in problems], ["F-14"])

    def test_frozen_digest_ignores_format_and_mutable(self):
        a = R.frozen_digest({"id": "DEC-0001", "updated": "2026-09-28"}, "\n# X\n", ["updated"])
        b = R.frozen_digest({"updated": "2026-10-01", "id": "DEC-0001"}, "# X", ["updated"])
        c = R.frozen_digest({"id": "DEC-0001"}, "# Y", ["updated"])
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_lock_and_stale_lock(self):
        root = os.path.join(self.tmp, "notes")
        with R.Lock(root):
            self.assertTrue(os.path.exists(os.path.join(root, ".state", "lock")))
            with self.assertRaises(R.EnvError):
                with R.Lock(root, timeout=0.2):
                    pass
        self.assertFalse(os.path.exists(os.path.join(root, ".state", "lock")))
        write(os.path.join(root, ".state", "lock"), "999999 1\n")   # dead pid, ancient
        with R.Lock(root, timeout=0.5):
            pass
        with open(os.path.join(root, ".state", ".gitignore")) as fh:
            self.assertIn("*", fh.read())

    def test_family_index_and_refs(self):
        write(os.path.join(self.tmp, "crm", "contacts", "CARD-0001-jane.md"), "---\n---\n")
        write(os.path.join(self.tmp, "crm", "accounts", "acct-acme.md"), "---\n---\n")
        write(os.path.join(self.tmp, "crm", "contacts", "_archive", "CARD-0002.md"), "---\n---\n")
        write(os.path.join(self.tmp, "tasks", "backlog", "TASK-LIT-042-amend.md"), "---\n---\n")
        write(os.path.join(self.tmp, "notes", "inbox", "README.md"), "x\n")
        idx = R.family_index(self.tmp)
        self.assertEqual(sorted(idx), ["CARD-0001", "CARD-0002", "TASK-LIT-042", "acct-acme"])
        self.assertTrue(idx["CARD-0002"]["archived"])
        mounted = R.mounted_modules(self.tmp)
        self.assertEqual(mounted, ["notes", "crm", "tasks"])
        rep = R.Report("t")
        R.check_ref("CARD-0001", idx, mounted, rep, "w", 1, key="about")
        R.check_ref("CARD-0009", idx, mounted, rep, "w", 1, key="about")
        R.check_ref("CARD-0009", idx, mounted, rep, "w", 1, body=True)
        R.check_ref("EVT-0001", idx, mounted, rep, "w", 1, key="about")
        self.assertEqual([(f.severity, f.inv) for f in rep.findings],
                         [("ERROR", "F-10"), ("WARN", "F-11"), ("INFO", "F-12")])

    def test_next_number_respects_history(self):
        rows = [R.HistoryRow("t", "NOTE-0009", "new", "-", "inbox", "a", "")]
        self.assertEqual(R.next_number("NOTE", ["NOTE-0002"], rows), 10)
        self.assertEqual(R.next_number("DEC", ["NOTE-0002"], rows), 1)

    def test_actor_resolution(self):
        self.assertEqual(R.resolve_actor("chazz", {}), "chazz")
        self.assertEqual(R.resolve_actor(None, {"default_actor": "claude"}), "claude")
        with self.assertRaises(R.Refusal):
            R.resolve_actor(None, {})
        with self.assertRaises(R.Refusal):
            R.resolve_actor("zed", {"actors": ["chazz"]})

    def test_doc_block(self):
        t = "a\n%s\nfamily text\n%s\nb\n" % (R.DOC_BEGIN, R.DOC_END)
        self.assertTrue(R.doc_block(t).startswith(R.DOC_BEGIN))
        self.assertIsNone(R.doc_block("nothing"))


class YamlInteropTests(unittest.TestCase):
    """If PyYAML happens to be installed, prove the grammar really is a YAML subset."""

    def test_pyyaml_reads_what_we_write(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML not installed; the subset claim is proven by construction")
        import yaml
        text = ("id: CARD-0001\ntags: [a, \"b, c\"]\nphone: \"5550100\"\n"
                "email:\n  - {label: work, value: jane@acme.com}\nwhen: -PT15M\n")
        data = yaml.safe_load(text)
        self.assertEqual(data["tags"], ["a", "b, c"])
        self.assertEqual(data["phone"], "5550100")
        self.assertEqual(data["email"][0]["value"], "jane@acme.com")
        self.assertEqual(data["when"], "-PT15M")

    def test_pyyaml_agrees_on_every_tricky_scalar(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        tricky = ["plain", "true", "False", "yes", "No", "on", "null", "~", "12", "-3", "1.5",
                  "1e3", "0x1F", "0o17", ".inf", "12:30", "1:2:3", "@acct-x", "#hash", "a #b",
                  "a: b", "ends:", "- dash", "-PT15M", "? q", "?q", ":colon", "'single'",
                  '"double"', "back\\slash", "a,b", "[x]", "{y}", "x]", "  lead", "trail  ",
                  "%pct", "&amp", "*star", "!bang", "|pipe", ">gt", "`tick", "mailto:a@b.co",
                  "http://x.org/a#b", "+1 555 010 0100", "5550100", "2026-09-28",
                  "09:00-17:00", "Ünïcode ✓", "tab\there"]
        for s in tricky:
            s = s.replace("\\t", "\t")
            for flow in (False, True):
                out = R.dump_scalar(s, flow=flow)
                doc = ("k: [%s]" % out) if flow else ("k: %s" % out)
                got = yaml.safe_load(doc)["k"]
                got = got[0] if flow else got
                if s == "2026-09-28":
                    got = str(got)           # YAML reads an ISO date as a date; same value
                self.assertEqual(got, s, "%r -> %r (flow=%s) -> %r" % (s, out, flow, got))
                v, _, errs = R.parse_mapping([doc], 1)
                self.assertEqual(errs, [], "%r: our parser rejects our own output %r" % (s, doc))
                ours = v["k"][0] if flow else v["k"]
                self.assertEqual(ours, s)
            # ... and as a flow-map value, in a key line and in a block-list item
            m = R.dump_scalar(s, flow=True)
            for doc in ("k: {a: %s, b: z}" % m, "k:\n  - {a: %s, b: z}" % m):
                got = yaml.safe_load(doc)["k"]
                got = got[0] if isinstance(got, list) else got
                if s == "2026-09-28":
                    got = dict(got, a=str(got["a"]))
                self.assertEqual(got, {"a": s, "b": "z"}, "%r in %r" % (s, doc))
                v, _, errs = R.parse_mapping(doc.split("\n"), 1)
                self.assertEqual(errs, [], "%r: map value %r" % (s, doc))
                ours = v["k"][0] if isinstance(v["k"], list) else v["k"]
                self.assertEqual(ours, {"a": s, "b": "z"})


if __name__ == "__main__":
    unittest.main()
