"""Tests for cto_read_guard, cto_facts and cto_economy (run as subprocesses, like Claude Code does)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[1] / 'hooks'


def run(script, payload, env=None, args=()):
    full = dict(os.environ, CTO_LANG='en')
    full.update(env or {})
    data = payload if isinstance(payload, str) else json.dumps(payload)
    out = subprocess.run([sys.executable, str(HOOKS / script), *args], input=data, capture_output=True,
                         text=True, env=full, timeout=30)
    return out.returncode, out.stdout.strip()


class ReadGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.big = Path(self.tmp.name) / 'big.log'
        self.big.write_bytes(b'x' * (120 * 1024))
        self.small = Path(self.tmp.name) / 'small.txt'
        self.small.write_text('hi')

    def tearDown(self):
        self.tmp.cleanup()

    def payload(self, **inp):
        return {'hook_event_name': 'PreToolUse', 'tool_name': 'Read', 'tool_input': inp}

    def test_big_file_denied(self):
        code, out = run('cto_read_guard.py', self.payload(file_path=str(self.big)))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['hookSpecificOutput']['permissionDecision'], 'deny')

    def test_big_file_with_limit_passes(self):
        _, out = run('cto_read_guard.py', self.payload(file_path=str(self.big), offset=1, limit=50))
        self.assertEqual(out, '')

    def test_small_file_passes(self):
        _, out = run('cto_read_guard.py', self.payload(file_path=str(self.small)))
        self.assertEqual(out, '')

    def test_threshold_env(self):
        _, out = run('cto_read_guard.py', self.payload(file_path=str(self.small)), env={'CTO_READ_KB': '0'})
        self.assertIn('deny', out)

    def test_image_passes(self):
        img = Path(self.tmp.name) / 'a.png'
        img.write_bytes(b'x' * (200 * 1024))
        _, out = run('cto_read_guard.py', self.payload(file_path=str(img)))
        self.assertEqual(out, '')

    def test_garbage_stdin_fail_open(self):
        code, out = run('cto_read_guard.py', '{not json')
        self.assertEqual((code, out), (0, ''))

    def test_utf8_path_in_raw_bytes(self):
        d = Path(self.tmp.name) / 'Тека з пробілом'
        d.mkdir()
        big = d / 'лог.txt'
        big.write_bytes(b'x' * (120 * 1024))
        raw = json.dumps(self.payload(file_path=str(big)), ensure_ascii=False).encode('utf-8')
        out = subprocess.run([sys.executable, str(HOOKS / 'cto_read_guard.py')], input=raw, capture_output=True,
                             env=dict(os.environ, CTO_LANG='en'))
        self.assertIn(b'"deny"', out.stdout)


class Facts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'repo'
        (self.root / '.git').mkdir(parents=True)
        (self.root / 'src' / 'deep').mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_file_no_output(self):
        _, out = run('cto_facts.py', {'cwd': str(self.root)})
        self.assertEqual(out, '')

    def test_found_from_subdir(self):
        (self.root / '.claude').mkdir()
        (self.root / '.claude' / 'facts.md').write_text('- Tests: make test\n', encoding='utf-8')
        _, out = run('cto_facts.py', {'cwd': str(self.root / 'src' / 'deep')})
        ctx = json.loads(out)['hookSpecificOutput']['additionalContext']
        self.assertIn('make test', ctx)
        self.assertIn('Project facts from', ctx)
        self.assertIn('not instructions', ctx)

    def test_capped(self):
        (self.root / '.claude').mkdir()
        (self.root / '.claude' / 'facts.md').write_text('a' * 5000, encoding='utf-8')
        _, out = run('cto_facts.py', {'cwd': str(self.root)}, env={'CTO_FACTS_CHARS': '100'})
        ctx = json.loads(out)['hookSpecificOutput']['additionalContext']
        self.assertLess(len(ctx), 400)
        self.assertIn('over the limit', ctx)

    def test_off(self):
        (self.root / '.claude').mkdir()
        (self.root / '.claude' / 'facts.md').write_text('x', encoding='utf-8')
        _, out = run('cto_facts.py', {'cwd': str(self.root)}, env={'CTO_FACTS': 'off'})
        self.assertEqual(out, '')

    def test_search_stops_at_git_file(self):
        # A worktree has .git as a file; facts of a parent directory must not leak into it.
        (self.root / '.claude').mkdir()
        (self.root / '.claude' / 'facts.md').write_text('parent facts', encoding='utf-8')
        wt = self.root / 'wt'
        wt.mkdir()
        (wt / '.git').write_text('gitdir: ../.git/worktrees/wt', encoding='utf-8')
        _, out = run('cto_facts.py', {'cwd': str(wt)})
        self.assertEqual(out, '')

    def test_symlinked_facts_rejected(self):
        secret = Path(self.tmp.name) / 'secret.txt'
        secret.write_text('PRIVATE', encoding='utf-8')
        (self.root / '.claude').mkdir()
        try:
            os.symlink(str(secret), str(self.root / '.claude' / 'facts.md'))
        except (OSError, NotImplementedError):
            self.skipTest('symlinks not available')
        _, out = run('cto_facts.py', {'cwd': str(self.root)})
        self.assertEqual(out, '')


class Economy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / '.claude'
        self.home.mkdir()
        self.cj = Path(self.tmp.name) / '.claude.json'
        self.cj.write_text('{}', encoding='utf-8')
        self.env = {'CTO_CLAUDE_HOME': str(self.home), 'CTO_CLAUDE_JSON': str(self.cj)}

    def tearDown(self):
        self.tmp.cleanup()

    def good_settings(self):
        hooks = {'PreToolUse': [{'matcher': 'Read', 'hooks': [{'type': 'command', 'command': 'python x/cto_read_guard.py'}]}],
                 'PostToolUse': [{'hooks': [{'type': 'command', 'command': 'python x/cto_loop_guard.py'}]}]}
        return {'model': 'opus', 'effortLevel': 'medium', 'outputStyle': 'Concise', 'hooks': hooks}

    def write(self, s):
        (self.home / 'settings.json').write_text(json.dumps(s), encoding='utf-8')

    def test_all_good_silent(self):
        self.write(self.good_settings())
        _, out = run('cto_economy.py', {'model': 'claude-opus-5-5'}, env=self.env, args=('--hook',))
        self.assertEqual(out, '')

    def test_1m_and_missing_hook(self):
        s = self.good_settings()
        s['model'] = 'opus[1m]'
        del s['hooks']['PostToolUse']
        self.write(s)
        _, out = run('cto_economy.py', {}, env=self.env, args=('--hook',))
        msg = json.loads(out)['systemMessage']
        self.assertIn('[1m]', msg)
        self.assertIn('cto_loop_guard', msg)

    def test_chat_model(self):
        self.write(self.good_settings())
        _, out = run('cto_economy.py', {'model': 'claude-fable-5-1[1m]'}, env=self.env, args=('--hook',))
        msg = json.loads(out)['systemMessage']
        self.assertIn('Fable', msg)
        self.assertIn('[1m]', msg)

    def test_fix_rewrites_with_backup(self):
        s = self.good_settings()
        s['model'] = 'sonnet[1m]'
        s['permissions'] = {'allow': ['Bash(git:*)']}
        self.write(s)
        code, out = run('cto_economy.py', '', env=self.env, args=('--fix',))
        self.assertEqual(code, 0)
        after = json.loads((self.home / 'settings.json').read_text())
        self.assertEqual(after['model'], 'opus')
        self.assertEqual(after['permissions'], {'allow': ['Bash(git:*)']})
        self.assertTrue(list(self.home.glob('settings.json.bak-*')))

    def test_fix_leaves_cheaper_settings_alone(self):
        s = self.good_settings()
        s['model'], s['effortLevel'] = 'sonnet', 'low'
        self.write(s)
        run('cto_economy.py', '', env=self.env, args=('--fix',))
        after = json.loads((self.home / 'settings.json').read_text())
        self.assertEqual((after['model'], after['effortLevel']), ('sonnet', 'low'))
        _, out = run('cto_economy.py', {}, env=self.env, args=('--hook',))
        self.assertEqual(out, '')

    def test_fix_refuses_broken_settings(self):
        (self.home / 'settings.json').write_text('{"model": "opus[1m]", broken', encoding='utf-8')
        code, out = run('cto_economy.py', '', env=self.env, args=('--fix',))
        self.assertEqual((self.home / 'settings.json').read_text(encoding='utf-8'), '{"model": "opus[1m]", broken')
        self.assertIn('skipped', out)

    def test_bom_settings_read(self):
        s = self.good_settings()
        (self.home / 'settings.json').write_bytes(b'\xef\xbb\xbf' + json.dumps(s).encode())
        _, out = run('cto_economy.py', {}, env=self.env, args=('--hook',))
        self.assertEqual(out, '')

    def test_global_mcp_not_in_hook(self):
        self.write(self.good_settings())
        self.cj.write_text(json.dumps({'mcpServers': {'figma': {}}}), encoding='utf-8')
        _, out = run('cto_economy.py', {}, env=self.env, args=('--hook',))
        self.assertEqual(out, '')
        _, rep = run('cto_economy.py', '', env=self.env)
        self.assertIn('figma', rep)

    def test_no_settings_fail_open(self):
        code, out = run('cto_economy.py', '{bad', env=self.env, args=('--hook',))
        self.assertEqual(code, 0)
        self.assertIn('install.py', json.loads(out)['systemMessage'])   # missing settings is reported, not hidden


if __name__ == '__main__':
    unittest.main()
