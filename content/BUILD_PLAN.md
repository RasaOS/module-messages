# `rasa.module.messages` — design record

Author-time document; not installed (`opt-in`). The installed law is
`content/messages-rules.md`; this file is the *why* behind it.

---

## 1. Why this module, and why the kind is the axis

**Owner direction, 2026-09-28:** *"we need modules for each of the following…
notes, calendar events, messages, contacts — we need as much structure and
organization as we can get."* Messages had no home: no RasaOS module kept a
person's conversations with other people. Mid-build the owner sharpened it:
*"module.messages needs to be able to handle a range of message types like
text messages, emails, voicemails, etc."* and *"record + draft, never send"*.

Those two sentences set the design:

1. **The kind is the load-bearing axis, not an attribute.** An email, a text,
   a call and a voicemail differ in *what they are made of*: a call has an
   outcome and a length and no body worth the name; a voicemail is an audio
   file whose text is someone's (or something's) transcription; an `sms`
   cannot carry a file; a chat message only means something with its
   service. A single loose "message" record with optional everything would
   let every kind carry every field and validate nothing. So each kind has
   its own field law, written as JSON Schema (`allOf` + `if`/`then`, one
   block per kind), and the kinds are module vocabulary — a project cannot
   add a kind without adding its law.
2. **Record and draft; never send.** These records are other people's words.
   A module that can send can send the wrong thing to the wrong person, and
   its credentials become the project's problem. So the release gate forbids
   any network or process import, drafts leave only as an `.eml` for the
   person's own mail program, and the send is recorded after the fact.
   Sending, fetching and syncing belong to Connections (canon task SA-031).

**Distinct from `rasa.module.chat`.** That module is the assistant's own
fast-assist chat surface with a user profile. This one keeps the
conversations a person has with *other people*. Different subject, different
records; no overlap in files or skills.

## 2. The records family

messages 0.1.0 is one of four modules built together to one standard,
**records-family/1**:

| module | concern | records |
|---|---|---|
| `rasa.module.notes` 0.2.0 | notes + decisions | NOTE, DEC |
| `rasa.module.schedule` 0.2.0 | the calendar | EVT |
| `rasa.module.crm` 0.2.0 | the address book + accounts + sales funnel | CARD, acct-, LEAD |
| `rasa.module.messages` 0.1.0 | email, texts, chat, calls, voicemail, letters, faxes | THR, MSG |

The shared conventions (strict-YAML frontmatter, JSON Schema field law, ids
as the only join key, soft family-wide references, the history log as audit
trail and id-retirement ledger, `updated` as a fact, frozen records, archive
never delete, declare-to-constrain, offline) are stated once in the family
block and enforced once by `records.py`, both identical in all four modules.

## 3. Messages-specific decisions

