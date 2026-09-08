"""Synthetic publisher-layout and provenance tests; no network or sealed data."""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import tempfile
import unittest

from model.providers.pboc import (INDEX_URL, PBoCError, _decode, _official_url,
                                  collect, parse_balance, parse_tsf_flow,
                                  parse_tsf_stock)

BASE = "https://www.pbc.gov.cn"
YEAR = BASE + "/annual/index.html"
MONEY = BASE + "/money/index.html"
TSF = BASE + "/tsf/index.html"
BALANCE = BASE + "/files/balance.htm"
STOCK = BASE + "/files/stock.htm"
FLOW = BASE + "/files/flow.htm"
NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


def row(*cells):
    return "<tr>" + "".join("<td>" + str(cell) + "</td>" for cell in cells) + "</tr>"


def balance(assets="502068.47", deposits="226384.49", liabilities="502068.47"):
    return '<meta charset="utf-8">单位：亿元人民币<table>' + row(
        "项目 Item", "2026.06", "2026.07", "2026.08") + row(
        "总资产 Total Assets", "494334.98", assets, "　") + row(
        "储备货币 Reserve Money", "405945.01", "404903.37", "　") + row(
        "货币发行 Currency Issue", "151943.02", "152532.19", "　") + row(
        "其他存款性公司存款 Deposits of Other Depository Corporations", "228581.80", deposits, "　") + row(
        "总负债 Total Liabilities", "494334.98", liabilities, "　") + "</table>"


def stock(latest="463.27", growth="7.4"):
    return '<meta charset="utf-8">单位：万亿元人民币<table>' + row(
        "社会融资规模存量统计表") + '<tr><td>项目</td><td colspan="2">2026.6</td><td colspan="2">2026.7</td></tr>' + row(
        "项目 Items", "存量", "增速（%）", "存量", "增速（%）") + row(
        "社会融资规模存量 AFRE(stock)", "462.06", "7.4", latest, growth) + "</table>"


def flow(latest="14017"):
    return '<meta charset="utf-8">单位：亿元人民币<table>' + row(
        "社会融资规模增量统计表") + row("社会融资规模增量", "人民币贷款") + row(
        "AFRE(flow)", "RMB loans") + row("2026.06", "33671", "17650") + row(
        "2026.07", latest, "-5896") + row("2026.08", "　", "　") + "</table>"


def sources():
    def table_link(label, href):
        return '<table>' + row(label, '<a href="' + href + '">htm</a>') + '</table>'
    return {url: html.encode("utf-8") for url, html in {
        INDEX_URL: '<a href="/annual/index.html">2026年统计数据</a>',
        YEAR: '<a href="/money/index.html">货币统计概览</a><a href="/tsf/index.html">社会融资规模</a>',
        MONEY: table_link("货币当局资产负债表", BALANCE),
        TSF: table_link("社会融资规模存量统计表", STOCK) + table_link("社会融资规模增量统计表", FLOW),
        BALANCE: balance(), STOCK: stock(), FLOW: flow(),
    }.items()}


