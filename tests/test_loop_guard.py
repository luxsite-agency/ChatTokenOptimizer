import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'hooks' / 'cto_loop_guard.py'


class LoopGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = dict(os.environ, CTO_STATE_DIR=self.tmp.name, CTO_LOOP_N='3',
                        CTO_LOOP_MAX='3', CTO_LOOP_GUARD='on', CTO_LANG='en')

    def run_hook(self, command='secret command', error='private failure', success=False, **extra):
        payload = {'session_id': 'session', 'hook_event_name': 'PostToolUse' if success else 'PostToolUseFailure',
                   'tool_name': 'Bash', 'tool_input': {'command': command}, 'error': error,
                   'tool_response': {}}
        payload.update(extra)
        result = subprocess.run([sys.executable, str(SCRIPT)], input=json.dumps(payload),
                                text=True, capture_output=True, env=self.env)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, '')
        return json.loads(result.stdout) if result.stdout else None

    def test_third_failure_only_once_and_private_state(self):
        self.assertIsNone(self.run_hook())
        self.assertIsNone(self.run_hook())
        output = self.run_hook()
        self.assertEqual(output['hookSpecificOutput']['hookEventName'], 'PostToolUseFailure')
        for _ in range(4):
            self.assertIsNone(self.run_hook())
        state = next(Path(self.tmp.name).glob('*.json')).read_text()
        self.assertNotIn('secret command', state)
        self.assertNotIn('private failure', state)

    def test_success_resets_action(self):
        self.run_hook(error='one')
        self.run_hook(error='two')
        self.run_hook(success=True)
        self.assertIsNone(self.run_hook(error='three'))
        self.assertIsNone(self.run_hook(error='four'))
        self.assertIsNotNone(self.run_hook(error='five'))

    def test_same_error_different_actions(self):
        for command in ('a', 'b', 'c'):
            self.assertIsNone(self.run_hook(command=command))
        self.assertIsNotNone(self.run_hook(command='d'))

    def test_maximum(self):
        self.env['CTO_LOOP_MAX'] = '1'
        for command, error in [('a', 'first'), ('b', 'second')]:
            results = [self.run_hook(command=command, error=error) for _ in range(3)]
            self.assertEqual(sum(r is not None for r in results), int(command == 'a'))

    def test_disabled_and_interrupt(self):
        for _ in range(5):
            self.assertIsNone(self.run_hook(is_interrupt=True))
        self.env['CTO_LOOP_GUARD'] = 'off'
        for _ in range(5):
            self.assertIsNone(self.run_hook())
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_bash_failure_event_with_exit_code_text(self):
        results = [self.run_hook(error='Exit code 1\nnpm ERR! missing script: test') for _ in range(3)]
        self.assertEqual([r is not None for r in results], [False, False, True])

    def test_bare_exit_codes_are_not_a_shared_error(self):
        # grep, test -f, diff ... each failing once with only "Exit code 1" must not look like one loop.
        for command in ('grep a f', 'test -f x', 'diff a b', 'cmp a b', 'ls nothing'):
            self.assertIsNone(self.run_hook(command=command, error='Exit code 1'))

    def test_query_command_exit_1_in_tool_response_is_not_failure(self):
        for _ in range(4):
            self.assertIsNone(self.run_hook(success=True, command='grep -r needle .',
                                            tool_response={'exitCode': 1, 'stdout': ''}))

    def test_fail_success_cycles_do_not_nudge(self):
        for _ in range(5):
            self.assertIsNone(self.run_hook(error='flaky'))
            self.assertIsNone(self.run_hook(success=True))

    def test_utf8_payload_bytes(self):
        payload = {'session_id': 'сесія', 'hook_event_name': 'PostToolUseFailure', 'tool_name': 'Bash',
                   'tool_input': {'command': 'echo Тека з пробілом'}, 'error': 'Помилка', 'tool_response': {}}
        raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        outs = [subprocess.run([sys.executable, str(SCRIPT)], input=raw, capture_output=True, env=self.env)
                for _ in range(3)]
        self.assertTrue(all(o.returncode == 0 and o.stderr == b'' for o in outs))
        self.assertEqual([bool(o.stdout) for o in outs], [False, False, True])

    def test_garbage(self):
        for value in ('garbage', '[]', '{}', 'null'):
            result = subprocess.run([sys.executable, str(SCRIPT)], input=value,
                                    text=True, capture_output=True, env=self.env)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))


if __name__ == '__main__':
    unittest.main()
