"""Synthetic HTTP payloads only; no market outcomes or credentials are accessed."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error

from model.providers.crypto import CONTRACT, FUNDING_URL, OI_URL, SUPPLY_URL, collect


class CryptoProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/"crypto"
        self.now = datetime(2026, 9, 9, 12, 0, 0, tzinfo=timezone.utc)
        self.supply = {"peggedAssets": [
            {"id": "1", "symbol": "USDT", "pegType": "peggedUSD", "price": .9,
             "circulating": {"peggedUSD": 100.}, "circulatingPrevDay": {"peggedUSD": 70.},
             "authorized": 9999., "circulatingUSD": {"peggedUSD": 90.}},
            {"id": "2", "symbol": "USDC", "pegType": "peggedUSD", "price": 1.1,
             "circulating": {"peggedUSD": 50.}, "circulatingPrevDay": {"peggedUSD": 1.},
             "authorized": 9999., "circulatingUSD": {"peggedUSD": 55.}},
            {"id": "999", "symbol": "OTHER", "pegType": "peggedUSD", "circulating": {"peggedUSD": 100000.}},
        ]}
        self.calls = []

    def transport(self, url):
        self.calls.append(url)
        self.assertIn(url, (SUPPLY_URL, FUNDING_URL, OI_URL))
        ms = str(int(self.now.timestamp()*1000))
        if url == SUPPLY_URL:
            return copy.deepcopy(self.supply)
        if url == FUNDING_URL:
            return {"code": "0", "data": [{"instId": CONTRACT, "instType": "SWAP",
                "fundingTime": str(int((self.now-timedelta(hours=4)).timestamp()*1000)),
                "realizedRate": "-0.0001", "fundingRate": ".999", "method": "current_period", "formulaType": "withRate"}]}
        return {"code": "0", "data": [{"instId": CONTRACT, "instType": "SWAP", "ts": ms,
            "oi": "2000", "oiCcy": "20", "oiUsd": "2000000"}]}

    def run_collect(self, transport=None):
        return collect(self.root, transport=transport or self.transport, clock=lambda: self.now)

    def rows(self, result):
        return {r["series_id"]: r for r in result["observations"]}

    def test_first_snapshot_is_supply_only_not_invented_issuance(self):
        result = self.run_collect()
        rows = self.rows(result)
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["net_issuance_available"])
        self.assertEqual(rows["CRYPTO_USDT_CIRCULATING"]["value"], 100.)
        self.assertEqual(rows["CRYPTO_USDC_CIRCULATING"]["value"], 50.)
        self.assertEqual(rows["CRYPTO_USDT_USDC_CIRCULATING"]["value"], 150.)
        self.assertIsNone(rows["CRYPTO_USDT_CIRCULATING"]["observed_at"])
        self.assertTrue(all(r["track"] == "crypto" and r["research_eligible"] is False for r in rows.values()))
        self.assertEqual(result["missing_channels"], ["spot_etf_net_flows", "miner_selling_pressure"])
        json.dumps(result, allow_nan=False)

    def test_changes_only_use_actual_stored_compatible_snapshots(self):
        self.run_collect()
        self.now += timedelta(hours=7)
        self.supply["peggedAssets"][0]["circulating"]["peggedUSD"] = 105.
        self.supply["peggedAssets"][1]["circulating"]["peggedUSD"] = 48.
        result = self.run_collect()
        rows = self.rows(result)
        flow = rows["CRYPTO_USDT_USDC_NET_ISSUANCE_PROXY"]
        self.assertEqual(flow["value"], 3.)
        self.assertEqual(flow["components"], {"USDT": 5., "USDC": -2.})
        self.assertEqual(flow["elapsed_capture_seconds"], 7*3600)
        self.assertFalse(flow["is_daily_rate"])
        self.assertFalse(flow["is_market_value"])
        self.assertIn("period_start", flow)
        self.assertIn("period_end", flow)
        for which in ("prior", "current"):
            raw = (self.root/flow[which+"_snapshot_path"]).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), flow[which+"_snapshot_sha256"])

    def test_price_authorization_and_provider_historical_fields_never_drive_flow(self):
        self.run_collect()
        self.now += timedelta(hours=1)
        for asset in self.supply["peggedAssets"][:2]:
            asset.update(price=999., authorized=500000., circulatingUSD={"peggedUSD": 500000.},
                         circulatingPrevDay={"peggedUSD": 0.})
        flow = self.rows(self.run_collect())["CRYPTO_USDT_USDC_NET_ISSUANCE_PROXY"]
        self.assertEqual(flow["value"], 0.)
        self.assertEqual(flow["value_kind"], "derived_net_circulation_change_proxy")

    def test_oi_units_and_realized_funding_cannot_be_interchanged(self):
        rows = self.rows(self.run_collect())
        funding = rows["CRYPTO_OKX_BTC_USDT_SWAP_REALIZED_FUNDING"]
        self.assertEqual(funding["value"], -.0001)
        self.assertFalse(funding["indicative_rate_used"])
        self.assertIsNone(funding["funding_interval_hours"])
        for suffix, value, unit in [("CONTRACTS", 2000., "contracts"), ("BTC", 20., "BTC"), ("USD_NOTIONAL", 2000000., "USD notional")]:
            row = rows["CRYPTO_OKX_BTC_USDT_SWAP_OI_"+suffix]
            self.assertEqual(row["value"], value)
            self.assertEqual(row["unit"], unit)
            self.assertEqual(row["contract"], CONTRACT)
            self.assertEqual(row["is_notional"], suffix == "USD_NOTIONAL")

    def test_missing_realized_rate_is_not_replaced_with_indicative_rate(self):
        def transport(url):
            data = self.transport(url)
            if url == FUNDING_URL:
                data["data"][0].pop("realizedRate")
            return data
        result = self.run_collect(transport)
        self.assertEqual(result["status"], "partial")
        self.assertNotIn("CRYPTO_OKX_BTC_USDT_SWAP_REALIZED_FUNDING", self.rows(result))

    def test_geo_denial_does_not_switch_host_or_drop_good_supply(self):
        def restricted(url):
            if url in (FUNDING_URL, OI_URL):
                self.calls.append(url)
                raise urllib.error.HTTPError(url, 451, "location restricted", {}, None)
            return self.transport(url)
        result = self.run_collect(restricted)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["observations"]), 3)
        self.assertEqual(self.calls, [SUPPLY_URL, FUNDING_URL, OI_URL])
        self.assertTrue(all(e["code"] == "regional_or_access_denial_451" for e in result["errors"]))

    def test_invalid_stablecoin_identity_prevents_both_sum_and_state_write(self):
        self.supply["peggedAssets"][1]["symbol"] = "FAKE"
        result = self.run_collect()
        self.assertEqual(result["status"], "partial")
        self.assertFalse((self.root/"stablecoin_previous.json").exists())
        self.assertFalse(any("CIRCULATING" in r["series_id"] for r in result["observations"]))

    def test_missing_stablecoin_does_not_silently_change_total_universe(self):
        self.supply["peggedAssets"] = self.supply["peggedAssets"][:1]
        result = self.run_collect()
        self.assertFalse(any("CIRCULATING" in r["series_id"] for r in result["observations"]))

    def test_bad_oi_number_or_wrong_contract_rejects_that_source_atomically(self):
        for mutation in [{"oiCcy": "NaN"}, {"instId": "ETH-USDT-SWAP"}, {"oi": "-1"}]:
            def changed(url):
                data = self.transport(url)
                if url == OI_URL:
                    data["data"][0].update(mutation)
                return data
            result = self.run_collect(changed)
            self.assertFalse(any("_OI_" in r["series_id"] for r in result["observations"]))
            self.assertEqual(result["status"], "partial")

    def test_stale_or_future_oi_is_not_a_current_observation(self):
        for minutes in (-31, 1):
            def changed(url):
                data = self.transport(url)
                if url == OI_URL:
                    data["data"][0]["ts"] = str(int((self.now+timedelta(minutes=minutes)).timestamp()*1000))
                return data
            result = self.run_collect(changed)
            self.assertFalse(any("_OI_" in r["series_id"] for r in result["observations"]))

    def test_small_exchange_clock_skew_is_reported_without_future_observation_claim(self):
        def changed(url):
            data = self.transport(url)
            if url == OI_URL:
                data["data"][0]["ts"] = str(int(self.now.timestamp()*1000)+100)
            return data
        rows = self.rows(self.run_collect(changed))
        oi = rows["CRYPTO_OKX_BTC_USDT_SWAP_OI_BTC"]
        self.assertIsNone(oi["observed_at"])
        self.assertEqual(oi["source_clock_ahead_seconds"], .1)
        self.assertEqual(oi["timestamp_quality"], "source_clock_ahead_observation_time_unknown")
        self.assertEqual(oi["known_by"], self.now.isoformat().replace("+00:00", "Z"))

    def test_capture_hashes_match_immutable_files_and_identical_source_bytes_deduplicate(self):
        result = self.run_collect()
        for source in result["sources"]:
            original = (self.root/source["raw_path"]).read_bytes()
            self.assertEqual(hashlib.sha256(original).hexdigest(), source["raw_sha256"])
        original_files = {p: p.read_bytes() for p in (self.root/"raw").glob("*.json")}
        self.run_collect()
        self.assertEqual(original_files, {p: p.read_bytes() for p in (self.root/"raw").glob("*.json")})
        body = (self.root/result["capture_path"]).read_bytes()
        self.assertEqual(hashlib.sha256(body).hexdigest(), result["capture_sha256"])

    def test_failed_refresh_preserves_last_stablecoin_baseline_and_never_echoes_remote_exception(self):
        self.run_collect()
        original = (self.root/"stablecoin_previous.json").read_bytes()
        self.now += timedelta(hours=1)
        def fail(url):
            raise RuntimeError("SECRET SHOULD NEVER BE SAVED")
        result = self.run_collect(fail)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["observations"], [])
        self.assertEqual(original, (self.root/"stablecoin_previous.json").read_bytes())
        self.assertNotIn("SECRET", json.dumps(result))

    def test_tampered_prior_snapshot_cannot_produce_false_issuance(self):
        self.run_collect()
        ref = json.loads((self.root/"stablecoin_previous.json").read_text())
        old = self.root/ref["path"]
        old.write_text(old.read_text().replace('100.0', '999.0'))
        self.now += timedelta(hours=1)
        result = self.run_collect()
        self.assertFalse(result["net_issuance_available"])
        self.assertIn("prior_snapshot_hash_mismatch", [e["code"] for e in result["errors"]])

    def test_same_capture_timestamp_cannot_create_net_issuance(self):
        self.run_collect()
        self.supply["peggedAssets"][0]["circulating"]["peggedUSD"] = 130.
        self.assertFalse(self.run_collect()["net_issuance_available"])

    def test_backwards_clock_preserves_later_baseline(self):
        self.run_collect()
        original = (self.root/"stablecoin_previous.json").read_bytes()
        self.now -= timedelta(hours=1)
        result = self.run_collect()
        self.assertFalse(result["net_issuance_available"])
        self.assertEqual(original, (self.root/"stablecoin_previous.json").read_bytes())

    def test_injected_raw_bytes_preserve_exact_source_hash(self):
        raw = json.dumps(self.supply, separators=(",", ":")).encode()
        def source(url):
            return raw if url == SUPPLY_URL else self.transport(url)
        result = self.run_collect(source)
        evidence = next(s for s in result["sources"] if s["name"] == "stablecoin_supply")
        self.assertEqual(evidence["raw_sha256"], hashlib.sha256(raw).hexdigest())


if __name__ == "__main__":
    unittest.main()
