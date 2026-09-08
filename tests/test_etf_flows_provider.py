from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from model.providers import etf_flows as etf

NOW = datetime(2026, 9, 8, 18, 0, tzinfo=timezone.utc)


def html(spec, latest=None, total=None, rows=None, funds=None):
    funds = tuple(funds or spec['funds'])
    if rows is None:
        latest = latest if latest is not None else ['1.0'] * len(funds)
        total = total if total is not None else str(sum(float(value.replace('(', '-').replace(')', '')) for value in latest if value not in ('-', '')))
        rows = [('03 Sep 2026', ['0.0'] * len(funds), '0.0'), ('04 Sep 2026', latest, total),
                ('08 Sep 2026', ['99.0'] * len(funds), str(99 * len(funds)))]
    body = '<html><body><h1>ETF Flow (US$m)</h1><table><tr><th>Date</th>'
    body += ''.join('<th>' + value + '</th>' for value in funds) + '<th>Total</th></tr>'
    body += '<tr><td>Fee</td>' + '<td>0.25%</td>' * len(funds) + '<td></td></tr>'
    for day, values, total_value in rows:
        body += '<tr><td>' + day + '</td>' + ''.join('<td>' + str(value) + '</td>' for value in values) + '<td>' + str(total_value) + '</td></tr>'
    body += '</table><footer>Your IP: 203.0.113.99 private request metadata</footer></body></html>'
    return body.encode()


def transport(url, headers):
    spec = next(spec for spec in etf.SPECS if spec['url'] == url)
    return html(spec)


