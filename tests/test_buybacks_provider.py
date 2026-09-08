import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from model.providers import buybacks

NOW = datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc)


def fixture(cik='0000320193', value=1200):
    return {'cik': int(cik), 'entityName': 'Fixture issuer', 'taxonomy': 'us-gaap',
            'tag': buybacks.CONCEPT, 'label': 'Cash payments for common-stock repurchases',
            'description': 'Executed cash payments fixture', 'units': {'USD': [
                {'start': '2026-01-01', 'end': '2026-06-30', 'val': value,
                 'filed': '2026-08-01', 'accn': f'{cik}-26-000001', 'form': '10-Q', 'fy': 2026, 'fp': 'Q2'},
                {'start': '2026-04-01', 'end': '2026-06-30', 'val': value / 3,
                 'filed': '2026-08-01', 'accn': f'{cik}-26-000001', 'form': '10-Q', 'fy': 2026, 'fp': 'Q2', 'frame': 'CY2026Q2'},
            ]}}


def transport(url, headers):
    cik = re.search(r'CIK(\d{10})', url).group(1)
    return fixture(cik)


class BuybackTests(unittest.TestCase):
    def test_fixed_panel_is_not_a_market_aggregate(self):
        self.assertEqual([item['ticker'] for item in buybacks.PANEL], ['AAPL', 'MSFT', 'GOOGL', 'META', 'V'])
        self.assertEqual(len(set(item['cik'] for item in buybacks.PANEL)), 5)
        with self.assertRaises(buybacks.BuybackError):
            buybacks.concept_url('0000000001')

    def test_latest_ytd_duration_selected_without_summing_quarter(self):
        rows = buybacks.select_latest_facts(fixture(), buybacks.PANEL[0], NOW)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['value'], 1200)
        self.assertEqual(row['period_start'], '2026-01-01')
        self.assertEqual(row['period_end'], '2026-06-30')
        self.assertEqual(row['duration_days'], 181)
        self.assertEqual(len(row['alternative_current_durations']), 1)
        self.assertEqual(row['frequency'], 'filing_duration')
        self.assertFalse(row['research_eligible'])
        self.assertEqual(row['track'], 'equity')
        self.assertEqual(row['role'], 'flow')

    def test_zero_cash_payment_is_preserved(self):
        rows = buybacks.select_latest_facts(fixture(value=0), buybacks.PANEL[0], NOW)
        self.assertEqual(rows[0]['value'], 0)

    def test_authorization_concept_is_rejected(self):
        payload = fixture()
        payload['tag'] = 'StockRepurchaseProgramAuthorizedAmount'
        with self.assertRaisesRegex(buybacks.BuybackError, 'concept_identity_mismatch'):
            buybacks.select_latest_facts(payload, buybacks.PANEL[0], NOW)

    def test_non_usd_or_missing_cash_is_not_imputed(self):
        payload = fixture()
        payload['units']['shares'] = payload['units'].pop('USD')
        with self.assertRaisesRegex(buybacks.BuybackError, 'required_usd_cash_concept_unavailable'):
            buybacks.select_latest_facts(payload, buybacks.PANEL[0], NOW)

    def test_future_filing_rejected(self):
        payload = fixture()
        payload['units']['USD'][0]['filed'] = '2026-09-10'
        with self.assertRaisesRegex(buybacks.BuybackError, 'future_filing'):
            buybacks.select_latest_facts(payload, buybacks.PANEL[0], NOW)

    def test_wrong_issuer_rejected(self):
        with self.assertRaisesRegex(buybacks.BuybackError, 'issuer_identity_mismatch'):
            buybacks.select_latest_facts(fixture('0000789019'), buybacks.PANEL[0], NOW)

    def test_latest_amendment_is_retained_as_a_new_filing(self):
        payload = fixture()
        revised = dict(payload['units']['USD'][0], val=1300, filed='2026-08-15',
                       accn='0000320193-26-000002', form='10-Q/A')
        payload['units']['USD'].append(revised)
        selected = buybacks.select_latest_facts(payload, buybacks.PANEL[0], NOW)[0]
        self.assertEqual(selected['value'], 1300)
        self.assertEqual(selected['accession'], '0000320193-26-000002')
        self.assertEqual(selected['form'], '10-Q/A')

    def test_conflicting_same_filing_is_not_arbitrarily_chosen(self):
        payload = fixture()
        payload['units']['USD'].append(dict(payload['units']['USD'][0], val=1800))
        with self.assertRaisesRegex(buybacks.BuybackError, 'conflicting_same_filing'):
            buybacks.select_latest_facts(payload, buybacks.PANEL[0], NOW)

    def test_negative_cash_requires_review(self):
        with self.assertRaisesRegex(buybacks.BuybackError, 'invalid_cash_payment_value'):
            buybacks.select_latest_facts(fixture(value=-1), buybacks.PANEL[0], NOW)

    def test_capture_hash_and_actual_knowledge_time(self):
        with tempfile.TemporaryDirectory() as directory:
            result = buybacks.collect(directory, transport=transport, clock=lambda: NOW)
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(result['success'], 5)
            self.assertIsNone(result['market_aggregate'])
            self.assertEqual(len(result['observations']), 5)
            row = result['observations'][0]
            self.assertEqual(row['known_by'], '2026-09-09T10:00:00Z')
            self.assertNotEqual(row['known_by'][:10], row['filed_date'])
            self.assertIsNone(row['original_release_at'])
            self.assertEqual(hashlib.sha256((Path(directory) / row['raw_path']).read_bytes()).hexdigest(), row['raw_sha256'])
            self.assertEqual(len((Path(directory) / 'observed.jsonl').read_text().splitlines()), 5)

    def test_unchanged_capture_does_not_rewrite_first_knowledge_time(self):
        with tempfile.TemporaryDirectory() as directory:
            first = buybacks.collect(directory, transport=transport, clock=lambda: NOW)
            later = NOW.replace(hour=11)
            second = buybacks.collect(directory, transport=transport, clock=lambda: later)
            self.assertEqual(second['observations'][0]['known_by'], first['observations'][0]['known_by'])
            self.assertEqual(len((Path(directory) / 'observed.jsonl').read_text().splitlines()), 5)
            self.assertEqual(len(list((Path(directory) / 'raw').glob('*/*.json'))), 10)

    def test_changed_cash_preserves_prior_raw_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            first = buybacks.collect(directory, transport=transport, clock=lambda: NOW)
            def revised(url, headers):
                return fixture(re.search(r'CIK(\d{10})', url).group(1), value=1400)
            second = buybacks.collect(directory, transport=revised, clock=lambda: NOW.replace(hour=11))
            self.assertEqual(second['observations'][0]['value'], 1400)
            self.assertEqual(json.loads((Path(directory) / first['observations'][0]['raw_path']).read_text())['units']['USD'][0]['val'], 1200)
            self.assertEqual(len((Path(directory) / 'observed.jsonl').read_text().splitlines()), 10)

    def test_outage_retains_last_good_and_redacts_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            first = buybacks.collect(directory, transport=transport, clock=lambda: NOW)
            def failing(url, headers):
                raise RuntimeError('private_contact@example.com')
            second = buybacks.collect(directory, transport=failing, clock=lambda: NOW.replace(hour=11))
            self.assertEqual(second['status'], 'error')
            self.assertEqual(second['observations'], [])
            self.assertEqual(second['issuer_status'][0]['last_good_observations'][0]['value'], first['observations'][0]['value'])
            self.assertNotIn('private_contact', json.dumps(second))

    def test_sec_403_stops_remaining_panel_requests_without_bypass(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            def forbidden(url, headers):
                calls.append(url)
                raise buybacks.BuybackError('sec_http_403')
            result = buybacks.collect(directory, transport=forbidden, clock=lambda: NOW)
            self.assertEqual(len(calls), 1)
            self.assertEqual(result['status'], 'error')
            self.assertEqual(result['success'], 0)
            self.assertEqual(len(result['errors']), 5)
            self.assertEqual(result['errors'][1]['error'], 'sec_http_403_remaining_panel_not_requested')

    def test_research_output_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'isolated_from_research'):
            buybacks.collect(Path(__file__).resolve().parents[1] / 'research' / 'bad', transport=transport)

    def test_user_agent_has_true_project_identity_without_invented_contact(self):
        with patch.dict('os.environ', {}, clear=True):
            value = buybacks._user_agent()
            self.assertIn('github.com/arahant-tech/macro-liquidity-dashboard', value)
            self.assertNotIn('@', value)


if __name__ == '__main__':
    unittest.main()
