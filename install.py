# -*- coding: utf-8 -*-
"""Install ChatTokenOptimizer into Claude Code (user scope).

  python install.py              install / update
  python install.py --dry-run    show what would change, change nothing
  python install.py --uninstall  remove the kit's hooks and files (skills and your CLAUDE.md stay)
  options: --keep-settings (do not touch model/effort/style), --force-skills (replace skills that exist),
           --cache-ttl 5m|1h, --subagent-cache-ttl 5m|1h (prompt cache lifetime; opt-in, see README)

What it does: copies hooks/ and tools/ to ~/.claude/cto/, skills/ to ~/.claude/skills/,
adds the hooks to ~/.claude/settings.json (backup first, other content kept), lowers expensive
defaults (a 1M-context or top-tier model, high effort, verbose style) to opus / medium / Concise,
and puts the CLAUDE.md template in place if you have none.
Nothing is downloaded, nothing runs in the background, no network.
"""
import json
import os
import shutil
import sys
import tempfile
import time

KIT = os.path.dirname(os.path.abspath(__file__))
CLAUDE_HOME = os.environ.get("CTO_CLAUDE_HOME") or os.path.join(os.path.expanduser("~"), ".claude")
DEST = os.path.join(CLAUDE_HOME, "cto")
HOOK_DIR = os.path.join(DEST, "hooks")
SETTINGS = os.path.join(CLAUDE_HOME, "settings.json")
# Upper bounds, not targets: a cheaper setting is left alone.
DEFAULTS = {"model": "opus", "effortLevel": "medium", "outputStyle": "Concise"}
EXPENSIVE_EFFORT = ("high", "xhigh", "max")
# (event, matcher, script, extra args, timeout s, names of hooks with the same job installed earlier)
HOOKS = [
    ("SessionStart", None, "cto_economy.py", ["--hook"], 10, ("economy_check",)),
    ("SessionStart", None, "cto_facts.py", [], 10, ()),
    ("PreToolUse", "Read", "cto_read_guard.py", [], 10, ("read-guard", "read_guard")),
    ("PostToolUse", None, "cto_loop_guard.py", [], 10, ()),
    ("PostToolUseFailure", None, "cto_loop_guard.py", [], 10, ()),
]

# Opt-in: the right cache lifetime depends on how the account is billed (README, "Prompt cache lifetime").
CACHE_FLAGS = {"--cache-ttl": "promptCacheTtl", "--subagent-cache-ttl": "subagentPromptCacheTtl"}
CACHE_VALUES = ("5m", "1h")

DRY = "--dry-run" in sys.argv


def say(msg):
    print(("[dry-run] " if DRY else "") + msg)


def norm(p):
    return os.path.normcase(os.path.normpath(p)).replace("\\", "/")


def interpreter():
    """A Python that will still exist after this process: the base interpreter when run from a venv."""
    exe = sys.executable
    if sys.prefix != sys.base_prefix:
        name = "python.exe" if os.name == "nt" else "python3"
        cand = os.path.join(sys.base_prefix, name) if os.name == "nt" else os.path.join(sys.base_prefix, "bin", name)
        if os.path.exists(cand):
            say("running inside a virtualenv; hooks will use %s" % cand)
            exe = cand
    if "WindowsApps" in exe and not os.path.exists(exe):
        sys.exit("The 'python' on PATH is the Microsoft Store alias. Install Python from python.org or run: py -3 install.py")
    return exe


def hook_entry(exe, script, args, timeout):
    # Exec form: no shell parsing, so paths with spaces or non-ASCII letters are safe in Bash and PowerShell.
    return {"type": "command", "command": exe, "args": [os.path.join(HOOK_DIR, script)] + list(args), "timeout": timeout}


def is_kit_hook(h):
    if not isinstance(h, dict):
        return False
    args = h.get("args") if isinstance(h.get("args"), list) else []
    first = norm(str(args[0])) if args else ""
    cmd = norm(str(h.get("command", "")))
    marker = norm(HOOK_DIR) + "/"
    return first.startswith(marker) or marker in cmd


def load_settings():
    if not os.path.exists(SETTINGS):
        return {}
    with open(SETTINGS, encoding="utf-8-sig") as fh:
        s = json.load(fh)
    if not isinstance(s, dict):
        raise ValueError("settings.json is not a JSON object")
    return s


def normalize_hooks(s):
    """Keep every entry we understand (list of {hooks: [ {...} ]}); report and drop what we do not."""
    hooks = s.get("hooks")
    if hooks is None:
        return
    if not isinstance(hooks, dict):
        raise ValueError("settings.json: 'hooks' is not an object; fix it by hand first")
    for event in list(hooks):
        entries = hooks[event]
        if not isinstance(entries, list):
            raise ValueError("settings.json: hooks.%s is not a list; fix it by hand first" % event)
        clean = []
        for e in entries:
            if isinstance(e, dict) and isinstance(e.get("hooks"), list):
                clean.append(e)
            else:
                say("hooks.%s: skipped an entry of unexpected shape" % event)
        hooks[event] = clean


