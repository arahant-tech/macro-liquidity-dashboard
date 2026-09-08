import io
import json
from datetime import date, datetime, timezone
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
import xml.etree.ElementTree as ET
import zipfile

from model.providers import intermediary as m

TODAY = date(2026, 9, 9)
CLOCK = lambda: datetime(2026, 9, 9, 0, 0, tzinfo=timezone.utc)
PD_DATE = "2026-08-26"
PD_DEFS = {"pd": {"timeseries": [
    {"seriesbreak": "SBN2024", "keyid": key, "description": " ".join(value[2:])}
    for key, value in m.PD_SERIES.items()]}}
PD_VALUES = {"pd": {"timeseries": [
    {"asofdate": PD_DATE, "keyid": key, "value": str(i * 100)}
    for i, key in enumerate(m.PD_SERIES, 1)]}}
BREAKS = {"pd": {"seriesbreaks": [{"seriesbreak": "SBN2024", "startdate": "2024-07-03", "enddate": "9999-12-31"}]}}
JPM_URL = m.JPM_BASE + "/content/dam/jpmc/jpmorgan-chase-and-co/investor-relations/documents/quarterly-earnings/2026/2nd-quarter/2q26-earnings-supplement.xlsx"
JPM_FEED = {"items": [{"year": "2026", "quarter": "2nd", "docs": {"tenkSupplementalDoc": {
    "title": "2Q26 Earnings Supplement (xls)", "link": JPM_URL}}}]}
ICI_URL = m.ICI_BASE + "/research/stats/combined_active_index_0726"
ICI_LIST = f'<a href="{ICI_URL}">Release: Active and Index Investing, July 2026</a>'.encode()


def finra(values=("1,000", "200", "300"), extra=""):
    headers = "".join(f"<th>{x}</th>" for x in ["Month/Year", *(x[2] for x in m.FINRA_SERIES)])
    cells = "".join(f"<td>{x}</td>" for x in ["Jul-26", *values])
    return f'FINRA Statistics (shown in $ millions)<table><tr>{headers}</tr><tr>{cells}</tr>{extra}</table>'.encode()


def ici(index="300.0", share="75.0", month="Jul 2026"):
    rows = "".join(f'<tr><td>{key}</td><td>100.0</td><td>{index}</td><td>{share}</td></tr>' for key in m.ICI_SERIES)
    return (f'<h5>Total Net Assets Long-Term Mutual Funds and ETFs*</h5>Billions of dollars'
            f'<table><tr><td></td><td>Active {month}</td><td>Index {month}</td><td>Index as a % of Total</td></tr>{rows}</table>'
            '<h5>Flows of Long-Term Mutual Funds and ETFs</h5><table><tr><td>Total</td><td>BAD</td></tr></table>').encode()


def workbook(slr="0.055", exposure="100000", capital="5500", percent=True, year="2026", duplicate_capital=None):
    ns = m.NS["m"]
    root = ET.Element("worksheet", xmlns=ns)
    data = ET.SubElement(root, "sheetData")
    rows = [
        {"B": "JPMORGAN CHASE & CO."},
        {"B": "CAPITAL AND OTHER SELECTED BALANCE SHEET ITEMS (in millions, except ratio data)"},
        {"F": "Jun 30,"}, {"F": year},
        {"B": "Leverage-based capital metrics"},
        {"C": "Tier 1 capital", "F": float(capital)},
        {"C": "Total leverage exposure", "F": float(exposure)},
        {"C": "SLR", "F": float(slr)},
    ]
    if duplicate_capital is not None:
        rows.append({"C": "Tier 1 capital", "F": float(duplicate_capital)})
    for i, values in enumerate(rows, 1):
        row = ET.SubElement(data, "row", r=str(i))
        for col, value in values.items():
            cell = ET.SubElement(row, "c", r=f"{col}{i}", s="1" if values.get("C") == "SLR" and col == "F" else "0")
            if isinstance(value, str):
                cell.set("t", "inlineStr")
                ET.SubElement(ET.SubElement(cell, "is"), "t").text = value
            else:
                ET.SubElement(cell, "v").text = str(value)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", ET.tostring(root))
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{ns}"/>')
        z.writestr("xl/styles.xml", f'<styleSheet xmlns="{ns}"><cellXfs><xf numFmtId="0"/><xf numFmtId="{9 if percent else 0}"/></cellXfs></styleSheet>')
    return stream.getvalue()


