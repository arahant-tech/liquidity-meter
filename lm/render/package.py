"""Build the public site from an explicit allow-list.

Only named page artifacts are published: never credentials, runtime state,
receipts or code. Static assets come from ``site/``, the two public data
snapshots from the data branch checkout, and reports from the paths listed in
``public-files.json``. Nothing is swept in by directory.
"""
import argparse
import json
from pathlib import Path
import shutil

from lm import catalog

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path("lm") / "render" / "public-files.json"
PUBLIC_DATA = ("live-data.json", "chart-data.json")
VINTAGE_POLICY = "current_snapshot_not_historical_availability"


def _plan(public_dir):
    """Map each published path to the repository file it is copied from."""
    manifest = json.loads((ROOT / MANIFEST).read_text())
    plan = {}
    for relative in manifest["site"]:          # site/<x> is served as /<x>
        plan[relative] = Path("site") / relative if isinstance(relative, str) else relative
    for relative in manifest["repo"]:          # served at the same path it has in the repository
        plan[relative] = Path(relative) if isinstance(relative, str) else relative
    for path in sorted((ROOT / "docs").glob("*_SOURCE*.md")):
        plan["docs/" + path.name] = Path("docs") / path.name
    plan["docs/CLOUD_OPERATION.md"] = Path("docs") / "CLOUD_OPERATION.md"
    for name in PUBLIC_DATA:
        plan[name] = public_dir / name
    return plan


def package(public_dir=None):
    public_dir = Path(public_dir) if public_dir else ROOT / "data" / "public"
    if not public_dir.is_absolute():
        public_dir = ROOT / public_dir
    snapshot = json.loads((public_dir / "live-data.json").read_text())
    if snapshot.get("research_eligible") is not False or not snapshot.get("generated_at"):
        raise ValueError("invalid_observation_snapshot")
    if {p["provider"] for p in snapshot["providers"]} != {p["id"] for p in catalog.providers()}:
        raise ValueError("provider_status_incomplete")
    charts = json.loads((public_dir / "chart-data.json").read_text())
    if charts.get("research_eligible") is not False or charts.get("vintage_policy") != VINTAGE_POLICY:
        raise ValueError("invalid_chart_history")
    destination = ROOT / "_site"
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir()
    plan = _plan(public_dir)
    for published, source in plan.items():
        if not isinstance(published, str) or Path(published).is_absolute() or ".." in Path(published).parts:
            raise ValueError("unsafe_public_artifact_path")
        if not isinstance(source, Path) or ".." in source.parts:
            raise ValueError("unsafe_public_artifact_path")
        source = source if source.is_absolute() else ROOT / source
        if source.is_symlink() or not source.resolve().is_relative_to(ROOT.resolve()) or not source.is_file():
            raise ValueError("unsafe_or_missing_public_artifact")
        target = destination / published
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    print(f"Packaged {len(plan)} explicitly approved public artifacts")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-dir", type=Path, default=None,
                        help="Directory holding live-data.json and chart-data.json (default: data/public)")
    args = parser.parse_args(argv)
    package(args.public_dir)


if __name__ == "__main__":
    main()
