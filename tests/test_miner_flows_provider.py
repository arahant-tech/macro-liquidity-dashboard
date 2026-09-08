import json
from pathlib import Path
import tempfile
import unittest
from datetime import date, datetime, timezone
from model.providers import miner_flows as m


TITLE = "CleanSpark Releases July 2026 Operational Update"
CLSK_URL = m.CLSK_ROOT + "/news/news-details/2026/report/default.aspx"
MARA_URL = m.MARA_ROOT + "/sec-filings/all-sec-filings/content/123/mara-20260630.htm"
CLSK_DOC = f"<h1>{TITLE}</h1><p>month ended July 31, 2026</p><table><tr><td>Bitcoin sold at spot</td><td>(12)</td></tr><tr><td>Bitcoin sold pursuant to call exercises</td><td>(3)</td></tr><tr><td>Bitcoin produced</td><td>100</td></tr><tr><td>Total bitcoin holdings</td><td>1000</td></tr></table>"
SELECTED = ("2026-07-31", CLSK_URL, "2026-07-01", "2026-08-05", TITLE)
TODAY = date(2026, 9, 9)


class MinerTests(unittest.TestCase):
    def test_explicit_sales_include_call_delivery_not_production(self):
        row = m.parse_clsk(CLSK_DOC, SELECTED)
        self.assertEqual(row["value"], 15)
        self.assertTrue(row["includes_derivative_delivery"])
        self.assertEqual(row["period_start"], "2026-07-01")

    def test_production_or_holdings_cannot_substitute_for_sales(self):
        text = f"{TITLE} month ended July 31, 2026<table><tr><td>Bitcoin produced</td><td>100</td></tr></table>"
        with self.assertRaises(m.MinerError):
            m.parse_clsk(text, SELECTED)

    def test_missing_sales_not_zero_and_explicit_zero_is_valid(self):
        with self.assertRaises(m.MinerError):
            m.parse_clsk(CLSK_DOC.replace("(12)", "–"), SELECTED)
        self.assertEqual(m.parse_clsk(CLSK_DOC.replace("(12)", "0").replace("(3)", "0"), SELECTED)["value"], 0)

    def test_unknown_sales_category_does_not_silently_disappear(self):
        with self.assertRaises(m.MinerError):
            m.parse_clsk(CLSK_DOC.replace("at spot", "in an unknown transaction"), SELECTED)

    def test_duplicate_category_and_period_mismatch_are_rejected(self):
        for text in [CLSK_DOC.replace("pursuant to call exercises", "at spot"), CLSK_DOC.replace("ended July 31", "ended June 30")]:
            with self.assertRaises(m.MinerError):
                m.parse_clsk(text, SELECTED)

    def test_mara_keeps_direct_ytd_without_monthly_or_quarter_difference(self):
        row = m.parse_mara("During the six months ended June 30, 2026, we sold approximately 2,300 bitcoin", ("2026-06-30", MARA_URL), TODAY)
        self.assertEqual((row["value"], row["period_start"], row["duration_months"]), (2300, "2026-01-01", 6))

    def test_mara_direct_quarter_preferred_if_both_directly_disclosed(self):
        text = "For the six months ended June 30, 2026, we sold 2,300 bitcoin. During the three months ended June 30, 2026, we sold 500 bitcoin"
        row = m.parse_mara(text, ("2026-06-30", MARA_URL), TODAY)
        self.assertEqual((row["value"], row["period_start"]), (500, "2026-04-01"))

    def test_mara_announcements_and_old_period_sales_are_not_current_sales(self):
        for text in ["We plan to sell 500 bitcoin", "During the six months ended June 30, 2025, we sold 500 bitcoin"]:
            with self.assertRaises(m.MinerError):
                m.parse_mara(text, ("2026-06-30", MARA_URL), TODAY)

    def test_conflicting_duplicate_mara_facts_are_rejected(self):
        with self.assertRaises(m.MinerError):
            m.parse_mara("For the six months ended June 30, 2026, we sold 500 bitcoin. During the six months ended June 30, 2026, we sold 600 bitcoin", ("2026-06-30", MARA_URL), TODAY)

    def test_future_and_unapproved_source_are_rejected(self):
        with self.assertRaises(m.MinerError):
            m.period(2027, 1, 1, TODAY)
        for url in ["https://example.com/", "http://ir.mara.com/", "https://ir.mara.com@evil.example/"]:
            with self.assertRaises(m.MinerError):
                m.allowed_url(url)

    def test_capture_provenance_and_partial_failure(self):
        feed = json.dumps({"GetPressReleaseListResult": [{"Headline": TITLE, "PressReleaseDate": "08/05/2026 08:30:00", "LinkToDetailPage": CLSK_URL}]})
        documents = {m.CLSK_FEED: feed, CLSK_URL: CLSK_DOC, m.MARA_INDEX: f'<a href="{MARA_URL}">HTML</a>', MARA_URL: "During the six months ended June 30, 2026, we sold 2,300 bitcoin"}
        clock = lambda: datetime(2026, 9, 9, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as folder:
            output = m.collect(folder, transport=lambda u: documents[u].encode(), clock=clock)
            self.assertEqual((output["status"], output["success"]), ("ok", 2))
            for row in output["observations"]:
                self.assertFalse(row["research_eligible"])
                self.assertEqual(row["known_by"], "2026-09-09T00:00:00Z")
                self.assertEqual(m.hashlib.sha256((Path(folder) / row["raw_path"]).read_bytes()).hexdigest(), row["raw_sha256"])
            del documents[MARA_URL]
            result = m.collect(folder, transport=lambda u: documents[u].encode(), clock=clock)
            self.assertEqual((result["status"], result["success"]), ("partial", 1))
            self.assertNotIn(MARA_URL, json.dumps(result["errors"]))


if __name__ == "__main__":
    unittest.main()
