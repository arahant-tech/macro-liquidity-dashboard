"""Issuer cash-flow parsing tests using synthetic statements, with no network."""
from datetime import date, datetime, timezone
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from model.providers.issuer_buybacks import (INDEX_URL, SHORT_URL,
    IssuerBuybackError, _url, collect, discover_release, parse_microsoft,
    parse_alphabet_pdf, parse_apple_pdf, parse_meta_pdf, parse_visa_pdf,
    discover_q4_document, _q4_feed_url, META_REGISTERED_DOCUMENT)

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
            result = collect(directory, transport(), lambda: NOW, tickers=["MSFT"])
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
            result = collect(directory, lambda _: b"<html>Unavailable</html>", lambda: NOW, tickers=["MSFT"])
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["observations"], [])


def alphabet_pages():
    return ["Alphabet Announces Second Quarter 2026 Results\nMOUNTAIN VIEW, Calif. – July 22, 2026 – Alphabet Inc.",
            "Alphabet Inc.\nCONSOLIDATED STATEMENTS OF CASH FLOWS\n(In millions, unaudited)\n"
            "Quarter Ended June 30, Year To Date June 30,\n2025 2026 2025 2026\n"
            "Repurchases of stock  (100)  0  (300)  0\n"]


def visa_pages():
    return ["Visa Reports Fiscal Third Quarter 2026 Results\nSan Francisco, CA, July 28, 2026 – Visa",
            "Visa Consolidated Statements of Cash Flows (unaudited)\nNine Months Ended\nJune 30,\n2026 2025\n(in millions)\n"
            "Repurchases of class A common stock  (400)  (300)\n"]


def apple_pages():
    return ["Apple Inc. CONDENSED CONSOLIDATED BALANCE SHEETS (Unaudited) (In millions) "
            "June 27, 2026 September 27, 2025 ASSETS: Cash and cash equivalents 50 40",
            "Apple Inc. CONDENSED CONSOLIDATED STATEMENTS OF CASH FLOWS (Unaudited) (In millions) "
            "Nine Months Ended June 27, 2026 June 28, 2025 Cash beginning balances 40 30 "
            "Repurchases of common stock (200) (300) Proceeds from issuance of term debt 1 2"]


def meta_pages():
    return ["Meta Reports Second Quarter 2026 Results\nMENLO PARK, Calif. – July 29, 2026 – Meta Platforms, Inc.",
            "META PLATFORMS, INC.\nCONDENSED CONSOLIDATED STATEMENTS OF CASH FLOWS\n(In millions)\n(Unaudited)\n"
            "Three Months Ended June 30, Six Months Ended June 30,\n2026 2025 2026 2025\n"
            "Cash flows from financing activities\n"
            "Taxes paid related to net share settlement of equity awards  (12)  (10)  (24)  (20)\n"
            "Repurchases of Class A common stock  —  (5)  —  (10)\n"
            "Payments for dividends and dividend equivalents  (2)  (1)  (4)  (2)\n"
            "Proceeds from issuance of long-term debt, net  30  —  60  —\n"
            "Principal payments on finance leases  (1)  (1)  (2)  (2)\n"
            "Other financing activities  (4)  1  (8)  2\n"
            "Net cash provided by (used in) financing activities  11  (16)  22  (32)\n"]


