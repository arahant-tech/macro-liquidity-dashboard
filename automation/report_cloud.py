"""Summarize partial coverage after publishing failures honestly."""
import json
import os
from pathlib import Path

path = Path("live-data.json")
if not path.exists():
    raise SystemExit("No current observation snapshot; inspect collection failure.")
data = json.loads(path.read_text())
expected_run = f"https://github.com/{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/{os.environ.get('GITHUB_RUN_ID')}"
if data.get("github_run_url") != expected_run:
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as handle:
        handle.write("## Collection failed before a current snapshot was written\n\n"
                     "The repository still contains a previous run's observations. "
                     "They are not this run's successful collection. Inspect the failed step.\n")
    raise SystemExit("Current-run snapshot missing; previous data must not count as this run's success.")
lines = ["## Hourly observation collection", "", "Capture: " + data["generated_at"], "",
         "|Source|Status|Fresh observations|", "|---|---|---|"]
for source in data["providers"]:
    lines.append(f"|{source['provider']}|{source['status']}|{source['success']}/{source['total']}|")
    if source["status"] != "ok":
        print("::warning::" + source["provider"] + " source coverage: " + source["status"])
lines += ["", "Known-by is actual retrieval time. Model validity and sealed outcomes are not tested.",
          "Failed sources retain earlier values and original timestamps. See published coverage and errors."]
chart_path = Path("chart-data.json")
charts = json.loads(chart_path.read_text()) if chart_path.exists() else {}
if charts.get("github_run_url") == expected_run:
    lines += ["", "## Display-only native histories", "",
              f"Series: {len(charts['series'])}/10. Errors: {len(charts.get('errors', []))}.",
              "Current-vintage levels only; no historical availability, global score or model validity claim."]
    if charts.get("errors"):
        print("::warning::Some native chart histories are unavailable or retained.")
else:
    lines += ["", "Current-run chart history was not written; do not count old charts as this run's success."]
with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as handle:
    handle.write("\n".join(lines) + "\n")
if all(source["status"] == "error" for source in data["providers"]):
    raise SystemExit("All sources failed; status and retained observations were published.")
