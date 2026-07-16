#!/usr/bin/env python3
"""
Optional LLM-backed explanation layer for the QoR Debugging Assistant.

This module wraps an LLM API (OpenAI-compatible) to provide natural-language
explanations on top of deterministic parsed metrics and rule-based diagnoses.

IMPORTANT: The LLM never receives raw logs. It only receives:
  1. Structured parsed metrics (from qor_metrics.csv)
  2. Rule-engine diagnoses (from rule_engine.py)
  3. Structured recommendations and QoR classification
  4. The user's question
  5. Evidence file paths

Activation:
  - Command-line flag: --llm
  - Environment variable: USE_LLM=true
  - If no API key is found, falls back gracefully to rule-based mode.

Configuration:
  - OPENAI_API_KEY or LLM_API_KEY environment variable
  - LLM_BASE_URL for non-OpenAI endpoints (default: https://api.openai.com/v1)
  - LLM_MODEL (default: gpt-4o-mini)

Post-response validation:
  - Numerical claims in LLM output are checked against the structured context.
  - Unsupported numerical claims cause fallback to the deterministic response.
"""

import json
import os
import re
import sys
from pathlib import Path

# Try importing requests/urllib for HTTP calls
try:
    import urllib.request
    import urllib.error
    HAS_URLLIB = True
except ImportError:
    HAS_URLLIB = False


SYSTEM_PROMPT = """You are an ASIC QoR debugging assistant for designs implemented using LibreLane/OpenROAD.

Your role:
- Explain the provided parsed metrics and rule-based diagnosis in clear natural language.
- Help the user understand timing failures, area trade-offs, power breakdown, and violations.
- Do NOT invent metrics. Do NOT invent report contents.
- If a field is missing, say it is missing.
- Always distinguish evidence (what the data shows) from recommendation (what to do next).
- Reference the source files when available.

Critical rules:
- The fastest setup-clean run is NOT the best overall QoR run. It only considers setup timing.
- Positive WS (worst slack) must NEVER be called positive WNS. WNS is zero when no violation exists.
- Zero final DRC errors do NOT prove zero congestion. Only explicit overflow data quantifies congestion.
- Clock optimization must NOT be recommended ahead of blocking electrical, hold, setup, DRC, or
  congestion problems. Mention timing margin as secondary information only.
- Unavailable fields must remain explicitly stated as unavailable.
- Use the design name provided in the context, not a hardcoded name.

Use technical EDA terminology correctly: WNS, WS, TNS, setup slack, hold slack, critical path,
slew rate, capacitance, fanout, congestion, utilization.

You receive structured data, not raw logs. Answer based only on what is provided."""


def get_config():
    """Get LLM configuration from environment."""
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY", "")
    base_url = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
    model = os.environ.get("LLM_MODEL", "gpt-4o-mini")
    return api_key, base_url, model


def is_llm_available():
    """Check if LLM mode can be activated."""
    api_key, _, _ = get_config()
    return bool(api_key) and HAS_URLLIB


def should_use_llm():
    """Determine if LLM mode is requested and available."""
    # Check environment variable
    use_llm = os.environ.get("USE_LLM", "").lower() in ("true", "1", "yes")
    # Check command-line flag
    if "--llm" in sys.argv:
        use_llm = True

    if use_llm and not is_llm_available():
        print("[WARNING] LLM mode requested but API key not found. Falling back to rule-based mode.")
        return False

    return use_llm


