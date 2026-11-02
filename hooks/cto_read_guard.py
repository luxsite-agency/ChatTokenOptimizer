# -*- coding: utf-8 -*-
"""PreToolUse hook on Read: refuse to read a large file whole.

A rule in CLAUDE.md can be ignored, a hook cannot. A file bigger than CTO_READ_KB (default 100 KB)
read without offset/limit is denied with a hint: Grep first, then Read with offset+limit.
Images, PDFs and notebooks pass. Any error lets the call through (fail-open).
"""
import json
import os
import sys

LIMIT_KB = int(os.environ.get("CTO_READ_KB", "100"))
SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico", ".pdf", ".ipynb"}


def t(uk, en):
    return uk if os.environ.get("CTO_LANG", "en") == "uk" else en


def main():
    data = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    inp = data.get("tool_input") or {}
    path = inp.get("file_path") or ""
    if not path or inp.get("limit") or inp.get("offset") or inp.get("pages"):
        return
    if os.path.splitext(path)[1].lower() in SKIP_EXT:
        return
    if not os.path.isabs(path) and data.get("cwd"):
        path = os.path.join(data["cwd"], path)
    size = os.path.getsize(path)
    if size <= LIMIT_KB * 1024:
        return
    kb = size // 1024
    reason = t(
        "ChatTokenOptimizer: файл %s — %d КБ (> %d КБ), цілком не читаю, щоб не витрачати токени. "
        "Знайди потрібне місце (Grep, `grep -n`, `sed -n 'A,Bp'`) і читай Read з offset+limit. "
        "Якщо файл справді потрібен увесь — Read з явними offset=1 і limit." % (path, kb, LIMIT_KB),
        "ChatTokenOptimizer: %s is %d KB (> %d KB); not reading it whole to save tokens. "
        "Locate the part you need (Grep, `grep -n`, `sed -n 'A,Bp'`) and Read with offset+limit. "
        "If you really need all of it, Read with explicit offset=1 and limit." % (path, kb, LIMIT_KB))
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                             "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}, ensure_ascii=True))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
