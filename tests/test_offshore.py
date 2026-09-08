from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
import xml.etree.ElementTree as ET

from model.providers import offshore as o


TODAY = date(2026, 9, 8)
CLOCK = lambda: datetime(2026, 9, 8, 20, 0, tzinfo=timezone.utc)


def bis_xml():
    ns = "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"
    root = ET.Element("{" + ns + "}StructureSpecificData")
    ET.SubElement(root, "Ref", {"agencyID": "BIS", "id": "WS_GLI"})
    data = ET.SubElement(root, "DataSet")
    values = ["14742816.992", "6683005.559", "8059811.433"]
    for (key, spec), value in zip(o.BIS_SERIES.items(), values):
        attributes = dict(zip(o.BIS_DIMS, key.split(".")))
        attributes.update({"TITLE": spec[2], "UNIT_MULT": "6", "AVAILABILITY": "A"})
        series = ET.SubElement(data, "Series", attributes)
        ET.SubElement(series, "Obs", {"TIME_PERIOD": "2026-Q1", "OBS_VALUE": value, "OBS_STATUS": "A", "OBS_CONF": "F"})
    return ET.tostring(root, encoding="unicode")


def ofr_object():
    result = {}
    for i, (key, spec) in enumerate(o.OFR_SERIES.items()):
        result[key] = {"timeseries": {"aggregation": [["2026-09-02", 3300000000000 - i * 1e12],
            ["2026-09-03", 3400000000000 - i * 1e12]], "disclosure_edits": []},
            "metadata": {"mnemonic": key,
              "description": {**{field: spec[field] for field in ("description", "name", "subtype")}, "vintage": "Preliminary", "subsetting": "Total"},
              "schedule": {"observation_period": "Single Day", "observation_frequency": "Daily", "seasonal_adjustment": "None", "last_update": "2026-09-08 15:20:27"},
              "unit": {"name": "USD", "magnitude": 0, "type": "Volume"}}}
    return result


