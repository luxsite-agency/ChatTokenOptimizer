"""Which tools and MCP servers a project's Claude Code sessions actually call.

Reads the session logs under ~/.claude/projects locally and keeps only tool names and counts:
message text and tool inputs are parsed in memory and dropped. The report lists configured MCP
servers with zero calls; `--apply` denies them in that project's .claude/settings.local.json
(reversible with `--restore`). Global settings are never touched.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

EN = os.environ.get('CTO_LANG', 'en') != 'uk'
MAX_LINE = 8 * 1024 * 1024    # a longer JSONL line is a file dump inside a tool result; skip it
EXCLUDES = ['.claude/settings.local.json', '.claude/settings.local.json.bak-*', '.claude/cto-applied.json']

# Windows consoles default to a legacy code page; the report is UTF-8 whatever the console says.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        _stream.reconfigure(encoding='utf-8', errors='replace')


def text(uk, en):
    return en if EN else uk


def normalized(path):
    return os.path.normcase(os.path.realpath(str(path))).replace('\\', '/').rstrip('/')


def read_json(path):
    try:
        with path.open(encoding='utf-8-sig') as stream:
            data = json.load(stream)
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError(text('%s: очікувався JSON-об\'єкт', '%s: expected a JSON object') % path)
    return data


def write_json(path, data):
    """Atomic: temp file in the same directory, fsync, replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(json.dumps(data, ensure_ascii=True, indent=2) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def scan(home, days):
    """[(session_id, {cwd: Counter(tool -> calls)})] for sessions modified within `days`."""
    sessions = []
    cutoff = time.time() - days * 86400
    for path in (home / 'projects').glob('*/*.jsonl'):
        try:
            if path.stat().st_mtime < cutoff:
                continue
            by_cwd = {}
            with path.open('rb') as stream:
                for raw in stream:
                    if len(raw) > MAX_LINE:
                        continue
                    try:
                        row = json.loads(raw.decode('utf-8', errors='replace'))
                    except ValueError:
                        continue
                    if not isinstance(row, dict) or not isinstance(row.get('cwd'), str):
                        continue
                    cwd = normalized(row['cwd'])
                    counts = by_cwd.setdefault(cwd, Counter())
                    message = row.get('message')
                    if not isinstance(message, dict) or row.get('type', message.get('role')) != 'assistant':
                        continue
                    content = message.get('content')
                    if not isinstance(content, list):
                        continue
                    for item in content:
                        if isinstance(item, dict) and item.get('type') == 'tool_use' and isinstance(item.get('name'), str):
                            counts[item['name']] += 1
            if by_cwd:
                sessions.append((path.stem, by_cwd))
        except OSError:
            continue
    return sessions


def report(project, sessions, config):
    root = normalized(project)
    counts, total = Counter(), 0
    for _, by_cwd in sessions:
        hit = False
        for cwd, tools in by_cwd.items():
            if cwd == root or cwd.startswith(root + '/'):
                counts.update(tools)
                hit = True
        total += hit
    configured = set(config.get('mcpServers') or {})
    for key, value in (config.get('projects') or {}).items():
        if normalized(key) == root and isinstance(value, dict):
            configured.update(value.get('mcpServers') or {})
    configured.update(read_json(Path(project) / '.mcp.json').get('mcpServers') or {})
    used = Counter()
    for name, count in counts.items():
        parts = name.split('__', 2)
        if len(parts) == 3 and parts[0] == 'mcp' and parts[1]:
            used[parts[1]] += count
    return {'project': str(project), 'sessions': total, 'tools': dict(counts.most_common()),
            'top_tools': dict(counts.most_common(15)), 'unused_servers': sorted(configured - used.keys()),
            'used_servers': dict(sorted(used.items()))}


def git_exclude(project):
    """Path of the repository's info/exclude, worktrees included; None when not a git checkout."""
    if not (project / '.git').exists():
        return None
    try:
        out = subprocess.run(['git', 'rev-parse', '--git-path', 'info/exclude'], cwd=str(project),
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            p = Path(out.stdout.strip())
            return p if p.is_absolute() else project / p
    except (OSError, subprocess.SubprocessError):
        pass
    return project / '.git' / 'info' / 'exclude' if (project / '.git').is_dir() else None


def add_excludes(project):
    exclude = git_exclude(project)
    if exclude is None:
        return
    existing = exclude.read_text(encoding='utf-8') if exclude.exists() else ''
    missing = [e for e in EXCLUDES if e not in existing.splitlines()]
    if missing:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        with exclude.open('a', encoding='utf-8') as stream:
            stream.write(('\n' if existing and not existing.endswith('\n') else '') + '\n'.join(missing) + '\n')


def apply(project, result, minimum):
    if result['sessions'] < minimum:
        raise ValueError(text('Лише %d сесій, потрібно %d для --apply.', 'Only %d sessions; %d needed for --apply.')
                         % (result['sessions'], minimum))
    settings = project / '.claude' / 'settings.local.json'
    record = settings.with_name('cto-applied.json')
    data = read_json(settings)
    permissions = data.setdefault('permissions', {})
    if not isinstance(permissions, dict) or not isinstance(permissions.get('deny', []), list):
        raise ValueError(text('%s: permissions.deny не список', '%s: permissions.deny is not a list') % settings)
    deny = permissions.setdefault('deny', [])
    added = ['mcp__' + name for name in result['unused_servers'] if 'mcp__' + name not in deny]
    if not added:
        return []
    previous = read_json(record).get('added') or []
    # The exclude entries and the record go first: if the settings write fails after them,
    # nothing is lost and --restore still knows what to undo.
    add_excludes(project)
    write_json(record, {'added': list(dict.fromkeys(list(previous) + added))})
    if settings.exists():
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        shutil.copy2(settings, settings.with_name(settings.name + '.bak-' + stamp))
    deny.extend(added)
    write_json(settings, data)
    return added


def restore(project):
    settings = project / '.claude' / 'settings.local.json'
    record = settings.with_name('cto-applied.json')
    if not record.exists():
        raise ValueError(text('%s не знайдено: нічого відновлювати.', '%s not found: nothing to restore.') % record)
    added = read_json(record).get('added')
    if not isinstance(added, list):
        raise ValueError(text('%s: поле added не список', '%s: field added is not a list') % record)
    data = read_json(settings)
    permissions = data.get('permissions')
    if isinstance(permissions, dict) and isinstance(permissions.get('deny'), list):
        permissions['deny'] = [item for item in permissions['deny'] if item not in added]
        write_json(settings, data)
    record.unlink()
    return added


def main():
    parser = argparse.ArgumentParser(description=text('Використання інструментів Claude Code', 'Claude Code tool usage'))
    parser.add_argument('--project', type=Path, default=Path.cwd())
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--days', type=int, default=30)
    parser.add_argument('--min-sessions', type=int, default=5)
    parser.add_argument('--json', action='store_true')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    if args.all and (args.apply or args.restore):
        parser.error(text('--all доступний лише для звіту.', '--all is report-only.'))
    project = args.project.resolve()
    if args.restore:
        removed = restore(project)
        print(json.dumps({'restored': removed}) if args.json
              else text('Відновлено, прибрано: %s', 'Restored, removed: %s') % (', '.join(removed) or '-'))
        return
    home = Path(os.environ.get('CTO_CLAUDE_HOME', str(Path.home() / '.claude')))
    config = read_json(Path(os.environ.get('CTO_CLAUDE_JSON', str(Path.home() / '.claude.json'))))
    sessions = scan(home, args.days)
    projects = [project]
    if args.all:
        roots = {cwd for _, by_cwd in sessions for cwd in by_cwd} | set(config.get('projects') or {})
        projects = [Path(p) for p in sorted(roots)]
    results = [report(p, sessions, config) for p in projects]
    added = apply(project, results[0], args.min_sessions) if args.apply else None
    if args.json:
        out = results if args.all else dict(results[0], applied=added)
        print(json.dumps(out, ensure_ascii=True))
        return
    for result in results:
        if args.all:
            print('{}: {} {}; {}: {}; {}: {}'.format(
                result['project'], result['sessions'], text('сесій', 'sessions'),
                text('невикористані MCP', 'unused MCP'), ', '.join(result['unused_servers']) or '-',
                text('використані MCP', 'used MCP'), ', '.join(result['used_servers']) or '-'))
            continue
        print(text('Проаналізовано сесій: ', 'Sessions analysed: ') + str(result['sessions']))
        print(text('Топ 15 інструментів:', 'Top 15 tools:'))
        for name, count in result['top_tools'].items():
            print(f'  {name}: {count}')
        print(text('MCP без викликів (кандидати для вимкнення): ', 'MCP with zero calls (candidates to deny): ')
              + (', '.join(result['unused_servers']) or '-'))
        print(text('MCP у використанні: ', 'MCP servers in use: ') + (', '.join(result['used_servers']) or '-'))
        if added is not None:
            print(text('Додано в permissions.deny: %s', 'Added to permissions.deny: %s') % (', '.join(added) or '-'))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(text('Помилка: ', 'Error: ') + str(error), file=sys.stderr)
        sys.exit(1)
