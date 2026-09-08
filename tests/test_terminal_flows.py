import json
from datetime import date, datetime, timezone
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.parse import urlsplit, parse_qs

from model.providers import terminal_flows as p

TODAY = date(2026,9,9)
CLOCK = lambda: datetime(2026,9,9,2,tzinfo=timezone.utc)


def tic(value="181426", month="2026-06"):
    header=["country","country_code","date","for_lt_eqty_pos","for_lt_eqty_net","for_lt_eqty_valchg"]
    rows=[['Table 1: U.S. Long-Term Securities Held by Foreign Residents'],
          ['A positive number for net U.S. sales to foreigners denotes an increase in a foreign position'],
          ['Millions of dollars'],['','','','U.S. Corp. Equity','U.S. Corp. Equity','U.S. Corp. Equity'],
          ['Country','Country Code','Date','Holdings','Net U.S. Sales','Valuation Change'],header,
          ['Grand Total','99996',month,'24500074',value,'-150223']]
    return '\n'.join('\t'.join(row) for row in rows)


def ici(kind="MF", domestic="-25,924", world="-4,669", total="-30,593"):
    identity="Estimated Long-Term Mutual Fund Flows" if kind=="MF" else "Estimated ETF Net Issuance"
    first="Total equity" if kind=="MF" else "Equity"
    return f'''<h1>{identity}</h1><p>Washington, DC; September 2, 2026—Release</p>
    <h5>Millions of dollars</h5><table><tr><th></th><th>8/26/2026</th><th>8/19/2026</th></tr>
    <tr><td>{first}</td><td>{total}</td><td>1</td></tr><tr><td>Domestic</td><td>{domestic}</td><td>1</td></tr>
    <tr><td>World</td><td>{world}</td><td>0</td></tr></table>'''


def zmeta(spec=None):
    spec=spec or p.Z1_SPECS["Z1"]
    return json.dumps({"seriess":[{"id":spec["id"],"title":spec["title"],"units":"Millions of U.S. Dollars",
                                     "seasonal_adjustment_short":"NSA","frequency_short":"Q"}]})


def zhtml(spec=None):
    spec=spec or p.Z1_SPECS["Z1"]
    return f'<title>{spec["title"]} ({spec["id"]}) | FRED | St. Louis Fed</title><p>Millions of U.S. Dollars, Not Seasonally Adjusted</p><p>Quarterly, End of Period</p>'


