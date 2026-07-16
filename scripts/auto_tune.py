#!/usr/bin/env python3
"""
Iterative clock period auto-tuning for LibreLane/OpenROAD flows.

This script implements a tuning loop:
  1. Generate a config with a clock period.
  2. Optionally run LibreLane.
  3. Parse the resulting reports.
  4. Read the actual setup slack.
  5. Decide the next clock period.
  6. Record the iteration.

Supports dry-run mode (config generation only, no execution).
Never uses fake sentinel values (999, etc.) for unavailable slack.
Reads from results/qor_metrics.csv (the correct normalized output).

Usage:
  python scripts/auto_tune.py                    # dry-run by default
  python scripts/auto_tune.py --execute          # actually run LibreLane
  python scripts/auto_tune.py --start-clock 20   # start from 20 ns
  python scripts/auto_tune.py --max-iters 5      # up to 5 iterations
"""

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from tune_rules import (
    choose_next_clock_period,
    MIN_CLOCK_PERIOD,
    MAX_CLOCK_PERIOD,
    TARGET_MARGIN_LOW,
    TARGET_MARGIN_HIGH,
)

DESIGN_DIR = ROOT / "designs" / "pm32"
BASE_CONFIG = DESIGN_DIR / "config.json"
RESULTS_CSV = ROOT / "results" / "qor_metrics.csv"
QOR_RUNS_CSV = ROOT / "results" / "qor_runs.csv"
HISTORY_CSV = ROOT / "results" / "auto_tune_history.csv"

HISTORY_FIELDS = [
    "iteration",
    "run_id",
    "clock_period",
    "execution_status",
    "setup_wns",
    "setup_ws",
    "setup_slack",
    "decision",
    "next_clock_period",
    "report_path",
]


def write_config(clock_period: float, output_path: Path) -> None:
    """Generate a LibreLane config JSON for a given clock period."""
    if BASE_CONFIG.exists():
        config = json.loads(BASE_CONFIG.read_text(encoding="utf-8"))
    else:
        config = {
            "DESIGN_NAME": "pm32",
            "VERILOG_FILES": ["dir::pm32.v", "dir::spm.v"],
            "CLOCK_PORT": "clk",
        }

    config["CLOCK_PERIOD"] = clock_period
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(config, indent=2), encoding="utf-8")


def run_librelane(config_path: Path, run_tag: str) -> int:
    """
    Execute LibreLane. Uses LIBRELANE_CMD from environment.

    Returns: return code (0 = success), or None if binary not found.
    """
    cmd = os.environ.get("LIBRELANE_CMD", "")
    if not cmd:
        librelane_bin = shutil.which("librelane")
        if not librelane_bin:
            return None
        cmd = librelane_bin

    cmd_parts = [cmd, str(config_path), "--run-tag", run_tag]
    print(f"  Running: {' '.join(cmd_parts)}")

    try:
        result = subprocess.run(cmd_parts, timeout=3600)
        return result.returncode
    except subprocess.TimeoutExpired:
        print("  [ERROR] LibreLane timed out after 3600 seconds.")
        return -1
    except FileNotFoundError:
        print(f"  [ERROR] Command not found: {cmd}")
        return None
    except Exception as e:
        print(f"  [ERROR] {e}")
        return -1


def read_setup_slack_for_run(run_id: str) -> dict:
    """
    Read setup_wns, setup_ws, and setup_slack for a specific run from qor_metrics.csv.

    Returns dict with keys: setup_wns, setup_ws, setup_slack (all float or None).
    """
    result = {"setup_wns": None, "setup_ws": None, "setup_slack": None}

    if not RESULTS_CSV.exists():
        return result

    with RESULTS_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("run_id") == run_id:
                for key in result:
                    val = row.get(key, "").strip()
                    if val:
                        try:
                            result[key] = float(val)
                        except ValueError:
                            pass
                break

    return result


def _to_relative_posix(path: Path) -> str:
    """Convert path to repository-relative POSIX."""
    from pathlib import PurePosixPath
    try:
        rel = path.resolve().relative_to(ROOT.resolve())
        return str(PurePosixPath(rel))
    except ValueError:
        return str(PurePosixPath(path))


