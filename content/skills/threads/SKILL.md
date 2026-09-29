---
name: threads
description: Read the messages ledger — what needs a reply, what is unseen, what is waiting on someone else, open drafts, missed calls, unheard voicemails, everything with one person across email, texts, chat, calls and letters, a whole conversation, or a search. Read-only; it never changes a record. Use for "/threads", "what do I need to reply to", "anything new", "who haven't I heard back from", "did anyone call", "any voicemails", "what's my history with Jane", "show me the lease thread", "find the message about the deposit". For writing, drafting or logging, use /message.
---

# /threads — what needs you

Everything here reads; nothing changes a record (except `messages seen`,
which you run only when the person has actually read or heard something).
The law is `.claude/messages-rules.md`; what "needs a reply" means in this
project is `.claude/messages-gate.md` §4.

## What needs attention

```
.claude/bin/messages threads --needs-reply        # the last thing that happened came in
.claude/bin/messages threads --unseen             # received, not yet read or heard
.claude/bin/messages threads --waiting            # you wrote, no answer after stale_after days
.claude/bin/messages threads --drafts             # drafts not yet sent or discarded
.claude/bin/messages calls --missed               # incoming calls nobody picked up
.claude/bin/messages voicemails --unheard
```

- These are **derived**, not stored: computed from the messages each time.
  Treat "needs reply" as the starting list and apply the seam's definition —
  a newsletter or a one-time code never needs one; a missed call from a
  person usually does.
- Lead with what matters: people before automated senders, missed calls
  and voicemails before email, oldest waiting first. Name thread ids so the
  person can open them.

## One person, every channel

```
.claude/bin/messages timeline --with CARD-0007
.claude/bin/messages timeline --with jane@acme.com
.claude/bin/messages threads --with "+1 555 010 0200"
```

When `rasa.module.crm` is mounted, a card gathers every address the person
has used — emails, numbers, chat handles — into one chronology. Without it,
an address means just that address.

## Reading a conversation

```
.claude/bin/messages show THR-0003          # the whole thread, in order
.claude/bin/messages show MSG-0012          # one message
.claude/bin/messages search deposit --channel email
```

Summarise; quote only what the person needs. Voicemail bodies are
transcripts — say when one is machine-made (`text by machine`), and don't
present it as verbatim.

## Marking things read

Only when the person has read or listened:

```
.claude/bin/messages seen THR-0003
.claude/bin/messages seen MSG-0014
```

## Filters

`threads` also takes `--channel email|text|chat|voice|post`, `--kind K`,
`--label L`, and `--all` / `--archived`. Every read verb takes `--json`
when you need to process the result.

To answer or record anything, switch to `/message`.
