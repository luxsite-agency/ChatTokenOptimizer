# -*- coding: utf-8 -*-
"""Economy check for Claude Code: the settings that decide how fast the token limit burns.

  python cto_economy.py --hook    SessionStart hook: silent when all is well (0 tokens), otherwise one
                                  line for the person and a short note for the agent.
  python cto_economy.py           report
  python cto_economy.py --fix     set model/effort/style to the kit's values (settings.json backed up)

Checks: default model (no 1M-context variant, no top-price model for routine work), effort level,
output style, the kit's hooks, size of the current chat history since the last /compact,
global MCP servers (their tool descriptions ride along in every turn of every chat).
The report also shows the prompt cache lifetime keys (promptCacheTtl, subagentPromptCacheTtl); they are
shown, not judged: the right value depends on how the account is billed (README, "Prompt cache lifetime").
"""
import glob
import json
import mmap
import os
import shutil
import sys
import tempfile
import time

WANT = {"model": "opus", "effortLevel": "medium", "outputStyle": "Concise"}
SESSION_WARN_MB = float(os.environ.get("CTO_SESSION_MB", "8"))
CLAUDE_HOME = os.environ.get("CTO_CLAUDE_HOME") or os.path.join(os.path.expanduser("~"), ".claude")
CLAUDE_JSON = os.environ.get("CTO_CLAUDE_JSON") or os.path.join(os.path.expanduser("~"), ".claude.json")
SETTINGS = os.path.join(CLAUDE_HOME, "settings.json")
KIT_HOOKS = ("cto_read_guard", "cto_loop_guard")
# Hooks of the same purpose installed before the kit count as present.
ALIASES = {"cto_read_guard": ("read-guard", "read_guard"), "cto_loop_guard": ()}


def t(uk, en):
    return uk if os.environ.get("CTO_LANG", "en") == "uk" else en


def live_mb(path):
    """MB of history actually in context: from the last compact boundary to the end of the file."""
    with open(path, "rb") as fh:
        size = os.fstat(fh.fileno()).st_size
        if not size:
            return 0.0
        m = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            i = m.rfind(b'"subtype":"compact_boundary"')
        finally:
            m.close()
    return (size - max(i, 0)) / 1e6


def load(path):
    with open(path, encoding="utf-8-sig") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("settings.json is not a JSON object")
    return data


def hook_commands(settings):
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return
    for entries in hooks.values():
        if not isinstance(entries, list):
            continue
        for e in entries:
            if not isinstance(e, dict) or not isinstance(e.get("hooks"), list):
                continue
            for h in e["hooks"]:
                if isinstance(h, dict):
                    args = h.get("args") if isinstance(h.get("args"), list) else []
                    yield " ".join([str(h.get("command", ""))] + [str(a) for a in args])


def save(path, data):
    """Atomic replace next to the target; the previous file is kept as a dated backup."""
    if os.path.exists(path):
        bak = path + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        n = 1
        while os.path.exists(bak):
            bak = "%s.bak-%s-%d" % (path, time.strftime("%Y%m%d-%H%M%S"), n)
            n += 1
        shutil.copy2(path, bak)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def settings_issues(s):
    issues = []
    model = str(s.get("model", ""))
    if not model or "[1m]" in model or "fable" in model.lower():
        issues.append(t("модель за замовчуванням «%s» — має бути opus без [1m]",
                        "default model '%s' -- should be opus without [1m]") % (model or "-"))
    if s.get("effortLevel") in ("high", "xhigh", "max"):
        issues.append(t("рівень зусиль %s — для рутини medium", "effort %s -- use medium for routine work")
                      % s["effortLevel"])
    if s.get("outputStyle") != "Concise":
        issues.append(t("стиль відповідей не Concise", "output style is not Concise"))
    cmds = list(hook_commands(s))
    for name in KIT_HOOKS:
        if not any(name in c or any(a in c for a in ALIASES[name]) for c in cmds):
            issues.append(t("немає хука %s (запусти install)", "hook %s missing (run install)") % name)
    return issues


def global_mcp():
    try:
        return list((load(CLAUDE_JSON).get("mcpServers") or {}).keys())
    except Exception:
        return []


