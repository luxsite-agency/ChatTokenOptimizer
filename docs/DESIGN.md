# Design notes

## What the kit optimises

Token spend in Claude Code has two parts: the work, and the overhead around it. The kit touches only the
overhead, and only where a rule can be enforced mechanically:

1. **Defaults.** The model, effort and output style of a session multiply every turn. A session left on a
   1M-context model carries up to a million tokens of history at a higher rate. Checked at `SessionStart`,
   fixed by one command, never changed silently.
2. **Whole-file reads.** One `Read` of a 300 KB export is tens of thousands of tokens. Denied above a
   threshold unless the call names a slice. The agent gets a hint, not a wall.
3. **Failure loops.** The agent retries a failing command with small variations. The third identical
   failure (or the fourth identical error from different commands) gets one nudge. The guard never blocks
   a call and never stops a turn: the person decides.
4. **Re-learning.** How to run tests, where the build lives, what not to touch. Written once by the person
   (or by the agent at wrap-up, then approved), injected every session, capped at 1500 characters.

Across sessions, the tool-usage report shows configured MCP servers that a project's sessions never
called. Their tool descriptions are paid for in every turn.

## What was borrowed from TokenEconomizer and what was changed

[TokenEconomizer](https://github.com/microsoft/TokenEconomizer) (Microsoft, Apache 2.0) measured 18–31%
lower cost on SWE-Bench with three mechanisms. Reading its code (commit 85a8247) for our own use, three
properties did not fit a team that handles client data and credentials in its sessions:

| TokenEconomizer | ChatTokenOptimizer | Why |
|---|---|---|
| Micro-skills are **mined from transcripts** by a distiller that stores raw command and output blobs (up to 20k/40k characters) | Facts are **written by the person or at wrap-up** into a 1500-character file; nothing is mined | transcripts contain secrets and client data; a copy of them, however trimmed, is one more place to leak from |
| Loop guard calls a **judge model** (`claude -p --model haiku`) with a digest of recent commands | Loop guard is **deterministic**: SHA-256 of the action and of the normalised error text, counters per session | no side calls, no token cost of its own, nothing leaves the machine; the trade-off is coarser detection |
| Tool pruning **applies deny lists automatically** after enough evidence and an idle period | Tool usage **reports**; `--apply` is an explicit command with a `--restore`, per project, never global | a setting that changes on its own is hard to debug and easy to blame on the wrong thing |
| Background processes for fold/judge; on Windows the spawn spec writes the **whole environment** (tokens included) to a temp file | **No background processes**; hooks finish in milliseconds and exit | the environment holds API keys; a temp file is a temp leak |
| Hooks on six events, state under `~/.tokeneconomizer` with session records, transcript paths, injected text | Four hooks, state is **hashes and integers** in a temp directory, removed after two days | the state file should be safe to paste into a bug report |

Kept as is: the three ideas, fail-open hooks, `additionalContext` for the agent and `systemMessage` for
the person, the discipline of being silent when nothing is wrong.

## Rules the code follows

- Standard library only, Python 3.9+. A hook that needs `pip install` will not run on the colleague's laptop.
- Fail-open: any exception inside a hook results in exit 0 with no output. The kit must never be the reason
  the agent stopped.
- Under 200 ms per hook on the hot path; the loop guard does not touch disk on a successful call unless a
  failure was recorded earlier in the session.
- Hook output is ASCII-escaped JSON: Windows pipes do not agree on an encoding.
- Nothing written outside: `~/.claude/settings.json`, `~/.claude/cto/`, `~/.claude/skills/session-wrapup/`,
  `~/.claude/CLAUDE.md` (only when absent) at install; `.claude/settings.local.json`, `.claude/cto-applied.json`
  and the repository's `info/exclude` of one project on an explicit `--apply`; the loop guard's counters in a
  per-user temp directory.
- Hooks are registered in exec form (`command` + `args`), never as a quoted shell string: Claude Code on
  Windows may run shell-form hooks through PowerShell, where a quoted path in command position is a parse error.
- Hook input is decoded from raw bytes as UTF-8. Windows pipes default to the legacy code page, and a
  Cyrillic path in the payload would otherwise switch the whole kit off silently.
- Settings are never "repaired": an unreadable `settings.json` stops the installer and `--fix` before any
  write, because replacing it with a minimal file would drop the person's permissions and hooks.
- Defaults are upper bounds. `sonnet` or `low` effort is cheaper than the kit's default and stays.
- The facts file is read only through a path that is inside the project and not a symlink, and it is
  injected with the note that it is data written by the project's people, not instructions.
- Every behaviour has a test that runs the script as a subprocess, the way Claude Code runs it.

## Review before the first release

The code went through a four-member review (two external models, OpenAI Codex and DeepSeek, Gemini for a
partial pass, and a Claude critic that checked each finding against the code and reproduced the Windows
ones). Twenty findings were confirmed and fixed before the first commit: UTF-8 stdin, exec-form hook
registration, `--fix` on a broken settings file, the symlinked facts file, `.git` as a file in worktrees,
the order of writes in `--apply`, false loop nudges on `grep`/`test` exit status, lowering a setting that
was already cheaper, the global-MCP line that made the hook noisy for everyone, and the install.sh
executable bit, among others. Deliberately left as is: a race between two hooks of the same session writing
the state file at the same instant (the lock covers it; a lost increment at worst delays a nudge by one call).
