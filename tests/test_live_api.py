"""Synthetic transport only: publication timing, immutable capture and failure gates."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from model.live_api import FredClient, SeriesSpec, load_catalog, poll


class LiveApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "live"
        self.now = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
        self.key = "credential_should_never_be_saved1"
        self.rows = [
            {"date": "2026-09-02", "value": "125.5", "realtime_start": "2026-09-09", "realtime_end": "2026-09-09"},
            {"date": "2026-08-26", "value": "120.0", "realtime_start": "2026-09-09", "realtime_end": "2026-09-09"},
        ]
        self.metadata = {"id": "WALCL", "title": "Synthetic Fed asset metadata", "frequency": "Weekly, As of Wednesday",
                         "frequency_short": "W", "units": "Millions of U.S. Dollars", "seasonal_adjustment": "Not Seasonally Adjusted",
                         "last_updated": "2026-09-03 15:32:19-05", "observation_start": "2002-12-18", "observation_end": "2026-09-02"}
        self.calls = []
        self.spec = SeriesSpec("WALCL", 1, "fed", "weekly", 21, "Millions of U.S. Dollars")

    def transport(self, endpoint, params):
        self.calls.append((endpoint, copy.deepcopy(params)))
        self.assertNotIn("api_key", params)
        if endpoint == "series":
            return {"realtime_start": "2026-09-09", "realtime_end": "2026-09-09", "seriess": [copy.deepcopy(self.metadata)]}
        if endpoint == "series/observations":
            return {"count": len(self.rows), "offset": 0, "limit": 100000, "output_type": 1,
                    "realtime_start": "2026-09-09", "realtime_end": "2026-09-09", "observations": copy.deepcopy(self.rows)}
        raise AssertionError("Unexpected API endpoint " + endpoint)

    def client(self, transport=None):
        return FredClient(self.key, transport=transport or self.transport,
                          sleeper=lambda _: None, throttle_seconds=0, max_attempts=1)

    def run_poll(self, client=None, specs=None):
        return poll(self.output, specs=specs or [self.spec], client=client or self.client(), clock=lambda: self.now)

    def latest(self):
        return json.loads((self.output / "latest.json").read_text())["series"]["WALCL"]

    def ledger(self):
        return [json.loads(line) for line in (self.output / "observed.jsonl").read_text().splitlines() if line]

    def test_new_capture_known_only_at_actual_retrieval_and_not_research_input(self):
        result = self.run_poll()
        item = self.latest()
        good = item["last_good"]
        self.assertEqual(result["outcome"], "ok")
        self.assertEqual(good["observation_date"], "2026-09-02")
        self.assertEqual(datetime.fromisoformat(good["known_by"].replace("Z", "+00:00")), self.now)
        self.assertEqual(good["known_by"], good["retrieved_at"])
        self.assertFalse(item["research_eligible"])
        raw = Path(good["raw_path"])
        if not raw.is_absolute():
            raw = self.output / raw
        self.assertEqual(hashlib.sha256(raw.read_bytes()).hexdigest(), good["raw_sha256"])
        self.assertEqual(len(self.ledger()), 1)

    def test_unchanged_poll_is_idempotent_for_knowledge_and_ledger(self):
        self.run_poll()
        original = copy.deepcopy(self.latest()["last_good"])
        prior_files = {p: p.read_bytes() for p in (self.output / "raw").rglob("*.json")}
        self.now += timedelta(hours=1)
        self.run_poll()
        self.assertEqual(self.latest()["last_good"], original)
        self.assertEqual(len(self.ledger()), 1)
        self.assertGreater(len(list((self.output / "raw").rglob("*.json"))), len(prior_files))
        for path, data in prior_files.items():
            self.assertEqual(path.read_bytes(), data)

    def test_revision_of_old_point_is_recorded_without_overwriting_old_capture(self):
        self.run_poll()
        original = copy.deepcopy(self.latest()["last_good"])
        old_raw = {p: p.read_bytes() for p in (self.output / "raw").rglob("*.json")}
        self.rows[1]["value"] = "119.25"
        self.now += timedelta(hours=1)
        self.run_poll()
        self.assertEqual(len(self.ledger()), 2)
        self.assertNotEqual(self.latest()["last_good"]["known_by"], original["known_by"])
        for path, data in old_raw.items():
            self.assertEqual(path.read_bytes(), data)

    def test_network_failure_preserves_last_good_and_redacts_secret(self):
        self.run_poll()
        original = copy.deepcopy(self.latest()["last_good"])
        self.now += timedelta(hours=1)
        def failing(endpoint, params):
            raise RuntimeError("https://example.invalid/?api_key=" + self.key)
        result = self.run_poll(client=self.client(failing))
        self.assertEqual(result["outcome"], "error")
        self.assertEqual(self.latest()["last_good"], original)
        self.assertEqual(self.latest()["status"], "error")
        self.assertEqual(len(self.ledger()), 1)
        for path in self.output.rglob("*"):
            if path.is_file():
                self.assertNotIn(self.key, path.read_text())
        self.assertNotIn(self.key, json.dumps(result))

    def test_missing_credential_updates_error_status_and_preserves_last_good(self):
        self.run_poll()
        original = copy.deepcopy(self.latest()["last_good"])
        self.now += timedelta(hours=1)
        with patch("model.live_api.load_credential", side_effect=RuntimeError("Missing " + self.key)):
            result = poll(self.output, specs=[self.spec], clock=lambda: self.now)
        self.assertEqual(result["outcome"], "error")
        self.assertEqual(self.latest()["last_good"], original)
        self.assertEqual(self.latest()["error"], "credential_unavailable")
        self.assertNotIn(self.key, json.dumps(result))

    def test_zero_is_a_valid_observation(self):
        self.rows[0]["value"] = "0"
        result = self.run_poll()
        self.assertEqual(result["outcome"], "ok")
        self.assertEqual(float(self.latest()["last_good"]["value"]), 0.0)

    def test_initial_outage_does_not_prevent_later_successful_capture(self):
        def failing(endpoint, params):
            raise RuntimeError("Synthetic first-poll outage")
        self.assertEqual(self.run_poll(client=self.client(failing))["outcome"], "error")
        self.assertIsNone(self.latest()["last_good"])
        self.now += timedelta(hours=1)
        self.assertEqual(self.run_poll()["outcome"], "ok")
        self.assertEqual(float(self.latest()["last_good"]["value"]), 125.5)
        self.assertEqual(len(self.ledger()), 1)

    def test_old_observation_remains_available_but_is_marked_stale(self):
        self.rows[0]["date"] = "2026-06-03"
        self.rows[1]["date"] = "2026-05-27"
        result = self.run_poll()
        self.assertEqual(result["outcome"], "partial")
        self.assertEqual(self.latest()["status"], "stale")
        self.assertEqual(float(self.latest()["last_good"]["value"]), 125.5)

    def test_mmf_quarter_label_is_preserved_but_freshness_uses_verified_quarter_end(self):
        self.metadata.update({"id": "MMMFFAQ027S", "frequency": "Quarterly, End of Period"})
        self.rows[0]["date"] = "2026-01-01"
        self.rows[1]["date"] = "2025-10-01"
        spec = SeriesSpec("MMMFFAQ027S", 2, "sector_distribution", "Quarterly", 190,
                          "Millions of U.S. Dollars", reference_period_end=True)
        result = self.run_poll(specs=[spec])
        item = json.loads((self.output / "latest.json").read_text())["series"]["MMMFFAQ027S"]
        self.assertEqual(result["outcome"], "ok")
        self.assertEqual(item["last_good"]["observation_date"], "2026-01-01")
        self.assertEqual(item["freshness"]["basis_date"], "2026-03-31")
        self.assertEqual(item["freshness"]["basis"], "verified_quarter_end")
        self.assertGreater(item["freshness"]["raw_reference_age_days"], 190)
        self.assertLess(item["freshness"]["age_calendar_days"], 190)

    def test_sloos_is_not_reinterpreted_as_a_quarter_end_stock(self):
        self.metadata.update({"id": "DRTSCILM", "frequency": "Quarterly", "units": "Percent"})
        self.rows[0]["date"] = "2026-07-01"
        self.rows[1]["date"] = "2026-04-01"
        spec = SeriesSpec("DRTSCILM", 1, "bank_credit", "Quarterly", 190, "Percent")
        self.run_poll(specs=[spec])
        item = json.loads((self.output / "latest.json").read_text())["series"]["DRTSCILM"]
        self.assertEqual(item["freshness"]["basis_date"], "2026-07-01")
        self.assertEqual(item["freshness"]["basis"], "native_observation_date")
        with self.assertRaises(ValueError):
            self.run_poll(specs=[SeriesSpec("DRTSCILM", 1, "bank_credit", "Quarterly", 190,
                                           "Percent", reference_period_end=True)])

    def test_duplicate_dates_are_rejected_instead_of_arbitrarily_selected(self):
        self.rows[1]["date"] = self.rows[0]["date"]
        result = self.run_poll()
        self.assertEqual(result["outcome"], "error")
        self.assertIsNone(self.latest()["last_good"])

    def test_naive_clock_is_rejected_before_network(self):
        with self.assertRaises(ValueError):
            poll(self.output, specs=[self.spec], client=self.client(), clock=lambda: datetime(2026, 9, 9))
        self.assertEqual(self.calls, [])

    def test_sofr_iorb_derived_spread_matches_native_dates_and_units(self):
        rate_rows = {"SOFR": [{"date": "2026-09-04", "value": "4.1"}],
                     "IORB": [{"date": "2026-09-08", "value": "3.5"},
                              {"date": "2026-09-04", "value": "4.0"}]}
        def rates(endpoint, params):
            sid = params["series_id"]
            if endpoint == "series":
                return {"seriess": [{"id": sid, "frequency": "Daily", "units": "Percent"}]}
            return {"observations": copy.deepcopy(rate_rows[sid])}
        specs = [SeriesSpec(sid, 1, "fed", "Daily", 7, "Percent") for sid in ("SOFR", "IORB")]
        self.run_poll(client=self.client(rates), specs=specs)
        spread = json.loads((self.output / "latest.json").read_text())["derived"]["SOFR_IORB"]
        self.assertEqual(spread["observation_date"], "2026-09-04")
        self.assertAlmostEqual(spread["value"], 10.0)
        self.assertEqual(spread["units"], "basis points")
        self.assertFalse(spread["research_eligible"])
        rate_rows["IORB"] = [{"date": "2026-09-08", "value": "3.5"}]
        self.now += timedelta(hours=1)
        self.run_poll(client=self.client(rates), specs=specs)
        spread = json.loads((self.output / "latest.json").read_text())["derived"]["SOFR_IORB"]
        self.assertEqual(spread["status"], "unavailable")

    def test_missing_latest_does_not_replace_previous_real_value_with_zero(self):
        self.rows[0]["value"] = "."
        self.run_poll()
        self.assertEqual(self.latest()["last_good"]["observation_date"], "2026-08-26")
        self.assertEqual(float(self.latest()["last_good"]["value"]), 120.0)

    def test_future_observation_is_rejected(self):
        self.rows[0]["date"] = "2026-09-10"
        result = self.run_poll()
        self.assertEqual(result["outcome"], "error")
        self.assertIsNone(self.latest()["last_good"])

    def test_future_archive_date_is_rejected_even_for_past_observation(self):
        self.rows[0]["realtime_start"] = "2026-09-10"
        result = self.run_poll()
        self.assertEqual(result["outcome"], "error")
        self.assertIsNone(self.latest()["last_good"])

    def test_units_mismatch_is_not_silently_accepted(self):
        self.metadata["units"] = "Billions of U.S. Dollars"
        result = self.run_poll()
        self.assertEqual(result["outcome"], "error")
        self.assertIsNone(self.latest()["last_good"])

    def test_catalogue_and_allowlist_block_vix_price_and_returns(self):
        ids = {spec.series_id for spec in load_catalog()}
        self.assertTrue({"WALCL", "WRESBAL", "RRPONTSYD", "TOTBKCR", "ECBASSETSW", "JPNASSETS"} <= ids)
        self.assertFalse(ids & {"VIXCLS", "SP500", "NASDAQCOM", "BAMLH0A0HYM2", "CBBTCUSD"})
        for sid in ["VIXCLS", "SP500", "CBBTCUSD", "EQUITY_MONTHLY_LOGRETURN"]:
            with self.subTest(sid=sid):
                before = len(self.calls)
                with self.assertRaises(ValueError):
                    self.run_poll(specs=[SeriesSpec(sid, 1, "fed", "daily", 7)])
                self.assertEqual(len(self.calls), before)

    def test_research_directory_output_is_rejected_before_network(self):
        root = Path(__file__).resolve().parents[1]
        for destination in [root, root / "research" / "vix-preflight-v9_1", root / "backtest"]:
            with self.subTest(destination=destination):
                with self.assertRaises(ValueError):
                    poll(destination, specs=[self.spec], client=self.client(), clock=lambda: self.now)
        self.assertEqual(self.calls, [])

    def test_concurrent_poll_cannot_write_into_active_capture(self):
        entered = threading.Event()
        release = threading.Event()
        failures = []
        def blocked(endpoint, params):
            if not entered.is_set():
                entered.set()
                if not release.wait(10):
                    raise RuntimeError("Synthetic worker timed out")
            return self.transport(endpoint, params)
        def worker():
            try:
                self.run_poll(client=self.client(blocked))
            except BaseException as error:
                failures.append(error)
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            with self.assertRaisesRegex(RuntimeError, "poll_already_running"):
                self.run_poll()
        finally:
            release.set()
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
