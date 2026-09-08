"""Check numeric chart behavior without a browser or synthetic plotted data."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest


class ChartPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).resolve().parents[1]
        cls.path=root/'charts.js'
        if not cls.path.exists():
            cls.path=root/'publishing/cloud-feeds/charts.js'
        cls.node=shutil.which('node')
        if not cls.node:
            bundled=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node'
            if bundled.exists(): cls.node=str(bundled)
        if not cls.node: raise unittest.SkipTest('Node runtime unavailable')

    def evaluate(self,code):
        script="const c=require(process.argv[1]);"+code
        result=subprocess.run([self.node,'-e',script,str(self.path)],capture_output=True,text=True,check=True)
        return json.loads(result.stdout)

    def test_dates_are_calendar_dates_not_timezone_shifted(self):
        self.assertEqual(self.evaluate("console.log(JSON.stringify([Number.isNaN(c.timestamp('2026-02-30')),new Date(c.timestamp('2026-09-01')).toISOString()]));"),[True,'2026-09-01T00:00:00.000Z'])

    def test_missing_observation_breaks_line_and_zero_survives(self):
        actual=self.evaluate("const p=c.validPoints([{date:'2026-09-01',value:0},{date:'2026-09-02',value:null},{date:'2026-09-03',value:5}]);const g=c.geometry(p);console.log(JSON.stringify({segments:g.segments.map(s=>s.map(p=>p.value)),points:p.length}));")
        self.assertEqual(actual,{'segments':[[0],[5]],'points':3})

    def test_one_observation_is_a_point_not_fabricated_history(self):
        actual=self.evaluate("const p=c.validPoints([{date:'2026-09-01',value:5}]),g=c.geometry(p);console.log(JSON.stringify([g.segments[0].length,Number.isFinite(g.segments[0][0].x),Number.isFinite(g.segments[0][0].y)]));")
        self.assertEqual(actual,[1,True,True])

    def test_native_units_map_only_on_exact_source_definition(self):
        actual=self.evaluate("console.log(JSON.stringify([c.unitFor({series_id:'WRESBAL',unit:'Millions of U.S. Dollars'}),c.unitFor({series_id:'JPNASSETS',unit:'100 Million Yen'}),c.unitFor({series_id:'WRESBAL',unit:'changed unit'})]));")
        self.assertEqual([x['scale'] for x in actual],[1e6,1e4,1])

    def test_range_filter_does_not_create_monthly_observations(self):
        actual=self.evaluate("const p=c.selectPoints({points:[{date:'2026-01-31',value:1},{date:'2026-07-31',value:3}]},90,'2026-09-09T00:00:00Z');console.log(JSON.stringify(p.map(x=>x.date)));")
        self.assertEqual(actual,['2026-07-31'])

    def test_future_duplicate_and_invalid_values_rejected(self):
        actual=self.evaluate("const cases=[()=>c.selectPoints({points:[{date:'2027-01-01',value:1}]},365,'2026-09-09T00:00:00Z'),()=>c.validPoints([{date:'2026-01-01',value:1},{date:'2026-01-01',value:2}]),()=>c.validPoints([{date:'2026-01-01',value:true}])];console.log(JSON.stringify(cases.map(fn=>{try{fn();return false;}catch{return true;}})));")
        self.assertEqual(actual,[True,True,True])

    def test_negative_flow_bars_include_zero_baseline(self):
        actual=self.evaluate("const p=c.validPoints([{date:'2026-01-31',value:-2},{date:'2026-02-28',value:3}]),g=c.geometry(p,1000,300,true);console.log(JSON.stringify([g.lo<0,g.hi>0,g.base>g.pad.top,g.base<g.height-g.pad.bottom]));")
        self.assertEqual(actual,[True,True,True,True])

    def test_fetch_and_snapshot_warnings_survive_source_selection(self):
        actual=self.evaluate("const now=Date.parse('2026-09-09T06:00:00Z');console.log(JSON.stringify([c.chartStatus({status:'ok'},{failed:true},now),c.chartStatus({status:'ok'},{generatedAt:'2026-09-09T00:00:00Z'},now),c.chartStatus({status:'ok'},{generatedAt:'2026-09-09T05:00:00Z'},now)]));")
        self.assertEqual([x[0] for x in actual],['retained','stale','ok'])


if __name__=='__main__':
    unittest.main()