def append_run_to_qor_runs(run_id: str, clock_period: float, config_path: Path,
                           exec_status: str, report_paths: list = None):
    """Append or update a run entry in qor_runs.csv for dynamic run discovery."""
    from pathlib import PurePosixPath

    fieldnames = [
        "run_id", "clock_period", "config_generated", "execution_attempted",
        "return_code", "reports_found", "metrics_parsed", "status",
        "config_path", "report_paths", "notes",
    ]

    # Read existing rows
    existing_rows = []
    if QOR_RUNS_CSV.exists():
        with QOR_RUNS_CSV.open(newline="", encoding="utf-8") as f:
            existing_rows = list(csv.DictReader(f))

    # Remove existing entry for this run_id if present
    existing_rows = [r for r in existing_rows if r.get("run_id") != run_id]

    # Build new entry
    rp_str = ";".join(report_paths) if report_paths else ""
    new_row = {
        "run_id": run_id,
        "clock_period": str(int(clock_period)) if clock_period == int(clock_period) else str(clock_period),
        "config_generated": "yes",
        "execution_attempted": "yes",
        "return_code": "0" if exec_status == "success" else "",
        "reports_found": "yes" if report_paths else "no",
        "metrics_parsed": "no",
        "status": "completed" if report_paths else "execution_attempted_no_reports",
        "config_path": _to_relative_posix(config_path),
        "report_paths": rp_str,
        "notes": f"Auto-tune run ({exec_status}).",
    }
    existing_rows.append(new_row)

    # Write back
    QOR_RUNS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with QOR_RUNS_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(existing_rows)


