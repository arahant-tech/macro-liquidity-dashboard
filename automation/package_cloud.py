"""Publish only page artifacts, excluding credentials, runtime and model code."""
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def package():
    snapshot = json.loads((ROOT / "live-data.json").read_text())
    if snapshot.get("research_eligible") is not False or not snapshot.get("generated_at"):
        raise ValueError("invalid_observation_snapshot")
    if {p["provider"] for p in snapshot["providers"]} != {"fred", "pboc", "buybacks", "issuer_buybacks", "crypto"}:
        raise ValueError("provider_status_incomplete")
    # Preserve already-public archive links without reading research contents.
    files = json.loads((ROOT / "automation/public-files.json").read_text())
    files += ["accounting-v8.html", "live-model.json", "update-status.json", "live-data.json"]
    files += [str(p.relative_to(ROOT)) for p in (ROOT / "docs").glob("*_SOURCE*.md")]
    files += ["docs/CLOUD_OPERATION.md"]
    destination = ROOT / "_site"
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir()
    for relative in dict.fromkeys(files):
        source = ROOT / relative
        if source.is_symlink() or not source.resolve().is_relative_to(ROOT) or not source.is_file():
            raise ValueError("unsafe_or_missing_public_artifact")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    print(f"Packaged {len(set(files))} explicitly approved public artifacts")


if __name__ == "__main__":
    package()
