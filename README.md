# ChatTokenOptimizer: Deterministic Token Hygiene for Claude Code

<p align="center">
  <img src="https://img.shields.io/badge/Claude%20Code-hooks%20%2B%20skill-d97757.svg" alt="Claude Code hooks and skill">
  <img src="https://img.shields.io/badge/python-3.9%2B-3776ab.svg" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/dependencies-none-success.svg" alt="Standard library only">
  <img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="Apache 2.0 license">
</p>

<p align="center">
  A small kit of Claude Code hooks, one skill and a few templates that stop the four ways a team burns
  its token limit: <b>expensive defaults</b>, <b>reading big files whole</b>, <b>retrying the same
  failing command</b>, and <b>re-learning the project every session</b>. No model calls of its own,
  no network, no copies of your transcripts. Every rule is enforced by a hook, not suggested in a prompt.
</p>

<p align="center">
  <img src="docs/assets/overview.svg" width="100%" alt="One Claude Code session on a timeline: at session start the kit checks the economy settings and injects verified project facts; when the agent reads a file over 100 KB without an offset the read guard denies it and asks for Grep plus a slice; when the same tool call or the same error repeats three times the loop guard nudges the agent once to change approach; at the end of the task the wrap-up skill writes facts, gotchas, solutions and the person's feedback for the next session. Below: the tool-usage report across sessions, and what the kit never does.">
</p>

## Why

Working with Claude Code across a team, the token limit was going not to the work but to the overhead
around it. The same four patterns showed up in every long session:

| Pattern | What it costs | What the kit does |
|---|---|---|
| A chat left on the 1M-context model, `high` effort, verbose style | every turn carries up to a million tokens of history at a higher rate | **economy check** at session start: one line to the person when a setting is off, silent otherwise; `--fix` resets model, effort and style |
| `Read` on a 300 KB log or export | tens of thousands of tokens for a file where only ten lines matter | **read guard**: a file over 100 KB without `offset`/`limit` is denied with a hint: Grep first, then a slice |
| The same failing command run five times with tiny variations | a turn of 20 tool calls that a person would have stopped after the second | **loop guard**: the same action or the same error three times in a session gets one nudge to change approach; never more than three nudges a session |
| Every session rediscovers how to run the tests and where the build lives | a few thousand tokens of exploration per session, per person | **facts**: a 1500-character `.claude/facts.md` the person approved is injected at session start; the **wrap-up skill** keeps it current |

Across sessions, `tools/cto_tool_usage.py` reads which tools a project's sessions actually called and
lists the configured MCP servers that were never used. Their tool descriptions ride along in every turn;
denying them in that project is one reversible command.

## What sets it apart

