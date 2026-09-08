"""Historical chart contracts: no backdating, source revision loss or resampling."""
import calendar
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from model import chart_history as h, live_api
from test_pboc_provider import balance
from test_intermediary import finra
from test_terminal_flows import tic

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


def monthly_dates(count=13, last=date(2026, 7, 31)):
    months = [last.year * 12 + last.month - 1 - i for i in reversed(range(count))]
    return [date(n // 12, n % 12 + 1, calendar.monthrange(n // 12, n % 12 + 1)[1]) for n in months]


def acm_history(count=13):
    dates = monthly_dates(count, date(2026, 8, 31))
    # Preserve a source business-day month end instead of replacing it by 31st.
    rows = [f"{(d if d != date(2025, 8, 31) else date(2025, 8, 29)).strftime('%d-%b-%Y')},{i / 100},4,4"
            for i, d in enumerate(dates)]
    return ("RunDates,TERMYld,ACMFITYld,GSWYld\n" + "\n".join(rows)).encode()


def finra_history(count=13):
    extra = "".join(f"<tr><td>{d.strftime('%b-%y')}</td><td>{100+i}</td><td>200</td><td>300</td></tr>"
                    for i, d in enumerate(monthly_dates(count)[:-1]))
    return finra(extra=extra)


def tic_history(count=13):
    extra = "".join(f"\nGrand Total\t99996\t{d.strftime('%Y-%m')}\t999999\t{i-2}\t55555"
                    for i, d in enumerate(monthly_dates(count, date(2026, 6, 30))[:-1]))
    return (tic() + extra).encode()


def save_monthly_source(root, sid, body, known_by="2026-09-08T20:00:00Z", observation=None):
    provider, url, suffix, _ = h.MONTHLY_SOURCES[sid]
    folder = root / "live" / provider
    digest = hashlib.sha256(body).hexdigest()
    rel = "raw/" + digest + "." + suffix
    path = folder / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    latest, _ = h.monthly_history(sid, body, date(2023, 9, 1), NOW.date())
    receipt = {"source_url": url, "raw_path": rel, "raw_sha256": digest, "known_by": known_by}
    observation = observation or {**latest, **receipt}
    state = {"observations": [observation], "sources": [receipt]}
    (folder / "latest.json").write_text(json.dumps(state))
    return state


class Client:
    def __init__(self, failed=False):
        self.failed = failed
        self.calls = []

    def request(self, endpoint, params):
        self.calls.append((endpoint, params))
        if self.failed:
            raise live_api.FredError("synthetic_failure")
        sid = params["series_id"]
        spec = next(s for s in live_api.load_catalog() if s.series_id == sid)
        if endpoint == "series":
            return {"seriess": [{"id": sid, "units": spec.expected_units, "frequency": spec.expected_frequency}]}
        return {"count": 3, "observations": [{"date": d, "value": v} for d, v in
                [("2026-08-19", "0"), ("2026-08-26", "."), ("2026-09-02", "2")]]}


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.spec = next(s for s in live_api.load_catalog() if s.series_id == "WRESBAL")
        self.meta = Client().request("series", {"series_id": "WRESBAL"})
        self.payload = Client().request("series/observations", {"series_id": "WRESBAL"})

    def validate(self, body):
        return h.validate_fred(self.spec, self.meta, body, date(2023, 9, 1), NOW.date())

    def test_native_dates_missing_and_zero_stay_distinct(self):
        _, points = self.validate(self.payload)
        self.assertEqual([p["value"] for p in points], [0, None, 2])
        self.assertEqual(len(points), 3)

    def test_duplicate_dates_are_rejected(self):
        self.payload["observations"][1]["date"] = "2026-08-19"
        with self.assertRaises(live_api.FredError):
            self.validate(self.payload)

    def test_future_vintage_on_later_row_is_rejected(self):
        self.payload["observations"][2]["realtime_start"] = "2026-09-10"
        with self.assertRaises(live_api.FredError):
            self.validate(self.payload)

    def test_future_observation_is_rejected(self):
        self.payload["observations"][2]["date"] = "2026-09-10"
        with self.assertRaises(live_api.FredError):
            self.validate(self.payload)

    def test_incomplete_pagination_is_rejected(self):
        self.payload["count"] = 4
        with self.assertRaises(live_api.FredError):
            self.validate(self.payload)

    def test_unbounded_history_is_rejected(self):
        self.payload["observations"] *= 700
        with self.assertRaises(live_api.FredError):
            self.validate(self.payload)

    def test_unit_change_is_rejected(self):
        self.meta["seriess"][0]["units"] = "Billions of dollars"
        with self.assertRaises(live_api.FredError):
            self.validate(self.payload)

    def test_unchanged_then_failure_preserves_first_evidence_and_known_time(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = h.collect(root / "run1", root / "live", root / "state", client=Client(), clock=lambda: NOW)
            second = h.collect(root / "run2", root / "live", root / "state", client=Client(), clock=lambda: NOW+timedelta(hours=1))
            third = h.collect(root / "run3", root / "live", root / "state", client=Client(True), clock=lambda: NOW+timedelta(hours=2))
            for a, b, c in zip(first["series"], second["series"], third["series"]):
                for field in ("points", "known_by", "raw_path", "raw_sha256"):
                    self.assertEqual(a[field], b[field])
                    self.assertEqual(a[field], c[field])
                self.assertEqual(c["status"], "retained")
                self.assertEqual(hashlib.sha256((root/"state"/c["raw_path"]).read_bytes()).hexdigest(), c["raw_sha256"])
                self.assertEqual(c["known_by"], "2026-09-09T00:00:00Z")
                self.assertFalse(c["research_eligible"])

    def test_pboc_older_cell_revision_uses_this_runs_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); live=root/"live/pboc"; live.mkdir(parents=True)
            url="https://www.pbc.gov.cn/balance.htm"
            def receipt(body, name, captured):
                rel="raw/"+name+".html"; p=live/rel; p.parent.mkdir(exist_ok=True); p.write_bytes(body)
                return {"source_url":url,"raw_path":rel,"source_sha256":hashlib.sha256(body).hexdigest(),"known_by":captured}
            old=receipt(balance().encode(),"first","2026-09-09T00:00:00Z")
            obs={"series_id":"PBOC_TOTAL_ASSETS","unit":"100 million CNY",**old}
            (live/"latest.json").write_text(json.dumps({"observations":[obs],"sources":[old]}))
            first=h.collect(root/"run1",root/"live",root/"state",client=Client(),clock=lambda:NOW)
            new=receipt(balance().replace("494334.98","490000.00").encode(),"revised","2026-09-09T01:00:00Z")
            # A stable latest month deliberately leaves the observation's old
            # provenance intact; only this run's sources contains the revision.
            (live/"latest.json").write_text(json.dumps({"observations":[obs],"sources":[new]}))
            second=h.collect(root/"run2",root/"live",root/"state",client=Client(),clock=lambda:NOW+timedelta(hours=1))
            a=next(s for s in first["series"] if s["series_id"]=="PBOC_TOTAL_ASSETS")
            b=next(s for s in second["series"] if s["series_id"]=="PBOC_TOTAL_ASSETS")
            self.assertEqual(a["points"][-1],b["points"][-1])
            self.assertEqual(b["points"][0]["value"],490000)
            self.assertNotEqual(a["known_by"],b["known_by"])
            self.assertEqual(b["raw_sha256"],new["source_sha256"])
            (live/"latest.json").write_text(json.dumps({"observations":[obs],"sources":[]}))
            third=h.collect(root/"run3",root/"live",root/"state",client=Client(),clock=lambda:NOW+timedelta(hours=2))
            c=next(s for s in third["series"] if s["series_id"]=="PBOC_TOTAL_ASSETS")
            self.assertEqual(c["points"],b["points"])
            self.assertEqual(c["status"],"retained")

    def test_only_fixed_macro_series_and_native_units_requested(self):
        client=Client()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            h.collect(root/"run",root/"live",root/"state",client=client,clock=lambda:NOW)
        self.assertEqual({p["series_id"] for _,p in client.calls},set(h.FRED_IDS))
        for endpoint,p in client.calls:
            self.assertNotIn("frequency",p)
            self.assertEqual(p["realtime_start"],"2026-09-09")
            if endpoint=="series/observations":
                self.assertEqual(p["units"],"lin")
                self.assertLessEqual(p["limit"],2000)


class MonthlyHistoryTests(unittest.TestCase):
    def parse(self, sid, body):
        return h.monthly_history(sid, body, date(2023, 9, 1), NOW.date())

    def test_acm_business_date_percent_negative_and_zero_are_native(self):
        body = acm_history().replace(b",0.01,4,4", b",-0.01,4,4")
        latest, points = self.parse("NYFED_ACM_TP10_MONTHLY", body)
        self.assertEqual(len(points), 13)
        self.assertEqual(points[0], {"date": "2025-08-29", "value": 0})
        self.assertEqual(points[1]["value"], -.01)
        self.assertEqual(latest["unit"], "percent")
        self.assertEqual(latest["value"], .12)

    def test_finra_selects_debit_balance_not_free_credit(self):
        latest, points = self.parse("FINRA_MARGIN_DEBT", finra_history())
        self.assertEqual(len(points), 13)
        self.assertEqual(points[0], {"date": "2025-07-31", "value": 100})
        self.assertEqual(points[-1], {"date": "2026-07-31", "value": 1000})
        self.assertEqual(latest["unit"], "million USD")

    def test_tic_selects_signed_transactions_world_total_after_break(self):
        body = tic_history() + ("\nOther country\t12345\t2026-06\t999999\t888888\t55555"
                               "\nGrand Total\t99996\t2023-01\t999999\tn.a.\t55555").encode()
        latest, points = self.parse("TIC_US_EQUITY_FOREIGN_NET_PURCHASES", body)
        self.assertEqual(len(points), 13)
        self.assertEqual([p["value"] for p in points[:3]], [-2, -1, 0])
        self.assertEqual(points[-1], {"date": "2026-06-30", "value": 181426})
        self.assertEqual(latest["sign_convention"], "positive_foreign_net_purchase")
        self.assertEqual(latest["structural_break"], "2023-02")

    def test_tic_missing_older_cell_is_a_gap_not_zero(self):
        body = tic_history().replace(b"\t-1\t55555", b"\tn.a.\t55555")
        _, points = self.parse("TIC_US_EQUITY_FOREIGN_NET_PURCHASES", body)
        self.assertIsNone(points[1]["value"])
        self.assertEqual(points[2]["value"], 0)

    def test_older_duplicate_tic_month_cannot_hide_behind_valid_latest(self):
        body = tic_history() + b"\nGrand Total\t99996\t2025-06\t999999\t4\t55555"
        with self.assertRaisesRegex(h.HistoryError, "duplicate_reference_month"):
            self.parse("TIC_US_EQUITY_FOREIGN_NET_PURCHASES", body)

    def test_too_short_history_is_rejected_without_padding(self):
        for sid, factory in [("NYFED_ACM_TP10_MONTHLY", acm_history), ("FINRA_MARGIN_DEBT", finra_history),
                             ("TIC_US_EQUITY_FOREIGN_NET_PURCHASES", tic_history)]:
            with self.subTest(sid=sid), self.assertRaisesRegex(h.HistoryError, "fewer_than_12"):
                self.parse(sid, factory(11))

    def test_original_table_definition_and_future_dates_remain_enforced(self):
        cases = [("NYFED_ACM_TP10_MONTHLY", acm_history().replace(b"TERMYld", b"GSWTERM")),
                 ("NYFED_ACM_TP10_MONTHLY", acm_history().replace(b"31-Aug-2026", b"30-Sep-2026")),
                 ("FINRA_MARGIN_DEBT", finra_history().replace(b"$ millions", b"$ billions")),
                 ("TIC_US_EQUITY_FOREIGN_NET_PURCHASES", tic_history().replace(b"Net U.S. Sales", b"Holdings"))]
        for sid, body in cases:
            with self.subTest(sid=sid), self.assertRaises((h.funding_structure.FundingError,
                                                        h.intermediary.IntermediaryError,
                                                        h.terminal_flows.TerminalError)):
                self.parse(sid, body)

    def test_entire_history_unchanged_then_failure_preserves_first_receipt(self):
        sid = "NYFED_ACM_TP10_MONTHLY"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = save_monthly_source(root, sid, acm_history())
            client = Client()
            first = h.collect(root/"run1", root/"live", root/"state", client=client, clock=lambda: NOW)
            # The raw page can change in another column; the selected history
            # remains identical and must retain its original full evidence.
            save_monthly_source(root, sid, acm_history().replace(b",4,4", b",5,4"),
                                "2026-09-09T01:00:00Z", state["observations"][0])
            second = h.collect(root/"run2", root/"live", root/"state", client=Client(), clock=lambda: NOW+timedelta(hours=1))
            (root/"live/funding_structure/latest.json").write_text(json.dumps({"observations": state["observations"], "sources": []}))
            third = h.collect(root/"run3", root/"live", root/"state", client=Client(), clock=lambda: NOW+timedelta(hours=2))
            a, b, c = [next(s for s in r["series"] if s["series_id"] == sid) for r in (first, second, third)]
            for field in ("points", "known_by", "source_retrieved_at", "raw_path", "raw_sha256"):
                self.assertEqual(a[field], b[field])
                self.assertEqual(a[field], c[field])
            self.assertEqual(c["status"], "retained")
            self.assertEqual(c["known_by"], "2026-09-08T20:00:00Z")
            self.assertFalse(c["research_eligible"])
            self.assertEqual(hashlib.sha256((root/"state"/c["raw_path"]).read_bytes()).hexdigest(), c["raw_sha256"])
            self.assertEqual({p["series_id"] for _, p in client.calls}, set(h.FRED_IDS))

    def test_older_month_revision_uses_new_raw_not_unchanged_live_cell(self):
        sid = "FINRA_MARGIN_DEBT"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = save_monthly_source(root, sid, finra_history())
            first = h.collect(root/"run1", root/"live", root/"state", client=Client(), clock=lambda: NOW)
            revised = finra_history().replace(b"<td>100</td>", b"<td>90</td>")
            save_monthly_source(root, sid, revised, "2026-09-09T01:00:00Z", state["observations"][0])
            second = h.collect(root/"run2", root/"live", root/"state", client=Client(), clock=lambda: NOW+timedelta(hours=1))
            a, b = [next(s for s in r["series"] if s["series_id"] == sid) for r in (first, second)]
            self.assertEqual(a["points"][-1], b["points"][-1])
            self.assertEqual(b["points"][0]["value"], 90)
            self.assertEqual(b["known_by"], "2026-09-09T01:00:00Z")
            self.assertNotEqual(a["raw_sha256"], b["raw_sha256"])

    def test_cache_hash_path_timestamp_and_latest_identity_guards(self):
        sid = "TIC_US_EQUITY_FOREIGN_NET_PURCHASES"
        mutations = [lambda s: s["sources"][0].update(raw_sha256="0"*64),
                     lambda s: s["sources"][0].update(raw_path="../source.txt"),
                     lambda s: s["sources"][0].update(known_by="2026-09-10T00:00:00Z"),
                     lambda s: s["sources"][0].update(known_by="2026-09-08T00:00:00"),
                     lambda s: s["sources"][0].update(known_by="2026-05-01T00:00:00Z"),
                     lambda s: s["observations"][0].update(value=999999),
                     lambda s: s["observations"][0].update(unit="billion USD"),
                     lambda s: s["sources"].append(dict(s["sources"][0]))]
        for mutation in mutations:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                state = save_monthly_source(root, sid, tic_history())
                mutation(state)
                (root/"live/terminal_flows/latest.json").write_text(json.dumps(state))
                with self.assertRaises(h.HistoryError):
                    h.cached_monthly_history(sid, root/"live", date(2023, 9, 1), NOW)

    def test_cache_symlink_cannot_read_outside_provider_root(self):
        sid = "NYFED_ACM_TP10_MONTHLY"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = save_monthly_source(root, sid, acm_history())
            rel = state["sources"][0]["raw_path"]
            raw = root/"live/funding_structure"/rel
            outside = root/"outside.csv"
            raw.rename(outside)
            raw.symlink_to(outside)
            with self.assertRaisesRegex(h.HistoryError, "evidence_file"):
                h.cached_monthly_history(sid, root/"live", date(2023, 9, 1), NOW)

    def test_three_cached_histories_join_fixed_fred_without_mutating_live_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for sid, factory in [("NYFED_ACM_TP10_MONTHLY", acm_history), ("FINRA_MARGIN_DEBT", finra_history),
                                 ("TIC_US_EQUITY_FOREIGN_NET_PURCHASES", tic_history)]:
                save_monthly_source(root, sid, factory())
            before = {str(p): p.read_bytes() for p in (root/"live").rglob("*") if p.is_file()}
            result = h.collect(root/"run", root/"live", root/"state", client=Client(), clock=lambda: NOW)
            self.assertEqual({s["series_id"] for s in result["series"]}, set(h.FRED_IDS) | set(h.MONTHLY_SOURCES))
            after = {str(p): p.read_bytes() for p in (root/"live").rglob("*") if p.is_file()}
            self.assertEqual(before, after)
            for row in result["series"]:
                self.assertFalse(row["research_eligible"])
                self.assertEqual(hashlib.sha256((root/"state"/row["raw_path"]).read_bytes()).hexdigest(), row["raw_sha256"])
                if row["series_id"] in h.MONTHLY_SOURCES:
                    self.assertEqual(row["frequency"], "Monthly")
                    self.assertEqual(row["chart_type"], "bar" if row["series_id"].startswith("TIC_") else "line")

    def test_monthly_window_is_bounded_and_evidence_cannot_be_overwritten(self):
        _, points = self.parse("NYFED_ACM_TP10_MONTHLY", acm_history(48))
        self.assertEqual(len(points), 36)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proof = h.evidence(b"original", root, "csv")
            (root/proof["raw_path"]).write_bytes(b"corrupted")
            with self.assertRaisesRegex(h.HistoryError, "immutable_history_evidence_conflict"):
                h.evidence(b"original", root, "csv")


if __name__ == "__main__":
    unittest.main()