def build_context(metrics_rows, diagnoses, question, recommendation=None):
    """Build a structured context string for the LLM.

    The LLM receives only structured data, not raw logs.
    Includes all important metric fields when available.
    """
    context_parts = []

    # Add metrics summary with expanded fields
    context_parts.append("=== Parsed QoR Metrics ===")
    expanded_keys = [
        "setup_wns", "setup_ws", "setup_tns", "setup_slack",
        "hold_wns", "hold_ws", "hold_tns", "hold_slack",
        "worst_setup_corner", "worst_hold_corner", "timing_corners_count",
        "area", "utilization",
        "power_total", "power_internal", "power_switching", "power_leakage",
        "slew_violations", "cap_violations", "fanout_violations",
        "route_drc_errors", "congestion_overflow", "congestion_status",
        "confidence", "missing_fields",
    ]

    for row in metrics_rows:
        if row.get("confidence") == "none":
            context_parts.append(f"Run {row.get('run_id')}: No metrics available")
            continue

        parts = [f"Run {row.get('run_id')} (clock={row.get('clock_period')} ns):"]
        for key in expanded_keys:
            val = row.get(key, "")
            if val:
                parts.append(f"  {key}={val}")
        parts.append(f"  source={row.get('source_files', 'N/A')}")
        context_parts.append("\n".join(parts))

    # Add diagnoses
    if diagnoses:
        context_parts.append("\n=== Rule-Based Diagnoses ===")
        for d in diagnoses:
            context_parts.append(
                f"[{d['severity'].upper()}] {d['run_id']}: {d['category']} - {d['finding']}"
            )

    # Add structured recommendation if available
    if recommendation:
        context_parts.append("\n=== Structured Recommendation ===")
        context_parts.append(f"Primary category: {recommendation.get('primary_category', 'N/A')}")
        blocking = recommendation.get("blocking_issues", [])
        if blocking:
            context_parts.append(f"Blocking issues: {', '.join(blocking)}")
        secondary = recommendation.get("secondary_opportunities", [])
        if secondary:
            context_parts.append(f"Secondary opportunities: {', '.join(secondary)}")
        context_parts.append(f"Recommendation: {recommendation.get('text', 'N/A')}")

    return "\n\n".join(context_parts)


def _build_allowlist(metrics_rows, diagnoses, question):
    """Build an allowlist of numerical values from structured context.

    Returns a set of string representations of allowed numbers.
    """
    allowed = set()

    # Common structural numbers
    allowed.update(["0", "1", "2", "3", "4", "5", "6", "100"])

    # Extract numbers from the question
    for num in re.findall(r"-?\d+\.?\d*", question):
        allowed.add(num)

    # Extract all metric values from rows
    for row in metrics_rows:
        for key, val in row.items():
            if key.startswith("_"):
                continue
            val_str = str(val).strip()
            if not val_str:
                continue
            # Add exact value
            allowed.add(val_str)
            # Add common formatted variants
            try:
                fval = float(val_str)
                allowed.add(f"{fval:.3f}")
                allowed.add(f"{fval:.2f}")
                allowed.add(f"{fval:.1f}")
                allowed.add(f"{fval:.0f}")
                allowed.add(str(int(fval)) if fval == int(fval) else "")
                # mW conversion
                if abs(fval) < 0.1 and fval != 0:
                    allowed.add(f"{fval*1000:.3f}")
                    allowed.add(f"{fval*1000:.2f}")
                    allowed.add(f"{fval*1000:.1f}")
            except (ValueError, TypeError):
                pass

    # Extract numbers from diagnoses
    if diagnoses:
        for d in diagnoses:
            evidence = d.get("evidence", {})
            for k, v in evidence.items():
                if k == "source":
                    continue
                try:
                    fval = float(v)
                    allowed.add(str(v))
                    allowed.add(f"{fval:.3f}")
                    allowed.add(f"{fval:.1f}")
                except (ValueError, TypeError):
                    pass

    # Remove empty strings
    allowed.discard("")

    return allowed


def validate_llm_response(response, metrics_rows, diagnoses, question):
    """Validate that LLM response does not contain unsupported numerical claims.

    Uses two levels of validation:
      1. Token-level: checks all numbers against an allowlist (existing behavior).
      2. Claim-level: checks metric+value pairs against structured data.

    This is a conservative safeguard, not a formal guarantee against all hallucinations.

    Returns:
        dict with keys: valid (bool), unsupported_numbers (list), reason (str)
    """
    if not response:
        return {"valid": True, "unsupported_numbers": [], "reason": "Empty response"}

    allowlist = _build_allowlist(metrics_rows, diagnoses, question)

    # --- Level 1: Token-level validation ---
    # Extract numerical tokens from the LLM response
    response_numbers = re.findall(r"(?<![#v])\b(\d+\.?\d*)\b", response)

    # Filter out very common harmless numbers
    harmless = {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10",
                "100", "32", "64"}  # structural constants only

    # Clock periods are harmless only in clock/period context
    clock_periods = {"8", "10", "11", "12", "15", "18", "20", "22", "25", "30"}

    unsupported = []
    for num in response_numbers:
        if num in allowlist:
            continue
        if num in harmless:
            continue
        if num in clock_periods:
            # Allow clock period numbers only if not used as metric values
            # (they are allowlisted from the rows anyway if they exist)
            continue
        # Check if it's a close match to any allowed value
        try:
            fnum = float(num)
            close_match = any(
                abs(fnum - float(a)) < 0.01
                for a in allowlist
                if a and re.match(r"-?\d+\.?\d*$", a)
            )
            if close_match:
                continue
        except (ValueError, TypeError):
            continue
        unsupported.append(num)

    if unsupported:
        return {
            "valid": False,
            "unsupported_numbers": unsupported[:5],
            "reason": "Numerical claims not found in structured context",
        }

    # --- Level 2: Structured claim validation ---
    # Check for metric+value claims that contradict the data
    claim_issues = _check_structured_claims(response, metrics_rows)
    if claim_issues:
        return {
            "valid": False,
            "unsupported_numbers": claim_issues[:3],
            "reason": "Structured metric claims contradict parsed data",
        }

    return {"valid": True, "unsupported_numbers": [], "reason": "All numbers verified"}


