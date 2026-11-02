"""install.py against a temporary Claude home."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]


def run(home, *args):
    env = dict(os.environ, CTO_CLAUDE_HOME=str(home), CTO_LANG='en')
    return subprocess.run([sys.executable, str(KIT / 'install.py'), *args], capture_output=True, text=True,
                          env=env, timeout=60)


class Install(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / '.claude'
        self.home.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def settings(self):
        return json.loads((self.home / 'settings.json').read_text(encoding='utf-8'))

    def test_dry_run_changes_nothing(self):
        out = run(self.home, '--dry-run')
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse((self.home / 'settings.json').exists())
        self.assertFalse((self.home / 'cto').exists())
        self.assertIn('[dry-run]', out.stdout)

    def test_install_keeps_existing_settings_and_adds_hooks(self):
        (self.home / 'settings.json').write_text(json.dumps({
            'permissions': {'allow': ['Bash(git:*)']}, 'model': 'opus[1m]',
            'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'echo bye'}]}]}}), encoding='utf-8')
        out = run(self.home)
        self.assertEqual(out.returncode, 0, out.stderr)
        s = self.settings()
        self.assertEqual(s['permissions']['allow'], ['Bash(git:*)'])
        self.assertEqual(s['model'], 'opus')
        self.assertEqual(s['outputStyle'], 'Concise')
        self.assertEqual(s['hooks']['Stop'][0]['hooks'][0]['command'], 'echo bye')
        events = set(s['hooks'])
        self.assertTrue({'SessionStart', 'PreToolUse', 'PostToolUse', 'PostToolUseFailure'} <= events)
        kit = [h for e in s['hooks'].values() for x in e for h in x['hooks'] if 'args' in h]
        self.assertEqual(len(kit), 5)
        for h in kit:   # exec form: interpreter in command, script path + flags in args, no shell quoting
            self.assertEqual(h['command'], sys.executable)
            self.assertTrue(h['args'][0].endswith('.py'))
        scripts = ' '.join(' '.join(h['args']) for h in kit)
        for name in ('cto_economy.py --hook', 'cto_facts.py', 'cto_read_guard.py', 'cto_loop_guard.py'):
            self.assertIn(name, scripts)
        self.assertTrue((self.home / 'cto' / 'hooks' / 'cto_loop_guard.py').exists())
        self.assertTrue((self.home / 'cto' / 'tools' / 'cto_tool_usage.py').exists())
        self.assertTrue((self.home / 'skills' / 'session-wrapup' / 'SKILL.md').exists())
        self.assertTrue((self.home / 'CLAUDE.md').exists())
        self.assertTrue(list(self.home.glob('settings.json.bak-*')))

    def test_reinstall_is_idempotent(self):
        run(self.home)
        run(self.home)
        s = self.settings()
        self.assertEqual(len(s['hooks']['PreToolUse']), 1)
        self.assertEqual(len(s['hooks']['SessionStart']), 2)

    def test_existing_same_job_hook_skipped(self):
        (self.home / 'settings.json').write_text(json.dumps({'hooks': {'PreToolUse': [
            {'matcher': 'Read', 'hooks': [{'type': 'command', 'command': 'python my/read-guard.py'}]}]}}),
            encoding='utf-8')
        out = run(self.home)
        self.assertIn('skipped', out.stdout)
        cmds = [h['command'] for x in self.settings()['hooks']['PreToolUse'] for h in x['hooks']]
        self.assertEqual(len(cmds), 1)
        self.assertIn('read-guard', cmds[0])

    def test_alias_on_other_event_does_not_skip(self):
        (self.home / 'settings.json').write_text(json.dumps({'hooks': {'Stop': [
            {'hooks': [{'type': 'command', 'command': 'python my/read-guard.py'}]}]}}), encoding='utf-8')
        out = run(self.home)
        self.assertNotIn('skipped', out.stdout)
        self.assertEqual(len(self.settings()['hooks']['PreToolUse']), 1)

    def test_malformed_hooks_abort_before_copy(self):
        (self.home / 'settings.json').write_text(json.dumps({'hooks': {'PreToolUse': {'bad': 1}}}), encoding='utf-8')
        out = run(self.home)
        self.assertNotEqual(out.returncode, 0)
        self.assertFalse((self.home / 'cto').exists())
        self.assertFalse(list(self.home.glob('settings.json.bak-*')))

    def test_cheaper_settings_kept(self):
        (self.home / 'settings.json').write_text(json.dumps({'model': 'sonnet', 'effortLevel': 'low'}), encoding='utf-8')
        run(self.home)
        s = self.settings()
        self.assertEqual((s['model'], s['effortLevel'], s['outputStyle']), ('sonnet', 'low', 'Concise'))

    def test_foreign_hook_with_similar_path_kept_on_uninstall(self):
        (self.home / 'settings.json').write_text(json.dumps({'hooks': {'Stop': [
            {'hooks': [{'type': 'command', 'command': 'echo /cto/hooks/ is a nice path'}]}]}}), encoding='utf-8')
        run(self.home)
        run(self.home, '--uninstall')
        self.assertEqual(self.settings()['hooks']['Stop'][0]['hooks'][0]['command'], 'echo /cto/hooks/ is a nice path')

    def test_keep_settings_and_uninstall(self):
        (self.home / 'settings.json').write_text(json.dumps({'model': 'sonnet'}), encoding='utf-8')
        run(self.home, '--keep-settings')
        self.assertEqual(self.settings()['model'], 'sonnet')
        out = run(self.home, '--uninstall')
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertNotIn('hooks', self.settings())
        self.assertFalse((self.home / 'cto').exists())
        self.assertTrue((self.home / 'skills' / 'session-wrapup').exists())


if __name__ == '__main__':
    unittest.main()
