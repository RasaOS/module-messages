# Messages rules — `rasa.module.messages` 0.1.0

The law for everything under `messages/`: every kind of message — email,
texts, chat, calls, voicemail, letters, faxes — kept as records in threads,
with drafts. **Read this file before recording a message, drafting one,
importing a mailbox or a phone backup, or answering "what do I owe a reply
to?"**

**This module records and drafts. It never sends, fetches or syncs
anything.** A draft leaves it only as a file you open in your own mail
program (`messages export-eml`); when you have sent it there, you record that
here (`messages mark-sent`).

This file is **Element-owned** — it is replaced on upgrade. It deliberately
does not decide the things that vary from project to project:

| seam | owner | holds |
|---|---|---|
| `.claude/messages-gate.md` | the project | whose messages may be kept and with what consent, what may be kept about other people and for how long, which identities the assistant may write as, what "needs a reply" means here (judgement) |
| `messages/messages.config.yml` | the project | your identities per channel, the channel / service / label vocabularies, `stale_after`, `timezone`, actors (checked) |

Writing as you **hard-stops** until an identity is declared for the channel:
`messages draft`, `reply`, `mark-sent`, `log --sent` and importing messages
you sent all refuse without one. Guessing who you may write as is the one
inference this module refuses to make.

Two programs, installed at `.claude/bin/`:

- **`messages <verb>`** — the only sanctioned way to change the ledger.
- **`check-messages [--fix]`** — the enforcement. Run it before you call
  anything finished.

<!-- BEGIN records-family/1 -->
## The family conventions — records-family/1

This module is one of four that share one way of keeping records:
`rasa.module.notes` (notes + decisions), `rasa.module.schedule` (the
calendar), `rasa.module.crm` (the address book, accounts, the sales funnel) and
`rasa.module.messages` (every kind of message). This section is **identical
in all four rules files**; each module's own law follows it. The shared code
that enforces it (`records.py`) is identical in all four as well, and each
module's own gate (`bin/check-manifest`) proves both.

Rules marked **F-nn** are checked by the validator under that id. Rules
marked **judgement** are for a person; no machine can settle them.

### One module, one root

Each module owns one directory at the project root — `notes/`, `schedule/`,
`crm/`, `messages/` — and writes nothing outside it except its installed
surface under `.claude/`. Inside a root, `history.tsv`,
`<module>.config.yml`, `.state/` and `_archive/` are reserved.

### One record, one file

- A record is one UTF-8, LF, newline-terminated `.md` file (**F-01**):
  frontmatter between two `---` lines, then a markdown body.
- The filename is `<ID>-<slug>.md` (or exactly `<ID>.md` for a record type
  without a title) (**F-02**). The slug is lower-case words joined by `-`,
  at most 60 characters, and cosmetic: nothing ever joins on it.
- The id prefix says what the record is, and it must belong to this module
  (**F-03**).
- **The title is the H1.** Exactly one `# ` line, and it is the first
  non-blank line of the body (**F-04**). There is no `title:` key.
- **One fact, one home.** A fact is stored twice only when it is immutable
  and machine-written. Anything derivable — backlinks, "superseded", "past",
  "needs reply", an account's contacts — is computed, never stored.

### The frontmatter grammar — a strict subset of YAML

Any YAML tool can read these files; the validator accepts only this subset
and names the line it rejects (**F-05**).

```yaml
---
# comments are whole lines
id: CARD-0042
tags: [family, climbing]
name: {given: Jane, family: Doe}
email:
  - {label: work, value: jane@acme.com, pref: true}
x-source_system: legacy-crm
---
```

- Keys are lower-case (`a-z 0-9 _`); `x-<name>` is the extension namespace,
  kept verbatim and never interpreted. Any other key the record type does not
  define is an error (**F-06**). A key appears once (**F-07**).
