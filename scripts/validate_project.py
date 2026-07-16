#!/usr/bin/env python3
"""
Professor-ready project validation script.

Runs all checks in sequence and produces a summary.
Returns non-zero exit code if any check fails.

Usage:
  python scripts/validate_project.py
"""

import csv
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
RESULTS_DIR = ROOT / "results"
TESTS_DIR = ROOT / "tests"

# Files to compile-check
COMPILE_FILES = [
    "scripts/parse_existing_runs.py",
    "scripts/rule_engine.py",
    "scripts/assistant.py",
    "scripts/llm_client.py",
    "scripts/auto_tune.py",
    "scripts/tune_rules.py",
    "scripts/run_experiments.py",
    "scripts/fmt.py",
    "evaluation/evaluate.py",
]

# Absolute path patterns to reject
ABS_PATH_PATTERNS = [
    r"C:\\Users\\",
    r"C:/Users/",
    r"/home/\w+",
    r"/Users/\w+",
]

# Generated output files to check for absolute paths
PATH_CHECK_FILES = [
    "results/qor_metrics.csv",
    "results/qor_runs.csv",
    "results/parsed_summary.json",
    "evaluation/results.txt",
]
DOC_FILES = [
    "README.md",
    "evaluation/results.txt",
]

# Patterns that should NOT appear in active docs
FORBIDDEN_DOC_PATTERNS = [
    (r"\b10/10 scenarios\b", "says 10/10 scenarios (should be 24/24)"),
    (r"\b10 scenarios\b", "says 10 scenarios (should be 24)"),
    (r"(?:setup |Setup )WNS\s*=?\s*13\.2", "labels 13.2xx as setup WNS (should be WS/margin)"),
    (r"(?:setup |Setup )WNS\s*=?\s*17\.2", "labels 17.2xx as setup WNS (should be WS/margin)"),
    (r"zero DRC proves? no congestion", "claims zero DRC proves no congestion"),
    (r"clock_25 is (?:the )?(?:globally|global) best", "claims clock_25 is globally best"),
    (r"all six.*(?:runs|experiments).*(?:complete|executed|finished)", "claims all six runs completed"),
    (r"\b81 unit", "says 81 unit tests (should be 105)"),
    (r"Parsed dataset: 6 runs", "says 6 runs (should be 10)"),
    (r"(?:contains|has) six (?:runs|configurations)", "says six runs/configurations (should be ten)"),
    (r"Complete all 6 runs", "says complete all 6 runs"),
]


class ValidationResult:
    def __init__(self):
        self.checks = []
        self.passed = 0
        self.failed = 0

    def add(self, name, passed, detail=""):
        self.checks.append((name, passed, detail))
        if passed:
            self.passed += 1
        else:
            self.failed += 1

    def print_summary(self):
        print()
        print("=" * 60)
        print("PROJECT VALIDATION SUMMARY")
        print("=" * 60)
        print()
        for name, passed, detail in self.checks:
            icon = "PASS" if passed else "FAIL"
            line = f"  [{icon}] {name}"
            if detail and not passed:
                line += f" - {detail}"
            print(line)
        print()
        total = self.passed + self.failed
        print(f"  Total: {self.passed}/{total} checks passed.")
        if self.failed == 0:
            print()
            print("  Project validation completed successfully.")
        else:
            print()
            print(f"  {self.failed} check(s) FAILED.")
        print()
        return self.failed == 0


def check_compilation(result):
    """Check that all Python files compile cleanly."""
    all_ok = True
    for filepath in COMPILE_FILES:
        full = ROOT / filepath
        if not full.exists():
            result.add(f"Compile: {filepath}", False, "file not found")
            all_ok = False
            continue
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "py_compile", str(full)],
                capture_output=True, text=True, timeout=30
            )
            if proc.returncode != 0:
                all_ok = False
        except Exception as e:
            all_ok = False

    result.add("Python compilation (all scripts)", all_ok)