class TestFiveIssuerStatements(unittest.TestCase):
    def test_alphabet_current_quarter_zero_not_prior_year(self):
        item = parse_alphabet_pdf(alphabet_pages(), NOW.date())
        self.assertEqual((item["value"], item["duration_months"], item["period_start"]), (0, 3, "2026-04-01"))
        self.assertEqual(item["not_additive_with"], ["SEC_BUYBACKS_GOOGL"])

    def test_alphabet_year_order_change_rejected(self):
        pages = alphabet_pages()
        pages[1] = pages[1].replace("2025 2026 2025 2026", "2026 2025 2026 2025")
        with self.assertRaisesRegex(IssuerBuybackError, "year_columns_changed"):
            parse_alphabet_pdf(pages, NOW.date())

    def test_visa_native_ytd_not_quarter(self):
        item = parse_visa_pdf(visa_pages(), NOW.date())
        self.assertEqual((item["value"], item["duration_months"], item["period_start"]), (400, 9, "2025-10-01"))
        self.assertEqual(item["frequency"], "fiscal_year_to_date")
        self.assertFalse(item["aggregation_allowed"])

    def test_visa_ytd_fiscal_month_mismatch_rejected(self):
        pages = visa_pages()
        pages[1] = pages[1].replace("June 30", "March 31")
        with self.assertRaisesRegex(IssuerBuybackError, "fiscal_period_mismatch"):
            parse_visa_pdf(pages, NOW.date())

    def test_apple_flat_pdf_text_preserves_fiscal_ytd(self):
        item = parse_apple_pdf(apple_pages(), NOW.date(), date(2026, 7, 30))
        self.assertEqual((item["value"], item["period_start"], item["period_end"], item["duration_months"]),
                         (200, "2025-09-28", "2026-06-27", 9))
        self.assertEqual(item["native_period_basis"], "fiscal_year_to_date")

    def test_apple_missing_fiscal_start_not_assumed_calendar_start(self):
        pages = apple_pages()
        pages[0] = pages[0].replace("September 27, 2025", "September XX, 2025")
        with self.assertRaisesRegex(IssuerBuybackError, "fiscal_start_ambiguous"):
            parse_apple_pdf(pages, NOW.date())

    def test_apple_authorization_not_actual_cash_payment(self):
        pages = apple_pages()
        pages[1] = pages[1].replace("Repurchases of common stock", "Authorized repurchases of common stock")
        with self.assertRaises(IssuerBuybackError):
            parse_apple_pdf(pages, NOW.date())

    def test_meta_dash_zero_requires_full_financing_reconciliation(self):
        item = parse_meta_pdf(meta_pages(), NOW.date())
        self.assertEqual(item["value"], 0)
        self.assertEqual(item["duration_months"], 3)
        self.assertIn("reconcile", item["zero_validation"])

    def test_meta_dash_with_inconsistent_subtotal_is_not_zero(self):
        pages = meta_pages()
        pages[1] = pages[1].replace("activities  11  (16)", "activities  12  (16)")
        with self.assertRaisesRegex(IssuerBuybackError, "subtotal_failed"):
            parse_meta_pdf(pages, NOW.date())

    def test_pdf_currency_scale_change_rejected(self):
        pages = visa_pages()
        pages[1] = pages[1].replace("in millions", "in billions")
        with self.assertRaisesRegex(IssuerBuybackError, "units_changed"):
            parse_visa_pdf(pages, NOW.date())

    def test_future_pdf_period_rejected(self):
        with self.assertRaisesRegex(IssuerBuybackError, "reference_period"):
            parse_alphabet_pdf(alphabet_pages(), date(2026, 6, 1))

    def test_q4_null_metadata_ignored_and_latest_document_discovered(self):
        path = "https://s206.q4cdn.com/479360582/files/doc_financials/2026/q2/release.pdf"
        payload = {"GetFinancialReportListResult": [{"ReportYear": 2026, "ReportSubType": "Second Quarter",
            "ReportDate": "12/31/2099 00:00:00", "Documents": [
                {"DocumentFileType": None, "DocumentTitle": "Webcast", "DocumentCategory": None},
                {"DocumentFileType": "PDF", "DocumentTitle": "Earnings Release", "DocumentPath": path}]}]}
        # A publisher CMS ReportDate is neither reference period nor release date.
        self.assertEqual(discover_q4_document(payload, "GOOGL"), path)

    def test_q4_external_download_rejected(self):
        payload = {"GetFinancialReportListResult": [{"ReportYear": 2026, "ReportSubType": "Second Quarter",
            "Documents": [{"DocumentFileType": "PDF", "DocumentTitle": "Earnings Release", "DocumentPath": "https://evil.invalid/release.pdf"}]}]}
        with self.assertRaisesRegex(IssuerBuybackError, "not_allowed"):
            discover_q4_document(payload, "GOOGL")

    def test_registered_meta_document_does_not_claim_auto_discovery(self):
        import urllib.error
        def mocked(url):
            if url == META_REGISTERED_DOCUMENT:
                return b"%PDF-synthetic-meta"
            raise urllib.error.HTTPError(url, 403, "Forbidden", None, None)
        with tempfile.TemporaryDirectory() as directory, patch(
                "model.providers.issuer_buybacks._pdf_pages", return_value=meta_pages()):
            result = collect(directory, mocked, lambda: NOW, tickers=["META"])
        self.assertEqual(result["success_count"], 1)
        self.assertEqual(result["observations"][0]["discovery_status"], "blocked")
        self.assertEqual(result["discovery_warnings"][0]["code"], "http_403")

    def test_unknown_panel_member_rejected(self):
        with self.assertRaisesRegex(ValueError, "fixed_panel"):
            collect("unused", tickers=["UNKNOWN"])


if __name__ == "__main__":
    unittest.main()
