# Rules for every Claude Code session

## Before a task
- Read the project's `CLAUDE.md` and `.claude/facts.md`, then the headings of `knowledge/gotchas.md` and
  `knowledge/solutions.md`. Say in two lines what you will take into account.
- Do not guess names, paths, versions or numbers. Missing fact: check it or ask.

## Working
- One chat, one task. A new task starts in a new chat; a long one continues after `/compact`.
- Large files are read in parts: Grep first, then Read with offset and limit. Logs, exports and other
  people's code: find the place, read the place.
- Mechanical work (renaming, bulk edits, resizing, format conversion) goes to a cheaper model when one
  is available; the main model reviews.
- Repeating the same failing command is not a strategy. Read the whole error, check the assumption,
  change the approach, or ask.
- "Done" comes with proof: the command output, the response code, the screenshot, the test run.

## After a task (skill `/session-wrapup`)
- Project facts that will stay true (how to run tests, where the build lives, what not to touch) go to
  `.claude/facts.md`, one line each, at most 1500 characters in the file.
- What cost time: `knowledge/gotchas.md` (date, symptom as seen, cause, fix).
- What worked and is worth repeating: `knowledge/solutions.md` (what, why, how to repeat).
- Feedback from the person about style or taste: the project's `CLAUDE.md`, one dated line.

## Never
- Passwords, tokens, keys, connection strings, `.env` contents: not in chat, not in files, not in journals.
  Journals point to where the access is kept, never to its value.
- Publishing, sending, paying, deleting, deploying to production: only after an explicit "yes" from the
  person for that specific action.
- Third-party plugins, hooks and MCP servers: only after the owner's written decision.