class TestPBoCProvider(unittest.TestCase):
    def test_collect_all_four_native_observations_and_hashes(self):
        payload = sources()
        with tempfile.TemporaryDirectory() as directory:
            result = collect(directory, payload.__getitem__, lambda: NOW)
            self.assertEqual(result["status"], "ok", result["errors"])
            self.assertEqual(len(result["observations"]), 4)
            by_id = {item["id"]: item for item in result["observations"]}
            self.assertEqual(by_id["PBOC_TOTAL_ASSETS"]["value"], 502068.47)
            self.assertEqual(by_id["PBOC_DEPOSITS_OTHER_DEPOSITORY_CORPORATIONS"]["value"], 226384.49)
            self.assertEqual(by_id["PBOC_TSF_STOCK"]["value"], 463.27)
            self.assertEqual(by_id["PBOC_TSF_FLOW"]["value"], 14017)
            for item in result["observations"]:
                self.assertEqual(item["period"], "2026-07-31")
                self.assertEqual(item["period_start"], "2026-07-01")
                self.assertEqual(item["known_by"], "2026-09-09T00:00:00Z")
                self.assertIsNone(item["original_release_at"])
                self.assertFalse(item["research_eligible"])
                self.assertEqual(hashlib.sha256((Path(directory) / item["raw_path"]).read_bytes()).hexdigest(), item["source_sha256"])
            self.assertEqual(by_id["PBOC_TSF_STOCK"]["unit"], "trillion CNY")
            self.assertEqual(by_id["PBOC_TSF_FLOW"]["unit"], "100 million CNY")

    def test_blank_latest_month_is_missing_not_zero(self):
        result = parse_balance(balance())
        self.assertEqual(max(result[0][3]), "2026-07-31")

    def test_blank_stock_does_not_consume_growth_column(self):
        result = parse_tsf_stock(stock(latest="　", growth="8.2"))
        self.assertEqual(result[0][3], {"2026-06-30": 462.06})

    def test_balance_rejects_accounting_identity_failure(self):
        with self.assertRaisesRegex(PBoCError, "identity_failed"):
            parse_balance(balance(liabilities="500000"))

    def test_deposits_not_currency_or_reserve_money(self):
        result = parse_balance(balance())[1]
        self.assertEqual(result[3]["2026-07-31"], 226384.49)
        self.assertEqual(result[2], "其他存款性公司存款")

    def test_units_changed_rejected(self):
        with self.assertRaisesRegex(PBoCError, "units_changed"):
            parse_balance(balance().replace("亿元人民币", "万亿元人民币"))
        with self.assertRaisesRegex(PBoCError, "units_changed"):
            parse_tsf_stock(stock().replace("万亿元人民币", "亿元人民币"))

    def test_negative_reported_flow_is_valid(self):
        self.assertEqual(parse_tsf_flow(flow("-100"))[0][3]["2026-07-31"], -100)

    def test_reordered_flow_columns_rejected(self):
        with self.assertRaisesRegex(PBoCError, "column_identity"):
            parse_tsf_flow(flow().replace("<td>社会融资规模增量</td><td>人民币贷款</td>",
                                         "<td>人民币贷款</td><td>社会融资规模增量</td>"))

    def test_gb2312_decoding(self):
        raw = balance().replace('charset="utf-8"', 'charset="gb2312"').encode("gb18030")
        self.assertEqual(parse_balance(_decode(raw))[0][3]["2026-07-31"], 502068.47)

    def test_non_official_link_rejected(self):
        for href in ("https://evil.invalid/data.htm", "https://www.pbc.gov.cn.evil.invalid/data.htm", "file:///etc/passwd"):
            with self.assertRaisesRegex(PBoCError, "non_official"):
                _official_url(BASE, href)

    def test_partial_failure_does_not_invent_tsf_stock(self):
        payload = sources()
        payload[STOCK] = b"service unavailable"
        with tempfile.TemporaryDirectory() as directory:
            result = collect(directory, payload.__getitem__, lambda: NOW)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["observations"]), 3)
        self.assertNotIn("PBOC_TSF_STOCK", {item["id"] for item in result["observations"]})

    def test_discovery_failure_is_error_with_no_values(self):
        with tempfile.TemporaryDirectory() as directory:
            result = collect(directory, lambda _: b"<html>Unavailable</html>", lambda: NOW)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["observations"], [])

    def test_future_reference_month_rejected(self):
        payload = sources()
        payload[BALANCE] = balance().replace("2026.07", "2026.12").encode()
        with tempfile.TemporaryDirectory() as directory:
            result = collect(directory, payload.__getitem__, lambda: NOW)
        self.assertEqual(len(result["observations"]), 2)
        self.assertEqual(result["errors"][0]["code"], "future_or_wrong_year_observation")

    def test_raw_content_deduplicated(self):
        payload = sources()
        with tempfile.TemporaryDirectory() as directory:
            collect(directory, payload.__getitem__, lambda: NOW)
            before = set(Path(directory).rglob("*.html"))
            collect(directory, payload.__getitem__, lambda: NOW)
            self.assertEqual(before, set(Path(directory).rglob("*.html")))

    def test_naive_clock_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone_aware"):
            collect("unused", clock=lambda: datetime(2026, 9, 9))


if __name__ == "__main__":
    unittest.main()
