import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / 'tools' / 'cto_tool_usage.py'


class ToolUsageTests(unittest.TestCase):
    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding='utf-8')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        (self.project / '.git').mkdir()
        self.home = self.root / 'home'
        config = self.root / 'claude.json'
        self.write(config, {'mcpServers': {'used': {}, 'global_idle': {}},
                            'projects': {str(self.project): {'mcpServers': {'project_idle': {}}}}})
        self.write(self.project / '.mcp.json', {'mcpServers': {'local_idle': {}}})
        self.env = dict(os.environ, CTO_CLAUDE_HOME=str(self.home),
                        CTO_CLAUDE_JSON=str(config), CTO_LANG='en')
        for index in range(5):
            self.transcript(str(index), self.project / 'sub', ['Read', 'mcp__used__fetch'])
        self.transcript('other', self.root / 'project-other', ['mcp__local_idle__fetch'])

    def transcript(self, name, cwd, tools):
        path = self.home / 'projects' / 'encoded' / (name + '.jsonl')
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {'cwd': str(cwd), 'type': 'assistant', 'message': {'content': [
            {'type': 'text', 'text': 'PRIVATE MESSAGE'}, *[
                {'type': 'tool_use', 'name': name, 'input': {'secret': 'PRIVATE INPUT'}} for name in tools]]}}
        path.write_text('broken\n[]\n' + json.dumps(row) + '\n', encoding='utf-8')

    def run_cli(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(SCRIPT), '--project', str(self.project), *args],
                                text=True, capture_output=True, env=self.env)
        self.assertEqual(result.returncode == 0, ok, result.stderr)
        self.assertNotIn('PRIVATE', result.stdout + result.stderr)
        return result

    def test_counts_and_other_project_ignored(self):
        report = json.loads(self.run_cli('--json').stdout)
        self.assertEqual(report['sessions'], 5)
        self.assertEqual(report['tools'], {'Read': 5, 'mcp__used__fetch': 5})
        self.assertEqual(report['unused_servers'], ['global_idle', 'local_idle', 'project_idle'])
        self.assertEqual(report['used_servers'], {'used': 5})

    def test_apply_backup_restore(self):
        settings = self.project / '.claude' / 'settings.local.json'
        original = {'permissions': {'deny': ['Read', 'mcp__global_idle'], 'allow': ['Write']}, 'other': 42}
        self.write(settings, original)
        self.run_cli('--apply', '--json')
        data = json.loads(settings.read_text())
        self.assertEqual(data['other'], 42)
        self.assertEqual(data['permissions']['allow'], ['Write'])
        record = settings.with_name('cto-applied.json')
        self.assertEqual(json.loads(record.read_text())['added'], ['mcp__local_idle', 'mcp__project_idle'])
        backups = list(settings.parent.glob('settings.local.json.bak-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text()), original)
        exclude = (self.project / '.git' / 'info' / 'exclude').read_text()
        self.assertIn('.claude/settings.local.json', exclude)
        self.assertIn('.claude/cto-applied.json', exclude)
        self.run_cli('--restore')
        self.assertEqual(json.loads(settings.read_text()), original)
        self.assertFalse(record.exists())

    def test_minimum_refusal(self):
        self.run_cli('--apply', '--min-sessions', '6', ok=False)
        self.assertFalse((self.project / '.claude').exists())

    def test_create_settings_and_all(self):
        self.run_cli('--apply')
        settings = self.project / '.claude' / 'settings.local.json'
        self.assertEqual(len(json.loads(settings.read_text())['permissions']['deny']), 3)
        reports = json.loads(self.run_cli('--all', '--json').stdout)
        self.assertTrue(any(r['sessions'] == 1 for r in reports))

    def test_multi_cwd_session_counts_per_cwd(self):
        # One session that moved between two projects: only the calls made under this project count.
        path = self.home / 'projects' / 'encoded' / 'mixed.jsonl'
        rows = [{'cwd': str(self.project), 'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'Edit'}]}},
                {'cwd': str(self.root / 'elsewhere'), 'type': 'assistant',
                 'message': {'content': [{'type': 'tool_use', 'name': 'mcp__local_idle__fetch'}]}}]
        path.write_text('\n'.join(json.dumps(r) for r in rows) + '\n', encoding='utf-8')
        report = json.loads(self.run_cli('--json').stdout)
        self.assertEqual(report['sessions'], 6)
        self.assertEqual(report['tools'].get('Edit'), 1)
        self.assertIn('local_idle', report['unused_servers'])

    def test_apply_twice_and_restore_without_record(self):
        first = json.loads(self.run_cli('--apply', '--json').stdout)['applied']
        second = json.loads(self.run_cli('--apply', '--json').stdout)['applied']
        self.assertEqual(len(first), 3)
        self.assertEqual(second, [])
        settings = self.project / '.claude' / 'settings.local.json'
        self.assertEqual(len(list(settings.parent.glob('*.bak-*'))), 0)   # nothing existed before the first apply
        exclude = (self.project / '.git' / 'info' / 'exclude').read_text()
        self.assertIn('.claude/settings.local.json.bak-*', exclude)
        self.run_cli('--restore')
        self.run_cli('--restore', ok=False)
        self.assertEqual(json.loads(settings.read_text())['permissions']['deny'], [])

    def test_huge_line_skipped(self):
        path = self.home / 'projects' / 'encoded' / 'huge.jsonl'
        row = {'cwd': str(self.project), 'type': 'assistant', 'message': {'content': [
            {'type': 'tool_use', 'name': 'Glob'}, {'type': 'text', 'text': 'x' * (9 * 1024 * 1024)}]}}
        path.write_text(json.dumps(row) + '\n', encoding='utf-8')
        report = json.loads(self.run_cli('--json').stdout)
        self.assertNotIn('Glob', report['tools'])

    def test_old_sessions_excluded(self):
        for path in (self.home / 'projects').glob('*/*.jsonl'):
            os.utime(path, (1, 1))
        self.assertEqual(json.loads(self.run_cli('--json').stdout)['sessions'], 0)


if __name__ == '__main__':
    unittest.main()