The design borrows three ideas from Microsoft's
[TokenEconomizer](https://github.com/microsoft/TokenEconomizer): facts learned per repository, pruning of
unused tools, and intervention in failure loops. It re-implements them for a team whose sessions touch client
data, production servers and credentials, and that is what the differences come from.

| | ChatTokenOptimizer | Prompt rules in `CLAUDE.md` alone | TokenEconomizer |
|---|---|---|---|
| Who enforces the rule | a hook: the call is denied or the context is amended before the model sees the result | the model, which forgets after a long turn | hooks plus a judge model |
| Cost of the kit itself | zero model calls; hooks run in milliseconds and print nothing when all is well | zero, but rules are re-read in every turn | a `claude -p` judge call per suspected loop, billed to your account |
| What it stores | 16-hex hashes and integer counters, deleted after two days | nothing | session records, transcript paths, injected text; the micro-skill distiller keeps raw command and output blobs |
| What it reads | the current hook event, `.claude/facts.md`, tool names in session logs (for the usage report) | nothing | whole transcripts, folded in a background process |
| Changes to your settings | only at install, with a backup; project deny lists only on an explicit `--apply`, with `--restore` | none | deny lists applied automatically after an idle period |
| Background processes | none | none | fold and judge jobs; on Windows the spawn spec writes the whole environment to a temp file |
| Secrets exposure | none by construction: no transcript copies, no environment dumps, no model side calls | none | possible through the blobs, the digest sent to the judge, and the environment dump |
| Where project knowledge comes from | a person writes it (or approves what the wrap-up skill proposes), capped at 1500 characters | the person | mined from transcripts, confirmed after three sessions |
| Dependencies and install | Python standard library, `git clone` + `python install.py`, `--dry-run` shows every change, `--uninstall` reverts | none | plugin marketplace per agent |
| Agents | Claude Code | Claude Code | Claude Code, Codex, Copilot CLI, Pi, Cursor, opencode |

The trade-off is deliberate: a deterministic loop guard is coarser than a judge model and will miss loops
whose error text changes each time, and facts written by hand cover less than facts mined from every session.
In exchange the kit can be installed on a colleague's laptop without reading its code first, and its state
file can be pasted into a bug report. [docs/DESIGN.md](docs/DESIGN.md) lists each borrowed idea and the
change made to it.

## How it works

Claude Code runs the kit's scripts at four points of a session. Each is a short Python process that reads the
event on stdin and either prints nothing (the common case, zero tokens) or one JSON line.

| Event | Script | When it speaks |
|---|---|---|
| `SessionStart` | `hooks/cto_economy.py --hook` | the default model has `[1m]` or is the top-price tier; effort is `high`+; style is not `Concise`; a kit hook is missing; the chat's history since the last `/compact` is over 8 MB. A cheaper setting than the kit's default is never flagged |
| `SessionStart` | `hooks/cto_facts.py` | `.claude/facts.md` exists in the project (cwd or a parent up to the repo root): its text goes into the session as context, capped at 1500 characters |
| `PreToolUse` on `Read` | `hooks/cto_read_guard.py` | the file is over 100 KB and the call has no `offset`/`limit`: the read is denied with a hint. Images, PDFs and notebooks pass |
| `PostToolUse`, `PostToolUseFailure` | `hooks/cto_loop_guard.py` | the same action failed 3 times in a row, or the same error text came back from 4 different actions: one `additionalContext` nudge and one line to the person. A success resets that action's count. Interrupts, a bare "Exit code N" with no output, and exit status 1 of `grep`/`test`/`diff`-style queries are not failures |

The skill `/session-wrapup` closes a task: facts to `.claude/facts.md`, traps to `knowledge/gotchas.md`,
repeatable wins to `knowledge/solutions.md`, the person's feedback to the project `CLAUDE.md`, then a
secrets check and a suggestion to `/compact`.

What the kit never does: call a model, open a network connection, copy or mine transcripts, store commands,
paths or error text (the loop guard keeps 16-hex hashes, counters and a timestamp in a per-user temp directory),
change settings outside the user's `~/.claude/settings.json` at install time, or block the agent when a hook
itself fails (every hook is fail-open). Hooks are registered in exec form (`command` = the interpreter,
`args` = the script), so paths with spaces or non-ASCII letters work in Git Bash and PowerShell alike.

## Quick start

Requirements: Claude Code 2.1 or newer (hooks with `args`; verified on 2.1.302), Python 3.9+ from python.org
or your package manager (standard library only), Windows, macOS or Linux.

```bash
git clone https://github.com/luxsite-agency/ChatTokenOptimizer.git
cd ChatTokenOptimizer
python install.py --dry-run   # shows every change, makes none
python install.py             # installs
```

Windows: `.\install.ps1` (if PowerShell refuses to run scripts:
`powershell -ExecutionPolicy Bypass -File .\install.ps1`); macOS and Linux: `./install.sh`. Both call
`install.py` with the same options.

The installer copies `hooks/` and `tools/` to `~/.claude/cto/`, the skill to `~/.claude/skills/`, adds the
hooks to `~/.claude/settings.json` (your file is backed up first and everything else in it is kept), lowers
expensive defaults only (a `[1m]` or top-tier model to `opus`, `high`+ effort to `medium`, any style to
`Concise`; a cheaper model or `low` effort is left alone), and puts `templates/CLAUDE.global.md` at
`~/.claude/CLAUDE.md` when you have none. A hook you already have for the same job on the same event (for
example your own read guard) is detected and the kit's version is skipped. A malformed `settings.json` stops
the installer before it copies anything.

Options: `--keep-settings` leaves model, effort and style alone; `--force-skills` replaces an existing
`session-wrapup` skill (the old one is renamed, not deleted); `--uninstall` removes the hooks and
`~/.claude/cto/` (skills, `CLAUDE.md` and the model/effort/style values stay as they are).

Open a new chat. When everything is in order the kit is silent. Check by hand:

```bash
python ~/.claude/cto/hooks/cto_economy.py          # report
python ~/.claude/cto/hooks/cto_economy.py --fix    # reset model, effort, style (settings.json backed up)
```

### Per project

```bash
cp templates/CLAUDE.project.md <project>/CLAUDE.md          # fill in the blanks
mkdir -p <project>/.claude <project>/knowledge
cp templates/knowledge/facts.md <project>/.claude/facts.md  # replace the examples with your facts
cp templates/knowledge/gotchas.md templates/knowledge/solutions.md <project>/knowledge/
```

From then on, end each task with `/session-wrapup`.

### Which MCP servers a project never uses

```bash
python ~/.claude/cto/tools/cto_tool_usage.py --project <path> --days 30
python ~/.claude/cto/tools/cto_tool_usage.py --all                      # one line per project
python ~/.claude/cto/tools/cto_tool_usage.py --project <path> --apply   # deny the unused ones in that project
python ~/.claude/cto/tools/cto_tool_usage.py --project <path> --restore # undo exactly that
```

The report parses the session logs under `~/.claude/projects/` locally and keeps only tool names and
counts per working directory; message text and tool inputs are dropped in memory and never printed or
stored. A session that moved between projects is counted per directory. `--apply` refuses with fewer than
5 sessions, writes `permissions.deny` entries to the project's `.claude/settings.local.json` (backed up when
it existed), records what it added in `.claude/cto-applied.json` first so `--restore` always knows what to
undo, and lists the settings file, its backups and the record in the repository's `info/exclude` (worktrees
included). It never touches global settings. After `--apply`, open a new chat in that project and check
`/context`: the denied servers' tools should be gone from the tool list. If your Claude Code version only
blocks the calls but still lists the tools, disable those servers for the project in the connector menu
instead; the deny list alone will not save tokens there.

