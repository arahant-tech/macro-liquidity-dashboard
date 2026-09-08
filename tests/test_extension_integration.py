"""Independent integration audit: availability, dependent evidence, and scope.

Uses only synthetic collector results. No HTTP, research, or sealed data.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError

from model import cloud_feeds
from model.providers import terminal_flows


class ExtensionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / "runtime"
        self.current = datetime(2026, 9, 9, 2, tzinfo=timezone.utc)

    def stamp(self):
        return self.current.isoformat().replace("+00:00", "Z")

    def collector(self, *, components=None):
        def run(output_root):
            root = Path(output_root)
            evidence = {}
            sources = []
            for key in ("raw", "metadata_raw"):
                body = json.dumps({"kind": key, "capture": self.stamp()}).encode()
                digest = hashlib.sha256(body).hexdigest()
                path = Path("raw") / (digest + ".json")
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_bytes(body)
                evidence[key + "_path"] = str(path)
                evidence[key + "_sha256"] = digest
                sources.append({"raw_path": str(path), "raw_sha256": digest, "known_by": self.stamp()})
            row = {"series_id": "SYNTHETIC_Z1", "provider": "terminal_flows", "label": "Synthetic transaction",
                   "value": 25., "unit": "million USD", "units": "million USD", "layer": 4,
                   "track": "equity", "frequency": "quarterly", "period_start": "2026-01-01",
                   "period_end": "2026-03-31", "observation_date": "2026-03-31", "known_by": self.stamp(),
                   "source_url": "https://fred.stlouisfed.org/series/SYNTHETIC", "components": components,
                   "source_universe": "same synthetic population", **evidence}
            return {"status": "ok", "success": 1, "observations": [row], "sources": sources, "errors": []}
        return run

    def test_unchanged_observation_preserves_its_metadata_evidence_at_known_by(self):
        cloud_feeds.collect(self.runtime, collectors={"terminal_flows": self.collector()}, clock=lambda: self.current)
        before = cloud_feeds.read_json(self.runtime / "terminal_flows/latest.json")["observations"][0]
        self.current += timedelta(hours=1)
        cloud_feeds.collect(self.runtime, collectors={"terminal_flows": self.collector()}, clock=lambda: self.current)
        after = cloud_feeds.read_json(self.runtime / "terminal_flows/latest.json")["observations"][0]
        self.assertEqual(after["known_by"], before["known_by"])
        self.assertEqual(after["raw_path"], before["raw_path"])
        self.assertEqual(after["metadata_raw_path"], before["metadata_raw_path"])
        self.assertEqual(after["metadata_raw_sha256"], before["metadata_raw_sha256"])
        checkpoint = self.root / "checkpoint"
        cloud_feeds.save_checkpoint(self.runtime, checkpoint)
        old_metadata = checkpoint / "terminal_flows" / before["metadata_raw_path"]
        self.assertTrue(old_metadata.exists())
        self.assertEqual(hashlib.sha256(old_metadata.read_bytes()).hexdigest(), before["metadata_raw_sha256"])

    def test_same_ratio_with_changed_underlying_components_is_new_information(self):
        first = cloud_feeds.collect(self.runtime, collectors={"terminal_flows": self.collector(components={"n": 1, "d": 4})}, clock=lambda: self.current)
        self.current += timedelta(hours=1)
        second = cloud_feeds.collect(self.runtime, collectors={"terminal_flows": self.collector(components={"n": 2, "d": 8})}, clock=lambda: self.current)
        a, b = first["observations"][0], second["observations"][0]
        self.assertEqual(a["value"], b["value"])
        self.assertGreater(b["known_by"], a["known_by"])
        self.assertNotEqual(b["raw_sha256"], a["raw_sha256"])

    def test_public_failure_identifies_affected_source_and_series(self):
        def failure(output_root):
            return {"status": "partial", "success": 0, "observations": [], "errors": [{
                "source_group": "ici_index", "series_ids": ["ICI_DOMESTIC_EQUITY_INDEX_SHARE"],
                "code": "http_403"}, {"series_id": "JPM_REPORTED_SLR", "code": "source_changed"}]}
        result = cloud_feeds.collect(self.runtime, collectors={"intermediary": failure}, clock=lambda: self.current)
        first, second = result["providers"][0]["errors"]
        self.assertEqual(first.get("source_group"), "ici_index")
        self.assertEqual(first.get("series_ids"), ["ICI_DOMESTIC_EQUITY_INDEX_SHARE"])
        self.assertEqual(second.get("series_id"), "JPM_REPORTED_SLR")

    def test_terminal_http_error_and_url_never_expose_api_key(self):
        key = "deadbeef" * 4
        def failure(url):
            raise HTTPError(url, 403, "private " + key, {}, None)
        result = terminal_flows.collect(self.runtime, transport=failure, clock=lambda: self.current, api_key=key)
        self.assertEqual(result["success"], 0)
        self.assertNotIn(key, json.dumps(result))
        self.assertNotIn("api_key", json.dumps(result))
        self.assertTrue(all(e["code"] == "http_403" for e in result["errors"]))
        self.assertFalse(any(self.runtime.rglob("*.json")))


if __name__ == "__main__":
    unittest.main()
