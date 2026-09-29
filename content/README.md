# content/ — what `rasa.module.messages` installs

Everything under `content/` is Element-owned: refreshed on every upgrade,
never project state. `bin/init` reads `rasa.json` and applies each entry by
its policy; this table mirrors the manifest and must change with it.

| from | to | policy | what |
|---|---|---|---|
| `content/messages-rules.md` | `.claude/messages-rules.md` | file-replace | the law: the records-family/1 conventions + the messages law |
| `content/schemas/` | `.claude/schemas/messages/` | directory-mirror | JSON Schemas: thread, message (the per-kind law), config |
| `content/skills/` | `.claude/skills/` | directory-mirror | `/message`, `/threads` |
| `content/bin/` | `.claude/bin/` | directory-mirror | `messages`, `check-messages`, and the `rasa_messages/` package |
| `content/README.md` | — | opt-in | this file (not installed) |
| `content/BUILD_PLAN.md` | — | opt-in | the design record (not installed) |

Project-owned, copied once (`seed/`, never overwritten):

| from | to | policy |
|---|---|---|
| `seed/messages-gate.md.template` | `.claude/messages-gate.md` | skip-if-exists |
| `seed/messages/messages.config.yml.template` | `messages/messages.config.yml` | skip-if-exists |
| `seed/messages/history.tsv.template` | `messages/history.tsv` | skip-if-exists |
| `seed/messages/email/.keep` | `messages/email/.keep` | skip-if-exists |
| `seed/messages/text/.keep` | `messages/text/.keep` | skip-if-exists |
| `seed/messages/chat/.keep` | `messages/chat/.keep` | skip-if-exists |
| `seed/messages/voice/.keep` | `messages/voice/.keep` | skip-if-exists |
| `seed/messages/post/.keep` | `messages/post/.keep` | skip-if-exists |
| `seed/rasa.lock.json.template` | `.claude/rasa.lock.json` | init-only-with-sha |

The package: `records.py` is the records-family/1 core, byte-identical in
notes, schedule, crm and messages; `FAMILY` records its hash and the hash of
the family block in `messages-rules.md`. `model.py` (paths, the kind table,
config, crm resolution, the derived views), `validate.py` (M-01…M-16),
`cli.py` (the verbs), `mail.py` (`.eml` / mbox / SMS Backup & Restore XML in,
`.eml` out — files only, never the network).
