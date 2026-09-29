# CLAUDE.md — `rasa.module.messages`

Per-repo working contract for Claude sessions opened inside this folder.
Extends `~/.claude/CLAUDE.md` and the workspace `CLAUDE.md` at the workspace
root (the folder holding `canon/` and `elements/` — the `rasa.tenant.rasaos`
tenant's contract); does not override them.

> **There is no identity header here, and none is installed.** A mounted
> module is a capability a project pulled in; it does not speak for the
> project. This module ships no `/whoami`, `/sync` or `/promote`, no identity
> stamp and no vendored source clone — the `rasa.module.tasks` v1.0.0
> precedent. Do not introduce them.

## What you are when you're in this folder

You are working on **`rasa.module.messages`** 0.1.0 — a `module`-kind
Element: every kind of message (email, texts, chat, calls, voicemail,
letters, faxes) kept as records in threads, with drafts. It is one of the
four **records-family/1** modules, built together on 2026-09-28:

| module | records | root |
|---|---|---|
| `rasa.module.notes` | NOTE, DEC | `notes/` |
| `rasa.module.schedule` | EVT | `schedule/` |
| `rasa.module.crm` | CARD, acct-, LEAD | `crm/` |
| `rasa.module.messages` (this) | THR, MSG | `messages/` |

## The load-bearing ideas

1. **The kind is the axis.** Ten kinds, five channels. Each kind's law is a
   block in `content/schemas/message.schema.json` and the same table in
   `content/bin/rasa_messages/model.py` (`KINDS`); `KindTableTests` proves
   they agree. Change both together, or the test fails.
2. **Record and draft — never send.** No network, no process, ever (gate
   check 11). A draft leaves as an `.eml` file; the person sends it; the tool
   records that it went. Sending/fetching/syncing is Connections' (canon
   SA-031), not this module's. If you find yourself reaching for `smtplib`
   or an API client, stop.
3. **The family core is shared, byte for byte.**
   `content/bin/rasa_messages/records.py` and the family block in
   `content/messages-rules.md` are identical in all four modules; `FAMILY`
   records their hashes; `bin/check-manifest` check 10 compares siblings.
   **Never edit the core or the family block in one module alone.**
   `tests/test_records_core.py` is shared the same way.
4. **One refused inference: who you may write as.** The config's
   `identities` gate every write-as-you verb (the hard-stop); the prose seam
   `.claude/messages-gate.md` says whether the assistant is allowed.
5. **Addresses are literal; people are resolved at view time.** crm cards
   are read, never written, never copied into a message.
6. **Not `rasa.module.chat`.** That is the assistant's chat surface.

## Source of truth

- **`canon/` at the workspace root** — authoritative; ELEMENT_CONTRACT §2
  (the `module` kind), §7 (install policies), §8 (the vocabulary lock).
  Fetch it before citing it.
- **`content/messages-rules.md`** — the installed law; every rule is an
  invariant id or "judgement".
- **`content/schemas/*.schema.json`** — the field law, evaluated by the
  validator.
- **`content/BUILD_PLAN.md`** — the design record and the invariant map.
- **`rasa.json`** — the manifest; must pass `schema/bin/validate`.

## Don'ts

- **Don't open the network, start a process, or touch version control from
  anything under `content/bin/`.** Check 11 fails the gate. Interop is files.
- **Don't let engineering vocabulary into anything that installs.** A firm, a
  clinic and a novelist read these files; check 9 enforces it (watch
  identifiers too — `src`, `build_…`). Author-time docs
  (`content/BUILD_PLAN.md`, `content/README.md`) are opt-in and exempt.
- **Don't name the phone platform the SMS backup app runs on** in anything
  that installs — call it "SMS Backup & Restore XML".
- **Don't claim paths a parent owns** — no `CLAUDE.md` seed, no identity
  file, no skill outside `message` / `threads` (never `inbox`), no vendored
  source clone.
- **Don't add a field or a kind without adding it to the schema, the
  `KINDS` table, `x-key-order`, the rules and a test.**
- **Don't `bin/init` this Element into itself.** `content/` is the source.
- **Don't push without the owner's go-ahead.** Local branch + commit only.

## How a version bump works

Pre-1.0 (0.x): a minor may change a record shape, but only with a
CHANGELOG entry that says what a ledger must do (or that no ledger exists
yet). From 1.0.0, strict semver as in `rasa.module.tasks`.

1. Edit `VERSION`, `rasa.json#version`, `content/bin/rasa_messages/model.py`
   `VERSION`, and README's `**Version:**` line together.
2. Write the CHANGELOG entry — honest, including what was not done.
3. If `rasa.json` changed, update `content/README.md`'s table.
4. If the change touches `records.py` or the family block: make it in all four
   modules, `bin/check-manifest --stamp-family` in each.
5. `bin/check-manifest` — all 14 checks GREEN, check 3 "canonical validator
   PASS" (point `RASA_SCHEMA_DIR` at a `RasaOS/schema` checkout whose `.venv`
   has `jsonschema`). Run it through the workspace path of this folder so
   check 10 finds the sibling modules.
6. Commit + tag `v<version>` on a branch; update the workspace
   `elements/REGISTRY.md` and `elements/CHANGELOG.md` (track 2).
