"""Small behavior regressions for published feed presentation, without a browser dependency."""
import json
import contextlib
import importlib.util
import io
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


def page_root():
    root = Path(__file__).resolve().parents[1]
    deployment = root / 'publishing' / 'cloud-feeds'
    return deployment if deployment.is_dir() else root


class CloudPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The overview is now the entry page; existing raw-feed behavior remains
        # on the explicit details page rather than being silently skipped.
        cls.page = page_root() / 'details.html'
        node = shutil.which('node')
        bundled = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node'
        if not node and bundled.exists():
            node = str(bundled)
        if not node:
            raise unittest.SkipTest('Node.js is required for the static page behavior check')
        cls.html = cls.page.read_text()
        css = cls.page.with_name('dashboard.css')
        if css.exists():
            cls.html += '\n' + css.read_text()
        script = cls.html.split('<script>')[1].split('</script>')[0]
        prefix = script.split("document.querySelectorAll('.filter').forEach(button")[0]
        program = "const vm=require('vm');const input=JSON.parse(require('fs').readFileSync(0,'utf8'));new vm.Script(input.script);console.log(vm.runInNewContext(input.prefix+input.query,{document:{createElement:()=>({})},URL,Intl,Date,Number,JSON,Array,Object,String}));"
        query = "JSON.stringify({retained:badge('retained'),missing:numeric(null),zero:numeric(0),tiny:numeric(1e-10),scriptUrl:safeUrl('javascript:alert(1)'),dataUrl:safeUrl('data:text/html,x'),issuer:providerKey('issuer_buybacks'),sec:providerKey('buybacks')})"
        result = subprocess.run([node, '-e', program], input=json.dumps({'script': script, 'prefix': prefix, 'query': query}), text=True, capture_output=True, check=True)
        cls.result = json.loads(result.stdout)

    def test_retained_value_is_explicit_and_amber(self):
        self.assertEqual(self.result['retained']['textContent'], '이전 정상값')
        self.assertEqual(self.result['retained']['className'], 'badge retained')
        self.assertIn('.badge.partial,.badge.stale,.badge.retained{', self.html)

    def test_missing_and_small_values_are_not_displayed_as_zero(self):
        self.assertEqual(self.result['missing'], '—')
        self.assertEqual(self.result['zero'], '0')
        self.assertNotEqual(self.result['tiny'], '0')

    def test_unsafe_source_links_and_distinct_buyback_providers(self):
        self.assertIsNone(self.result['scriptUrl'])
        self.assertIsNone(self.result['dataUrl'])
        self.assertEqual(self.result['issuer'], 'issuer_buybacks')
        self.assertEqual(self.result['sec'], 'buybacks')


class CloudPackagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = page_root() / 'automation' / 'package_cloud.py'
        spec = importlib.util.spec_from_file_location('isolated_public_packager', path)
        cls.packager = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.packager)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.write('automation/public-files.json', json.dumps(['index.html', '.nojekyll']))
        for name in (*self.packager.PUBLIC_RUNTIME_FILES, 'index.html', '.nojekyll', 'docs/CLOUD_OPERATION.md', 'docs/SYNTHETIC_SOURCE.md'):
            self.write(name, 'public fixture: ' + name)
        providers = ('fred', 'pboc', 'buybacks', 'issuer_buybacks', 'crypto', 'etf_flows',
                     'miner_flows', 'funding_structure', 'intermediary', 'terminal_flows',
                     'offshore', 'market_buybacks')
        self.write('live-data.json', json.dumps({'research_eligible': False, 'generated_at': '2026-09-09T00:00:00Z',
                                               'providers': [{'provider': p} for p in providers]}))
        self.write('chart-data.json', json.dumps({'research_eligible': False, 'vintage_policy': 'current_snapshot_not_historical_availability'}))

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def package(self):
        with patch.object(self.packager, 'ROOT', self.root), contextlib.redirect_stdout(io.StringIO()):
            self.packager.package()

    def test_entry_details_and_all_local_dependencies_are_explicitly_packaged(self):
        self.package()
        names = {str(p.relative_to(self.root / '_site')) for p in (self.root / '_site').rglob('*') if p.is_file()}
        required = {'index.html', 'details.html', 'overview.js', 'overview.css', 'interpretation.js',
                    'interpretation-copy.json', 'vendor/chart.umd.min.js', 'charts.js', 'dashboard.css',
                    'chart-data.json', 'live-data.json'}
        self.assertTrue(required <= names, required - names)
        for name in required:
            self.assertEqual((self.root / '_site' / name).read_bytes(), (self.root / name).read_bytes())

    def test_private_runtime_model_and_unapproved_design_files_are_not_swept_in(self):
        excluded = ('state/live/raw/receipt.json', 'model/live_api.py', '.env', '.run/raw.json',
                    'design/overview-concepts/private-preview.json', 'docs/PRIVATE_NOTES.md')
        for name in excluded:
            self.write(name, 'PRIVATE_CREDENTIAL_SENTINEL')
        self.package()
        for name in excluded:
            self.assertFalse((self.root / '_site' / name).exists())
        for path in (self.root / '_site').rglob('*'):
            if path.is_file():
                self.assertNotIn(b'PRIVATE_CREDENTIAL_SENTINEL', path.read_bytes())

    def test_missing_vendor_file_fails_instead_of_publishing_a_broken_overview(self):
        (self.root / 'vendor/chart.umd.min.js').unlink()
        with self.assertRaisesRegex(ValueError, 'unsafe_or_missing_public_artifact'):
            self.package()

    def test_symlinked_public_asset_is_rejected(self):
        (self.root / 'overview.js').unlink()
        self.write('private.js', 'PRIVATE_CREDENTIAL_SENTINEL')
        (self.root / 'overview.js').symlink_to(self.root / 'private.js')
        with self.assertRaisesRegex(ValueError, 'unsafe_or_missing_public_artifact'):
            self.package()

    def test_manifest_cannot_escape_the_public_output_directory(self):
        self.write('automation/public-files.json', json.dumps(['../outside.txt']))
        with self.assertRaisesRegex(ValueError, 'unsafe_public_artifact_path'):
            self.package()


if __name__ == '__main__':
    unittest.main()