def _check_structured_claims(response, metrics_rows):
    """Check for metric-value claims that contradict the structured data.

    Detects patterns like 'power is X W', 'area is X', 'WNS is X' and validates
    the claimed value against the actual parsed data for the referenced run.
    """
    issues = []

    # Build a lookup: run_id -> {metric: value}
    run_data = {}
    for row in metrics_rows:
        rid = row.get("run_id", "")
        run_data[rid] = row

    # Metric claim patterns: (regex, metric_key, unit_context)
    claim_patterns = [
        (r"(?:total\s+)?power\s+(?:is|=|:)\s*(\d+\.?\d*)\s*([Ww])", "power_total", None),
        (r"area\s+(?:is|=|:)\s*(\d+\.?\d*)", "area", None),
        (r"WNS\s+(?:is|=|:)\s*(-?\d+\.?\d*)", "setup_wns", None),
        (r"setup\s+slack\s+(?:is|=|:)\s*(-?\d+\.?\d*)", "setup_slack", None),
        (r"utilization\s+(?:is|=|:)\s*(\d+\.?\d*)\s*%", "utilization", "percent"),
    ]

    for pattern, metric_key, unit_ctx in claim_patterns:
        matches = re.finditer(pattern, response, re.IGNORECASE)
        for m in matches:
            claimed_val = m.group(1)
            try:
                claimed_f = float(claimed_val)
            except (ValueError, TypeError):
                continue

            # Check if this value exists in any run's data for this metric
            found_match = False
            for rid, data in run_data.items():
                actual = data.get(metric_key, "")
                if not actual:
                    continue
                try:
                    actual_f = float(actual)
                    # For utilization in percent context, multiply
                    if unit_ctx == "percent" and actual_f <= 1.0:
                        actual_f *= 100
                    if abs(claimed_f - actual_f) < 0.1:
                        found_match = True
                        break
                    # mW vs W conversion tolerance
                    if metric_key == "power_total":
                        if abs(claimed_f - actual_f * 1000) < 0.1:
                            found_match = True
                            break
                except (ValueError, TypeError):
                    continue

            if not found_match and claimed_f > 0.01:
                issues.append(f"{metric_key}={claimed_val}")

    return issues


def call_llm(question, context, api_key=None, base_url=None, model=None):
    """Call the LLM API with structured context.

    Returns the LLM's response text, or None on failure.
    """
    if api_key is None:
        api_key, base_url, model = get_config()

    if not api_key:
        return None

    url = f"{base_url}/chat/completions"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
    ]

    payload = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 1000,
    }).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, json.JSONDecodeError) as e:
        print(f"[LLM ERROR] {e}")
        return None


def llm_explain(question, metrics_rows, diagnoses, recommendation=None,
                deterministic_response=None):
    """Get an LLM explanation for the given question.

    If the LLM response contains unsupported numerical claims, falls back
    to the deterministic response.

    Returns the explanation string or None if LLM is unavailable/invalid.
    """
    if not is_llm_available():
        return None

    context = build_context(metrics_rows, diagnoses, question, recommendation)
    response = call_llm(question, context)

    if not response:
        return None

    # Validate numerical claims
    validation = validate_llm_response(response, metrics_rows, diagnoses, question)
    if not validation["valid"]:
        unsupported = ", ".join(validation["unsupported_numbers"])
        print(f"[LLM VALIDATION] Rejected: unsupported numbers ({unsupported}). "
              "Falling back to deterministic response.")
        return None

    return f"[LLM Explanation]\n{response}\n[End LLM Explanation]"


if __name__ == "__main__":
    # Quick test
    if is_llm_available():
        print("LLM mode is available.")
        print(f"Model: {get_config()[2]}")
    else:
        print("LLM mode is NOT available (no API key found).")
        print("Set OPENAI_API_KEY or LLM_API_KEY environment variable to enable.")
        print("The assistant works fully in rule-based mode without an API key.")
