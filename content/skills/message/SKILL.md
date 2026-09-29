---
name: message
description: Record and draft messages of every kind — draft an email, a text, a chat message, a letter or a fax; draft a reply; record that a draft was sent or discard it; log a call you took, a voicemail you got, a letter that arrived or a text you sent from your phone; import .eml files, an mbox, or an SMS Backup & Restore XML file; export an email draft to open in your own mail program; label, pin, rename or archive a thread. This module never sends anything. Writes go through .claude/bin/messages. Use for "/message", "draft a reply to Jane", "write to the landlord", "log that call", "Dr Lee's office left a voicemail", "I got a letter from the bank", "I sent it", "never mind, bin that draft", "import my mailbox", "import my texts backup". For reading — what needs a reply, missed calls, a person's history — use /threads.
---

# /message — record and draft

You decide **what** to record or write and where it belongs;
`.claude/bin/messages` makes the change. Never write, move or delete a
message file by hand — the tool keeps the history log and the content cache
honest, and a hand change is exactly what `check-messages` reports.

**This module never sends.** You draft; the person sends from their own
mail program or phone; then you record that it went. Never tell the person
a message was sent because you drafted it.

The law is `.claude/messages-rules.md`. This skill executes it; if they ever
disagree, the rules win.

## Before the first write in a session

1. Read `.claude/messages-gate.md` — whose messages may be kept, what may be
   kept about other people, **which identities you may draft as**, and any
   hard gate. If it forbids something, stop and say so.
2. Read `messages/messages.config.yml` — the declared `identities` (who you
   can write as, per channel), channels, services, labels, `default_actor`.
3. Know who is acting: `--by <handle>`, or `$RASA_ACTOR` / `default_actor`.
   Never invent a person; if nobody is declared, ask.

If the channel you need has no identity, **stop**: every command that writes
as the person refuses (the hard-stop). Ask them which address is theirs, and
have them declare it — never type an address into `--from` to get around it.

## Drafting

```
.claude/bin/messages draft --kind email --to jane@acme.com --subject "Lease renewal" \
    --body "Hi Jane, …"
.claude/bin/messages draft --kind sms --to "+1 555 010 0200" --body "Running 10 min late"
.claude/bin/messages draft --kind chat --service slack --to slack:U0JANE --body "…"
.claude/bin/messages reply MSG-0007 --body "Thanks — yes."          # to the sender
.claude/bin/messages reply MSG-0007 --all --body "…"                # email: everyone
```

- **Pick the kind deliberately** — it decides the channel, the address form
  and what the message can carry (`.claude/messages-rules.md` §2). A text is
  `sms` unless it carries media (`mms`). Calls and voicemails are never
  drafted.
- An email needs `--subject` (the thread's title) or `--thread THR`. Texts
  and chat find their conversation by the people in it.
- **Addresses as the person uses them.** Look up a contact first
  (`.claude/bin/crm contact find jane`, when crm is mounted) rather than
  guessing; a `CARD-…` id works where no address is known.
- Long text: write it to a temp file and pass `--body-file`.
- Show the draft back (`messages show MSG`) before anything else happens.

## Handing an email draft over, and recording the send

```
.claude/bin/messages export-eml MSG-0012 > ~/Desktop/lease-reply.eml
```

The person opens that file in their own mail program and sends it. **Only
when they say it went:**

```
.claude/bin/messages mark-sent MSG-0012                 # now
.claude/bin/messages mark-sent MSG-0012 --at "2026-09-28T16:05:00-04:00"
.claude/bin/messages discard MSG-0012                    # they decided not to
```

A sent message is frozen: to correct it, draft a new one.

## Recording what happened elsewhere

```
.claude/bin/messages log --kind call --from "+1 555 010 0200" --at now \
    --outcome answered --duration 6:30 --body "Agreed Tuesday 10am."
.claude/bin/messages log --kind voicemail --from "+1 555 010 0200" --at "2026-09-28T09:12:00-04:00" \
    --audio ~/Downloads/voicemail.m4a --transcript machine --body "Hi, it's Dr Lee's office…"
.claude/bin/messages log --kind letter --from "Acme Property Mgmt" --at 2026-09-27 \
    --subject "Rent increase notice" --attach ~/Scans/letter.pdf
.claude/bin/messages log --kind sms --sent --to "+1 555 010 0200" --at now --body "On my way"
```

- `--at` is when it happened, with its offset (or `now`; a letter may be a
  day). Never guess a time: ask, or say "now" only if it is.
- A voicemail's body is its transcript: say who wrote it
  (`--transcript machine` for a phone's transcription, `human` if typed).
  Attach the audio when you have it.
- A call's `--outcome`: answered, missed, declined, busy, failed, voicemail,
  blocked.

## Importing

```
.claude/bin/messages import ~/Downloads/*.eml
.claude/bin/messages import --mbox ~/Mail/archive.mbox
.claude/bin/messages import --sms-xml ~/Backups/sms-20260928.xml
.claude/bin/messages import --sms-xml ~/Backups/calls-20260928.xml
```

Check the gate first: import only what the seam allows. Declare the text
identity before a texts backup (the sent ones are from it). Re-importing is
safe — anything already imported is skipped. Report the counts and any
notes the tool prints.

## Organising

```
.claude/bin/messages label THR-0003 +bills -urgent
.claude/bin/messages about THR-0003 +EVT-0012
.claude/bin/messages rename THR-0005 "Jane — birthday plans"
.claude/bin/messages seen THR-0003
.claude/bin/messages archive THR-0003 --note "resolved"
```

## Finish

Run `.claude/bin/check-messages` after a batch of changes. Report what you
did in a line or two (ids and threads); never paste whole messages back
unless asked.
