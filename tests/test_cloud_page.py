"""Small behavior regressions for published feed presentation, without a browser dependency."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest


class CloudPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        cls.page = root / 'publishing' / 'cloud-feeds' / 'index.html'
        if not cls.page.exists():
            cls.page = root / 'index.html'
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


if __name__ == '__main__':
    unittest.main()
