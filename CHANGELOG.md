# CHANGELOG — `rasa.module.messages`

Reverse-chronological. Each entry is a version bump.

---

## 0.1.0 — 2026-09-28 — every kind of message, as records (records-family/1)

**New module.** No earlier version exists and nothing has installed it, so
there is nothing to migrate.

Owner direction, 2026-09-28: *"we need modules for each of the following…
notes, calendar events, messages, contacts — as much structure and
organization as we can get"*; then, mid-build, *"module.messages needs to be
able to handle a range of message types like text messages, emails,
voicemails, etc."* and *"record + draft, never send"*. Built together with
`rasa.module.notes` 0.2.0, `rasa.module.schedule` 0.2.0 and
`rasa.module.crm` 0.2.0 as one family, to the `rasa.module.tasks` v1.0.0
standard.

### Family core 1.0.1

Ships `records.py` **1.0.1**, byte-identical in notes, schedule, crm and
messages. Two defects were found while the family was built, and fixed in
all four at once rather than worked around in one:

- a quoted flow-map value holding `,` `[` `]` `{` `}` (a street "Suite 5,
  Floor 2", a name "Doe, Jane") was written but could not be read back —
  found independently by the crm and messages builds;
- a file too broken to parse was also reported as deleted (F-17); it is now
  reported only for what is wrong with it.

Every write path now proves the rendered record reads back exactly as
written (`R.verify_roundtrip`) before it touches the disk. `bin/init` never
copies bytecode, and the release gate fails if any sits under `content/` or
`seed/`. Integration run 2026-09-29: all four family modules plus
`rasa.module.tasks` installed into one project, cross-linked records, every
validator clean, a hand-deleted contact caught by its referrers.

### Added

- **Ten kinds in five channels** — `email` (email); `sms`, `mms`,
  `imessage`, `rcs` (text); `chat` (chat, with a `service`); `call`,
  `voicemail` (voice — recorded, never drafted); `letter`, `fax` (post).
  Each kind's field law is an `allOf` + `if`/`then` block in
  `message.schema.json`, tagged with the invariant it is reported under;
  `model.py` holds the same table for the tool, and a test proves they agree.
- **Threads and messages as files** —
  `messages/<channel>/THR-nnnn-<slug>/THR-nnnn.md` plus one `MSG-nnnn.md`
  per message and an `attachments/` directory. Status `draft → sent |
  discarded`, `received` arrives received; frozen once sent or received
  (only `updated` and `seen` change). Addresses stored literally; CARD ids
  where no address is known.
- **The identity hard-stop** — `draft`, `reply`, `mark-sent`, `log --sent`
  and importing messages you sent refuse until `identities` declares one for
  the channel; `from` on a draft or a sent composed message must be one
  (M-11).
- **Recording what happened elsewhere** — `messages log`: calls with
  outcome and length, voicemails with the audio copied in and transcript
  provenance, letters and faxes with scans and pages.
- **File interop, offline** — `import` of `.eml` files and mbox (Message-ID
  dedupe, In-Reply-To/References threading, offsets kept, HTML-only mail
  made plain, attachments copied) and of SMS Backup & Restore XML (texts,
  picture messages with their media, and the call log; UTC times; stable
  synthesized ids for dedupe). `export-eml` hands an email draft to your own
  mail program. Imports are one act: checked in full, then written, or
  nothing is.
- **Derived views** — `threads --needs-reply | --unseen | --waiting |
  --drafts | --with | --label | --channel | --kind`, `calls [--missed]`,
  `voicemails [--unheard]`, `timeline --with CARD|address` (every kind, every
  channel, one chronology), `show`, `search`; people resolved through
  `rasa.module.crm` cards at view time, read-only.
- **Two programs** — `.claude/bin/messages` (15 write verbs, 10 read verbs)
  and `.claude/bin/check-messages` (F-01…F-27 family invariants + M-01…M-16;
  `--fix` repairs history drift and never invents a person, a date or a
  status).
- **JSON Schemas** for the thread, the message and the config, installed
  under `.claude/schemas/messages/`.
- **The seam** — `.claude/messages-gate.md` (judgement: consent, what is
  kept about other people and for how long, who the assistant may write as,
  what "needs a reply" means) and `messages/messages.config.yml` (checked).
- **Skills** `/message` and `/threads` (not `/inbox`: `rasa.domain.code`
  ships one).
- **Release gate** — `bin/check-manifest`, 14 checks (the nine from
  `rasa.module.tasks` plus family lockstep, offline, schema well-formedness,
  executables, and the test suite with an install smoke).
- **Tests** — `tests/test_records_core.py` (the family core; identical in
  all four modules) and `tests/test_messages_cli.py` (walkthroughs for every
  kind, imports of every format, every refusal including the hard-stop, a
  test per invariant, the schema/table agreement, install smoke).

### Known limits (honest)

- **A records.py defect found here was fixed in the core, family-wide.**
  The family parser (core 1.0.0) honoured a quote only at the start of a flow
  item, so a quoted `{k: v}` value holding `,` `[` `]` `{` `}` — the core's
  own `{name: "Doe, Jane"}` — was written but could not be read back. This
  build and the crm build both hit it and worked around it with `\uXXXX`
  escapes; core 1.0.1 fixes `_split_flow` in all four modules at once, the
  workarounds are gone, and every write now proves the record reads back
  exactly as written (`R.verify_roundtrip`) before it touches the disk.
- Imports hold one file in memory while they plan; a very large phone backup
  with media needs the memory to match.
- Phone numbers are matched by their last ten digits — right for the
  numbering plans that have ten, approximate elsewhere.
