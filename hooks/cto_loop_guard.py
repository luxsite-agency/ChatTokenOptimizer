"""PostToolUse / PostToolUseFailure hook: one nudge when the agent keeps failing the same way.

Deterministic, no model calls. A failure is a PostToolUseFailure event (not an interrupt) or a tool
response that reports an error. The same action failing N times in a row, or the same error coming
back from N+1 different actions, gets one additionalContext nudge and one line for the person.
State per session: 16-hex hashes and integers only, in a temp directory, under a file lock.
Fail-open: any exception -> exit 0, no output.
"""
import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

LOCK_WAIT_S = 0.3
STATE_TTL_S = 2 * 86400
# Bash commands whose exit status 1 means "nothing found", not "it broke".
QUERY_CMDS = ('grep', 'rg', 'egrep', 'fgrep', 'diff', 'cmp', 'test', '[', 'find', 'which')


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()[:16]


def action_signature(name, data):
    data = data if isinstance(data, dict) else {}
    if name in ('Bash', 'PowerShell'):
        primary = ' '.join(str(data.get('command', '')).split())
    elif name in ('Read', 'Edit', 'Write', 'NotebookEdit'):
        primary = str(data.get('file_path', ''))
    elif name in ('Grep', 'Glob'):
        primary = json.dumps([data.get('pattern', ''), data.get('path', '')])
    elif name == 'WebFetch':
        primary = str(data.get('url', ''))
    else:
        primary = json.dumps(data, sort_keys=True)[:500]
    return digest(name + '\n' + primary)


def exit_code(response):
    for key in ('exitCode', 'exit_code', 'returnCode'):
        value = response.get(key)
        if isinstance(value, bool) or value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def is_failure(payload, response):
    if payload.get('is_interrupt'):
        return False
    if isinstance(response, dict) and (response.get('interrupted') or response.get('is_interrupt')):
        return False
    if payload.get('hook_event_name') == 'PostToolUseFailure':
        return True
    if not isinstance(response, dict):
        return False
    if response.get('is_error') is True:
        return True
    code = exit_code(response)
    if code is None or code == 0:
        return False
    if code == 1 and payload.get('tool_name') in ('Bash', 'PowerShell'):
        first = str((payload.get('tool_input') or {}).get('command', '')).strip().split(' ')[0]
        if os.path.basename(first) in QUERY_CMDS:
            return False
    return True


def error_signature(payload, response):
    raw = payload.get('error')
    if raw is None and isinstance(response, dict):
        raw = next((response[k] for k in ('error', 'stderr', 'message', 'content', 'stdout') if response.get(k)),
                   response)
    elif raw is None:
        raw = response
    raw = raw if isinstance(raw, str) else json.dumps(raw, sort_keys=True)
    norm = ' '.join(re.sub(r'\d', '0', raw[:300].lower()).split())
    if re.fullmatch(r'(exit code 0|exit status 0|command failed)[.:]?', norm):
        return None
    return digest(norm)


class Lock:
    """Cross-platform exclusive lock: an O_EXCL file next to the state. Stale locks are broken."""

    def __init__(self, path):
        self.path = str(path) + '.lock'
        self.fd = None

    def __enter__(self):
        deadline = time.time() + LOCK_WAIT_S
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > 5:
                        os.unlink(self.path)
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    raise TimeoutError
                time.sleep(0.01)

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
        try:
            os.unlink(self.path)
        except OSError:
            pass


def cleanup(directory, now):
    for index, old in enumerate(directory.glob('*.json')):
        if index >= 128:
            break
        try:
            if old.stat().st_mtime < now - STATE_TTL_S:
                old.unlink()
        except OSError:
            pass


def messages(count, en):
    if en:
        return (f'ChatTokenOptimizer: the same failure came back {count} times in this session; '
                f'nudged the agent to change approach.',
                f'ChatTokenOptimizer: this action (or the same error) has failed {count} times in this session. '
                f'Do not repeat it again. Read the full error, check the assumption (path, version, permissions, '
                f'shell syntax, whether the file or command exists), change the approach, or ask the person briefly.')
    return (f'ChatTokenOptimizer: та сама помилка повторилась {count} разів у цій сесії; підказав агентові змінити підхід.',
            f'ChatTokenOptimizer: ця дія (або та сама помилка) не вдалась {count} разів у цій сесії. Не повторюй її '
            f'вкотре. Прочитай повний текст помилки, перевір припущення (шлях, версію, права, синтаксис оболонки, чи '
            f'існує файл чи команда), зміни підхід або коротко спитай людину.')


def main():
    if os.environ.get('CTO_LOOP_GUARD', '').lower() == 'off':
        return
    payload = json.loads(sys.stdin.buffer.read().decode('utf-8'))
    event = payload.get('hook_event_name')
    if event not in ('PostToolUse', 'PostToolUseFailure'):
        return
    sid = payload.get('session_id')
    if not isinstance(sid, str) or not sid:
        return
    response = payload.get('tool_response', {})
    failure = is_failure(payload, response)
    action = action_signature(str(payload.get('tool_name', '')), payload.get('tool_input'))

    directory = Path(os.environ.get('CTO_STATE_DIR') or
                     (Path(tempfile.gettempdir()) / ('cto-loop-guard-' + digest(os.path.expanduser('~')))))
    path = directory / (digest(sid) + '.json')
    # Most calls succeed: without a recorded failure there is nothing to reset, so no disk write.
    if not failure and not path.exists():
        return
    directory.mkdir(parents=True, exist_ok=True)
    now = int(time.time())
    cleanup(directory, now)
    threshold = max(1, int(os.environ.get('CTO_LOOP_N', '3')))
    maximum = max(0, int(os.environ.get('CTO_LOOP_MAX', '3')))

    count = 0
    with Lock(path):
        try:
            state = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            state = {}
        state.setdefault('consecutive_fail', {})
        state.setdefault('error_actions', {})     # error hash -> list of distinct action hashes
        state.setdefault('nudged', [])
        state.setdefault('nudges', 0)
        if failure:
            error = error_signature(payload, response)
            actions = state['consecutive_fail']
            actions[action] = actions.get(action, 0) + 1
            seen = state['error_actions'].setdefault(error, []) if error else []
            if error and action not in seen:
                seen.append(action)
            candidates = []
            if actions[action] >= threshold:
                candidates.append(actions[action])
            if len(seen) >= threshold + 1:
                candidates.append(len(seen))
            if (candidates and action not in state['nudged'] and error not in state['nudged']
                    and state['nudges'] < maximum):
                count = max(candidates)
                state['nudged'].extend([action] + ([error] if error else []))
                state['nudges'] += 1
        else:
            state['consecutive_fail'].pop(action, None)
        state['updated'] = now
        fd, temporary = tempfile.mkstemp(dir=str(directory), suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(state, stream)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    if count:
        person, agent = messages(count, os.environ.get('CTO_LANG', 'en') != 'uk')
        print(json.dumps({'systemMessage': person, 'hookSpecificOutput': {
            'hookEventName': event, 'additionalContext': agent}}, ensure_ascii=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
