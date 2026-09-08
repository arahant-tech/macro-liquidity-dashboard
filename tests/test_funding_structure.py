import csv
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
import urllib.error

from model.providers import funding_structure as f


TODAY = date(2026, 9, 8)
CLOCK = lambda: datetime(2026, 9, 8, 19, 30, tzinfo=timezone.utc)
ACM = "RunDates,TERMYld,ACMFITYld,GSWYld\n31-Jul-2026,-0.25,4,4\n31-Aug-2026,0.762512945770763,4,4\n"


def ecb_rows():
    values = {"A0000": "28868.4573", "A6310": "5159.3301", "A6400": "4917.7321", "A6401": "2253.4547",
              "A6250": "1668.7695", "A6403": "393.3521", "A6404": "336.9112", "A6280": "109.0583"}
    return [{"KEY": "SUP.Q.B01.W0._Z." + item + "._T.SII._Z.ALL.LE.E.C", "FREQ": "Q", "REF_AREA": "B01",
             "COUNT_AREA": "W0", "COUNTERPART_SECTOR": "_Z", "CB_ITEM": item, "SBS_BREAKDOWN": "_T", "SBS_DI_1": "SII",
             "SBS_DI_2": "_Z", "CB_EXP_TYPE": "ALL", "DATA_TYPE": "LE", "BS_SUFFIX": "E", "SBS_SAMPLE_TYPE": "C",
             "TIME_PERIOD": "2026-Q1", "OBS_VALUE": values[item], "OBS_STATUS": "A", "CONF_STATUS": "F",
             "TIME_PER_COLLECT": "E", "TITLE": spec[2], "UNIT_MEASURE": "EUR", "UNIT_MULT": "9"}
            for item, spec in f.ECB_ITEMS.items()]


def ecb_csv(rows=None):
    rows = ecb_rows() if rows is None else rows
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(ecb_rows()[0]))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