## Settings

All by environment variable; none is required.

| Variable | Default | Meaning |
|---|---|---|
| `CTO_LANG` | `en` | `uk` for Ukrainian messages to the person and the agent |
| `CTO_READ_KB` | `100` | file size above which a whole-file `Read` is denied |
| `CTO_LOOP_N` | `3` | identical failures of one action before a nudge; the same error from different actions nudges at `N+1` |
| `CTO_LOOP_MAX` | `3` | nudges per session |
| `CTO_LOOP_GUARD` | | `off` disables the loop guard |
| `CTO_FACTS` | | `off` disables fact injection |
| `CTO_FACTS_CHARS` | `1500` | cap on injected facts |
| `CTO_SESSION_MB` | `8` | chat history since the last `/compact` that triggers the "time to compact" line |
| `CTO_STATE_DIR` | `<tmp>/cto-loop-guard-<user hash>` | where the loop guard keeps its per-session counters (hashes only; files older than 2 days are removed when the hook next runs) |
| `CTO_CLAUDE_HOME`, `CTO_CLAUDE_JSON` | `~/.claude`, `~/.claude.json` | overrides used by the tests |

## Repository

```
hooks/      cto_economy.py  cto_facts.py  cto_read_guard.py  cto_loop_guard.py
tools/      cto_tool_usage.py
skills/     session-wrapup/SKILL.md
templates/  CLAUDE.global.md  CLAUDE.project.md  knowledge/{facts,gotchas,solutions}.md
tests/      unittest, standard library: python -m unittest discover -s tests
docs/       DESIGN.md (what was borrowed, what was left out, and why)  assets/
install.py  install.ps1  install.sh
```

## License

Apache 2.0; see [LICENSE](LICENSE).
