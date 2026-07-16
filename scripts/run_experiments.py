#!/usr/bin/env python3
"""
Generate LibreLane configurations and optionally run experiments.

Produces results/qor_runs.csv with consistent metadata and
repository-relative paths.

Usage:
  python scripts/run_experiments.py --dry-run          # generate configs only
  python scripts/run_experiments.py --execute          # attempt LibreLane execution
  python scripts/run_experiments.py --clocks 15 25 30  # specific clock periods
"""

import json
import csv
import subprocess
import shutil
from pathlib import Path, PurePosixPath
import argparse
import sys

CLOCKS = [15, 18, 20, 22, 25, 30]

ROOT = Path(__file__).resolve().parents[1]
DESIGN_DIR = ROOT / "designs" / "pm32"
BASE_CONFIG = DESIGN_DIR / "config.json"
RESULTS_CSV = ROOT / "results" / "qor_runs.csv"
IMPORTANT_DIR = ROOT / "results" / "important_reports"

# CSV fieldnames for experiment metadata
FIELDNAMES = [
    "run_id",
    "clock_period",
    "config_generated",
    "execution_attempted",
    "return_code",
    "reports_found",
    "metrics_parsed",
    "status",
    "config_path",
    "report_paths",
    "notes",
]


def _to_relative_posix(path: Path) -> str:
    """Convert absolute path to repository-relative POSIX path."""
    try:
        rel = path.resolve().relative_to(ROOT.resolve())
        return str(PurePosixPath(rel))
    except ValueError:
        return str(PurePosixPath(path))


def create_config(clock):
    """Generate a LibreLane config JSON for a given clock period."""
    if BASE_CONFIG.exists():
        with open(BASE_CONFIG, "r", encoding="utf-8") as f:
            config = json.load(f)
    else:
        config = {
            "DESIGN_NAME": "pm32",
            "VERILOG_FILES": ["dir::pm32.v", "dir::spm.v"],
            "CLOCK_PORT": "clk",
        }

    config["CLOCK_PERIOD"] = clock

    out_config = DESIGN_DIR / f"config_clock{clock}.json"
    with open(out_config, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    return out_config


def run_librelane(config_path, dockerized=True):
    """Attempt to run LibreLane. Returns return code or None if binary missing."""
    librelane_bin = shutil.which("librelane")
    if librelane_bin:
        cmd = [librelane_bin]
        if dockerized:
            cmd += ["--dockerized"]
        cmd += [str(config_path)]
    else:
        print("  librelane not found in PATH. Skipping actual run.")
        return None

    print(f"  Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, timeout=3600)
        return result.returncode
    except subprocess.TimeoutExpired:
        print("  [ERROR] LibreLane timed out.")
        return -2
    except Exception as e:
        print(f"  [ERROR] {e}")
        return -1


def _check_reports(run_id, clock):
    """Check if report artifacts exist for a given run."""
    # Check mapping file
    mapping_file = IMPORTANT_DIR / "run_mapping.json"
    if mapping_file.exists():
        try:
            data = json.loads(mapping_file.read_text(encoding="utf-8"))
            for entry in data.get("mappings", []):
                if entry.get("run_id") == run_id:
                    paths = []
                    for key in ("metrics_json", "summary_rpt"):
                        fname = entry.get(key)
                        if fname and (IMPORTANT_DIR / fname).exists():
                            paths.append(_to_relative_posix(IMPORTANT_DIR / fname))
                    return paths
        except (json.JSONDecodeError, OSError):
            pass

    # Fallback: look for files matching the clock period
    found = []
    if IMPORTANT_DIR.exists():
        for f in IMPORTANT_DIR.iterdir():
            if f"clock{clock}" in f.name.lower() or f"clock_{clock}" in f.name.lower():
                found.append(_to_relative_posix(f))
    return found


def main():
    parser = argparse.ArgumentParser(
        description="Generate configs and optionally run LibreLane experiments"
    )
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="Only generate configs, do not execute (default)")
    parser.add_argument("--execute", action="store_true",
                        help="Attempt to execute LibreLane")
    parser.add_argument("--dockerized", action="store_true",
                        help="Pass --dockerized to librelane")
    parser.add_argument("--clocks", nargs="+", type=int, metavar="NS",
                        help="Clock periods to process (default: 15 18 20 22 25 30)")
    args = parser.parse_args()

    # --execute overrides --dry-run
    dry_run = not args.execute

    clocks = args.clocks if args.clocks else CLOCKS

    RESULTS_CSV.parent.mkdir(parents=True, exist_ok=True)

    print(f"Clock periods to process: {clocks}")
    print(f"Mode: {'dry-run (configs only)' if dry_run else 'execution'}")
    print()

    rows = []

    for clock in clocks:
        run_id = f"clock_{clock}"
        config_path = create_config(clock)
        config_rel = _to_relative_posix(config_path)

        print(f"[{run_id}] CLOCK_PERIOD = {clock} ns")
        print(f"  Config: {config_rel}")

        return_code = ""
        execution_attempted = "no"
        notes = "Configuration generated."

        if not dry_run:
            execution_attempted = "yes"
            rc = run_librelane(config_path, dockerized=args.dockerized)
            if rc is None:
                return_code = ""
                notes = "LibreLane binary not found; execution skipped."
            else:
                return_code = str(rc)
                notes = f"LibreLane executed with return code {rc}."
        else:
            print(f"  [DRY-RUN] Skipped execution")

        # Check for existing reports
        report_paths = _check_reports(run_id, clock)
        reports_found = "yes" if report_paths else "no"

        # Determine status
        if report_paths:
            status = "completed"
            if not notes.startswith("LibreLane"):
                notes = "Reports available from prior execution."
        elif execution_attempted == "yes" and return_code == "0":
            status = "execution_attempted_no_reports"
        elif execution_attempted == "yes":
            status = "execution_failed"
        else:
            status = "config_only"

        rows.append({
            "run_id": run_id,
            "clock_period": clock,
            "config_generated": "yes",
            "execution_attempted": execution_attempted,
            "return_code": return_code,
            "reports_found": reports_found,
            "metrics_parsed": "yes" if report_paths else "no",
            "status": status,
            "config_path": config_rel,
            "report_paths": ";".join(report_paths) if report_paths else "",
            "notes": notes,
        })

    print()

    # Merge with existing qor_runs.csv (update by run_id, preserve existing entries)
    existing_rows = {}
    if RESULTS_CSV.exists():
        with open(RESULTS_CSV, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for existing in reader:
                existing_rows[existing.get("run_id", "")] = existing

    # Update/add new rows
    for row in rows:
        existing_rows[row["run_id"]] = row

    # Write merged results CSV (sorted by clock_period for readability)
    merged = sorted(existing_rows.values(),
                    key=lambda r: int(r.get("clock_period", 0) or 0))

    with open(RESULTS_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(merged)

    print(f"Saved: {_to_relative_posix(RESULTS_CSV)} ({len(merged)} runs total)")


if __name__ == "__main__":
    main()