class FundingSourceTests(unittest.TestCase):
    def test_actual_units_and_native_periods(self):
        acm = f.parse_acm(ACM, TODAY)
        self.assertEqual(acm["period_start"], "2026-08-31")
        self.assertEqual(acm["frequency"], "monthly")
        self.assertAlmostEqual(acm["value"], 0.762512945770763)
        rows, errors = f.parse_ecb(ecb_csv(), TODAY)
        self.assertEqual(errors, [])
        self.assertEqual(rows["A6310"]["period_start"], "2026-01-01")
        self.assertEqual(rows["A6310"]["period_end"], "2026-03-31")
        self.assertEqual(rows["A6310"]["units"], "billion EUR")
        self.assertEqual(rows["A6310"]["value"], 5159.3301)

    def test_acm_negative_is_valid(self):
        row = f.parse_acm(ACM.split("31-Aug")[0], TODAY)
        self.assertEqual(row["value"], -0.25)

    def test_acm_frequency_change_or_duplicate_fails(self):
        with self.assertRaisesRegex(f.FundingError, "duplicate_month"):
            f.parse_acm(ACM + "28-Aug-2026,0.3,4,4\n", TODAY)

    def test_acm_future_date_fails(self):
        with self.assertRaisesRegex(f.FundingError, "future_reference"):
            f.parse_acm(ACM.replace("31-Aug-2026", "30-Sep-2026"), TODAY)

    def test_acm_missing_is_never_zero(self):
        for value in ("", "NaN", "null", "Infinity", "1,2"):
            with self.subTest(value=value), self.assertRaises(f.FundingError):
                f.parse_acm(ACM.replace("0.762512945770763", value), TODAY)

    def test_acm_does_not_accept_other_estimator(self):
        with self.assertRaisesRegex(f.FundingError, "schema_changed"):
            f.parse_acm(ACM.replace("TERMYld", "KimWright"), TODAY)

    def test_ecb_status_Q_or_private_fails_for_series(self):
        for key, value in (("OBS_STATUS", "Q"), ("CONF_STATUS", "C"), ("OBS_VALUE", "")):
            rows = ecb_rows()
            rows[0][key] = value
            parsed, errors = f.parse_ecb(ecb_csv(rows), TODAY)
            self.assertNotIn("A6310", parsed)
            self.assertEqual(len(errors), 1)

    def test_ecb_changed_scope_unit_title_rejected(self):
        for key, value in (("REF_AREA", "US"), ("SBS_DI_1", "LSI"), ("UNIT_MULT", "6"),
                           ("UNIT_MEASURE", "USD"), ("SBS_SAMPLE_TYPE", "F"), ("TIME_PER_COLLECT", "A"),
                           ("TITLE", "Central bank reserves")):
            with self.subTest(key=key):
                rows = ecb_rows()
                rows[0][key] = value
                parsed, errors = f.parse_ecb(ecb_csv(rows), TODAY)
                self.assertNotIn("A6310", parsed)
                self.assertEqual(errors[0]["code"], "ecb_scope_unit_or_definition_changed")

    def test_ecb_future_or_invalid_quarter_rejected(self):
        for value in ("2026-Q3", "2025-Q5", "2026-03-31"):
            rows = ecb_rows()
            rows[0]["TIME_PERIOD"] = value
            parsed, errors = f.parse_ecb(ecb_csv(rows), TODAY)
            self.assertNotIn("A6310", parsed)
            self.assertEqual(len(errors), 1)

    def test_ecb_duplicates_fail_and_absence_is_reported(self):
        rows = ecb_rows()
        with self.assertRaisesRegex(f.FundingError, "duplicate"):
            f.parse_ecb(ecb_csv(rows + [rows[0]]), TODAY)
        parsed, errors = f.parse_ecb(ecb_csv(rows[1:]), TODAY)
        self.assertEqual(len(parsed), 7)
        self.assertEqual(errors[0]["code"], "ecb_series_missing_from_response")

    def test_unexpected_series_fails(self):
        rows = ecb_rows()
        rows[0]["CB_ITEM"] = "A6290"
        with self.assertRaisesRegex(f.FundingError, "unexpected"):
            f.parse_ecb(ecb_csv(rows), TODAY)

    def test_shares_match_native_components(self):
        parsed, _ = f.parse_ecb(ecb_csv(), TODAY)
        shares, errors = f.derive_shares(parsed)
        self.assertEqual(errors, [])
        self.assertAlmostEqual(shares[0]["value"], 17.87185940136815)
        self.assertAlmostEqual(shares[1]["value"], 45.82304717249644)
        self.assertFalse(shares[0]["aggregation_allowed"])
        self.assertEqual(shares[0]["components"]["denominator_series"], "ECB_SI_TOTAL_ASSETS")

    def test_shares_reject_mixed_quarters_units_or_scope(self):
        for key, value in (("period_end", "2025-12-31"), ("source_universe", "different banks"), ("units", "EUR")):
            parsed, _ = f.parse_ecb(ecb_csv(), TODAY)
            parsed["A0000"][key] = value
            shares, errors = f.derive_shares(parsed)
            self.assertEqual(len(shares), 2)
            self.assertEqual(errors[0]["code"], "share_components_period_scope_or_unit_mismatch")

    def test_share_denominator_missing_zero_or_smaller_fails(self):
        for value in (None, 0, 1):
            parsed, _ = f.parse_ecb(ecb_csv(), TODAY)
            if value is None:
                del parsed["A0000"]
            else:
                parsed["A0000"]["value"] = value
            shares, errors = f.derive_shares(parsed)
            self.assertEqual(len(shares), 2)
            self.assertEqual(len(errors), 1)

    def test_capture_records_evidence_and_does_not_backdate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "live"
            r = f.collect(root, transport=lambda url: (ACM if url == f.ACM_URL else ecb_csv()).encode(), clock=CLOCK)
            self.assertEqual((r["success"], r["total"]), (12, 12))
            for row in r["observations"]:
                self.assertEqual(row["known_by"], "2026-09-08T19:30:00Z")
                self.assertIsNone(row["original_release_at"])
                self.assertFalse(row["research_eligible"])
                self.assertFalse(row["release_timestamp_verified"])
                self.assertEqual(hashlib.sha256((root / row["raw_path"]).read_bytes()).hexdigest(), row["raw_sha256"])
            self.assertEqual(len(list((root / "raw/funding_structure").iterdir())), 2)
            self.assertFalse(any("BASIS" in row["series_id"] for row in r["observations"]))

    def test_ecb_failure_keeps_acm_and_errors_every_ecb_series(self):
        def transport(url):
            if url == f.ECB_URL:
                raise urllib.error.HTTPError(url, 403, "contact a third party", {}, None)
            return ACM.encode()
        with tempfile.TemporaryDirectory() as temp:
            r = f.collect(Path(temp) / "live", transport, CLOCK)
            self.assertEqual((r["success"], r["status"]), (1, "partial"))
            self.assertEqual(len(r["errors"]), 11)
            self.assertTrue(all(x["code"] == "http_403" for x in r["errors"]))

    def test_source_urls_and_size_are_bounded(self):
        for url in ("http://www.newyorkfed.org/", f.ACM_URL + "?anything=1", "https://example.org/data.csv"):
            with self.assertRaises(f.FundingError):
                f.allowed_url(url)
        with tempfile.TemporaryDirectory() as temp:
            for body in (b"", b"x" * (f.MAX_BYTES + 1), "not bytes"):
                with self.assertRaisesRegex(f.FundingError, "invalid_source_size"):
                    f.request(f.ACM_URL, Path(temp), lambda _: body, CLOCK)

    def test_stale_thresholds_preserve_values(self):
        acm = f.parse_acm(ACM, date(2027, 1, 1))
        self.assertEqual(acm["status"], "stale")
        parsed, _ = f.parse_ecb(ecb_csv(), date(2027, 1, 1))
        shares, _ = f.derive_shares(parsed)
        self.assertTrue(all(row["status"] == "stale" for row in [*parsed.values(), *shares]))

    def test_timezone_required(self):
        with self.assertRaisesRegex(ValueError, "timezone_aware"):
            f.now(lambda: datetime(2026, 9, 8))


if __name__ == "__main__":
    unittest.main()