class IntermediaryTests(unittest.TestCase):
    def test_finra_native_balances_without_netting(self):
        rows = m.parse_finra(finra(), TODAY)
        self.assertEqual([r["value"] for r in rows], [1000, 200, 300])
        self.assertTrue(all(r["period_start"] == r["period_end"] == "2026-07-31" for r in rows))
        self.assertTrue(all(not r["research_eligible"] for r in rows))

    def test_finra_columns_cannot_be_reordered(self):
        body = finra().replace(m.FINRA_SERIES[0][2].encode(), b"Unknown debt definition")
        with self.assertRaisesRegex(m.IntermediaryError, "columns_changed"):
            m.parse_finra(body, TODAY)

    def test_finra_missing_value_not_zero(self):
        for value in ("-", "N/A", "1,2", "-1", "NaN"):
            with self.subTest(value=value), self.assertRaises(m.IntermediaryError):
                m.parse_finra(finra((value, "200", "300")), TODAY)

    def test_finra_duplicate_month_fails(self):
        with self.assertRaisesRegex(m.IntermediaryError, "duplicate"):
            m.parse_finra(finra(extra="<tr><td>Jul-26</td><td>1</td><td>2</td><td>3</td></tr>"), TODAY)

    def test_finra_future_month_fails(self):
        with self.assertRaisesRegex(m.IntermediaryError, "future"):
            m.parse_finra(finra().replace(b"Jul-26", b"Sep-26"), TODAY)

    def test_pd_gross_sides_separate(self):
        rows = m.parse_pd(PD_VALUES, PD_DEFS, "SBN2024", TODAY)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(not r["aggregation_allowed"] for r in rows))
        self.assertTrue(all(r["frequency"] == "weekly" for r in rows))

    def test_pd_negative_positions_valid_negative_repo_invalid(self):
        p = json.loads(json.dumps(PD_VALUES))
        p["pd"]["timeseries"][0]["value"] = "-120"
        self.assertEqual(m.parse_pd(p, PD_DEFS, "SBN2024", TODAY)[0]["value"], -120)
        p["pd"]["timeseries"][1]["value"] = "-1"
        with self.assertRaises(m.IntermediaryError):
            m.parse_pd(p, PD_DEFS, "SBN2024", TODAY)

    def test_pd_definition_change_requires_review(self):
        d = json.loads(json.dumps(PD_DEFS))
        d["pd"]["timeseries"][0]["description"] = "Different asset class"
        with self.assertRaisesRegex(m.IntermediaryError, "definition_changed"):
            m.parse_pd(PD_VALUES, d, "SBN2024", TODAY)

    def test_new_seriesbreak_not_silently_spliced(self):
        b = json.loads(json.dumps(BREAKS))
        b["pd"]["seriesbreaks"][0]["seriesbreak"] = "SBN2026"
        with self.assertRaisesRegex(m.IntermediaryError, "definition_review"):
            m.choose_break(b, TODAY)

    def test_pd_missing_duplicate_and_date_disagreement_rejected(self):
        for mode in ("missing", "duplicate", "wrong_date"):
            p = json.loads(json.dumps(PD_VALUES))
            if mode == "missing": p["pd"]["timeseries"].pop()
            if mode == "duplicate": p["pd"]["timeseries"].append(p["pd"]["timeseries"][0])
            if mode == "wrong_date": p["pd"]["timeseries"][0]["asofdate"] = "2026-08-19"
            with self.subTest(mode=mode), self.assertRaises(m.IntermediaryError):
                m.parse_pd(p, PD_DEFS, "SBN2024", TODAY)

    def test_jpm_percent_format_and_identity(self):
        rows = m.parse_jpm(workbook(), date(2026, 6, 30), TODAY)
        self.assertEqual(rows[0]["value"], 5.5)
        self.assertEqual(rows[0]["components"]["tier1_capital_million_usd"], 5500)
        self.assertEqual(rows[0]["unit"], "percent")
        self.assertEqual(rows[0]["period_start"], rows[0]["period_end"])

    def test_jpm_unit_swap_cannot_pass(self):
        for params in ({"percent": False}, {"slr": "5.5"}, {"capital": "99000"}):
            with self.subTest(params=params), self.assertRaises(m.IntermediaryError):
                m.parse_jpm(workbook(**params), date(2026, 6, 30), TODAY)

    def test_jpm_wrong_period_cannot_pass(self):
        with self.assertRaisesRegex(m.IntermediaryError, "reference_column"):
            m.parse_jpm(workbook(year="2025"), date(2026, 6, 30), TODAY)

    def test_jpm_inconsistent_capital_cannot_pass(self):
        with self.assertRaisesRegex(m.IntermediaryError, "conflicting"):
            m.parse_jpm(workbook(duplicate_capital="6000"), date(2026, 6, 30), TODAY)

    def test_jpm_feed_discovers_report_and_checks_period(self):
        self.assertEqual(m.select_jpm(JPM_FEED, TODAY), (date(2026, 6, 30), JPM_URL))
        bad = json.loads(json.dumps(JPM_FEED))
        bad["items"][0]["docs"]["tenkSupplementalDoc"]["link"] = JPM_URL.replace("/2026/", "/2025/")
        with self.assertRaisesRegex(m.IntermediaryError, "period_mismatch"):
            m.select_jpm(bad, TODAY)

    def test_jpm_legacy_url_does_not_block_current_and_no_old_fallback(self):
        feed = json.loads(json.dumps(JPM_FEED))
        feed["items"].append({"year": "2010", "quarter": "2nd", "docs": {"tenkSupplementalDoc": {
            "title": "Legacy workbook", "link": "https://old.test/report.xls"}}})
        self.assertEqual(m.select_jpm(feed, TODAY), (date(2026, 6, 30), JPM_URL))
        feed["items"][0]["docs"] = {}
        with self.assertRaisesRegex(m.IntermediaryError, "current_supplement_missing"):
            m.select_jpm(feed, TODAY)

    def test_ici_correct_identical_population_denominator(self):
        rows = m.parse_ici(ici(), date(2026, 7, 31), TODAY)
        self.assertEqual([r["value"] for r in rows], [75, 75, 75])
        self.assertTrue(all(r["components"] == {"active_assets_billion_usd": 100, "index_assets_billion_usd": 300} for r in rows))

    def test_ici_rounded_reported_share_reconciliation(self):
        for params in ({"share": "30"}, {"index": "-"}, {"month": "Jun 2026"}):
            with self.subTest(params=params), self.assertRaises(m.IntermediaryError):
                m.parse_ici(ici(**params), date(2026, 7, 31), TODAY)

    def test_ici_discovery_checks_url_month(self):
        self.assertEqual(m.select_ici(ICI_LIST, TODAY), (date(2026, 7, 31), ICI_URL))
        with self.assertRaisesRegex(m.IntermediaryError, "period_mismatch"):
            m.select_ici(ICI_LIST.replace(b"_0726", b"_0626"), TODAY)

    def test_allowlist_blocks_credentials_external_and_queries(self):
        for url in ("https://evil.test/a", m.FINRA + "?api_key=x", m.JPM_FEED.replace("https://", "https://x@"), m.ICI_INDEX + "#x"):
            with self.subTest(url=url), self.assertRaises(m.IntermediaryError):
                m.allowed_url(url)

    def test_collector_isolates_403_and_retains_evidence(self):
        fixtures = {
            m.NY_BREAKS: json.dumps(BREAKS).encode(), m.NY_DEFS: json.dumps(PD_DEFS).encode(),
            m.NY_BASE+"/api/pd/latest/SBN2024.json": json.dumps(PD_VALUES).encode(),
            m.FINRA: finra(), m.JPM_FEED: json.dumps(JPM_FEED).encode(), JPM_URL: workbook(),
        }
        called = []
        def transport(url):
            called.append(url)
            if url == m.ICI_INDEX:
                raise HTTPError(url, 403, "Forbidden", {}, None)
            return fixtures[url]
        with tempfile.TemporaryDirectory() as folder:
            result = m.collect(folder, transport=transport, clock=CLOCK)
            self.assertEqual((result["success"], result["total"], result["status"]), (7, 10, "partial"))
            self.assertEqual(result["errors"][0]["code"], "http_403")
            self.assertNotIn(ICI_URL, called)
            self.assertTrue(all(o["known_by"] == "2026-09-09T00:00:00Z" for o in result["observations"]))
            for source in result["sources"]:
                body = (Path(folder)/source["raw_path"]).read_bytes()
                self.assertEqual(m.hashlib.sha256(body).hexdigest(), source["raw_sha256"])

    def test_collector_success_requires_all_sources(self):
        fixtures = {
            m.NY_BREAKS: json.dumps(BREAKS).encode(), m.NY_DEFS: json.dumps(PD_DEFS).encode(),
            m.NY_BASE+"/api/pd/latest/SBN2024.json": json.dumps(PD_VALUES).encode(),
            m.FINRA: finra(), m.JPM_FEED: json.dumps(JPM_FEED).encode(), JPM_URL: workbook(),
            m.ICI_INDEX: ICI_LIST, ICI_URL: ici(),
        }
        with tempfile.TemporaryDirectory() as folder:
            result = m.collect(folder, transport=fixtures.__getitem__, clock=CLOCK)
            self.assertEqual((result["success"], result["total"], result["status"]), (10, 10, "ok"))


if __name__ == "__main__":
    unittest.main()
