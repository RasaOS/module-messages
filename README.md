# Rasa · Module · Messages

**Canonical name:** `rasa.module.messages`
**Repo / folder:** `module-messages`
**Kind:** `module` (mounts into a `domain` or a `tenant`)
**Contract:** Element Contract v1.3.0 · records-family/1
**Version:** 0.1.0
**Status:** 0.1.0 built on branch `claude/records-family`; not yet published (no remote repository yet — `RasaOS/module-messages` is the intended home).

## What this is

Every kind of message a person deals with, kept as records they own:

- **email**; **texts** — `sms`, `mms`, `imessage`, `rcs`; **chat** (any
  service, addressed `service:handle`); **phone calls** and **voicemail**;
  **letters** and **faxes**.
- One file per message, inside its **thread's** directory; threads grouped
  into five **channels** (`email/`, `text/`, `chat/`, `voice/`, `post/`).
- The message **kind** is the axis: each kind has its own field law (a call
  has an outcome and a length; a voicemail its audio and a transcript whose
  author is recorded; an `sms` carries no files; a chat message names its
  service), written as JSON Schema and checked by the validator.
- **Records and drafts — never sends.** A draft leaves only as an `.eml` file
  for your own mail program (`messages export-eml`); you record the send
  afterwards (`messages mark-sent`). Nothing fetches or syncs either: imports
  read files you hand over — `.eml`, mbox, and the XML the phone app
  "SMS Backup & Restore" writes for texts and the call log.
- **Derived, never stored:** what needs a reply, what is unseen, what is
  waiting on someone else, missed calls, unheard voicemails, and one
  person's timeline across every channel (resolved through
  `rasa.module.crm` cards when that module is mounted).

The one thing it refuses to guess is **who you may write as**: drafting,
replying and marking sent hard-stop until your identity for that channel is
declared in `messages/messages.config.yml`.

Not `rasa.module.chat` — that is the assistant's own chat surface. This
module keeps the conversations you have with other people.

## The records family

One of four modules built to one standard — **records-family/1** — with
`rasa.module.notes` (notes and decisions), `rasa.module.schedule` (the
calendar) and `rasa.module.crm` (the address book, accounts and sales
funnel). Same file grammar, same history log, same validator conventions,
and references that resolve across all four when they are mounted together.
The shared core (`records.py`) is byte-identical in each, and each module's
release gate proves it.

## Install

```bash
bin/init /path/to/project
```

Installs `.claude/messages-rules.md`, `.claude/schemas/messages/`, the
`/message` and `/threads` skills, and two programs — `.claude/bin/messages`
and `.claude/bin/check-messages` — plus the project-owned `messages/` ledger
and the `.claude/messages-gate.md` seam. Pure Python 3 standard library:
nothing else to install, no network, no version control needed.

```bash
.claude/bin/messages log --kind voicemail --from "+1 555 010 0200" --at now \
    --audio voicemail.m4a --transcript machine --body "Hi, it's Dr Lee's office…"
.claude/bin/messages import --mbox ~/Mail/archive.mbox
.claude/bin/messages threads --needs-reply
.claude/bin/messages reply MSG-0007 --body "Tuesday works."      # needs your email identity declared
.claude/bin/messages export-eml MSG-0012 > reply.eml             # send it from your own program
.claude/bin/check-messages
```

## Layout

- `content/messages-rules.md` — the installed law (family conventions + messages law)
- `content/schemas/` — thread, message (the per-kind law) and config JSON Schemas
- `content/bin/` — the `messages` tool, the `check-messages` validator, the `rasa_messages` package (`mail.py` is the file interop)
- `content/skills/` — `/message`, `/threads`
- `seed/` — the project-owned seam, config, history header and the five channel placeholders
- `content/BUILD_PLAN.md` — the design record
- `bin/check-manifest` — the release gate (14 checks); `bin/init` — the installer
- `tests/` — the family-core suite (identical in all four modules) and the messages suite