class OffshoreTests(unittest.TestCase):
    def test_bis_actual_native_definition_and_no_duplicate_total(self):
        rows = o.parse_bis(bis_xml(), TODAY)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["value"], 14742816.992)
        self.assertEqual(rows[0]["units"], "million USD")
        self.assertEqual(rows[0]["period_end"], "2026-03-31")
        self.assertEqual(rows[0]["period_start"], "2026-01-01")
        self.assertFalse(rows[0]["aggregation_allowed"])
        self.assertEqual(len(rows[0]["not_additive_with"]), 2)

    def test_bis_scope_substitution_fails(self):
        for original, substitute in (("BORROWERS_CTY=\"3P\"", "BORROWERS_CTY=\"US\""),
                                     ("BORROWERS_SECTOR=\"N\"", "BORROWERS_SECTOR=\"P\""),
                                     ("UNIT_MULT=\"6\"", "UNIT_MULT=\"9\""),
                                     ("WS_GLI", "WS_LBS")):
            with self.subTest(substitute=substitute), self.assertRaises(o.OffshoreError):
                o.parse_bis(bis_xml().replace(original, substitute), TODAY)

    def test_bis_missing_confidential_zero_not_substituted(self):
        for original, substitute in (("OBS_STATUS=\"A\"", "OBS_STATUS=\"Q\""),
                                     ("OBS_CONF=\"F\"", "OBS_CONF=\"C\""),
                                     ("14742816.992", "")):
            with self.subTest(substitute=substitute), self.assertRaises(Exception):
                o.parse_bis(bis_xml().replace(original, substitute), TODAY)

    def test_bis_components_must_reconcile(self):
        with self.assertRaisesRegex(o.OffshoreError, "reconcile"):
            o.parse_bis(bis_xml().replace("14742816.992", "14742800"), TODAY)

    def test_bis_mixed_period_or_future_fails(self):
        with self.assertRaisesRegex(o.OffshoreError, "period_mismatch"):
            o.parse_bis(bis_xml().replace("2026-Q1", "2025-Q4", 1), TODAY)
        with self.assertRaises(Exception):
            o.parse_bis(bis_xml().replace("2026-Q1", "2026-Q3"), TODAY)

    def test_bis_duplicate_missing_or_unexpected_series_fails(self):
        for action in ("duplicate", "missing", "unexpected"):
            root = ET.fromstring(bis_xml())
            dataset = root.find("DataSet")
            if action == "duplicate":
                dataset.append(deepcopy(dataset[0]))
            elif action == "missing":
                dataset.remove(dataset[0])
            else:
                dataset[0].set("L_INSTR", "INVALID")
            with self.subTest(action=action), self.assertRaises(o.OffshoreError):
                o.parse_bis(ET.tostring(root, encoding="unicode"), TODAY)

    def test_bis_no_xml_entity_expansion(self):
        with self.assertRaisesRegex(o.OffshoreError, "entities_forbidden"):
            o.parse_bis('<!DOCTYPE x [<!ENTITY abc "0">]>' + bis_xml(), TODAY)

    def test_ofr_distinguishes_transaction_volume_from_stock(self):
        rows, errors = o.parse_ofr(json.dumps(ofr_object()), TODAY)
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]["role"], "stock")
        self.assertEqual(rows[1]["role"], "funding_activity")
        self.assertIn("excluding Federal Reserve", rows[1]["source_universe"])
        for row in rows:
            self.assertEqual(row["units"], "USD")
            self.assertFalse(row["collateral_reuse_measured"])
            self.assertFalse(row["research_eligible"])

    def test_ofr_suppressed_last_observation_is_not_zero_or_current(self):
        payload = ofr_object()
        payload["REPO-DVP_OV_TOT-P"]["timeseries"]["aggregation"].append(["2026-09-04", None])
        rows, errors = o.parse_ofr(json.dumps(payload), TODAY)
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]["period_end"], "2026-09-03")
        self.assertEqual(rows[0]["latest_source_reference_date"], "2026-09-04")
        self.assertEqual(rows[0]["status"], "partial")
        self.assertGreater(rows[0]["value"], 0)

    def test_ofr_disclosure_edits_take_precedence(self):
        payload = ofr_object()
        payload["REPO-DVP_OV_TOT-P"]["timeseries"]["disclosure_edits"] = [["2026-09-03", None]]
        rows, _ = o.parse_ofr(json.dumps(payload), TODAY)
        self.assertEqual(rows[0]["period_end"], "2026-09-02")
        self.assertEqual(rows[0]["status"], "partial")

    def test_ofr_scope_vintage_frequency_units_must_match(self):
        for area, field, value in (("description", "description", "Different definition"),
                                   ("description", "vintage", "Final"), ("description", "subtype", "Transaction Volume"),
                                   ("schedule", "observation_frequency", "Monthly"), ("unit", "magnitude", 6),
                                   ("unit", "name", "EUR")):
            payload = ofr_object()
            payload["REPO-DVP_OV_TOT-P"]["metadata"][area][field] = value
            rows, errors = o.parse_ofr(json.dumps(payload), TODAY)
            self.assertEqual(len(rows), 1)
            self.assertEqual(len(errors), 1)

    def test_ofr_future_dates_rejected_even_when_suppressed(self):
        payload = ofr_object()
        payload["REPO-DVP_OV_TOT-P"]["timeseries"]["disclosure_edits"] = [["2026-09-09", None]]
        rows, errors = o.parse_ofr(json.dumps(payload), TODAY)
        self.assertEqual(len(rows), 1)
        self.assertEqual(errors[0]["code"], "ofr_future_reference_date")

    def test_ofr_duplicate_dates_or_keys_rejected(self):
        payload = ofr_object()
        values = payload["REPO-DVP_OV_TOT-P"]["timeseries"]["aggregation"]
        values.append(values[-1])
        rows, errors = o.parse_ofr(json.dumps(payload), TODAY)
        self.assertEqual(len(rows), 1)
        self.assertEqual(errors[0]["code"], "ofr_duplicate_reference_date")
        with self.assertRaisesRegex(o.OffshoreError, "duplicate_json_key"):
            o.parse_ofr('{"x":1,"x":2}', TODAY)

    def test_ofr_invalid_number_never_cast_to_volume(self):
        for value in (True, "12", float("nan"), -1, 1e18):
            payload = ofr_object()
            payload["REPO-DVP_OV_TOT-P"]["timeseries"]["aggregation"][-1][1] = value
            rows, errors = o.parse_ofr(json.dumps(payload), TODAY)
            self.assertEqual(len(rows), 1)
            self.assertEqual(errors[0]["code"], "ofr_invalid_volume")

    def test_ofr_missing_series_universe_fails(self):
        payload = ofr_object()
        del payload["REPO-DVP_OV_TOT-P"]
        with self.assertRaisesRegex(o.OffshoreError, "universe"):
            o.parse_ofr(json.dumps(payload), TODAY)

    def test_staleness_native_frequency(self):
        rows = o.parse_bis(bis_xml(), date(2027, 1, 1))
        self.assertTrue(all(r["status"] == "stale" for r in rows))
        rows, _ = o.parse_ofr(json.dumps(ofr_object()), date(2026, 9, 30))
        self.assertTrue(all(r["status"] == "stale" for r in rows))

    def test_collection_first_known_and_source_hashes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "live"
            r = o.collect(root, lambda url: (bis_xml() if url == o.BIS_URL else json.dumps(ofr_object())).encode(), CLOCK)
            self.assertEqual((r["success"], r["total"], r["status"]), (5, 5, "ok"))
            self.assertEqual(len(r["sources"]), 2)
            for row in r["observations"]:
                self.assertEqual(row["known_by"], "2026-09-08T20:00:00Z")
                self.assertFalse(row["release_timestamp_verified"])
                self.assertIsNone(row["original_release_at"])
                self.assertEqual(hashlib.sha256((root / row["raw_path"]).read_bytes()).hexdigest(), row["raw_sha256"])

    def test_source_failure_keeps_independent_other_source(self):
        def transport(url):
            if url == o.BIS_URL:
                raise urllib.error.HTTPError(url, 500, "Unavailable", {}, None)
            return json.dumps(ofr_object()).encode()
        with tempfile.TemporaryDirectory() as temp:
            r = o.collect(Path(temp) / "live", transport, CLOCK)
            self.assertEqual((r["success"], r["status"]), (2, "partial"))
            self.assertEqual(len(r["errors"]), 3)
            self.assertTrue(all(e["code"] == "http_500" for e in r["errors"]))

    def test_next_day_sliding_request_retains_first_known_in_cloud(self):
        from model import cloud_feeds
        transport = lambda url: (bis_xml() if url == o.BIS_URL else json.dumps(ofr_object())).encode()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "live"
            day1 = CLOCK()
            day2 = datetime(2026, 9, 9, 20, 0, tzinfo=timezone.utc)
            first = cloud_feeds.collect(root, collectors={"offshore": lambda output_root: o.collect(output_root, transport, lambda: day1)}, clock=lambda: day1)
            receipt1 = json.loads((root / "offshore/latest.json").read_text())
            second = cloud_feeds.collect(root, collectors={"offshore": lambda output_root: o.collect(output_root, transport, lambda: day2)}, clock=lambda: day2)
            receipt2 = json.loads((root / "offshore/latest.json").read_text())
            old = {r["series_id"]: r for r in receipt1["observations"]}
            for row in receipt2["observations"]:
                prior = old[row["series_id"]]
                self.assertEqual(row["known_by"], prior["known_by"])
                self.assertEqual(row["raw_sha256"], prior["raw_sha256"])
                self.assertEqual(row["source_url"], prior["source_url"])
                if "requested_url" in row:
                    self.assertEqual(row["requested_url"], prior["requested_url"])
                    latest_requests = [e["source_url"] for e in receipt2["sources"] if "multifull" in e["source_url"]]
                    self.assertEqual(latest_requests, [o.ofr_url(day2.date())])
                    self.assertNotEqual(latest_requests[0], row["requested_url"])
                    self.assertNotIn("start_date", row["source_url"])

    def test_requests_are_bounded_and_allowlisted(self):
        self.assertEqual(o.allowed_url(o.ofr_url(TODAY)), o.ofr_url(TODAY))
        for bad in (o.ofr_url(TODAY) + "&remove_nulls=true", o.ofr_url(TODAY).replace("2026-07-25", "1990-01-01"),
                    o.ofr_url(TODAY).replace("https:", "http:"), o.BIS_URL.replace("stats.bis.org", "example.org")):
            with self.subTest(url=bad), self.assertRaises(o.OffshoreError):
                o.allowed_url(bad)


if __name__ == "__main__":
    unittest.main()
