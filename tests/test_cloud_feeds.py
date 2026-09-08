"""Independent cloud-state audit. Synthetic sources only; no HTTP or research data."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from model import cloud_feeds
from model.providers import crypto


class CloudFeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.runtime = self.base/"run"/"live"
        self.checkpoint = self.base/"state"/"live"
        self.now = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
        self.old = "2026-09-08T10:00:00Z"
        self.catalog = cloud_feeds.read_json(Path(cloud_feeds.live_api.__file__).with_name("live_catalog.json"))

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, allow_nan=False))

    def item(self, sid="SYNTHETIC", value=10., **extra):
        return {"series_id": sid, "value": value, "units": "native units", "known_by": self.old,
                "observation_date": "2026-08-31", "period_end": "2026-08-31", "layer": 1,
                "source_url": "https://source.invalid/public", "raw_sha256": "a"*64, **extra}

    def fred_payload(self):
        entries = {}
        for i, spec in enumerate(self.catalog["series"]):
            entries[spec["series_id"]] = {"status": "ok", "last_good": {
                **self.item(spec["series_id"], str(i+1)), "source_units": spec["expected_units"],
                "source_frequency": spec["expected_frequency"], "raw_path": "raw/old.json"}}
        return {"series": entries, "completed_at": "2026-09-09T11:00:00Z",
                "derived": {"SOFR_IORB": {"value": 10., "status": "ok", "units": "basis points",
                    "observation_date": "2026-09-08", "known_by": self.old}}}

    def collector(self, name, observations=None):
        if name == "fred":
            def fred(output_root):
                self.write(Path(output_root)/"latest.json", self.fred_payload())
                return {"outcome": "ok", "success": len(self.catalog["series"]), "series": {}}
            return fred
        def other(output_root):
            return {"provider": name, "status": "ok", "observations": copy.deepcopy(observations or []), "errors": []}
        return other

    def initial(self):
        return cloud_feeds.collect(self.runtime, collectors={
            "fred": self.collector("fred"),
            "pboc": self.collector("pboc", [self.item("PBOC_SYNTHETIC")]),
            "buybacks": self.collector("buybacks", [self.item("SEC_SYNTHETIC", ticker="SYN")]),
            "crypto": self.collector("crypto", [self.item("CRYPTO_SYNTHETIC", track="crypto", layer=5)]),
        }, clock=lambda: self.now)

    def test_all_sixteen_fred_series_preserve_native_units_and_research_gate(self):
        result = cloud_feeds.fred_rows(self.fred_payload(), self.catalog)
        lookup = {r["series_id"]: r for r in result}
        self.assertEqual(len(self.catalog["series"]), 16)
        self.assertEqual(len(lookup), 17)
        for i, spec in enumerate(self.catalog["series"]):
            row = lookup[spec["series_id"]]
            self.assertEqual(float(row["value"]), i+1)
            self.assertEqual(row["unit"], spec["expected_units"])
            self.assertEqual(row["frequency"], spec["expected_frequency"])
            self.assertEqual(row["known_by"], self.old)
            self.assertFalse(row["research_eligible"])
        self.assertNotEqual(lookup["RRPONTSYD"]["unit"], lookup["WALCL"]["unit"])

    def test_derived_spread_preserves_earliest_actual_known_by(self):
        rows = {r["series_id"]: r for r in cloud_feeds.fred_rows(self.fred_payload(), self.catalog)}
        self.assertEqual(rows["SOFR_IORB"]["known_by"], self.old)

    def test_public_allowlist_excludes_raw_bodies_paths_and_credentials(self):
        private = {"raw_path": "/Users/private/raw.json", "authorization": "PRIVATE_TOKEN",
                   "api_key": "PRIVATE_TOKEN", "raw_response": {"secret": "PRIVATE_TOKEN"},
                   "private_account": "PRIVATE_TOKEN", "request": {"url": "PRIVATE_TOKEN"}}
        row = cloud_feeds.normalize_observation("crypto", self.item(track="crypto", **private))
        serialized = json.dumps(row)
        self.assertNotIn("PRIVATE_TOKEN", serialized)
        self.assertNotIn("/Users/private", serialized)
        self.assertNotIn("raw_response", row)
        self.assertEqual(row["raw_sha256"], "a"*64)

    def test_pboc_source_hash_alias_is_preserved_in_public_provenance(self):
        item = self.item("PBOC_ASSETS")
        item.pop("raw_sha256")
        item["source_sha256"] = "b"*64
        row = cloud_feeds.normalize_observation("pboc", item)
        self.assertEqual(row["raw_sha256"], "b"*64)

    def test_sec_provider_error_field_is_exposed_as_a_redacted_error_code(self):
        def denied(output_root):
            return {"status": "error", "success": 0, "observations": [],
                    "errors": [{"ticker": "SYN", "error": "sec_http_403"}]}
        result = cloud_feeds.collect(self.runtime, collectors={"buybacks": denied}, clock=lambda: self.now)
        self.assertEqual(result["providers"][0]["errors"][0]["code"], "sec_http_403")
        self.assertEqual(result["providers"][0]["errors"][0]["ticker"], "SYN")

    def test_retained_rows_are_resanitized_through_public_allowlist(self):
        row = cloud_feeds.normalize_observation("crypto", self.item(track="crypto"))
        row.update(raw_path="/private/old-version.json", api_key="DO_NOT_REPUBLISH", raw_payload={"secret": 1})
        result = cloud_feeds.merge_rows("crypto", {"status": "error", "observations": []}, [row], self.now)
        self.assertNotIn("DO_NOT_REPUBLISH", json.dumps(result))
        self.assertNotIn("/private", json.dumps(result))
        self.assertNotIn("raw_payload", result[0])
        self.assertEqual(result[0]["known_by"], self.old)

    def test_invalid_new_values_cannot_overwrite_last_good(self):
        previous = [cloud_feeds.normalize_observation("crypto", self.item(track="crypto"))]
        for value in (None, float("nan"), float("inf"), "not-a-number", True):
            fresh = {"status": "ok", "observations": [self.item(value=value, known_by="2026-09-09T12:00:00Z", track="crypto")]}
            rows = cloud_feeds.merge_rows("crypto", fresh, previous, self.now)
            self.assertEqual(rows[0]["value"], 10.)
            self.assertEqual(rows[0]["known_by"], self.old)
            self.assertEqual(rows[0]["status"], "retained")
            json.dumps(rows, allow_nan=False)

    def test_invalid_provider_receipts_are_degraded_without_losing_last_good_evidence(self):
        def first_provider(output_root):
            root = Path(output_root)
            body = b'{"supply":100}'
            path = root/"raw"/"prior.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            return {"status": "ok", "observations": [self.item("CRYPTO_SUPPLY", 100., track="crypto",
                raw_path="raw/prior.json", raw_sha256=hashlib.sha256(body).hexdigest())], "errors": []}
        initial = cloud_feeds.collect(self.runtime, collectors={"crypto": first_provider}, clock=lambda: self.now)
        for value in (None, float("nan")):
            self.now += timedelta(hours=1)
            def invalid_provider(output_root):
                return {"status": "ok", "success": 1, "observations": [self.item("CRYPTO_SUPPLY", value, track="crypto")], "errors": []}
            public = cloud_feeds.collect(self.runtime, collectors={"crypto": invalid_provider}, clock=lambda: self.now)
            row = next(r for r in public["observations"] if r["series_id"] == "CRYPTO_SUPPLY")
            self.assertEqual(row["value"], 100.)
            self.assertEqual(row["known_by"], self.old)
            self.assertEqual(row["status"], "retained")
            self.assertNotEqual(public["providers"][0]["status"], "ok")
            self.assertEqual(public["providers"][0]["success"], 0)
            self.assertEqual(public["providers"][0]["last_success_at"], initial["providers"][0]["last_success_at"])
            latest = cloud_feeds.read_json(self.runtime/"crypto"/"latest.json")
            receipts = latest.get("observations", [])+latest.get("retained_observations", [])
            retained = next(r for r in receipts if r["series_id"] == "CRYPTO_SUPPLY")
            self.assertEqual(retained["value"], 100.)
            self.assertEqual(retained["raw_path"], "raw/prior.json")
            json.dumps(public, allow_nan=False)
        cloud_feeds.save_checkpoint(self.runtime, self.checkpoint)
        self.assertEqual((self.checkpoint/"crypto"/"raw"/"prior.json").read_bytes(), b'{"supply":100}')

    def test_entire_source_failure_keeps_every_previous_value_and_knowledge_time(self):
        initial = self.initial()
        self.now += timedelta(hours=1)
        def broken(output_root):
            raise RuntimeError("PRIVATE_CREDENTIAL_MUST_NOT_LEAK")
        result = cloud_feeds.collect(self.runtime, collectors={p["provider"]: broken for p in initial["providers"]}, clock=lambda: self.now)
        old = {(r["provider"], r["series_id"]): r for r in initial["observations"]}
        actual = {(r["provider"], r["series_id"]): r for r in result["observations"]}
        self.assertEqual(set(old), set(actual))
        for key, row in actual.items():
            self.assertEqual(row["value"], old[key]["value"])
            self.assertEqual(row["known_by"], old[key]["known_by"])
            self.assertEqual(row["status"], "retained", key)
        self.assertTrue(all(p["status"] == "error" for p in result["providers"]))
        self.assertNotIn("PRIVATE_CREDENTIAL", json.dumps(result))
        self.assertEqual([p["last_success_at"] for p in initial["providers"]],
                         [p["last_success_at"] for p in result["providers"]])

    def test_unavailable_fred_spread_preserves_previous_derived_observation(self):
        first = self.initial()
        old = next(r for r in first["observations"] if r["series_id"] == "SOFR_IORB")
        def partial(output_root):
            latest = self.fred_payload()
            latest["derived"]["SOFR_IORB"] = {"status": "unavailable", "reason": "input_failed"}
            latest["series"]["SOFR"]["status"] = "error"
            self.write(Path(output_root)/"latest.json", latest)
            return {"outcome": "partial", "success": 15, "series": {"SOFR": {"error": "http_503"}}}
        result = cloud_feeds.collect(self.runtime, collectors={"fred": partial}, clock=lambda: self.now)
        spread = next(r for r in result["observations"] if r["series_id"] == "SOFR_IORB")
        self.assertEqual(spread["value"], old["value"])
        self.assertEqual(spread["known_by"], old["known_by"])
        self.assertEqual(spread["status"], "retained")

    def test_partial_crypto_failure_keeps_old_funding_but_accepts_new_supply(self):
        old = [cloud_feeds.normalize_observation("crypto", self.item(sid, value, track="crypto"))
               for sid, value in [("SUPPLY", 100.), ("FUNDING", .001)]]
        fresh = {"status": "partial", "observations": [self.item("SUPPLY", 110., track="crypto", known_by="2026-09-09T12:00:00Z")]}
        rows = {r["series_id"]: r for r in cloud_feeds.merge_rows("crypto", fresh, old, self.now)}
        self.assertEqual(rows["SUPPLY"]["value"], 110.)
        self.assertEqual(rows["SUPPLY"]["status"], "ok")
        self.assertEqual(rows["FUNDING"]["known_by"], self.old)
        self.assertEqual(rows["FUNDING"]["status"], "retained")

    def test_unchanged_macro_receipt_preserves_knowledge_but_crypto_capture_time_advances(self):
        for provider in ("pboc", "crypto"):
            runtime = self.base/provider/"live"
            def current(output_root):
                return {"status": "ok", "observations": [self.item("SAME", 100., unit="native units",
                    known_by=self.now.isoformat().replace("+00:00", "Z"),
                    track="crypto" if provider == "crypto" else "equity")], "errors": []}
            first = cloud_feeds.collect(runtime, collectors={provider: current}, clock=lambda: self.now)
            original = first["observations"][0]["known_by"]
            self.now += timedelta(hours=1)
            second = cloud_feeds.collect(runtime, collectors={provider: current}, clock=lambda: self.now)
            if provider == "pboc":
                self.assertEqual(second["observations"][0]["known_by"], original)
            else:
                self.assertGreater(second["observations"][0]["known_by"], original)

    def test_pboc_value_revision_is_a_new_receipt(self):
        value = [100.]
        def current(output_root):
            return {"status": "ok", "observations": [self.item("PBOC_REVISION", value[0], unit="100 million CNY",
                known_by=self.now.isoformat().replace("+00:00", "Z"))], "errors": []}
        first = cloud_feeds.collect(self.runtime, collectors={"pboc": current}, clock=lambda: self.now)
        self.now += timedelta(hours=1)
        value[0] = 101.
        second = cloud_feeds.collect(self.runtime, collectors={"pboc": current}, clock=lambda: self.now)
        self.assertEqual(second["observations"][0]["value"], 101.)
        self.assertGreater(second["observations"][0]["known_by"], first["observations"][0]["known_by"])

    def test_pboc_staleness_and_crypto_interval_limitations_survive_public_normalization(self):
        old = self.item("PBOC_OLD", period_end="2025-12-31")
        rows = cloud_feeds.merge_rows("pboc", {"observations": [old]}, [], self.now)
        self.assertEqual(rows[0]["status"], "stale")
        flow = cloud_feeds.normalize_observation("crypto", self.item("SUPPLY_CHANGE", 3., track="crypto",
            period_start=self.old, period_end="2026-09-09T12:00:00Z", elapsed_capture_seconds=93600,
            observed_at=None, upstream_observation_time_unknown=True, source_reported_at="2026-09-09T12:00:00.1Z"))
        self.assertEqual(flow["elapsed_capture_seconds"], 93600)
        self.assertIsNone(flow["observed_at"])
        self.assertTrue(any("일간" in n for n in flow["notes"]))
        self.assertTrue(any("시계" in n for n in flow["notes"]))

    def checkpoint_fixture(self):
        self.write(self.runtime/"public.json", {"observations": []})
        self.write(self.runtime/"crypto"/"raw"/"a.json", {"circulating": 100})
        self.write(self.runtime/"crypto"/"raw"/"b.json", {"circulating": 105})
        for name, raw in [("old", "a"), ("new", "b")]:
            self.write(self.runtime/"crypto"/"stablecoin_snapshots"/(name+".json"), {"raw_path": "raw/"+raw+".json"})
        self.write(self.runtime/"crypto"/"stablecoin_previous.json", {"path": "stablecoin_snapshots/new.json"})
        capture = {"prior_snapshot_path": "stablecoin_snapshots/old.json", "current_snapshot_path": "stablecoin_snapshots/new.json"}
        self.write(self.runtime/"crypto"/"captures"/"latest.json", capture)
        self.write(self.runtime/"crypto"/"latest.json", {"capture_path": "captures/latest.json", "observations": [capture]})
        self.write(self.runtime/"crypto"/"raw"/"unreferenced.json", {"do_not_copy": True})
        self.write(self.runtime/"credentials.json", {"secret": "PRIVATE"})
        self.write(self.runtime/"research"/"outcome.json", {"sealed": "DO_NOT_COPY"})
        self.write(self.runtime/"fred"/"raw"/"old.json", {"value": 1})
        self.write(self.runtime/"fred"/"raw"/"new.json", {"value": 1})
        self.write(self.runtime/"fred"/"latest.json", {"last_good": {"raw_path": "raw/old.json"}, "last_capture": {"raw_path": "raw/new.json"}})

    def test_checkpoint_is_bounded_to_receipts_and_transitive_supply_evidence(self):
        self.checkpoint_fixture()
        cloud_feeds.save_checkpoint(self.runtime, self.checkpoint)
        copied = {p.relative_to(self.checkpoint).as_posix() for p in self.checkpoint.rglob("*") if p.is_file()}
        expected = {"public.json", "crypto/latest.json", "crypto/stablecoin_previous.json",
                    "crypto/captures/latest.json", "crypto/stablecoin_snapshots/old.json", "crypto/stablecoin_snapshots/new.json",
                    "crypto/raw/a.json", "crypto/raw/b.json", "fred/latest.json", "fred/raw/old.json", "fred/raw/new.json"}
        self.assertEqual(copied, expected)
        for path in copied:
            self.assertEqual((self.runtime/path).read_bytes(), (self.checkpoint/path).read_bytes())

    def test_checkpoint_rejects_symlink_evidence_and_keeps_previous_state(self):
        self.checkpoint_fixture()
        self.write(self.checkpoint/"marker.json", {"version": "last_good"})
        (self.runtime/"crypto"/"raw"/"a.json").unlink()
        (self.runtime/"crypto"/"raw"/"a.json").symlink_to(self.runtime/"credentials.json")
        with self.assertRaises(ValueError):
            cloud_feeds.save_checkpoint(self.runtime, self.checkpoint)
        self.assertTrue((self.checkpoint/"marker.json").exists())
        self.assertFalse(self.checkpoint.with_name("live.candidate").exists())

    def test_public_checkpoint_cannot_follow_a_symlink_into_private_data(self):
        self.checkpoint_fixture()
        (self.runtime/"public.json").unlink()
        (self.runtime/"public.json").symlink_to(self.runtime/"credentials.json")
        with self.assertRaises(ValueError):
            cloud_feeds.save_checkpoint(self.runtime, self.checkpoint)
        self.assertFalse((self.checkpoint/"public.json").exists())

    def test_checkpoint_publish_failure_does_not_destroy_last_committed_state(self):
        self.checkpoint_fixture()
        self.write(self.checkpoint/"marker.json", {"version": "last_good"})
        original_rename = Path.rename
        def fail_candidate(path, target):
            if path.name.endswith(".candidate"):
                raise OSError("simulated_publish_failure")
            return original_rename(path, target)
        with patch.object(Path, "rename", fail_candidate):
            with self.assertRaises(OSError):
                cloud_feeds.save_checkpoint(self.runtime, self.checkpoint)
        self.assertEqual(json.loads((self.checkpoint/"marker.json").read_text()), {"version": "last_good"})

    def test_runtime_and_checkpoint_must_be_separate(self):
        self.runtime.mkdir(parents=True)
        with self.assertRaises(ValueError):
            cloud_feeds.save_checkpoint(self.runtime, self.runtime)
        with self.assertRaises(ValueError):
            cloud_feeds.save_checkpoint(self.runtime, self.runtime/"state")

    def test_restored_cloud_checkpoint_produces_change_from_actual_prior_capture(self):
        supply = {"peggedAssets": [{"id": "1", "symbol": "USDT", "pegType": "peggedUSD", "circulating": {"peggedUSD": 100.}},
                                    {"id": "2", "symbol": "USDC", "pegType": "peggedUSD", "circulating": {"peggedUSD": 50.}}]}
        def transport(url):
            if url == crypto.SUPPLY_URL:
                return copy.deepcopy(supply)
            if url == crypto.FUNDING_URL:
                return {"code": "0", "data": [{"instId": crypto.CONTRACT, "realizedRate": ".0001",
                    "fundingTime": str(int((self.now-timedelta(hours=1)).timestamp()*1000))}]}
            return {"code": "0", "data": [{"instId": crypto.CONTRACT, "oi": "200", "oiCcy": "2", "oiUsd": "100000",
                                              "ts": str(int(self.now.timestamp()*1000))}]}
        def provider(output_root):
            return crypto.collect(output_root, transport=transport, clock=lambda: self.now)
        first = cloud_feeds.collect(self.runtime, collectors={"crypto": provider}, clock=lambda: self.now)
        self.assertFalse(any("NET_ISSUANCE" in r["series_id"] for r in first["observations"]))
        cloud_feeds.save_checkpoint(self.runtime, self.checkpoint)
        restored = self.base/"next-run"/"live"
        shutil.copytree(self.checkpoint, restored)
        self.now += timedelta(hours=1)
        supply["peggedAssets"][0]["circulating"]["peggedUSD"] += 7.
        second = cloud_feeds.collect(restored, collectors={"crypto": provider}, clock=lambda: self.now)
        flow = next(r for r in second["observations"] if r["series_id"] == "CRYPTO_USDT_USDC_NET_ISSUANCE_PROXY")
        self.assertEqual(flow["value"], 7.)
        self.assertEqual(flow["elapsed_capture_seconds"], 3600.)
        self.assertEqual(flow["period_start"], "2026-09-09T12:00:00Z")
        self.assertEqual(flow["period_end"], "2026-09-09T13:00:00Z")
        self.assertEqual(flow["track"], "crypto")
        self.assertFalse(flow["research_eligible"])
        self.assertNotIn("raw_path", flow)


if __name__ == "__main__":
    unittest.main()
