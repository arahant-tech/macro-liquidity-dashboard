"""Contracts for live overview inputs, not artificial market observations."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
class OverviewDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node=shutil.which('node')
        if not cls.node:
            bundled=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node'
            if bundled.exists(): cls.node=str(bundled)
        if not cls.node: raise unittest.SkipTest('Node unavailable')

    def evaluate(self,body):
        setup='''const c=require(process.argv[1]);
const at='2026-09-09T00:00:00Z', now=Date.parse(at);
const live={generated_at:at,github_run_url:'https://example.test/run/1',observations:[{series_id:'WRESBAL',provider:'fred',value:'3000000',unit:'Millions of U.S. Dollars',observation_date:'2026-09-02',known_by:at,status:'ok'}]};
const history={generated_at:at,github_run_url:live.github_run_url,research_eligible:false,vintage_policy:'current_snapshot_not_historical_availability',series:[{series_id:'WRESBAL',provider:'fred',unit:'Millions of U.S. Dollars',frequency:'Weekly, Ending Wednesday',known_by:at,research_eligible:false,status:'ok',source_url:'https://fred.stlouisfed.org/series/WRESBAL',points:[{date:'2026-08-26',value:3100000},{date:'2026-09-02',value:3000000}]}]};
const get=()=>c.makeData(live,history,now).series[0];
'''
        run=subprocess.run([self.node,'-e',setup+body,str(ROOT/'overview.js')],text=True,capture_output=True,check=True)
        return json.loads(run.stdout)

    def test_native_history_and_live_value_agree(self):
        result=self.evaluate('console.log(JSON.stringify(get()));')
        self.assertEqual(result['source_status'],'ok')
        self.assertTrue(result['interpretation_eligible'])
        self.assertEqual(len(result['points']),2)

    def test_latest_mismatch_does_not_join_two_vintages(self):
        result=self.evaluate("live.observations[0].value='3100000';console.log(JSON.stringify(get()));")
        self.assertEqual(result['source_status'],'mismatch')
        self.assertFalse(result['interpretation_eligible'])
        self.assertEqual(result['points'][-1]['value'],3000000)

    def test_missing_value_stays_a_gap_not_zero_or_interpolation(self):
        result=self.evaluate("history.series[0].points.unshift({date:'2026-08-19',value:3200000});history.series[0].points[1].value=null;console.log(JSON.stringify(get().points));")
        self.assertIsNone(result[1]['value'])
        self.assertEqual(len(result),3)

    def test_retained_receipt_cannot_support_current_interpretation(self):
        result=self.evaluate("history.series[0].status='retained';console.log(JSON.stringify(get()));")
        self.assertEqual(result['source_status'],'retained')
        self.assertFalse(result['interpretation_eligible'])
        self.assertEqual(result['known_by'],'2026-09-09T00:00:00Z')

    def test_bad_units_and_future_history_never_plot_wrong_series(self):
        result=self.evaluate("history.series[0].unit='changed units';live.observations=[];console.log(JSON.stringify(get()));")
        self.assertEqual(result['points'],[])
        self.assertEqual(result['source_status'],'missing')
        future=self.evaluate("history.series[0].points.at(-1).date='2027-01-01';live.observations=[];console.log(JSON.stringify(get().points));")
        self.assertEqual(future,[])

    def test_zero_and_missing_values_are_distinct(self):
        result=self.evaluate("console.log(JSON.stringify([c.originalNumber(null),c.originalNumber(''),c.originalNumber(false),c.originalNumber('0')]));")
        self.assertEqual(result,[None,None,None,0])

    def test_run_mismatch_requires_retry(self):
        self.assertTrue(self.evaluate("history.github_run_url='https://example.test/run/2';try{get();console.log(false)}catch{console.log(true)}"))

    def test_backend_delay_does_not_reset_observation_age(self):
        result=self.evaluate("console.log(JSON.stringify(c.makeData(live,history,now+4*3600000))); ")
        self.assertTrue(result['collectorDelayed'])
        self.assertFalse(result['series'][0]['interpretation_eligible'])
        self.assertEqual(result['live_generated_at'],'2026-09-09T00:00:00Z')

    def test_main_page_loads_runtime_without_embedded_snapshot(self):
        html=(ROOT/'index.html').read_text()
        self.assertIn('overview.js',html)
        self.assertIn('interpretation.js',html)
        self.assertIn('details.html',html)
        self.assertNotIn('const DATA=',html)
        self.assertNotIn('2026-09-02',html)

if __name__=='__main__': unittest.main()