def check_unit_tests(result):
    """Run unit tests and verify they pass."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            capture_output=True, text=True, timeout=120,
            cwd=str(ROOT)
        )
        # Count tests from output
        combined = proc.stderr + proc.stdout
        match = re.search(r"Ran (\d+) test", combined)
        count = match.group(1) if match else "?"

        # Strict: require returncode == 0
        ok = proc.returncode == 0
        result.add(f"Unit tests: {count} tests", ok,
                   "" if ok else "returncode != 0")
    except Exception as e:
        result.add("Unit tests", False, str(e))


def check_parser(result):
    """Run the parser and verify output."""
    try:
        proc = subprocess.run(
            [sys.executable, "scripts/parse_existing_runs.py"],
            capture_output=True, text=True, timeout=30,
            cwd=str(ROOT)
        )
        ok = proc.returncode == 0
        csv_ok = (RESULTS_DIR / "qor_metrics.csv").exists()
        json_ok = (RESULTS_DIR / "parsed_summary.json").exists()
        result.add("Parser integration", ok and csv_ok and json_ok,
                   "" if ok else proc.stderr[:100])
    except Exception as e:
        result.add("Parser integration", False, str(e))


def check_schema(result):
    """Validate the CSV schema has required columns."""
    csv_path = RESULTS_DIR / "qor_metrics.csv"
    if not csv_path.exists():
        result.add("QoR schema validation", False, "CSV not found")
        return

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []

    required = [
        "setup_wns", "setup_ws", "setup_slack",
        "hold_wns", "hold_ws", "hold_slack",
        "area", "utilization", "confidence",
        "worst_setup_corner", "congestion_overflow",
    ]
    missing = [h for h in required if h not in headers]
    ok = len(missing) == 0
    result.add("QoR schema validation", ok,
               f"missing: {missing}" if not ok else "")


def check_metrics_semantics(result):
    """Validate metric values for correctness."""
    csv_path = RESULTS_DIR / "qor_metrics.csv"
    if not csv_path.exists():
        result.add("Metrics semantic validation", False, "CSV not found")
        return

    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    issues = []

    # Find clock_25 and clock_30
    for row in rows:
        rid = row.get("run_id", "")
        if rid == "clock_25":
            wns = float(row.get("setup_wns", "999"))
            ws = float(row.get("setup_ws", "0"))
            slack = float(row.get("setup_slack", "0"))
            if abs(wns) > 0.001:
                issues.append(f"clock_25: setup_wns should be 0, got {wns}")
            if abs(ws - 13.237) > 0.1:
                issues.append(f"clock_25: setup_ws should be ~13.237, got {ws}")
            if abs(slack - 13.237) > 0.1:
                issues.append(f"clock_25: setup_slack should be ~13.237, got {slack}")
            # Check no absolute paths in source_files
            src = row.get("source_files", "")
            for pat in ABS_PATH_PATTERNS:
                if re.search(pat, src):
                    issues.append(f"clock_25: source_files contains absolute path")
                    break

        elif rid == "clock_30":
            wns = float(row.get("setup_wns", "999"))
            ws = float(row.get("setup_ws", "0"))
            slack = float(row.get("setup_slack", "0"))
            if abs(wns) > 0.001:
                issues.append(f"clock_30: setup_wns should be 0, got {wns}")
            if abs(ws - 17.237) > 0.1:
                issues.append(f"clock_30: setup_ws should be ~17.237, got {ws}")
            if abs(slack - 17.237) > 0.1:
                issues.append(f"clock_30: setup_slack should be ~17.237, got {slack}")

        elif rid == "clock_11":
            wns = float(row.get("setup_wns", "999"))
            ws = float(row.get("setup_ws", "0"))
            slack = float(row.get("setup_slack", "0"))
            if abs(wns) > 0.001:
                issues.append(f"clock_11: setup_wns should be 0, got {wns}")
            if abs(ws - 1.237) > 0.1:
                issues.append(f"clock_11: setup_ws should be ~1.237, got {ws}")
            if abs(slack - 1.237) > 0.1:
                issues.append(f"clock_11: setup_slack should be ~1.237, got {slack}")

        elif rid == "clock_12":
            wns = float(row.get("setup_wns", "999"))
            ws = float(row.get("setup_ws", "0"))
            slack = float(row.get("setup_slack", "0"))
            if abs(wns) > 0.001:
                issues.append(f"clock_12: setup_wns should be 0, got {wns}")
            if abs(ws - 3.237) > 0.1:
                issues.append(f"clock_12: setup_ws should be ~3.237, got {ws}")
            if abs(slack - 3.237) > 0.1:
                issues.append(f"clock_12: setup_slack should be ~3.237, got {slack}")

        # Verify config_only runs don't claim metrics
        confidence = row.get("confidence", "")
        if confidence == "none":
            for field in ["setup_wns", "setup_ws", "area", "power_total"]:
                if row.get(field, "").strip():
                    issues.append(f"{rid}: has {field} but confidence=none")

    # Check source files exist for completed runs
    for row in rows:
        if row.get("confidence", "none") != "none":
            src = row.get("source_files", "")
            for sp in src.split(";"):
                sp = sp.strip()
                if sp and not (ROOT / sp).exists():
                    issues.append(f"{row['run_id']}: source file not found: {sp}")

    ok = len(issues) == 0
    result.add("Metrics semantic validation", ok,
               "; ".join(issues[:3]) if not ok else "")


def check_evaluation(result):
    """Run evaluation scenarios."""
    try:
        proc = subprocess.run(
            [sys.executable, "evaluation/evaluate.py"],
            capture_output=True, text=True, timeout=60,
            cwd=str(ROOT)
        )
        output = proc.stdout + proc.stderr
        match = re.search(r"(\d+) passed, (\d+) failed out of (\d+)", output)
        if match:
            passed = int(match.group(1))
            failed = int(match.group(2))
            total = int(match.group(3))
            ok = failed == 0
            result.add(f"Conversational scenarios: {passed}/{total}", ok,
                       f"{failed} failed" if not ok else "")
        else:
            result.add("Conversational scenarios", False, "could not parse output")
    except Exception as e:
        result.add("Conversational scenarios", False, str(e))


def check_absolute_paths(result):
    """Check for absolute local paths in output files."""
    violations = []
    for filepath in PATH_CHECK_FILES:
        full = ROOT / filepath
        if not full.exists():
            continue
        content = full.read_text(encoding="utf-8", errors="ignore")
        for pattern in ABS_PATH_PATTERNS:
            if re.search(pattern, content):
                violations.append(f"{filepath} matches {pattern}")
                break

    ok = len(violations) == 0
    result.add("No absolute local paths", ok,
               "; ".join(violations[:3]) if not ok else "")


def check_doc_consistency(result):
    """Scan all active documents for forbidden stale content."""
    violations = []

    for filepath in DOC_FILES:
        full = ROOT / filepath
        if not full.exists():
            continue
        content = full.read_text(encoding="utf-8", errors="ignore")
        for pattern, desc in FORBIDDEN_DOC_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                violations.append(f"{filepath}: {desc}")

    # Also check scenario count consistency
    scenarios_file = ROOT / "evaluation" / "scenarios.json"
    if scenarios_file.exists():
        with scenarios_file.open(encoding="utf-8") as f:
            data = json.load(f)
        scenario_count = len(data.get("scenarios", []))

        eval_results = ROOT / "evaluation" / "results.txt"
        if eval_results.exists():
            content = eval_results.read_text(encoding="utf-8")
            match = re.search(r"Total scenarios:\*?\*?\s*(\d+)", content)
            if match:
                doc_count = int(match.group(1))
                if doc_count != scenario_count:
                    violations.append(
                        f"results.txt says {doc_count} scenarios, "
                        f"scenarios.json has {scenario_count}")

    ok = len(violations) == 0
    result.add("Documentation consistency", ok,
               "; ".join(violations[:3]) if not ok else "")


def check_dynamic_run_discovery(result):
    """Test that a dynamic run ID can be discovered and parsed."""
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        from parse_existing_runs import (
            parse_metrics_json, build_output_row, load_explicit_mapping,
            _has_any_metric, FIELDNAMES
        )

        # Create a temporary metrics JSON simulating an auto_tune_1 run
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False,
            dir=str(ROOT / "results" / "important_reports")
        ) as f:
            json.dump({
                "timing__setup__wns": 0,
                "timing__setup__ws": 8.5,
                "timing__setup__tns": 0,
                "timing__hold__wns": 0,
                "timing__hold__ws": 0.2,
                "timing__hold__tns": 0,
                "design__core__area": 15942.8,
                "power__total": 0.0015,
            }, f)
            tmp_path = Path(f.name)

        try:
            # Build a run entry as if from qor_runs.csv
            from pathlib import PurePosixPath
            rel_path = str(PurePosixPath(tmp_path.relative_to(ROOT)))
            run = {
                "run_id": "auto_tune_1",
                "clock_period": "20",
                "report_paths": rel_path,
                "status": "completed",
            }

            explicit_map = load_explicit_mapping()
            row = build_output_row(run, explicit_map)

            # Verify it was parsed
            ok = (
                row.get("confidence", "none") != "none" and
                row.get("setup_ws", "") != "" and
                "auto_tune_1" == row.get("run_id")
            )
            result.add("Dynamic run discovery (auto_tune_1)", ok,
                       "" if ok else f"confidence={row.get('confidence')}")
        finally:
            tmp_path.unlink(missing_ok=True)
    except Exception as e:
        result.add("Dynamic run discovery (auto_tune_1)", False, str(e))


def check_experiment_statuses(result):
    """Validate experiment statuses in qor_runs.csv match expectations."""
    qor_runs_path = RESULTS_DIR / "qor_runs.csv"
    if not qor_runs_path.exists():
        result.add("Experiment status validation", False, "qor_runs.csv not found")
        return

    with qor_runs_path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    issues = []

    # Expected statuses
    expected = {
        "clock_8": "execution_failed",
        "clock_10": "execution_failed",
        "clock_11": "completed",
        "clock_12": "completed",
        "clock_15": "config_only",
        "clock_18": "config_only",
        "clock_20": "config_only",
        "clock_22": "config_only",
        "clock_25": "completed",
        "clock_30": "completed",
    }

    found_ids = set()
    for row in rows:
        rid = row.get("run_id", "")
        found_ids.add(rid)
        if rid in expected:
            actual = row.get("status", "")
            if actual != expected[rid]:
                issues.append(f"{rid}: expected status '{expected[rid]}', got '{actual}'")

    # Ensure all expected runs exist
    for rid in expected:
        if rid not in found_ids:
            issues.append(f"{rid}: missing from qor_runs.csv")

    ok = len(issues) == 0
    result.add("Experiment status validation", ok,
               "; ".join(issues[:3]) if not ok else "")


def main():
    result = ValidationResult()

    print("Running project validation...")
    print()

    check_compilation(result)
    check_unit_tests(result)
    check_parser(result)
    check_schema(result)
    check_metrics_semantics(result)
    check_evaluation(result)
    check_absolute_paths(result)
    check_doc_consistency(result)
    check_experiment_statuses(result)
    check_dynamic_run_discovery(result)

    success = result.print_summary()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
