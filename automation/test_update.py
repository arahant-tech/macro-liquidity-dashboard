from pathlib import Path
from datetime import timedelta,date
from unittest import TestCase,main
from unittest.mock import patch
from tempfile import TemporaryDirectory
import copy,json
import update as u

SEED=u.load(u.ROOT/'live-model.json')

class MeasurementTests(TestCase):
    def test_current_model(self):self.assertTrue(u.validate_model(SEED))
    def test_new_week_and_pool_direction(self):
        r=u.core_rows({'2020-01-01':u.D(100000),'2020-01-08':u.D(110000)}, {'2020-01-01':u.D(100000),'2020-01-08':u.D(50000)})
        change=u.core_change(*r)
        self.assertEqual(change['delta_S_bn'],-40)
        self.assertEqual(change['delta_R_bn'],10)
        self.assertAlmostEqual(change['scalar_effect_bn']+change['allocation_effect_bn'],10)
    def test_mismatched_core_calendars(self):
        with self.assertRaisesRegex(ValueError,'calendars'):u.core_rows({'2020-01-01':u.D(100)},{})
    def test_corrupt_core_rejected(self):
        m=copy.deepcopy(SEED);m['core_weekly'][-1]['S_bn']+=1
        with self.assertRaises(ValueError):u.validate_model(m)
    def test_monthly_identity_rejected(self):
        m=copy.deepcopy(SEED);m['transmission_monthly'][-1]['mmf_channels'][0]['allocation_effect_bn']+=1
        with self.assertRaises(ValueError):u.validate_model(m)
    def test_stale_windows_rejected(self):
        m=copy.deepcopy(SEED);m['primary']['F_US_bn']+=1
        with self.assertRaises(ValueError):u.validate_model(m)
    def test_future_date_rejected(self):
        m=copy.deepcopy(SEED);m['core_weekly'][-1]['date']=(u.TODAY+timedelta(days=7)).isoformat()
        with self.assertRaises(ValueError):u.validate_model(m)
    def test_history_regression_rejected(self):
        m=copy.deepcopy(SEED);m['core_weekly']=m['core_weekly'][1:]
        with self.assertRaises(ValueError):u.validate_model(m,SEED)
    def test_nonfinite_rejected(self):
        m=copy.deepcopy(SEED);m['quality']['bad']=float('nan')
        with self.assertRaises(ValueError):u.validate_model(m)
    def test_new_valid_core_week(self):
        m=copy.deepcopy(SEED)
        # Insert a new historical week in a purpose-built fixed calendar.
        row=m['core_weekly'][-1];new=copy.deepcopy(row)
        new['date']=(date.fromisoformat(row['date'])+timedelta(days=7)).isoformat()
        m['core_weekly'].append(new)
        with patch.object(u,'TODAY',date.fromisoformat(new['date'])):self.assertTrue(u.validate_model(m,SEED))
    def test_missing_fund_week_not_filled(self):
        m=copy.deepcopy(SEED);m['fund_weekly']=m['fund_weekly'][:4]
        funds={r['date']:r for r in m['fund_weekly']};core={r['date']:r for r in m['core_weekly']}
        del funds[m['fund_weekly'][1]['date']]
        result=u.fund_window(funds,core,4,m['fund_weekly'][-1]['date'])
        self.assertFalse(result['available'])
    def test_p_zero_stays_one(self):
        row=u.core_rows({'2020-01-01':u.D(100000)},{'2020-01-01':u.D(0)})[0]
        self.assertEqual(row['p_bank'],1);self.assertEqual(row['p_rrp'],0)
    def test_roundoff_is_not_a_revision(self):
        self.assertTrue(u.measurement_equal({'x':1.00000000000001},{'x':1.0}))

class FundContractTests(TestCase):
    def source(self,wrong_unit=False):
        class Fixture(u.Source):
            def get(self,url):
                name=next(k for k,v in u.URLS.items() if v==url)
                body=(Path(__file__).parent/'fixtures'/f'{name}.html').read_bytes()
                if wrong_unit:body=body.replace(b'Millions of dollars',b'Billions of dollars')
                self.refs[url]=u.digest(body);return body
        return Fixture('funds')
    def test_separate_product_publication_dates_are_valid(self):
        seed=copy.deepcopy(SEED);seed['fund_weekly']=[{'date':'2026-07-29'}];seed['sources'][1]['release_date']=None
        result=u.collect_funds(self.source(),seed)
        self.assertEqual(len(result['fund_weekly']),5)
        self.assertGreater(len(set(result['product_release_dates'].values())),1)
        self.assertEqual(result['fund_weekly'][0]['domestic_equity_bn'],18.288)
    def test_changed_unit_rejected(self):
        seed=copy.deepcopy(SEED);seed['fund_weekly']=[{'date':'2026-07-29'}];seed['sources'][1]['release_date']=None
        with self.assertRaisesRegex(ValueError,'USD millions'):u.collect_funds(self.source(True),seed)

class TransactionTests(TestCase):
    def invoke(self,collectors):
        with TemporaryDirectory() as folder,patch.object(u,'ROOT',Path(folder)),patch.object(u,'RAW',Path(folder)/'.run/raw'):
            u.write(Path(folder)/'live-model.json',SEED)
            before=(Path(folder)/'live-model.json').read_bytes()
            status=u.update(collectors)
            return status,before,(Path(folder)/'live-model.json').read_bytes()
    def collectors(self):
        return {'core':lambda s,m:{'core_weekly':copy.deepcopy(m['core_weekly'])},
                'funds':lambda s,m:{'fund_weekly':copy.deepcopy(m['fund_weekly'])},
                'monthly':lambda s,m:{'transmission_monthly':copy.deepcopy(m['transmission_monthly']),'transmission':copy.deepcopy(m['transmission'])},
                'z1':lambda s,m:{'equity_sectors':copy.deepcopy(m['equity_sectors'])}}
    @staticmethod
    def fail(s,m):raise ValueError('Injected source failure')
    def test_ici_failure_retains_fund_snapshot(self):
        collectors=self.collectors();collectors['funds']=self.fail
        status,before,after=self.invoke(collectors)
        self.assertEqual(status['state'],'partial')
        self.assertEqual(json.loads(after)['fund_weekly'],SEED['fund_weekly'])
        self.assertEqual(status['model_sha256'],u.digest(after))
    def test_core_failure_preserves_entire_model(self):
        collectors=self.collectors();collectors['core']=self.fail
        status,before,after=self.invoke(collectors)
        self.assertEqual(status['state'],'failed');self.assertEqual(before,after)
        self.assertEqual(status['model_sha256'],u.digest(before))
    def test_invalid_candidate_never_replaces_model(self):
        collectors=self.collectors()
        def bad(s,m):
            rows=copy.deepcopy(m['core_weekly']);rows[-1]['S_bn']+=100
            return {'core_weekly':rows}
        collectors['core']=bad
        status,before,after=self.invoke(collectors)
        self.assertEqual(status['state'],'failed');self.assertEqual(before,after)

if __name__=='__main__':main()