- A value is a scalar, a flow list `[a, b]`, a flow map `{k: v}`, or a block
  list of scalars or flow maps (`  - ` two spaces, dash, space). Nothing
  nests deeper. No null, no `~`, no anchors, no multi-line values, no comment
  after a value. **To say nothing, omit the key.**
- Every value is read as text; the record type's JSON Schema says what it
  must look like. The tools write keys in the schema's order and quote a
  value whenever another YAML reader could misread it.

### Ids and references

- Every record has one id, never reused and never renumbered (**F-08**
  unique, **F-09** grammar). Ids are `PREFIX-NNNN` (four digits or more);
  the one exception is the account, keyed `acct-<slug>`.

  | prefix | record | module |
  |---|---|---|
  | `NOTE`, `DEC` | note, decision | notes |
  | `EVT` | event | schedule |
  | `CARD`, `acct-<slug>`, `LEAD` | contact card, account, lead | crm |
  | `THR`, `MSG` | thread, message | messages |
  | `TASK` | task (read, never written) | `rasa.module.tasks` |

- **Ids are the only join key.** In frontmatter, a reference is a bare id
  (`about: [CARD-0042]`). In a body, it is a wiki-link `[[CARD-0042]]` or
  `[[CARD-0042|Jane]]`; `@acct-<slug>` in prose also names an account.
- References resolve across the whole family. A frontmatter reference into a
  mounted module that names no record is an error (**F-10**); a body link
  that names no record is a warning (**F-11**); a reference into a module
  that is not mounted here is unverifiable and only noted (**F-12**). No
  module ever requires another.

### The history log — `<root>/history.tsv`

```
at	id	verb	from	to	actor	note
2026-09-28T14:03:11Z	NOTE-0001	new	-	inbox	chazz
2026-09-28T14:09:40Z	NOTE-0001	move	inbox	work	chazz
```

- Seven tab-separated columns under that exact header (**F-13**); `at` is a
  UTC stamp and never runs backwards (**F-14**).
- `from` and `to` are the record's place (its directory inside the root) or a
  `key=value` status, or `-`.
- Every record on disk was filed by a `new` or `import` line (**F-15**), and
  sits where its last place line says (**F-16**). A filed id whose file is
  gone is reported (**F-17**); its number is never issued again.
- Only the tools append to it. **Never edit it by hand.** `check --fix`
  repairs F-15/F-16 with a `reconcile` line by `unknown`; it never invents
  a person or a date.

### `updated` is a fact

The tools keep a cache of each record's content in `<root>/.state/` (it has
its own `.gitignore`). A record whose content changed while `updated` did
not is an error (**F-18**) — run `<tool> touch <ID>`. Some records are
**frozen** (a ratified decision; a message once sent or received): changing
their content at all is an error (**F-19**). Frozen records are superseded
or followed by a new record, never rewritten.

### Archive, never delete

The tools never delete a record. `archive` moves it under `_archive/` and
`restore` brings it back; records with a lifecycle end in a terminal state
instead. Deleting by hand is detected (**F-17**). **Judgement:** a person
may still choose to erase personal data for a legal or privacy reason — do
it deliberately, outside the tools, and say so in the project's own record.

### Configuration — `<root>/<module>.config.yml`

- Same grammar; `schema: rasa.module.<module>/1` names the ledger format;
  unknown keys and malformed values are errors (**F-20**). A project with no
  config is valid, except for the one command behind the module's hard-stop.
- **Declare to constrain.** Every vocabulary — tags, types, notebooks,
  calendars, groups, stages, channels, labels — follows one rule: absent or
  `[]` means free (only the grammar is checked); a non-empty list is closed,
  and anything undeclared is an error (**F-21**).
- `actors` (declare to constrain) and `default_actor` are common to all four.

### Actors, dates and times

- Every `created_by` and every history actor is a handle
  (`^[a-z0-9][a-z0-9._-]{0,31}$`) or `unknown` (**F-22**). The tools take
  `--by`, else `$RASA_ACTOR`, else `default_actor`, else they refuse —
  attribution is never invented.