class TerminalFlowsTests(unittest.TestCase):
    def test_tic_equity_transaction_is_not_position_or_valuation(self):
        item=p.parse_tic(tic(),TODAY)
        self.assertEqual(item['value'],181426)
        self.assertEqual(item['period_start'],'2026-06-01')
        self.assertEqual(item['period_end'],'2026-06-30')
        self.assertEqual(item['structural_break'],'2023-02')
        self.assertFalse(item['research_eligible'])

    def test_tic_malformed_latest_world_row_cannot_fall_back_silently(self):
        content=tic()+"\nGrand Total\t99996\t2026-07\t999\t100"
        with self.assertRaises(p.TerminalError):p.parse_tic(content,TODAY)

    def test_tic_positive_and_negative_sign_preserved(self):
        self.assertEqual(p.parse_tic(tic('-101'),TODAY)['value'],-101)

    def test_tic_changed_column_semantics_fail(self):
        with self.assertRaises(p.TerminalError):
            p.parse_tic(tic().replace('Net U.S. Sales','Net U.S. Purchases'),TODAY)

    def test_tic_country_sum_cannot_replace_world_total(self):
        with self.assertRaises(p.TerminalError):
            p.parse_tic(tic().replace('99996','10189'),TODAY)
        with self.assertRaises(p.TerminalError):
            p.parse_tic(tic().replace('Grand Total','Austria'),TODAY)

    def test_tic_duplicates_missing_latest_and_future_fail(self):
        for content in [tic()+'\n'+tic().splitlines()[-1],tic('n.a.'),tic(month='2026-09')]:
            with self.subTest(content=content[-50:]), self.assertRaises(p.TerminalError):
                p.parse_tic(content,TODAY)

    def test_mf_and_etf_separate_native_weeks(self):
        mf=p.parse_ici(ici(), 'MF',TODAY)
        etf=p.parse_ici(ici('ETF','12,397','5,804','18,200'),'ETF',TODAY)
        self.assertEqual(len(mf),2)
        self.assertEqual(mf[0]['period_start'],'2026-08-20')
        self.assertEqual(mf[0]['period_end'],'2026-08-26')
        self.assertEqual(mf[0]['reported_release_lag_days'],7)
        self.assertEqual(etf[0]['value'],12397)
        self.assertIn('not_us_only',mf[1]['geographic_scope'])
        self.assertNotEqual(mf[0]['series_id'],etf[0]['series_id'])
        self.assertFalse(etf[0]['cash_settlement_verified'])

    def test_ici_cashflow_zero_valid_but_missing_not_zero(self):
        self.assertEqual(p.parse_ici(ici(domestic='0',world='0',total='0'),'MF',TODAY)[0]['value'],0)
        for bad in ['-','', 'N/A','1,2']:
            with self.subTest(bad=bad),self.assertRaises(p.TerminalError):
                p.parse_ici(ici(domestic=bad),'MF',TODAY)

    def test_ici_reconciliation_and_category_changes_fail(self):
        with self.assertRaises(p.TerminalError):
            p.parse_ici(ici(total='999'),'MF',TODAY)
        with self.assertRaises(p.TerminalError):
            p.parse_ici(ici().replace('Total equity','Total bond'),'MF',TODAY)

    def test_ici_future_release_or_nonweekly_date_fail(self):
        for changed in [ici().replace('September 2, 2026','September 20, 2026'),
                        ici().replace('8/26/2026','8/27/2026'),ici().replace('8/19/2026','8/12/2026')]:
            with self.assertRaises(p.TerminalError):p.parse_ici(changed,'MF',TODAY)

    def test_z1_keeps_actual_quarter_without_annualizing(self):
        result=p.parse_z1(zmeta(),json.dumps({'observations':[{'date':'2026-01-01','value':'31103'}]}),TODAY,api=True)
        self.assertEqual(result['value'],31103)
        self.assertEqual(result['period_end'],'2026-03-31')
        self.assertEqual(result['duration_months'],3)
        self.assertIn('buybacks',result['not_additive_with'])
        self.assertFalse(result['aggregation_allowed'])

    def test_z1_fund_transactions_are_separate_from_ici_subscriptions(self):
        for name,value in [("Z1_ETF","270543"),("Z1_MF","-169206")]:
            spec=p.Z1_SPECS[name]
            result=p.parse_z1(zmeta(spec),json.dumps({'observations':[{'date':'2026-01-01','value':value}]}),TODAY,api=True,spec=spec)
            self.assertEqual(result['series_id'],spec['output_id'])
            self.assertEqual(result['value'],float(value))
            self.assertEqual(result['period_end'],'2026-03-31')
            self.assertEqual(result['frequency'],'quarterly')
            self.assertFalse(result['aggregation_allowed'])
            self.assertIn('ICI_',result['not_additive_with'][0])
            self.assertNotIn(result['series_id'],result['not_additive_with'])

    def test_z1_fund_series_cannot_cross_assign_another_metadata(self):
        with self.assertRaises(p.TerminalError):
            p.parse_z1(zmeta(),json.dumps({'observations':[{'date':'2026-01-01','value':'1'}]}),TODAY,api=True,spec=p.Z1_SPECS['Z1_ETF'])

    def test_z1_refuses_stock_levels_and_saar(self):
        for meta in [zmeta().replace('Transactions','Level'),zmeta().replace('"NSA"','"SAAR"'),zmeta().replace('"Q"','"M"')]:
            with self.assertRaises(p.TerminalError):
                p.parse_z1(meta,json.dumps({'observations':[{'date':'2026-01-01','value':'31103'}]}),TODAY,api=True)

    def test_z1_csv_same_definition_and_missing_latest_fail(self):
        csv='observation_date,'+p.Z1_SERIES+'\n2026-01-01,31103\n'
        self.assertEqual(p.parse_z1(zhtml(),csv,TODAY)['value'],31103)
        with self.assertRaises(p.TerminalError):p.parse_z1(zhtml(),csv+'2026-04-01,.\n',TODAY)
        with self.assertRaises(p.TerminalError):p.parse_z1(zhtml(),csv.replace('2026-01-01','2026-02-01'),TODAY)

    def test_allowed_urls_reject_other_series_and_hosts(self):
        for url in ['https://example.org/x',p.Z1_URL+'/../SP500',
                    'https://fred.stlouisfed.org/graph/fredgraph.csv?id=SP500&cosd=2026-01-01']:
            with self.assertRaises(p.TerminalError):p.allow_url(url)

    def test_partial_access_failure_preserves_success_and_no_fake_ici_values(self):
        def transport(url):
            if url==p.TIC_URL:return tic().encode()
            for spec in p.Z1_SPECS.values():
                if url=='https://fred.stlouisfed.org/series/'+spec['id']:return zhtml(spec).encode()
            if 'fredgraph.csv' in url:
                sid=parse_qs(urlsplit(url).query)['id'][0]
                return ('observation_date,'+sid+'\n2026-01-01,31103\n').encode()
            raise HTTPError(url,403,'denied',{},None)
        with tempfile.TemporaryDirectory() as root:
            result=p.collect(root,transport,CLOCK,api_key='')
            self.assertEqual(result['success'],4)
            self.assertEqual(result['total'],8)
            self.assertEqual(result['status'],'partial')
            self.assertEqual([x['code'] for x in result['errors']],['http_403','http_403'])
            self.assertTrue(all(x['known_by']=='2026-09-09T02:00:00Z' for x in result['observations']))
            for source in result['sources']:self.assertTrue((Path(root)/source['raw_path']).exists())

    def test_api_key_not_exposed_in_raw_or_metadata(self):
        key='a'*32
        def transport(url):
            if '/fred/series/observations?' in url:return json.dumps({'observations':[{'date':'2026-01-01','value':'31103'}]}).encode()
            if '/fred/series?' in url:
                sid=parse_qs(urlsplit(url).query)['series_id'][0]
                spec=next(item for item in p.Z1_SPECS.values() if item['id']==sid)
                return zmeta(spec).encode()
            if url==p.TIC_URL:return tic().encode()
            return ici('ETF' if url==p.ICI_ETF_URL else 'MF').encode()
        with tempfile.TemporaryDirectory() as root:
            result=p.collect(root,transport,CLOCK,api_key=key)
            self.assertEqual(result['success'],8)
            self.assertNotIn(key,json.dumps(result))
            for file in Path(root).rglob('*'):
                if file.is_file():self.assertNotIn(key.encode(),file.read_bytes())

    def test_credential_echo_refused_before_archiving(self):
        with tempfile.TemporaryDirectory() as root,self.assertRaises(p.TerminalError):
            p.request(p.Z1_URL,Path(root),lambda url:b'a'*32,CLOCK,key='a'*32)

    def test_immutable_body_hash_collision_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            _, evidence=p.request(p.TIC_URL,Path(root),lambda url:tic().encode(),CLOCK)
            (Path(root)/evidence['raw_path']).write_bytes(b'changed')
            with self.assertRaises(p.TerminalError):p.request(p.TIC_URL,Path(root),lambda url:tic().encode(),CLOCK)


if __name__=='__main__':unittest.main()