class EtfFlowsTests(unittest.TestCase):
    def test_signed_values_and_explicit_zero_are_not_missing(self):
        self.assertEqual(etf._number('(1,234.5)'), -1234.5)
        self.assertEqual(etf._number('0.0'), 0)
        self.assertIsNone(etf._number('-'))
        self.assertIsNone(etf._number(''))
        self.assertIsNone(etf._number('1,2'))

    def test_current_ny_date_is_excluded_even_when_all_cells_are_numeric(self):
        result, _ = etf.parse_latest(html(etf.SPECS[0]), etf.SPECS[0], NOW)
        self.assertEqual(result['observation_date'], '2026-09-04')
        self.assertEqual(result['value'], 12)
        self.assertTrue(result['components_complete'])
        self.assertEqual(result['covered_funds'], 12)
        self.assertEqual(result['latest_excluded_dates'][0]['reason'], 'current_new_york_date_provisional')

    def test_new_york_date_not_utc_date_controls_provisional_exclusion(self):
        after_utc_midnight = datetime(2026, 9, 9, 2, 0, tzinfo=timezone.utc)
        result, _ = etf.parse_latest(html(etf.SPECS[0]), etf.SPECS[0], after_utc_midnight)
        self.assertEqual(result['observation_date'], '2026-09-04')

    def test_incomplete_newer_row_never_creates_partial_aggregate(self):
        spec = etf.SPECS[0]
        values = ['1.0'] * len(spec['funds'])
        values[3] = '-'
        result, _ = etf.parse_latest(html(spec, latest=values), spec, NOW)
        self.assertEqual(result['observation_date'], '2026-09-03')
        self.assertEqual(result['value'], 0)
        self.assertIn('missing_or_nonnumeric_component', json.dumps(result['latest_excluded_dates']))

    def test_signed_outflows_reconcile_and_remain_negative(self):
        spec = etf.SPECS[0]
        result, _ = etf.parse_latest(html(spec, latest=['(2.0)'] + ['0.0'] * 11, total='(2.0)'), spec, NOW)
        self.assertEqual(result['value'], -2)
        self.assertEqual(result['components'][0]['value'], -2)
        self.assertEqual(result['rounding_difference'], 0)

    def test_rounding_tolerance_is_bounded_by_number_of_reported_cells(self):
        spec = etf.SPECS[0]
        good, _ = etf.parse_latest(html(spec, total='12.6'), spec, NOW)
        self.assertEqual(good['observation_date'], '2026-09-04')
        bad, _ = etf.parse_latest(html(spec, total='20.0'), spec, NOW)
        self.assertEqual(bad['observation_date'], '2026-09-03')
        self.assertIn('published_total_does_not_reconcile', json.dumps(bad['latest_excluded_dates']))

    def test_changed_universe_fails_instead_of_silently_changing_coverage(self):
        spec = etf.SPECS[0]
        with self.assertRaisesRegex(etf.FlowError, 'source_fund_universe_changed'):
            etf.parse_latest(html(spec, funds=spec['funds'] + ('NEWF',)), spec, NOW)

    def test_changed_or_missing_unit_heading_is_rejected(self):
        spec = etf.SPECS[0]
        with self.assertRaisesRegex(etf.FlowError, 'source_usd_million_unit_unverified'):
            etf.parse_latest(html(spec).replace(b'US$m', b'US$bn'), spec, NOW)

    def test_future_business_date_is_rejected(self):
        spec = etf.SPECS[0]
        with self.assertRaisesRegex(etf.FlowError, 'future_business_date_rejected'):
            etf.parse_latest(html(spec, rows=[('09 Sep 2026', ['1'] * 12, '12')]), spec, NOW)

    def test_conflicting_duplicate_business_dates_are_rejected(self):
        spec = etf.SPECS[0]
        rows = [('04 Sep 2026', ['1'] * 12, '12'), ('04 Sep 2026', ['2'] * 12, '24')]
        with self.assertRaisesRegex(etf.FlowError, 'conflicting_duplicate_business_date'):
            etf.parse_latest(html(spec, rows=rows), spec, NOW)

    def test_no_complete_prior_date_is_missing_not_zero(self):
        spec = etf.SPECS[0]
        with self.assertRaisesRegex(etf.FlowError, 'no_complete_reconciled_prior_date'):
            etf.parse_latest(html(spec, rows=[('04 Sep 2026', ['-'] * 12, '-')]), spec, NOW)

    def test_raw_capture_excludes_request_ip_and_other_page_content(self):
        _, fragment = etf.parse_latest(html(etf.SPECS[0]), etf.SPECS[0], NOW)
        self.assertTrue(fragment.startswith(b'<table>'))
        self.assertTrue(fragment.endswith(b'</table>'))
        self.assertNotIn(b'203.0.113.99', fragment)
        self.assertNotIn(b'footer', fragment)

    def test_collect_preserves_period_cash_method_hash_and_actual_knowledge_time(self):
        with tempfile.TemporaryDirectory() as directory:
            result = etf.collect(directory, transport=transport, clock=lambda: NOW)
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(result['success'], 2)
            row = result['observations'][0]
            self.assertEqual(row['known_by'], '2026-09-08T18:00:00Z')
            self.assertEqual(row['period_start'], '2026-09-04')
            self.assertEqual(row['period_end'], '2026-09-04')
            self.assertEqual(row['method'], 'publisher_reported_net_flow')
            self.assertEqual(row['track'], 'crypto')
            self.assertEqual(row['layer'], 5)
            self.assertFalse(row['research_eligible'])
            self.assertEqual(hashlib.sha256((Path(directory) / row['raw_path']).read_bytes()).hexdigest(), row['raw_sha256'])

    def test_unchanged_value_keeps_first_known_time_and_revision_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            first = etf.collect(directory, transport=transport, clock=lambda: NOW)
            second = etf.collect(directory, transport=transport, clock=lambda: NOW.replace(hour=19))
            self.assertEqual(first['observations'][0]['known_by'], second['observations'][0]['known_by'])
            self.assertEqual(len((Path(directory) / 'observed.jsonl').read_text().splitlines()), 2)
            self.assertEqual(len(list((Path(directory) / 'raw').glob('*/*.html'))), 4)

    def test_revision_creates_new_capture_without_overwriting_prior(self):
        with tempfile.TemporaryDirectory() as directory:
            first = etf.collect(directory, transport=transport, clock=lambda: NOW)
            def revised(url, headers):
                spec = next(spec for spec in etf.SPECS if spec['url'] == url)
                return html(spec, latest=['2'] * len(spec['funds']))
            second = etf.collect(directory, transport=revised, clock=lambda: NOW.replace(hour=19))
            self.assertEqual(second['observations'][0]['value'], 24)
            self.assertTrue((Path(directory) / first['observations'][0]['raw_path']).exists())
            self.assertEqual(len((Path(directory) / 'observed.jsonl').read_text().splitlines()), 4)

    def test_access_denial_does_not_trigger_bypass_or_other_asset_request(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            def blocked(url, headers):
                calls.append(url)
                raise etf.FlowError('farside_http_403')
            result = etf.collect(directory, transport=blocked, clock=lambda: NOW)
            self.assertEqual(len(calls), 1)
            self.assertEqual(result['status'], 'error')
            self.assertEqual(result['observations'], [])
            self.assertEqual(len(result['errors']), 2)

    def test_incomplete_newer_date_is_visible_as_partial_status(self):
        with tempfile.TemporaryDirectory() as directory:
            def incomplete(url, headers):
                spec = next(spec for spec in etf.SPECS if spec['url'] == url)
                return html(spec, latest=['-'] + ['1'] * (len(spec['funds']) - 1))
            result = etf.collect(directory, transport=incomplete, clock=lambda: NOW)
            self.assertEqual(result['status'], 'partial')
            self.assertTrue(all(row['status'] == 'partial' for row in result['observations']))


if __name__ == '__main__':
    unittest.main()