- `created` and `updated` are real `YYYY-MM-DD` dates, never in the future,
  and `updated` is never before `created` (**F-23**).
- A moment is RFC 3339 **with an offset** (`2026-09-28T14:00:00-04:00`),
  never a bare local time (**F-24**). A timezone is an IANA name that
  resolves (**F-25**).
- Relative input — `today`, `tomorrow`, `+3d`, `fri 9am` — is resolved when
  it is captured and stored absolute.
- Every other field obeys its record type's JSON Schema, installed under
  `.claude/schemas/<module>/` (**F-26**).
- A `.md` file inside a root that is not a record is listed once and left
  alone (**F-27**).

### The tools

- Two programs per module under `.claude/bin/`: the tool (`notes`,
  `schedule`, `crm`, `messages`) and its validator (`check-notes`, …).
  Pure Python standard library: no network, no version control, nothing to
  install.
- **Every change goes through the tool.** One command validates, writes the
  record atomically, appends the history line and refreshes the cache — one
  act, under a lock. A refused command writes nothing and exits 1; an
  unusable environment exits 2. `--dry-run` shows what would happen;
  `--json` gives machine output.
- The validator exits 0 when there are no errors (warnings allowed). Run it
  before calling anything finished.

### The outside world

These records hold personal data about other people. **No tool in this
family opens a network connection, sends anything, or syncs anything** — each
module's gate refuses to ship one that tries. Moving data in and out is done with files
(`.ics`, `.vcf`, `.eml`, mbox, SMS backup XML). Sending and syncing belong
to Connections (canon task SA-031), not to this family. **Judgement:**
record about people only what the project's seam allows.
<!-- END records-family/1 -->

## 1. What this module keeps

A **message** is anything one person said to another at a distance: an
email, a text, a chat message, a phone call, a voicemail, a letter, a fax.
A **thread** is one conversation. Each message is one file, inside its
thread's directory; the thread's directory sits in the directory of its
**channel**.

```
messages/
  messages.config.yml      what the project declares
  history.tsv              every change, one line each
  email/THR-0001-lease-renewal/
    THR-0001.md            the thread record (its H1 is the subject)
    MSG-0001.md            one file per message
    MSG-0002.md
    attachments/MSG-0001-lease-2027.pdf
  text/THR-0003-jane-doe/MSG-0005.md
  chat/  voice/  post/     the other three channels
  _archive/<channel>/THR-…/   archived threads, never deleted
```

- **Records and drafts, never sending.** The tools record what happened and
  keep what you are writing. Sending, fetching and syncing belong to
  Connections (canon task SA-031), not to this module — *judgement*: never
  ask the assistant to send, and never let a draft stand in for a sent
  message.
- **Not an assistant chat.** `rasa.module.chat` is a conversation surface
  with the assistant. This module keeps the conversations *you* have with
  other people — *judgement* when deciding which module a thing belongs to.
- **One fact, one home.** The channel is the directory; the direction of a
  message is its status; "needs a reply", "unseen" and "waiting" are
  computed when asked (§8). None of them has a key, so writing one into a
  record is an error (**F-06**).

## 2. Kinds and channels

The **kind** of a message is the axis everything else follows. The kinds are
this module's own vocabulary — each has its own field law, so a project
cannot add one without adding its rules — and they group into five channels:

| channel | kinds | addressed by | you can draft it | carries |
|---|---|---|---|---|
| `email/` | `email` | email address | yes | cc, bcc; files; Message-ID threading |
| `text/` | `sms`, `mms`, `imessage`, `rcs` | phone number (imessage: or email); a sender name on incoming texts | yes | `sms`: nothing but text; the others: media |
| `chat/` | `chat` | `<service>:<handle>` | yes | `service` (required); files |
| `voice/` | `call`, `voicemail` | phone number | **no** — they are recorded, not composed | call: `outcome`, `duration`, a recording; voicemail: `duration`, the audio, a transcript |
| `post/` | `letter`, `fax` | letter: written address (or a card); fax: number | yes | letter: cc, scans; fax: pages |