| decision | why |
|---|---|
| Ten kinds in five channels; the channel is a directory | a thread is one conversation on one medium; the directory already says which (one fact, one home) |
| A thread may mix kinds only within its channel | SMS→MMS fallback and a voice thread of calls + voicemails with one person are real; an email inside a text thread is not |
| Thread = directory `THR-nnnn-<slug>/` holding `THR-nnnn.md` + `MSG-nnnn.md` files + `attachments/` | a conversation and its files move (archive) as one unit; the slug stays cosmetic because the record file is named by id alone |
| Messages have no title (`x-title: none`) | the body *is* the message; the thread carries the subject |
| History place = `<channel>/THR-nnnn` (slug stripped) | renaming a thread changes nothing the log depends on; a message hand-moved into another thread is caught (F-16) |
| Status `draft → sent \| discarded`; `received` from the start; nothing else moves | the only honest transitions; frozen once it happened (F-19), never reopened (M-08) |
| Initial status recorded in the filing line's note (`status=draft`) | the history's `to` column must carry the place on a filing line; the note carries the status so M-07 can compare the file with the log from the first line |
| Direction derived from status | one fact, one home |
| `at` keeps the offset it happened in; a letter's may be a day | the offset is part of the fact; a letter arrives on a day, and inventing a time would be a lie |
| Addresses stored literally; crm resolves at view time | the address used is the fact, and a card may later gain or lose addresses; copying names in would store a fact twice |
| A CARD id is accepted where no address is known | a letter from someone whose address you don't have still names a person |
| No record that is not a person is ever an address | `to: [NOTE-0001]` passes every "-or-ref" format; a schema `not` pattern closes it |
| Identities in config; the hard-stop on every write-as-you verb | who you may write as is the one inference refused; declaring makes it possible, the prose seam says whether it is allowed |
| Identity chosen by the kind's address form | the post channel carries letters (a written address) and faxes (a number) |
| `seen` is the only triage state stored | "needs reply", "unseen", "waiting" are derived; `seen` is a fact about the person (they read it), mutable on a frozen message |
| Answered calls do not "need a reply" | the conversation was had; everything else received does, pending the seam's definition |
| Missed calls include calls that went to voicemail | nobody picked up either way |
| `outcome` adds `blocked` to DESIGN's list | the phone call log records blocked calls as their own type; folding them into `declined` would misstate who refused |
| Incoming texts may come from a sender name (`AMAZON`) | short codes and alphanumeric sender ids are real addresses on a phone; rejecting them would fail most real backups |
| Imports sorted by time, planned in full, then written | parents land before replies (threading works whatever the file order), and a hard-stop halfway writes nothing |
| Import dedupe by `external_id`; SMS/call ids synthesized from stable fields | re-importing the same backup is safe |
| `export-eml` sets `X-Unsent: 1`, no Date, no Message-ID | the person's mail program opens it as a draft and stamps its own identity headers when it sends |
| `timezone` in config (optional; DESIGN did not list it) | wall-clock input ("2026-09-28 14:00") needs a zone; times with an offset and `now` need none, so no hard-stop |
| `where`, `pin`/`unpin`, `about`, `rename` verbs (beyond DESIGN's list) | the thread keys `pinned` and `about` need a way to be set; `rename` keeps a text thread's people-label honest |

### The core defect this module works around

**Found here, fixed in the core.** records.py 1.0.0 honoured a quote only at
the start of a flow item, so a comma or bracket inside a quoted map value was
split or rejected — and the core's own serializer writes exactly such values
(`{name: "Doe, Jane"}`). Messages writes names, postal addresses and file
names into maps, so it hit this at once, as did crm. Core 1.0.1 (all four
modules at once) opens a quote after a map key's `key: ` too, and every write
path now calls `R.verify_roundtrip` so a record that would not read back
exactly is refused before it reaches the disk.

## 4. Invariant map

| id | rule | where |
|---|---|---|
| F-01…F-27 | the family conventions | `records.py`, family block |
| M-01 | layout: records only inside `<channel>/THR-nnnn[-slug]/`; exactly one thread record there, named `THR-nnnn.md` with the directory's id; every message beside its thread record | `validate.check_layout` |
| M-02 | the channel directory is one of the five, and declared when `channels` is | `validate.check_layout` |
| M-03 | a message's kind belongs to its thread's channel | `validate.check_message` |
| M-04 | per-kind field law (addresses, copies, attachments and roles, service, outcome, duration, transcript, day-only `at` for letters; chat handles on the message's service) | schema `allOf` blocks + `validate.check_message` |
| M-05 | `at` exactly when sent/received; received has `from`; draft/sent has `to` | schema `allOf` blocks |
| M-06 | only composable kinds are drafts or discarded | schema `allOf` block |
| M-07 | status moves only draft→sent / draft→discarded, logged; file status = logged status; never filed discarded | `validate.check_lifecycle` |
| M-08 | a message the cache knows as sent/received is never reopened | `validate.run` (digest table) |
| M-09 | `in_reply_to`: same thread, happened, earlier, not itself | `validate.check_replies` |
| M-10 | `external_id` unique | `validate.check_external_ids` |
| M-11 | `from` on a draft / sent composed message is a declared identity of its channel | schema `allOf` (presence) + `validate.check_message` |
| M-12 | attachments exist inside the thread's `attachments/`; orphan files warned | `validate.check_message`, `validate.check_attachment_dir` |
| M-13 | a voicemail without audio (warning) | `validate.check_message` |
| M-14 | voicemail `transcript` present exactly when the body is not empty | `validate.check_message` |
| M-15 | `seen` only on received, never before `at` | schema `allOf` + `validate.check_message` |
| M-16 | participants in the channel's form, once each, never yourself (warning); thread identity declared, same channel | `validate.check_thread` |

## 5. Deferred, deliberately

- **Sending, fetching, syncing** — Connections (SA-031). Never here.
- **Streaming imports.** An import holds one file in memory while it plans
  (a very large phone backup with media needs matching memory).
- **iCalendar invitations inside email** — an invite is a message; the event
  belongs to `rasa.module.schedule`. Linking them automatically waits for a
  real consumer.
- **Other export formats** (mbox out, texts out). Nothing asked for them.
- **Other phone backup formats.** Only the SMS Backup & Restore XML is read;
  others can follow the same `Incoming` shape in `mail.py`.
- **Voicemail transcription.** The module records who wrote a transcript; it
  never makes one.
- **Encrypted or redacted bodies.** Needs a key-management answer the
  substrate does not have yet.

## 6. Version plan

- **0.1.0** — this: ten kinds, five channels, record + draft, file interop,
  derived views, to the records-family/1 standard. Local branch;
  merge/tag/push on the owner's go-ahead.
- **0.1.x** — fixes from the first real consumer.
- **1.0.0** — the record shapes, the kind registry, the seam and the install
  layout locked, after at least two real projects have run messages
  unchanged. A shape change after that is a major version with a migration
  tool, as in `rasa.module.tasks`.
