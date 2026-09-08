"""Independent regression tests for disclosure choice and BTC quantity semantics."""
import json
from datetime import date
import unittest

from model.providers import miner_flows as miner

TODAY = date(2026, 9, 9)
TITLE = 'CleanSpark Releases July 2026 Operational Update'
MARA_URL = miner.MARA_ROOT + '/sec-filings/all-sec-filings/content/123/mara-20260630.htm'


class IndependentMinerTests(unittest.TestCase):
    def test_latest_same_month_publication_wins_over_url_sort_order(self):
        older = miner.CLSK_ROOT + '/news/news-details/2026/z-original/default.aspx'
        newer = miner.CLSK_ROOT + '/news/news-details/2026/a-amended/default.aspx'
        feed = json.dumps({'GetPressReleaseListResult': [
            {'Headline': TITLE, 'PressReleaseDate': '08/05/2026 08:30:00', 'LinkToDetailPage': older},
            {'Headline': TITLE, 'PressReleaseDate': '08/20/2026 08:30:00', 'LinkToDetailPage': newer},
        ]})
        selected = miner.select_clsk(feed, TODAY)
        self.assertEqual(selected[1], newer)
        self.assertEqual(selected[3], '2026-08-20')

    def test_invalid_thousands_separators_do_not_become_valid_btc_amounts(self):
        for invalid in ('1,2', '1,,234', '12,34', '(0,12)'):
            with self.subTest(value=invalid), self.assertRaises(miner.MinerError):
                miner.number(invalid)

    def test_futures_contract_count_is_not_a_quantity_of_realized_bitcoin_sales(self):
        text = 'During the six months ended June 30, 2026, we sold 100 bitcoin futures contracts.'
        with self.assertRaises(miner.MinerError):
            miner.parse_mara(text, ('2026-06-30', MARA_URL), TODAY)

    def test_exact_quarter_disclosure_preserves_direct_period(self):
        text = ('During the six months ended June 30, 2026, we sold 1,200 bitcoin. '
                'For the three months ended June 30, 2026, we sold approximately 300 bitcoin.')
        row = miner.parse_mara(text, ('2026-06-30', MARA_URL), TODAY)
        self.assertEqual(row['value'], 300)
        self.assertEqual(row['period_start'], '2026-04-01')
        self.assertEqual(row['period_end'], '2026-06-30')
        self.assertEqual(row['duration_months'], 3)
        self.assertFalse(row['research_eligible'])
        self.assertFalse(row['market_aggregate'])

    def test_production_and_treasury_reconciliation_are_not_sales(self):
        text = ('During the six months ended June 30, 2026, we produced 1,000 bitcoin. '
                'Our bitcoin holdings decreased by 200 bitcoin after collateral movements.')
        with self.assertRaises(miner.MinerError):
            miner.parse_mara(text, ('2026-06-30', MARA_URL), TODAY)


if __name__ == '__main__':
    unittest.main()
