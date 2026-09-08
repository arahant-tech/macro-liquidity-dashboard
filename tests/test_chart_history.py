"""Historical chart contracts: no backdating, source revision loss or resampling."""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from model import chart_history as h, live_api
from test_pboc_provider import balance

NOW = datetime(2026, 9, 9, tzinfo=timezone.utc)


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


if __name__ == "__main__":
    unittest.main()
