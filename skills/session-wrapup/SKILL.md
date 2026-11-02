---
name: session-wrapup
description: Close a work session so the next one starts smarter and cheaper. Writes verified project facts to .claude/facts.md (read by the kit's SessionStart hook), traps to knowledge/gotchas.md, repeatable wins to knowledge/solutions.md, the person's feedback to the project CLAUDE.md, then suggests /compact. Use at the end of any task, when the person says "wrap up", "finish", "log it", or before switching to another task.
---

# /session-wrapup

The goal: everything this session learned that will still be true tomorrow is written down in the
place the next session reads automatically. Nothing else. Keep each file short; a long facts file costs
tokens in every session.

## 1. Facts -> `.claude/facts.md` (project root; create if missing), **after the person approves**
A fact is something that was **verified in this session** and will **stay true**: the test command,
where the build runs, which directory is generated, what must not be edited, which environment is
production. One line each, imperative, no narrative. Propose the change first: show the lines you
would add, rewrite or delete, as a diff against the current file, and write only after the person
says yes (the next session will trust this file without checking). Keep it under 1500 characters: if
it grows, merge or drop the least useful. Not a fact: anything about this task's content, anything you
assumed but did not check, any secret.

## 2. Traps -> `knowledge/gotchas.md` (append)
Anything that cost more than ten minutes and would cost the same again:
```
## YYYY-MM-DD — <symptom exactly as it appeared>
Cause: <one or two lines>
Fix: <commands or steps>
```
False alarms count too, in two lines.

## 3. Repeatable wins -> `knowledge/solutions.md` (append)
```
## YYYY-MM-DD — <what was achieved>
What: ... Why this way: ... How to repeat: <file paths, commands>
```
Skip it when the win was a one-off.

## 4. Feedback from the person -> project `CLAUDE.md`, section "Feedback from the person"
Every correction of taste, style or process they gave this session, one dated line, in their words,
plus what changes. Rules repeated twice in chat become rules in the file.

## 5. Secrets check
Reread what you wrote. Passwords, tokens, keys, connection strings, codes: none. Replace with "kept in
<path>". This step is not optional.

## 6. Close
Report in four lines what went into each file (or "nothing" for a file), then suggest `/compact` when the
task continues or a new chat when it does not.