- Every message carries one `kind` from the table (**F-26**).
- A thread lives in one of the five channel directories, and in a declared
  one when the config declares `channels` (**M-02**).
- A thread may mix kinds only within its channel — an SMS thread that
  falls back to MMS, a voice thread holding the calls and voicemails with one
  person (**M-03**).
- Each kind's field law is in `.claude/schemas/messages/message.schema.json`
  — one block per kind — and the validator reports a breach under **M-04**:
  - `email`: every address is an email address (or a CARD id); cc and bcc
    allowed; files allowed.
  - `sms`: phone numbers or sender names; no cc or bcc; **no attachments**.
    `mms`, `rcs`: the same, with media. `imessage`: phone numbers or email
    addresses, with media.
  - `chat`: `service` is required and every handle is on it
    (`slack:U0123` on a `slack` message).
  - `call`: `outcome` is required — `answered`, `missed`, `declined`,
    `busy`, `failed`, `voicemail` or `blocked` — and an answered call has a
    `duration`. The body is your call notes; at most one attachment, the
    `recording`.
  - `voicemail`: `duration` optional; at most one attachment, the `audio`;
    the body is the transcript (§6).
  - `letter`: addresses are free text or a CARD id; cc allowed; scans as
    attachments; `at` may be a bare day, because a letter arrives on a day.
  - `fax`: phone numbers; pages as attachments.
  - Only a letter's `at` may be a day; every other kind's is a moment.
- Only a kind you can compose is ever a draft or a discarded draft. A call
  or a voicemail is recorded once it has happened (**M-06**).

## 3. Threads

- A thread is a directory `<channel>/THR-nnnn-<slug>/` holding exactly one
  thread record, named `THR-nnnn.md` with the directory's id; every message
  file sits directly inside a thread directory, beside its thread record
  (**M-01**). The slug is cosmetic: `messages rename` changes it and nothing
  joins on it.
- The thread's title is its H1 (**F-04**): the subject for email and
  letters, the people for texts, chat and calls (the tool names it; you may
  rename it).

```yaml
---
id: THR-0001
created: 2026-09-28
created_by: chazz
updated: 2026-09-28
participants:
  - {who: jane@acme.com, name: Jane Doe}
  - {who: dana@acme.com, name: Dana}
identity: home
labels: [lease]
account: acct-acme
---

# Lease renewal
```

| key | required | what it is |
|---|---|---|
| `id` | yes | `THR-nnnn`, minted by the tool |
| `created`, `created_by`, `updated` | yes | bookkeeping; the tools write them |
| `participants` | no | the other people: `{who, name}` — the address as used, and the name the message carried |
| `identity` | no | which of your identities the conversation is conducted as |
| `labels` | no | declare `labels` to constrain (**F-21**) |
| `account` | no | the business account it belongs to (`acct-<slug>`, **F-10**) |
| `about` | no | family records it concerns — an event, a lead, a task (**F-10**) |
| `pinned` | no | only ever `true` |

