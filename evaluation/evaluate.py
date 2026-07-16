#!/usr/bin/env python3
"""
Automated evaluation of the QoR Debugging Assistant against predefined scenarios.

Checks that the assistant:
- Uses real metrics (not fabricated)
- Identifies timing failures correctly
- Identifies best timing-clean run correctly (among available data only)
- Admits when data is unavailable
- Gives useful tuning recommendations with conditional language
- Does not hallucinate unsupported metrics
- Correctly distinguishes WNS from WS
- Does not infer congestion from DRC alone

Usage:
  python evaluation/evaluate.py
"""

import csv
import json
import sys
from pathlib import Path

# Add scripts directory to path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from assistant import load_metrics, answer, answer_structured

SCENARIOS_FILE = ROOT / "evaluation" / "scenarios.json"
RESULTS_FILE = ROOT / "evaluation" / "results.txt"


def load_scenarios():
    """Load evaluation scenarios from JSON."""
    with SCENARIOS_FILE.open(encoding="utf-8") as f:
        data = json.load(f)
    return data["scenarios"]


def evaluate_scenario(scenario, rows):
    """Evaluate a single scenario against the assistant.

    Uses answer_structured() for structured fact-checking where available,
    plus keyword/forbidden checks on the text output.

    Returns (passed: bool, reason: str, response: str)
    """
    question = scenario["question"]

    # Get structured result
    structured = answer_structured(question, rows)
    response = structured["text"]
    data = structured["data"]
    intent = structured["intent"]

    if response == "EXIT":
        return False, "Assistant returned EXIT unexpectedly", ""

    response_lower = response.lower()
    failures = []

    # --- Structured fact checks (where data is available) ---
    if intent == "best_run" and data:
        if data.get("best_available_run") != "clock_11":
            failures.append(f"Structured: best_available_run={data.get('best_available_run')}, expected clock_11")
        if data.get("global_conclusion_possible") is not False:
            failures.append("Structured: global_conclusion_possible should be False")

    if intent == "congestion" and data:
        if data.get("congestion_quantifiable") is not False:
            failures.append("Structured: congestion_quantifiable should be False")
        if data.get("final_drc_errors") != 0:
            failures.append(f"Structured: final_drc_errors={data.get('final_drc_errors')}, expected 0")

    if intent == "missing_data" and data:
        missing = data.get("missing_runs", [])
        for expected_missing in ["clock_15", "clock_18", "clock_20", "clock_22"]:
            if expected_missing not in missing:
                failures.append(f"Structured: {expected_missing} not in missing_runs")

    if intent == "tuning" and data:
        if data.get("recommendation_basis") != "setup_slack":
            failures.append(f"Structured: recommendation_basis={data.get('recommendation_basis')}, expected setup_slack")

    # --- Keyword checks (user-facing phrasing) ---
    for keyword in scenario.get("expected_keywords", []):
        if keyword.lower() not in response_lower:
            failures.append(f"Missing expected keyword: '{keyword}'")

    # --- Forbidden hallucinations ---
    for forbidden in scenario.get("forbidden_hallucinations", []):
        if forbidden.lower() in response_lower:
            failures.append(f"Forbidden content found: '{forbidden}'")

    # Check that response is non-empty and meaningful
    if len(response.strip()) < 20:
        failures.append("Response too short (< 20 chars)")

    # Check expected metric fields are referenced (if the run has data)
    for field in scenario.get("expected_metric_fields", []):
        has_data = any(
            r.get(field, "").strip() not in ("", "none")
            for r in rows
        )
        if has_data and field not in response_lower and field.replace("_", " ") not in response_lower:
            values = [r.get(field, "") for r in rows if r.get(field, "").strip()]
            # Check if any value (exact or truncated to 3 decimals) appears
            value_found = False
            for v in values:
                if v in response:
                    value_found = True
                    break
                # Also check truncated version (formatting may shorten)
                try:
                    truncated = f"{float(v):.3f}"
                    if truncated in response:
                        value_found = True
                        break
                except (ValueError, TypeError):
                    pass
            if not value_found:
                failures.append(f"Expected metric field '{field}' or its value not referenced")

    if failures:
        return False, "; ".join(failures), response

    return True, "All checks passed", response


def run_evaluation():
    """Run full evaluation and produce results."""
    rows = load_metrics()
    scenarios = load_scenarios()

    results = []
    passed_count = 0
    failed_count = 0

    print("=" * 60)
    print("QoR Debugging Assistant - Evaluation")
    print("=" * 60)
    print()

    for scenario in scenarios:
        sid = scenario["scenario_id"]
        name = scenario["name"]

        passed, reason, response = evaluate_scenario(scenario, rows)

        status = "PASS" if passed else "FAIL"
        if passed:
            passed_count += 1
        else:
            failed_count += 1

        results.append({
            "scenario_id": sid,
            "name": name,
            "question": scenario["question"],
            "status": status,
            "reason": reason,
            "response_preview": response[:200] if response else "",
        })

        icon = "PASS" if passed else "FAIL"
        print(f"  [{icon}] {sid} - {name}")
        if not passed:
            print(f"         Reason: {reason}")

    print()
    print(f"Results: {passed_count} passed, {failed_count} failed out of {len(scenarios)} scenarios.")
    print()

    # Write results to Markdown
    write_results_md(results, passed_count, failed_count, len(scenarios))

    return failed_count == 0


def write_results_md(results, passed, failed, total):
    """Write evaluation results to plain text file."""
    lines = [
        "Evaluation Results",
        "",
        f"Auto-generated by evaluation/evaluate.py",
        f"Total scenarios: {total}",
        f"Passed: {passed}",
        f"Failed: {failed}",
        f"Pass rate: {passed/total*100:.1f}%",
        "",
        "Note: This is scenario coverage (predefined conversational tests),",
        "not a statistical generalization benchmark.",
        "",
        "",
        "Results Summary",
        "-" * 40,
        "",
    ]

    for r in results:
        status = r["status"]
        lines.append(f"  {r['scenario_id']}  {r['name']}  {status}")
        if status == "FAIL":
            lines.append(f"         Reason: {r['reason']}")

    lines.append("")
    lines.append("")
    lines.append("Detailed Results")
    lines.append("-" * 40)
    lines.append("")

    for r in results:
        lines.append(f"{r['scenario_id']} - {r['name']}")
        lines.append(f"  Question: {r['question']}")
        lines.append(f"  Status: {r['status']}")
        lines.append(f"  Reason: {r['reason']}")
        lines.append(f"  Response preview:")
        lines.append(f"    {r['response_preview']}")
        lines.append("")

    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"Results saved to: {RESULTS_FILE}")


if __name__ == "__main__":
    success = run_evaluation()
    sys.exit(0 if success else 1)
