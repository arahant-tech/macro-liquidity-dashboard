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
        deployment=root/'publishing/cloud-feeds'
        cls.page_root=deployment if deployment.is_dir() else root
        cls.path=cls.page_root/'charts.js'
        cls.node=shutil.which('node')
        if not cls.node:
            bundled=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node'
            if bundled.exists(): cls.node=str(bundled)
        if not cls.node: raise unittest.SkipTest('Node runtime unavailable')

    def evaluate(self,code):
        script="const c=require(process.argv[1]);"+code
        result=subprocess.run([self.node,'-e',script,str(self.path)],capture_output=True,text=True,check=True)
        return json.loads(result.stdout)

    def test_details_page_preserves_the_existing_chart_entrypoint(self):
        html=(self.page_root/'details.html').read_text()
        self.assertRegex(html, r'<script\b[^>]*\bsrc=["\'](?:\./)?charts\.js["\']')
        self.assertRegex(html, r'<link\b[^>]*\bhref=["\'](?:\./)?dashboard\.css["\']')

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

    def test_every_collector_history_is_supported_by_the_details_page(self):
        from model.chart_history import FRED_IDS, PBOC_PARSERS, MONTHLY_SOURCES
        expected=set(FRED_IDS) | set(PBOC_PARSERS) | set(MONTHLY_SOURCES)
        actual=self.evaluate("console.log(JSON.stringify(Object.keys(c.SPECS)));")
        self.assertEqual(set(actual),expected)

    def test_published_history_passes_the_same_validation_used_by_refresh(self):
        actual=self.evaluate("const fs=require('fs'),path=require('path');const data=JSON.parse(fs.readFileSync(path.join(path.dirname(process.argv[1]),'chart-data.json'),'utf8'));console.log(JSON.stringify(c.validateHistory(data).series.map(s=>s.series_id)));")
        self.assertEqual(len(actual),13)
        self.assertTrue({'NYFED_ACM_TP10_MONTHLY','FINRA_MARGIN_DEBT','TIC_US_EQUITY_FOREIGN_NET_PURCHASES'}.issubset(actual))

    def test_unknown_or_duplicate_history_cannot_pass_validation(self):
        actual=self.evaluate("const base={schema_version:1,research_eligible:false,vintage_policy:'current_snapshot_not_historical_availability',generated_at:'2026-09-09T00:00:00Z'};const row={series_id:'WRESBAL',points:[{date:'2026-09-01',value:1}]};console.log(JSON.stringify([[{...row,series_id:'UNKNOWN'}],[row,row]].map(series=>{try{c.validateHistory({...base,series});return false;}catch{return true;}})));")
        self.assertEqual(actual,[True,True])

    def test_added_monthly_series_keep_native_unit_scales(self):
        actual=self.evaluate("console.log(JSON.stringify([['NYFED_ACM_TP10_MONTHLY','percent'],['FINRA_MARGIN_DEBT','million USD'],['TIC_US_EQUITY_FOREIGN_NET_PURCHASES','million USD']].map(([series_id,unit])=>c.unitFor({series_id,unit}))));")
        self.assertEqual(actual,[{'scale':1,'label':'%'},{'scale':1e6,'label':'조 USD'},{'scale':1e3,'label':'십억 USD'}])

    def test_term_premium_changes_are_basis_points_across_zero(self):
        actual=self.evaluate("const row={series_id:'NYFED_ACM_TP10_MONTHLY',unit:'percent'};console.log(JSON.stringify([[0,.2],[-.2,.3],[.3,-.1]].map(values=>c.changeFor(row,values.map(value=>({value}))))));")
        self.assertEqual(actual,['+20 bp · 표시기간 첫 관측 대비','+50 bp · 표시기간 첫 관측 대비','-40 bp · 표시기간 첫 관측 대비'])

    def test_stock_change_and_signed_flow_are_distinct(self):
        actual=self.evaluate("console.log(JSON.stringify([c.changeFor({series_id:'FINRA_MARGIN_DEBT'},[{value:100},{value:90}]),c.changeFor({series_id:'TIC_US_EQUITY_FOREIGN_NET_PURCHASES',chart_type:'bar'},[{value:-100},{value:200}]),c.changeFor({series_id:'WRESBAL'},[{value:0},{value:10}])]));")
        self.assertEqual(actual,['-10% · 표시기간 첫 관측 대비','공시된 각 월의 흐름','첫 관측이 0 이하이므로 변화율 미표시'])

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
