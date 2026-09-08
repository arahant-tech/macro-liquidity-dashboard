from datetime import date, datetime, timezone
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
from model.providers import market_buybacks as p

TODAY=date(2026,9,9)
CLOCK=lambda:datetime(2026,9,9,1,tzinfo=timezone.utc)
URL='https://press.spglobal.com/2025-12-18-S-P-500-Q3-2025-Buybacks'
INDEX=f'<a href="{URL}">S&amp;P 500 Q3 2025 Buybacks increase; Q4 2025 expected to rise</a>'
DOCUMENT='''<p>S&amp;P 500 Q3 2025 buybacks were $249.0 billion</p>
<p>NEW YORK, Dec. 18, 2025 / PRNewswire / -- S&amp;P Dow Jones Indices (S&amp;P DJI) today announced the preliminary S&amp;P 500 ® stock buybacks or share repurchases data for Q3 2025.</p>
<p>Q3 2025 share repurchases were $249.0 billion, up from last quarter.</p>
<p>For the 12-month September 2025 period buybacks were $1.020 trillion. Q4 2025 is expected to increase.</p>'''


class MarketBuybacksTests(unittest.TestCase):
    def selected(self):return p.select_latest(INDEX,TODAY)

    def test_actual_quarter_and_ttm_are_separate(self):
        result=p.parse_report(DOCUMENT,self.selected(),TODAY)
        self.assertEqual(result['value'],249.0)
        self.assertEqual(result['unit'],'billion USD')
        self.assertEqual(result['period_start'],'2025-07-01')
        self.assertEqual(result['period_end'],'2025-09-30')
        self.assertEqual(result['rounding_unit_billion_usd'],0.1)
        self.assertEqual(result['original_release_date'],'2025-12-18')
        self.assertEqual(result['reported_release_lag_days'],79)
        self.assertFalse(result['research_eligible'])
        self.assertFalse(result['aggregation_allowed'])
        self.assertFalse(result['constituent_date_verified'])

    def test_current_date_does_not_turn_old_quarter_into_current_data(self):
        result=p.parse_report(DOCUMENT,self.selected(),TODAY)
        self.assertEqual(result['status'],'stale')
        self.assertGreater(result['age_calendar_days'],190)

    def test_forecast_or_authorization_cannot_become_actual(self):
        for phrase in ['buybacks were authorized at','buybacks are expected to be','total shareholder returns were']:
            with self.subTest(phrase=phrase),self.assertRaises(p.BuybackError):
                p.parse_report(DOCUMENT.replace('buybacks were',phrase),self.selected(),TODAY)

    def test_headline_and_body_must_agree(self):
        with self.assertRaises(p.BuybackError):
            p.parse_report(DOCUMENT.replace('share repurchases were $249.0','share repurchases were $999.0'),self.selected(),TODAY)

    def test_declared_period_and_date_must_match_discovery(self):
        for doc in [DOCUMENT.replace('data for Q3 2025','data for Q4 2025'),DOCUMENT.replace('Dec. 18, 2025','Dec. 19, 2025')]:
            with self.assertRaises(p.BuybackError):p.parse_report(doc,self.selected(),TODAY)

    def test_conflicting_same_quarter_same_day_not_chosen_arbitrarily(self):
        second=f'<a href="{URL}-Amended">S&amp;P 500 Q3 2025 Buybacks correction</a>'
        with self.assertRaises(p.BuybackError):p.select_latest(INDEX+second,TODAY)

    def test_later_amendment_selected_by_date(self):
        second=f'<a href="{URL.replace("12-18","12-19")}">S&amp;P 500 Q3 2025 Buybacks corrected</a>'
        self.assertEqual(p.select_latest(INDEX+second,TODAY)[1],date(2025,12,19))

    def test_new_format_does_not_silently_keep_old_report(self):
        second='<a href="https://press.spglobal.com/2026-06-18-S-P-500-Repurchases">S&amp;P 500 reports new buybacks</a>'
        with self.assertRaises(p.BuybackError):p.select_latest(INDEX+second,TODAY)

    def test_unfinished_quarter_and_future_publication_rejected(self):
        for index in [INDEX.replace('2025-12-18','2026-10-01'),INDEX.replace('Q3 2025','Q3 2026').replace('2025-12-18','2026-09-08')]:
            with self.assertRaises(p.BuybackError):p.select_latest(index,TODAY)

    def test_non_official_links_rejected(self):
        with self.assertRaises(p.BuybackError):p.select_latest(INDEX.replace('press.spglobal.com','example.org'),TODAY)

    def test_malformed_amount_and_conflicting_duplicate_fail(self):
        for doc in [DOCUMENT.replace('$249.0','$2,4'),DOCUMENT+'<p>Q3 2025 share repurchases were $250 billion</p>']:
            with self.assertRaises(p.BuybackError):p.parse_report(doc,self.selected(),TODAY)

    def test_collector_success_is_not_freshness_success(self):
        def transport(url):return (INDEX if url==p.INDEX_URL else DOCUMENT).encode()
        with tempfile.TemporaryDirectory() as root:
            result=p.collect(root,transport,CLOCK)
            self.assertEqual(result['success'],1)
            self.assertEqual(result['status'],'partial')
            self.assertEqual(result['errors'][0]['code'],'latest_public_quarter_stale')
            self.assertEqual(result['observations'][0]['known_by'],'2026-09-09T01:00:00Z')
            self.assertEqual(result['observations'][0]['original_release_date'],'2025-12-18')
            for source in result['sources']:self.assertTrue((Path(root)/source['raw_path']).exists())

    def test_public_access_failure_is_explicit(self):
        def transport(url):raise HTTPError(url,403,'denied',{},None)
        with tempfile.TemporaryDirectory() as root:
            result=p.collect(root,transport,CLOCK)
            self.assertEqual(result['success'],0)
            self.assertEqual(result['observations'],[])
            self.assertEqual(result['errors'][0]['code'],'http_403')


if __name__=='__main__':unittest.main()