def save_settings(s):
    if DRY:
        return
    os.makedirs(CLAUDE_HOME, exist_ok=True)
    if os.path.exists(SETTINGS):
        bak = SETTINGS + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        n = 1
        while os.path.exists(bak):
            bak = "%s.bak-%s-%d" % (SETTINGS, time.strftime("%Y%m%d-%H%M%S"), n)
            n += 1
        shutil.copy2(SETTINGS, bak)
        say("backup: " + bak)
    fd, tmp = tempfile.mkstemp(dir=CLAUDE_HOME, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(s, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, SETTINGS)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def copy_tree(src, dst):
    if DRY:
        return
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def strip_kit_hooks(s):
    hooks = s.get("hooks") or {}
    for event in list(hooks):
        kept = []
        for e in hooks[event]:
            e = dict(e)
            e["hooks"] = [h for h in e.get("hooks", []) if not is_kit_hook(h)]
            if e["hooks"]:
                kept.append(e)
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if hooks:
        s["hooks"] = hooks
    else:
        s.pop("hooks", None)


def same_job_exists(s, event, matcher, aliases):
    for e in (s.get("hooks") or {}).get(event, []):
        if matcher and e.get("matcher") not in (matcher, None, ""):
            continue
        for h in e.get("hooks", []):
            if not isinstance(h, dict):
                continue
            blob = " ".join([str(h.get("command", ""))] + [str(a) for a in (h.get("args") or [])])
            if any(a in blob for a in aliases):
                return True
    return False


def lower_defaults(s):
    changed = {}
    model = str(s.get("model") or "")
    if not model or "[1m]" in model or "fable" in model.lower():
        changed["model"] = (model or "-", DEFAULTS["model"])
    if s.get("effortLevel") in EXPENSIVE_EFFORT:
        changed["effortLevel"] = (s["effortLevel"], DEFAULTS["effortLevel"])
    if s.get("outputStyle") != DEFAULTS["outputStyle"]:
        changed["outputStyle"] = (s.get("outputStyle", "-"), DEFAULTS["outputStyle"])
    for k, (old, new) in changed.items():
        say("%s: %s -> %s" % (k, old, new))
        s[k] = new


def flag_value(flag):
    """Value of `--flag X` or `--flag=X`; None when the flag is absent."""
    for i, a in enumerate(sys.argv):
        if a == flag:
            if i + 1 >= len(sys.argv) or sys.argv[i + 1].startswith("--"):
                sys.exit("%s needs a value: %s" % (flag, " or ".join(CACHE_VALUES)))
            return sys.argv[i + 1]
        if a.startswith(flag + "="):
            return a[len(flag) + 1:]
    return None


def cache_ttl_requests():
    """{settings key: value} for the cache flags given; exits on a value the API does not accept."""
    wanted = {}
    for flag, key in CACHE_FLAGS.items():
        val = flag_value(flag)
        if val is None:
            continue
        if val not in CACHE_VALUES:
            sys.exit("%s: use %s, got %r" % (flag, " or ".join(CACHE_VALUES), val))
        wanted[key] = val
    return wanted


def apply_cache_ttl(s, wanted):
    for key, val in wanted.items():
        if s.get(key) != val:
            say("%s: %s -> %s" % (key, s.get(key, "-"), val))
            s[key] = val


def install():
    if sys.version_info < (3, 9):
        sys.exit("Python 3.9+ needed, found %s" % sys.version.split()[0])
    say("kit: " + KIT)
    # Read and validate settings before touching any file.
    try:
        s = load_settings()
        normalize_hooks(s)
    except (OSError, ValueError) as e:
        sys.exit("Cannot proceed: %s" % e)
    cache = cache_ttl_requests()  # validated before any file is touched
    exe = interpreter()

    say("copy hooks/ tools/ -> " + DEST)
    for sub in ("hooks", "tools"):
        copy_tree(os.path.join(KIT, sub), os.path.join(DEST, sub))
    for name in sorted(os.listdir(os.path.join(KIT, "skills"))):
        src = os.path.join(KIT, "skills", name)
        dst = os.path.join(CLAUDE_HOME, "skills", name)
        if not os.path.isdir(src):
            continue
        if os.path.isdir(dst) and "--force-skills" not in sys.argv:
            say("skill %s exists, kept (use --force-skills to replace)" % name)
            continue
        if os.path.isdir(dst) and not DRY:
            os.rename(dst, dst + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
        say("skill " + name)
        copy_tree(src, dst)

    strip_kit_hooks(s)
    for event, matcher, script, args, timeout, aliases in HOOKS:
        if aliases and same_job_exists(s, event, matcher, aliases):
            say("hook %s skipped: you already have one with the same job on %s" % (script, event))
            continue
        entry = {"hooks": [hook_entry(exe, script, args, timeout)]}
        if matcher:
            entry["matcher"] = matcher
        s.setdefault("hooks", {}).setdefault(event, []).append(entry)
        say("hook %-18s %s" % (event, script))
    if "--keep-settings" not in sys.argv:
        lower_defaults(s)
    apply_cache_ttl(s, cache)
    save_settings(s)

    user_md = os.path.join(CLAUDE_HOME, "CLAUDE.md")
    tpl = os.path.join(KIT, "templates", "CLAUDE.global.md")
    if not os.path.exists(user_md):
        say("CLAUDE.md template -> " + user_md)
        if not DRY:
            shutil.copy2(tpl, user_md)
    else:
        say("your CLAUDE.md kept; compare with " + tpl)
    say('done. Open a new chat; check: "%s" "%s"' % (exe, os.path.join(HOOK_DIR, "cto_economy.py")))


def uninstall():
    try:
        s = load_settings()
        normalize_hooks(s)
    except (OSError, ValueError) as e:
        sys.exit("Cannot proceed: %s" % e)
    strip_kit_hooks(s)
    save_settings(s)
    say("hooks removed from " + SETTINGS)
    if os.path.isdir(DEST):
        say("remove " + DEST)
        if not DRY:
            shutil.rmtree(DEST)
    say("skills, CLAUDE.md and the model/effort/style/cache-lifetime values were left as they are")


if __name__ == "__main__":
    uninstall() if "--uninstall" in sys.argv else install()
