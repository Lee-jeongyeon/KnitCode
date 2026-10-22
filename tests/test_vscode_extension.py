"""Exercise the adapter and the actual bundled engine outside its checkout."""
import json
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile
from scripts.package_extension import package
from vscode_export import export_project


class ExtensionTests(unittest.TestCase):
    def test_bundled_engine_matches_adapter_without_executing_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            project = base / 'project with spaces'
            project.mkdir()
            (project / 'main.py').write_text(
                "raise RuntimeError('must never execute')\n"
                'def target(): pass\ndef caller(): target()\ncaller()\n')
            (project / 'bad.py').write_text('def broken(:')
            reference = export_project(project)
            self.assertEqual(reference['summary']['files'], 1)
            self.assertEqual(len(reference['warnings']), 1)
            self.assertTrue(any(e['type'] == 'calls' and e['lineno'] == 3
                                for e in reference['edges']))
            self.assertTrue(all(n['file'] == 'main.py' for n in reference['nodes']))
            archive = package(base / 'test.vsix')
            with ZipFile(archive) as z:
                self.assertIsNone(z.testzip())
                manifest = json.loads(z.read('extension/package.json'))
                self.assertEqual(manifest['name'], 'knitcode-static')
                z.extractall(base / 'unpacked')
            bundled = base / 'unpacked/extension/vendor/vscode_export.py'
            result = subprocess.run([sys.executable, '-I', '-B', str(bundled),
                                     str(project)], cwd=base, check=True,
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(json.loads(result.stdout), reference)
            ids = {n['id'] for n in reference['nodes']}
            self.assertTrue(all(e['source'] in ids and e['target'] in ids
                                for e in reference['edges']))

    def test_empty_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = export_project(tmp)
            self.assertEqual(data['nodes'], [])
            self.assertEqual(data['edges'], [])

    def test_flow_webview_behavior(self):
        jsc = shutil.which('jsc') or '/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc'
        if not Path(jsc).exists():
            self.skipTest('JavaScriptCore unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'main.py').write_text('def target(value): return value\ndef caller():\n    target(1)\n    sorted([1], key=target)\n    unknown()\n')
            data = export_project(root)
            self.assertTrue(all(c['file'] == 'main.py' for c in data['call_statuses']))
            payload = root / 'analysis.json'
            payload.write_text(json.dumps(data))
            result = subprocess.run([jsc, 'tests/flow_webview_smoke.js', '--', str(payload)],
                                    cwd=Path(__file__).resolve().parents[1], capture_output=True,
                                    text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