def session_hook():
    try:
        data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    issues = []
    try:
        issues += settings_issues(load(SETTINGS))
    except Exception:
        issues.append(t("settings.json не читається — запусти install.py", "settings.json unreadable -- run install.py"))
    model = data.get("model")
    if isinstance(model, dict):
        model = model.get("id") or model.get("display_name") or ""
    model = str(model or "")
    if "fable" in model.lower():
        issues.append(t("цей чат на найдорожчій моделі (Fable) — для рутини переключи на Opus",
                        "this chat runs the top-price model (Fable) -- switch to Opus for routine work"))
    if "[1m]" in model:
        issues.append(t("цей чат з вікном 1 млн токенів — переключи на opus без [1m]",
                        "this chat has the 1M-token window -- switch to opus without [1m]"))
    tp = data.get("transcript_path") or ""
    try:
        mb = live_mb(tp) if tp and os.path.isfile(tp) and data.get("source") != "compact" else 0
        if mb > SESSION_WARN_MB:
            issues.append(t("історія чату %.0f МБ — після поточної задачі /compact або новий чат",
                            "chat history is %.0f MB -- /compact or a new chat after this task") % mb)
    except (OSError, ValueError):
        pass
    if not issues:
        return
    joined = "; ".join(issues)
    print(json.dumps({
        "systemMessage": t("Економ-режим: ", "Economy mode: ") + joined + t(
            ". Виправити: \"%s\" \"%s\" --fix", ". Fix: \"%s\" \"%s\" --fix") % (sys.executable, os.path.abspath(__file__)),
        "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": t(
            "Перевірка економного режиму знайшла: %s. Скажи про це людині одним рядком на початку відповіді. "
            "Великі файли читай частинами (Grep, offset/limit), після великої задачі пропонуй /compact.",
            "The economy check found: %s. Mention it to the person in one line at the start of your reply. "
            "Read large files in parts (Grep, offset/limit); suggest /compact after a big task.") % joined}},
        ensure_ascii=True))


def report(fix):
    print(t("# ChatTokenOptimizer — економ-режим", "# ChatTokenOptimizer -- economy check"),
          time.strftime("%Y-%m-%d %H:%M"))
    readable = True
    try:
        s = load(SETTINGS)
    except FileNotFoundError:
        s = {}
    except (OSError, ValueError) as e:
        # A broken settings.json is never replaced: --fix would silently drop permissions and hooks.
        print("!!", SETTINGS, e)
        s, readable = {}, False
    for k, v in WANT.items():
        cur = s.get(k)
        ok = (k == "model" and bool(cur) and "[1m]" not in str(cur) and "fable" not in str(cur).lower()) or              (k == "effortLevel" and cur not in ("high", "xhigh", "max")) or cur == v
        print("  %s %-12s %s" % ("OK" if ok else "!!", k, cur if cur is not None else "-"))
    for i in settings_issues(s):
        if "hook" in i or "хук" in i:
            print("  !!", i)
    print("  %s %-12s %s" % ("--", "cache TTL", t(
        "головний чат %s, субагенти %s (не задано = 1h на підписці в межах плану, 5m на API-ключі чи usage credits)",
        "main %s, subagents %s (unset = 1h on a subscription within plan usage, 5m on an API key or usage credits)")
        % (s.get("promptCacheTtl") or "-", s.get("subagentPromptCacheTtl") or "-")))
    mcp = global_mcp()
    print("  %s %-12s %s" % ("OK" if not mcp else "!!", "global MCP", ", ".join(mcp) or "0"))
    big, now = [], time.time()
    for f in glob.glob(os.path.join(CLAUDE_HOME, "projects", "*", "*.jsonl")):
        try:
            if now - os.path.getmtime(f) > 7 * 86400:
                continue
            mb = live_mb(f)
        except (OSError, ValueError):
            continue
        if mb > SESSION_WARN_MB:
            big.append((mb, os.path.basename(os.path.dirname(f))[-32:]))
    big.sort(reverse=True)
    print("  %s %-12s %s" % ("OK" if not big else "!!", t("довгі чати", "long chats"),
                             "; ".join("%s %.0f MB" % (n, mb) for mb, n in big[:6]) or "0"))
    if fix and not readable:
        print(t("  !! --fix пропущено: спершу полагодь settings.json", "  !! --fix skipped: repair settings.json first"))
    elif fix:
        changed = {}
        m = str(s.get("model") or "")
        if not m or "[1m]" in m or "fable" in m.lower():
            changed["model"] = WANT["model"]
        if s.get("effortLevel") in ("high", "xhigh", "max"):
            changed["effortLevel"] = WANT["effortLevel"]
        if s.get("outputStyle") != WANT["outputStyle"]:
            changed["outputStyle"] = WANT["outputStyle"]
        if changed:
            s.update(changed)
            save(SETTINGS, s)
            print(t("  -> виправлено: %s. Діє з нового чату.", "  -> fixed: %s. Applies to new chats.")
                  % ", ".join(changed))


if __name__ == "__main__":
    if "--hook" in sys.argv:
        try:
            session_hook()
        except Exception:
            pass
        sys.exit(0)
    report("--fix" in sys.argv)
