"""Issuer cash-flow parsing tests using synthetic statements, with no network."""
from datetime import date, datetime, timezone
import hashlib
from pathlib import Path
import tempfile
import unittest

from model.providers.issuer_buybacks import (INDEX_URL, SHORT_URL,
    IssuerBuybackError, _url, collect, discover_release, parse_microsoft)

FINAL = "https://www.microsoft.com/en-us/Investor/earnings/FY-2026-Q4/press-release-webcast"
NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


def row(*cells):
    return "<tr>" + "".join("<td>" + str(value) + "</td>" for value in cells) + "</tr>"


def statement(payment="(456)", units="millions", title="FY26 Q4", date_text="July 29, 2026"):
    return ('<meta charset="utf-8"><h2>Earnings Release ' + title + '</h2>'
            '<p>REDMOND, Wash. — ' + date_text + ' — Microsoft Corp.</p>'
            '<p>CASH FLOWS STATEMENTS (In ' + units + ') (Unaudited)</p><table>'
            '<tr><td></td><td colspan="3">Three Months Ended June 30,</td><td></td>'
            '<td colspan="3">Twelve Months Ended June 30,</td></tr>' +
            row("", "2026", "", "2025", "", "2026", "", "2025") +
            row("Operations", "", "", "", "", "", "", "") +
            row("Net cash from operations", "1000", "", "900", "", "5000", "", "4000") +
            row("Common stock issued", "30", "", "20", "", "100", "", "80") +
            row("Common\n<span>stock</span> repurchased", payment, "", "(111)", "", "(1234)", "", "(1100)") +
            row("Common stock cash dividends paid", "(55)", "", "(44)", "", "(200)", "", "(180)") +
            row("Other, net", "(2)", "", "(1)", "", "(10)", "", "(9)") +
            row("Net cash used in financing", "(483)", "", "(136)", "", "(1344)", "", "(1209)") +
            '</table>')


def transport():
    return {
        INDEX_URL: b'<a href="https://aka.ms/latestearnings">Press Release &amp; Webcast</a>',
        SHORT_URL: (statement().encode(), FINAL),
    }.__getitem__


class TestIssuerBuybacks(unittest.TestCase):
    def test_explicit_current_quarter_not_prior_year_or_cumulative(self):
        result = parse_microsoft(statement(), FINAL, NOW.date())
        self.assertEqual(result["value"], 456)
        self.assertEqual(result["raw_signed_value"], -456)
        self.assertEqual(result["unit"], "million USD")
        self.assertEqual(result["period_start"], "2026-04-01")
        self.assertEqual(result["period_end"], "2026-06-30")
        self.assertEqual(result["duration_months"], 3)
        self.assertEqual(result["frequency"], "quarterly")

    def test_collect_raw_evidence_and_conservative_known_by(self):
        with tempfile.TemporaryDirectory() as directory:
            result = collect(directory, transport(), lambda: NOW)
            self.assertEqual(result["status"], "ok", result["errors"])
            item = result["observations"][0]
            self.assertEqual(item["known_by"], "2026-09-09T00:00:00Z")
            self.assertEqual(item["published_date"], "2026-07-29")
            self.assertIsNone(item["original_release_at"])
            self.assertFalse(item["research_eligible"])
            body = (Path(directory) / item["raw_path"]).read_bytes()
            self.assertEqual(hashlib.sha256(body).hexdigest(), item["source_sha256"])

    def test_sec_overlap_is_explicit(self):
        item = parse_microsoft(statement(), FINAL, NOW.date())
        self.assertEqual(item["series_id"], "IR_BUYBACKS_MSFT")
        self.assertFalse(item["aggregation_allowed"])
        self.assertEqual(item["not_additive_with"], ["SEC_BUYBACKS_MSFT"])
        self.assertEqual(item["coverage"], "single_issuer_not_market_aggregate")

    def test_wrong_units_rejected(self):
        with self.assertRaisesRegex(IssuerBuybackError, "units_or_statement_identity"):
            parse_microsoft(statement(units="billions"), FINAL, NOW.date())

    def test_positive_repurchased_line_rejected(self):
        with self.assertRaisesRegex(IssuerBuybackError, "outflow_sign_changed"):
            parse_microsoft(statement(payment="456"), FINAL, NOW.date())

    def test_zero_cash_payment_valid(self):
        self.assertEqual(parse_microsoft(statement(payment="0"), FINAL, NOW.date())["value"], 0)

    def test_missing_cash_payment_not_zero(self):
        with self.assertRaisesRegex(IssuerBuybackError, "invalid_cash_payment"):
            parse_microsoft(statement(payment="—"), FINAL, NOW.date())

    def test_authorization_not_substituted_for_payment(self):
        altered = statement().replace("Common\n<span>stock</span> repurchased", "Repurchase authorization")
        with self.assertRaisesRegex(IssuerBuybackError, "row_missing"):
            parse_microsoft(altered, FINAL, NOW.date())

    def test_missing_quarter_header_not_ytd_difference(self):
        altered = statement().replace("Three Months Ended", "Twelve Months Ended")
        with self.assertRaisesRegex(IssuerBuybackError, "quarter_header_missing"):
            parse_microsoft(altered, FINAL, NOW.date())

    def test_column_period_mismatch_rejected(self):
        with self.assertRaisesRegex(IssuerBuybackError, "quarter_end_header_mismatch"):
            parse_microsoft(statement().replace("June 30,", "March 31,"), FINAL, NOW.date())

    def test_fiscal_title_mismatch_rejected(self):
        with self.assertRaisesRegex(IssuerBuybackError, "fiscal_period_title_mismatch"):
            parse_microsoft(statement(title="FY26 Q3"), FINAL, NOW.date())

    def test_missing_release_date_retained_unknown(self):
        item = parse_microsoft(statement().replace("REDMOND, Wash.", "Location unreported"), FINAL, NOW.date())
        self.assertIsNone(item["published_date"])
        self.assertEqual(item["publication_precision"], "unknown")

    def test_future_reference_period_rejected(self):
        with self.assertRaisesRegex(IssuerBuybackError, "future_reference_period"):
            parse_microsoft(statement(), FINAL, date(2026, 6, 1))

    def test_publication_before_reference_period_rejected(self):
        with self.assertRaisesRegex(IssuerBuybackError, "publication_date_outside"):
            parse_microsoft(statement(date_text="May 29, 2026"), FINAL, NOW.date())

    def test_external_redirect_or_price_endpoint_rejected(self):
        for url in ["https://evil.invalid/release", "https://www.microsoft.com/stock-price",
                    "https://aka.ms/other", "http://www.microsoft.com/en-us/Investor/earnings/"]:
            with self.assertRaisesRegex(IssuerBuybackError, "source_url_not_allowed"):
                _url(url)

    def test_ambiguous_discovery_link_rejected(self):
        html = ('<a href="https://aka.ms/latestearnings">Press Release &amp; Webcast</a>'
                '<a href="' + FINAL + '">Press Release &amp; Webcast</a>')
        with self.assertRaisesRegex(IssuerBuybackError, "missing_or_ambiguous"):
            discover_release(html)

    def test_unavailable_does_not_emit_estimated_values(self):
        with tempfile.TemporaryDirectory() as directory:
            result = collect(directory, lambda _: b"<html>Unavailable</html>", lambda: NOW)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["observations"], [])


if __name__ == "__main__":
    unittest.main()