- Participants are the **other** people: each address in the channel's form
  (an email thread's participants are email addresses or CARD ids), listed
  once; your own identity among them is reported as a warning (**M-16**).
  The tool adds people as they appear.
- `identity` names a declared identity of the thread's own channel
  (**M-16**). Replies default to it.
- Texts, chat and calls with the same set of people are one thread: the
  tool files a new one into the live thread of its channel whose
  participants are exactly those people, comparing phone numbers by their
  last ten digits. `--subject` always starts a new thread; `--thread THR`
  names one. Email threads by its headers (§7) or by `--thread`. Whether
  two conversations with the same people are really one is yours to say —
  *judgement*: name the thread when the tool's match is wrong.
- **Archive, never delete.** `messages archive THR` moves the whole thread
  directory to `_archive/<channel>/` and logs one line per record; `restore`
  brings it back (**F-16**). A thread with an open draft is not archived —
  send it or discard it first.

## 4. Messages

```yaml
---
id: MSG-0002
created: 2026-09-28
created_by: chazz
updated: 2026-09-28
kind: email
status: sent
at: 2026-09-21T10:00:00-04:00
from: me@example.org
to: [jane@acme.com]
in_reply_to: MSG-0001
---

Yes, please renew for a year.
```

| key | required | what it is |
|---|---|---|
| `id` | yes | `MSG-nnnn`; the file is exactly `MSG-nnnn.md` (**F-02**) — a message has no title |
| `created`, `created_by`, `updated` | yes | when it was recorded, by whom — not when it happened |
| `kind` | yes | §2 |
| `status` | yes | `draft`, `sent`, `received` or `discarded` |
| `at` | sent, received | when it happened, with the offset it happened in |
| `from`, `to`, `cc`, `bcc` | by status and kind | the addresses as used |
| `service`, `outcome`, `duration`, `transcript` | by kind | §2, §6 |
| `in_reply_to` | no | the earlier message this one answers |
| `external_id` | no | the id it carried elsewhere (an email Message-ID, a platform id, an id made on import) |
| `attachments` | no | `{name, path, role}` — files in the thread's `attachments/` |
| `seen` | no | when you read or heard a received message |

- **The lifecycle.** A message you write starts as a `draft` and becomes
  `sent` (`messages mark-sent`) or `discarded` (`messages discard`); a
  message from someone else is `received` from the start; a message that
  happened elsewhere is recorded `sent` or `received` (`messages log`).
  Nothing else ever moves: `draft → sent` and `draft → discarded` are the
  only changes, each logged as `status=draft` → `status=…`, and a message's
  status always equals what its history says (**M-07**). Only a draft is
  ever discarded (**M-07**), and a discarded draft is kept, never sent.
- **Direction is derived** from the status: `received` came in; `draft`,
  `sent` and `discarded` go out. There is no direction key (**F-06**).
- A message that happened has an `at`; a draft or a discarded draft has none.
  A received message has a `from`; a draft or a sent message has a `to`
  (**M-05**).
- **Frozen once it happened.** A `sent` or `received` message never changes
  again except `updated` and `seen` (**F-19**), and it is never reopened as
  a draft (**M-08**). A correction is a new message, not an edit.
- `seen` is set only on a received message, and never before its `at`
  (**M-15**). It is triage state, stamped by `messages seen MSG|THR`.
- **Addresses are stored literally** — the address a message actually used
  is the fact, even when a contact card knows the person by another. A CARD
  id stands in only where no address is known (a letter from someone whose
  address you do not have); a CARD id must name a card when
  `rasa.module.crm` is mounted (**F-10**), and nothing but a person is ever
  an address (**M-04**).
- `in_reply_to` names an earlier message **in the same thread**, one that
  happened, never itself (**M-09**).
- An `external_id` names one message in the whole ledger (**M-10**): it is
  how an import knows it has seen a message before.
- An attachment's `path` is `attachments/<name>` inside the thread's own
  directory, and the file is there (**M-12**). A file in `attachments/`
  that no message names is reported as a warning (**M-12**). The tool copies
  a file in as `<MSG-id>-<name>`; `name` keeps the original file name.

## 5. Identities — the hard-stop

- An identity is one of **your** addresses on one channel, declared in the
  config: `{id: home, channel: email, address: me@example.org}`, with an
  optional `card: CARD-nnnn` (your own card in `rasa.module.crm`). Its
  address is in the channel's form, and identity ids are unique (**F-20**).
- A draft, and a sent message of a kind you compose, is **from a declared
  identity of its channel** (**M-11**). The tools refuse to draft, reply,
  mark-sent, log a sent message or import messages you sent until the
  channel has one: that is the hard-stop, and it is never worked around by
  typing an address with `--from`.
- Two identities on one channel: pass `--as <id>`. The post channel carries
  both letters and faxes; the tool picks the identity whose address suits
  the kind.
- **Which identities the assistant may write as** — and whether it may mark
  a message sent at all — is `.claude/messages-gate.md`'s to say —
  *judgement*. Declaring an identity makes writing *possible*; the seam says
  whether it is *allowed*.

## 6. Recording what happened elsewhere

`messages log` records a message that did not pass through this tool: the
call you took, the voicemail you got, the letter that arrived, the text you
sent from your phone.

- `--at` is required: an RFC 3339 moment with its offset, `now`, or a
  wall-clock time read in the config's `timezone` (**F-24**). The offset is
  kept — it is part of when it happened. The tool refuses a moment in the
  future; never record something before it has happened — *judgement*.
- A **call**: `--outcome`, and `--duration` when it was answered
  (`PT4M12S` or `4:12`) (**M-04**). The body is your notes of the call.
- A **voicemail**: `--audio FILE` copies the recording into the thread; a
  voicemail recorded without its audio is reported as a warning (**M-13**).
  The body is the transcript, and `transcript: machine | human` says who
  wrote it — present exactly when the body is not empty (**M-14**). Never
  present a machine transcript as what was said word for word —
  *judgement*.
- A **letter** or a **fax**: `--attach` the scans or pages.
- `--sent` records one you sent; it is from your identity (§5).
- Record about other people only what `.claude/messages-gate.md` allows,
  with the consent it requires — *judgement*.

## 7. Import and export

Interop is files the person hands over. Nothing is fetched.

- `messages import FILE.eml …` and `messages import --mbox FILE` read
  standard mail files. The Message-ID becomes `external_id`; a message whose
  `external_id` is already in the ledger is skipped, so importing the same
  file twice changes nothing (**M-10**). In-Reply-To and References put a
  message into the thread of the message it answers (nearest first), and
  `in_reply_to` is set when that message happened earlier (**M-09**);
  otherwise the message starts a thread titled by its subject, `Re:`/`Fwd:`
  removed. `Date` becomes `at` with its offset. A message from one of your
  email identities is `sent`; anything else is `received`. The plain-text
  part is the body (an HTML-only message is turned into plain text), and
  attachments are copied into the thread.
- `messages import --sms-xml FILE` reads the XML written by the phone app
  "SMS Backup & Restore": texts (`sms`), picture messages (`mms`, their
  media copied in) and — from its separate call-log file — calls, with
  their outcome and length. Times become UTC. Texts and calls with the same
  people share a thread. Each gets a stable `external_id` so a re-import
  changes nothing. Drafts, queued texts and calls from withheld numbers are
  left out, and the tool says how many. Declare your text identity first:
  your sent texts are from it, and group threads come out right only when
  the tool knows which number is yours.
- An import is one act: it is checked in full, then written, or nothing is
  written.
- `messages export-eml MSG` writes an **email draft** as a standard message
  on standard output (`> draft.eml`) for you to open and send from your own
  mail program. It sets no date and no Message-ID — your program adds them
  when it sends. This module still sends nothing; record the send with
  `mark-sent` — *judgement*: only once it has really gone.

## 8. What is derived, never stored

Computed each time you ask, from the messages themselves:

| view | means |
|---|---|
| **needs reply** | the last message that happened in the thread was received — and is not an answered call, which was a conversation already had |
| **unseen** | received messages with no `seen` |
| **waiting** | the last message that happened was sent — and is not an answered call — more than `stale_after` days ago (default 7), and nothing came back |
| **drafts** | open drafts |
| **missed calls** | incoming calls nobody picked up (missed, or sent to voicemail) |
| **unheard voicemails** | received voicemails with no `seen` |
| **timeline** | everything with one person — every kind, every channel — in one chronology |

- Whether a thread *really* needs a reply — a newsletter does not, a
  missed call from a client does — is `.claude/messages-gate.md`'s
  definition — *judgement*. The derived view is the starting list, not
  the verdict.

## 9. People: addresses and contact cards

- When `rasa.module.crm` is mounted, the tools read its contact cards
  (never write them) to show names, to gather a person's timeline across
  every address they have used, and to turn a CARD id into an address for
  `export-eml`. Nothing from a card is copied into a message: resolution
  happens when you look (one fact, one home).
- Phone numbers are compared by their last ten digits, email addresses and
  chat handles without regard to case. The stored address never changes.
- Whether two addresses really are one person is decided on the card, in
  `rasa.module.crm`, never by guessing here — *judgement*.

## 10. The tool

| verb | does |
|---|---|
| `messages draft --kind K --to WHO… [--cc …] [--bcc …] [--subject S \| --thread THR] [--service S] [--as ID] [--attach FILE]… [--body …]` | write a draft |
| `messages reply MSG [--all] [--kind K] [--as ID] [--body …]` | draft an answer in the same thread |
| `messages mark-sent MSG [--at WHEN]` · `messages discard MSG` | you sent it · you won't |
| `messages log --kind K --at WHEN (--from WHO \| --sent) [--to …] [--outcome O] [--duration D] [--audio FILE] [--transcript machine\|human] [--attach FILE]… [--body …]` | record one that happened elsewhere |
| `messages seen MSG\|THR` | mark received messages read or heard |
| `messages import FILE.eml… \| --mbox FILE \| --sms-xml FILE [--as ID]` | import files |
| `messages export-eml MSG > draft.eml` | hand an email draft to your mail program |
| `messages label THR +a -b` · `messages about THR +REF -REF` · `messages pin/unpin THR` · `messages rename THR "Title"` | organise a thread |
| `messages archive THR` · `messages restore THR` | archive (never delete) · bring back |
| `messages touch ID` | record a hand edit (stamps `updated`) |
| `messages threads [--needs-reply] [--unseen] [--waiting] [--drafts] [--label L] [--with WHO] [--channel C] [--kind K]` | the conversations and what they need |
| `messages calls [--missed]` · `messages voicemails [--unheard]` · `messages timeline --with WHO` | the call log · voicemails · one person |
| `messages show THR\|MSG` · `search TEXT` · `where ID` · `history [ID]` | read |
| `messages check [--fix]` | validate (same as `check-messages`) |

Edit a thread's body, or a draft, by hand whenever you like, then
`messages touch ID`. A sent or received message is not edited at all (§4).

## 11. Soft cross-references

This module stands alone and plays well with its neighbours when they are
mounted. None is ever a `requires.elements[]` dependency.

- **`rasa.module.crm`** — contact cards for names, timelines and exports
  (§9); a thread's `account`.
- **`rasa.module.schedule`** — a thread can be `about` an event; an
  invitation is a message, the meeting is an event.
- **`rasa.module.notes`** — a note can be `about` a thread; a decision can
  name the thread it came out of in its `sources`.
- **`rasa.module.tasks`** — a thread can be `about` the task it concerns.
- **Connections** (canon task SA-031) — sending, fetching and syncing, when
  they exist. Not this module.
- **`rasa.module.chat`** — the assistant's own chat surface; unrelated to
  the conversations recorded here (§1).

## 12. What no machine will catch

Every invariant is cited at the rule it enforces: F-01…F-27 in the family
conventions above; M-02 M-03 M-04 M-06 §2 · M-01 M-16 §3 · M-05 M-07 M-08
M-09 M-10 M-12 M-15 §4 · M-11 §5 · M-13 M-14 §6. These are the ones nobody
checks but you:

- whether you may keep these messages at all, and with whose consent (§6)
- whether what is kept about other people is only what the seam allows,
  and whether old threads are archived or erased as the seam says (§6)
- whether the assistant may write as this identity, and whether a draft
  really went before it is marked sent (§5, §7)
- whether a thread flagged "needs reply" really needs one (§8)
- whether a machine transcript says what was said (§6)
- whether two addresses really are the same person (§9)
- whether a message belongs here or in the assistant's own chat (§1)