def update_metrics_parsed(run_id: str):
    """Update metrics_parsed to 'yes' for a specific run in qor_runs.csv."""
    if not QOR_RUNS_CSV.exists():
        return

    fieldnames = [
        "run_id", "clock_period", "config_generated", "execution_attempted",
        "return_code", "reports_found", "metrics_parsed", "status",
        "config_path", "report_paths", "notes",
    ]

    rows = []
    with QOR_RUNS_CSV.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    updated = False
    for row in rows:
        if row.get("run_id") == run_id:
            row["metrics_parsed"] = "yes"
            updated = True
            break

    if updated:
        with QOR_RUNS_CSV.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(
        description="Iterative clock period auto-tuning for PM32 design"
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="Actually run LibreLane (default: dry-run, config only)"
    )
    parser.add_argument(
        "--start-clock", type=float, default=25.0,
        help="Starting clock period in ns (default: 25.0)"
    )
    parser.add_argument(
        "--max-iters", type=int, default=5,
        help="Maximum tuning iterations (default: 5)"
    )
    args = parser.parse_args()

    clock = args.start_clock
    max_iters = args.max_iters
    dry_run = not args.execute
    tried_clocks = set()

    print("=" * 60)
    print("Auto-Tuning: Iterative Clock Period Optimization")
    print("=" * 60)
    print(f"  Design: pm32")
    print(f"  Starting CLOCK_PERIOD: {clock} ns")
    print(f"  Max iterations: {max_iters}")
    print(f"  Mode: {'dry-run (config only)' if dry_run else 'execute LibreLane'}")
    print(f"  Target margin range: [{TARGET_MARGIN_LOW}, {TARGET_MARGIN_HIGH}] ns")
    print()

    history = []

    for i in range(1, max_iters + 1):
        run_id = f"auto_tune_{i}"
        config_path = DESIGN_DIR / f"config_auto_tune_{i}.json"

        print(f"--- Iteration {i}: CLOCK_PERIOD = {clock} ns ---")

        # Safeguard: no repeated clock periods
        if clock in tried_clocks:
            print(f"  [STOP] Clock period {clock} ns was already tried. Stopping.")
            history.append({
                "iteration": i, "run_id": run_id, "clock_period": clock,
                "execution_status": "skipped_duplicate", "setup_wns": "",
                "setup_ws": "", "setup_slack": "",
                "decision": "stopped_duplicate", "next_clock_period": "",
                "report_path": "",
            })
            break
        tried_clocks.add(clock)

        # Safeguard: clock bounds
        if clock < MIN_CLOCK_PERIOD or clock > MAX_CLOCK_PERIOD:
            print(f"  [STOP] Clock period {clock} ns is out of bounds "
                  f"[{MIN_CLOCK_PERIOD}, {MAX_CLOCK_PERIOD}]. Stopping.")
            history.append({
                "iteration": i, "run_id": run_id, "clock_period": clock,
                "execution_status": "skipped_bounds", "setup_wns": "",
                "setup_ws": "", "setup_slack": "",
                "decision": "stopped_bounds", "next_clock_period": "",
                "report_path": "",
            })
            break

        # Step 1: Generate config
        write_config(clock, config_path)
        print(f"  Config: {config_path.relative_to(ROOT)}")

        # Step 2: Execute (or skip in dry-run)
        exec_status = "dry_run"
        if not dry_run:
            rc = run_librelane(config_path, run_id)
            if rc is None:
                exec_status = "binary_not_found"
                print("  [WARNING] LibreLane binary not found. Cannot execute.")
            elif rc == 0:
                exec_status = "success"
            else:
                exec_status = f"failed_rc{rc}"
                print(f"  [WARNING] LibreLane returned code {rc}.")
        else:
            print("  [DRY-RUN] Skipping execution. Run with --execute to run LibreLane.")

        # Step 3: Read results (only meaningful if execution happened)
        metrics = {"setup_wns": None, "setup_ws": None, "setup_slack": None}
        report_path = ""

        if exec_status == "success":
            # Discover report files - check run-specific directory first
            report_paths_list = []
            report_dir = ROOT / "results" / "important_reports"
            run_dir = DESIGN_DIR / "runs" / run_id

            # In run-specific directory: accept any .json/.rpt (no filename filter)
            if run_dir.exists():
                for f in run_dir.rglob("*"):
                    if f.suffix.lower() in (".json", ".rpt") and f.is_file():
                        report_paths_list.append(_to_relative_posix(f))

            # In shared report directory: require run_id in filename
            if not report_paths_list and report_dir.exists():
                for f in report_dir.iterdir():
                    if f.suffix.lower() in (".json", ".rpt") and run_id in f.name.lower():
                        report_paths_list.append(_to_relative_posix(f))

            # Update qor_runs.csv with this run
            append_run_to_qor_runs(run_id, clock, config_path, exec_status, report_paths_list)

            # Re-parse reports to update qor_metrics.csv
            try:
                from parse_existing_runs import main as parse_main
                parse_main()
            except Exception as e:
                print(f"  [WARNING] Could not re-parse: {e}")

            # Verify the run appears in parsed output
            metrics = read_setup_slack_for_run(run_id)
            if metrics["setup_slack"] is not None:
                # Update metadata to reflect successful parsing
                update_metrics_parsed(run_id)
            else:
                print(f"  [ERROR] Run {run_id} not found in parsed output after execution.")
                exec_status = "parse_failed"

            if report_paths_list:
                report_path = report_paths_list[0]
        elif exec_status == "dry_run":
            # Dry-run does NOT modify qor_runs.csv (canonical experiment metadata)
            pass

        setup_slack = metrics["setup_slack"]

        # Step 4: Decide next clock
        next_clock = choose_next_clock_period(setup_slack, clock)

        # Determine decision label
        if setup_slack is None:
            decision = "no_data_available"
        elif setup_slack < 0:
            decision = "timing_failure_increase"
        elif TARGET_MARGIN_LOW <= setup_slack <= TARGET_MARGIN_HIGH:
            decision = "in_target_range_stop"
        elif setup_slack > TARGET_MARGIN_HIGH:
            decision = "margin_too_large_decrease"
        else:
            decision = "margin_small_keep"

        print(f"  Setup slack: {setup_slack if setup_slack is not None else 'unavailable'}")
        print(f"  Decision: {decision}")
        print(f"  Next CLOCK_PERIOD: {next_clock} ns")
        print()

        history.append({
            "iteration": i,
            "run_id": run_id,
            "clock_period": clock,
            "execution_status": exec_status,
            "setup_wns": metrics["setup_wns"] if metrics["setup_wns"] is not None else "",
            "setup_ws": metrics["setup_ws"] if metrics["setup_ws"] is not None else "",
            "setup_slack": setup_slack if setup_slack is not None else "",
            "decision": decision,
            "next_clock_period": next_clock if next_clock != clock else "",
            "report_path": report_path,
        })

        # Stop conditions
        if setup_slack is None and not dry_run:
            print("[STOP] No setup slack data available after execution. Stopping.")
            break

        if decision == "in_target_range_stop":
            print(f"[DONE] Setup slack {setup_slack} ns is within target range. Tuning complete.")
            break

        if next_clock == clock:
            print(f"[STOP] No clock change needed. Stopping.")
            break

        if dry_run:
            print("[DRY-RUN] In actual mode, would continue with next iteration.")
            # In dry-run, show what would happen but stop after first
            break

        clock = next_clock

    # Write history
    HISTORY_CSV.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
        writer.writeheader()
        writer.writerows(history)

    print(f"Tuning history saved to: {HISTORY_CSV.relative_to(ROOT)}")
    print(f"Total iterations: {len(history)}")


if __name__ == "__main__":
    main()
