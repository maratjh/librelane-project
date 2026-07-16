#!/usr/bin/env python3
"""
Parse existing LibreLane/OpenROAD report artifacts and produce structured
QoR metrics for the conversational assistant.

Key design decisions:
- Stores raw WNS and WS separately (never mixes positive WS into WNS field).
- Computes normalized setup_slack and hold_slack for diagnosis logic.
- Uses repository-relative POSIX paths in all output.
- Parses multi-corner timing data when available.
- Never fabricates metrics: missing fields are left empty/null.
- Outputs both CSV (for the assistant) and JSON (for programmatic use).
"""

import csv
import json
import re
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
DESIGN_DIR = ROOT / "designs" / "pm32"
RUNS_DIR = DESIGN_DIR / "runs"
IMPORTANT = ROOT / "results" / "important_reports"
MAPPING_FILE = IMPORTANT / "run_mapping.json"
OUT_CSV = ROOT / "results" / "qor_metrics.csv"
OUT_JSON = ROOT / "results" / "parsed_summary.json"
QOR_RUNS = ROOT / "results" / "qor_runs.csv"

EXPECTED_RUNS = [
    ("clock_15", "15"),
    ("clock_18", "18"),
    ("clock_20", "20"),
    ("clock_22", "22"),
    ("clock_25", "25"),
    ("clock_30", "30"),
]

FIELDNAMES = [
    "run_id",
    "design",
    "clock_period",
    # Raw timing fields (separate WNS and WS)
    "setup_wns",
    "setup_ws",
    "setup_tns",
    "hold_wns",
    "hold_ws",
    "hold_tns",
    # Normalized slack (used for diagnosis)
    "setup_slack",
    "hold_slack",
    # Area
    "area",
    "utilization",
    # Power
    "power_total",
    "power_internal",
    "power_switching",
    "power_leakage",
    # Violations
    "slew_violations",
    "cap_violations",
    "fanout_violations",
    # Routing
    "route_drc_errors",
    "route_wirelength",
    "route_vias",
    "grt_wirelength",
    "grt_vias",
    "antenna_violations",
    # Congestion
    "congestion_overflow",
    "congestion_status",
    # Multi-corner info
    "worst_setup_corner",
    "worst_hold_corner",
    "timing_corners_count",
    # Metadata
    "decision",
    "source_files",
    "field_sources",
    "missing_fields",
    "confidence",
    "parser_warnings",
]

# Fields used for confidence and missing-fields computation
METRIC_KEYS = [
    "setup_wns", "setup_ws", "setup_tns",
    "hold_wns", "hold_ws", "hold_tns",
    "area", "utilization",
    "power_total", "power_internal", "power_switching", "power_leakage",
    "slew_violations", "cap_violations", "fanout_violations",
    "route_drc_errors", "route_wirelength", "route_vias",
    "grt_wirelength", "grt_vias", "antenna_violations",
]

NO_CONGESTION = "not_available"


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _safe_read_text(path: Path) -> str:
    """Read file text safely, returning empty string on failure."""
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _to_relative_posix(path: Path) -> str:
    """Convert an absolute path to a repository-relative POSIX path."""
    try:
        rel = path.resolve().relative_to(ROOT.resolve())
        return str(PurePosixPath(rel))
    except ValueError:
        # If the path is outside ROOT, return as-is but normalized
        return str(PurePosixPath(path))


def _normalise(value) -> str:
    """Convert a numeric value to string for CSV output."""
    if value is None:
        return ""
    if isinstance(value, float):
        if abs(value) < 0.0001 and value != 0:
            return f"{value:.10e}"
        return str(value)
    if isinstance(value, int):
        return str(value)
    return str(value)


def _safe_float(value):
    """Convert to float or return None."""
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compute_setup_slack(setup_wns, setup_ws):
    """
    Compute normalized setup slack from raw WNS and WS values.

    Normalization rule:
    - If WNS is available and WNS < 0: setup_slack = WNS (timing failure)
    - Elif WS is available: setup_slack = WS (positive margin)
    - Elif WNS is available: setup_slack = WNS (zero = boundary)
    - Else: None (missing)
    """
    wns = _safe_float(setup_wns)
    ws = _safe_float(setup_ws)

    if wns is not None and wns < 0:
        return wns
    elif ws is not None:
        return ws
    elif wns is not None:
        return wns
    else:
        return None


def compute_hold_slack(hold_wns, hold_ws):
    """Compute normalized hold slack (same logic as setup)."""
    wns = _safe_float(hold_wns)
    ws = _safe_float(hold_ws)

    if wns is not None and wns < 0:
        return wns
    elif ws is not None:
        return ws
    elif wns is not None:
        return wns
    else:
        return None


# ---------------------------------------------------------------------------
# JSON metric extraction (OpenROAD metrics.json format)
# ---------------------------------------------------------------------------

# Explicit key-priority system for global metrics
KEY_CANDIDATES = {
    "setup_wns": ["timing__setup__wns"],
    "setup_ws": ["timing__setup__ws"],
    "setup_tns": ["timing__setup__tns"],
    "hold_wns": ["timing__hold__wns"],
    "hold_ws": ["timing__hold__ws"],
    "hold_tns": ["timing__hold__tns"],
    "area": ["design__core__area", "design__die__area", "design__instance__area"],
    "utilization": ["design__instance__utilization", "design__instance__utilization__stdcell"],
    "power_total": ["power__total"],
    "power_internal": ["power__internal__total"],
    "power_switching": ["power__switching__total"],
    "power_leakage": ["power__leakage__total"],
    "slew_violations": ["design__max_slew_violation__count"],
    "cap_violations": ["design__max_cap_violation__count"],
    "fanout_violations": ["design__max_fanout_violation__count"],
    "route_drc_errors": ["route__drc_errors"],
    "route_wirelength": ["route__wirelength"],
    "route_vias": ["route__vias"],
    "grt_wirelength": ["global_route__wirelength"],
    "grt_vias": ["global_route__vias"],
    "antenna_violations": ["antenna__violating__nets", "route__antenna_violation__count"],
}

# Congestion overflow keys
CONGESTION_KEYS = [
    "global_route__overflow",
    "global_route__overflow__total",
    "global_route__congestion",
]


def _extract_corners(data: dict) -> dict:
    """Extract per-corner timing data from OpenROAD metrics JSON.

    Returns: {corner_name: {setup_wns, setup_ws, hold_wns, hold_ws, ...}}
    """
    corners = {}
    corner_pattern = re.compile(
        r"timing__(setup|hold)__(wns|ws|tns)__corner:(.+)"
    )
    violation_pattern = re.compile(
        r"design__max_(slew|cap|fanout)_violation__count__corner:(.+)"
    )

    for key, value in data.items():
        m = corner_pattern.match(key)
        if m:
            timing_type = m.group(1)  # setup or hold
            metric_type = m.group(2)  # wns, ws, tns
            corner_name = m.group(3)
            if corner_name not in corners:
                corners[corner_name] = {}
            corners[corner_name][f"{timing_type}_{metric_type}"] = value
            continue

        m = violation_pattern.match(key)
        if m:
            vio_type = m.group(1)
            corner_name = m.group(2)
            if corner_name not in corners:
                corners[corner_name] = {}
            corners[corner_name][f"{vio_type}_violations"] = value

    return corners


def _find_worst_corners(corners: dict) -> tuple:
    """Find worst setup and hold corners.

    Returns (worst_setup_corner_name, worst_hold_corner_name).
    Worst = minimum normalized slack.
    """
    worst_setup_corner = None
    worst_setup_slack = None
    worst_hold_corner = None
    worst_hold_slack = None

    for corner_name, cdata in corners.items():
        # Setup slack for this corner
        s_slack = compute_setup_slack(
            cdata.get("setup_wns"),
            cdata.get("setup_ws")
        )
        if s_slack is not None:
            if worst_setup_slack is None or s_slack < worst_setup_slack:
                worst_setup_slack = s_slack
                worst_setup_corner = corner_name

        # Hold slack for this corner
        h_slack = compute_hold_slack(
            cdata.get("hold_wns"),
            cdata.get("hold_ws")
        )
        if h_slack is not None:
            if worst_hold_slack is None or h_slack < worst_hold_slack:
                worst_hold_slack = h_slack
                worst_hold_corner = corner_name

    return worst_setup_corner, worst_hold_corner


def parse_metrics_json(path: Path) -> dict:
    """Extract QoR metrics from an OpenROAD-style metrics JSON file.

    Returns a dict with:
    - Individual metric values
    - Multi-corner data
    - Parser warnings
    - Field source tracking
    """
    text = _safe_read_text(path)
    if not text.strip():
        return {}

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"_parser_warnings": ["Malformed JSON file"]}

    if not isinstance(data, dict):
        return {"_parser_warnings": ["JSON root is not an object"]}

    warnings = []
    field_sources = {}

    def get_exact(*keys):
        """Get value by exact key match only (no partial matching)."""
        for k in keys:
            if k in data:
                return data[k], k
        return None, None

    # Extract global metrics using exact key lookups
    result = {}
    for field, candidates in KEY_CANDIDATES.items():
        value, matched_key = get_exact(*candidates)
        if value is not None:
            result[field] = _normalise(value)
            field_sources[field] = f"json:{matched_key}"
        else:
            result[field] = ""

    # Congestion overflow
    congestion_overflow = ""
    for ck in CONGESTION_KEYS:
        if ck in data:
            congestion_overflow = _normalise(data[ck])
            field_sources["congestion_overflow"] = f"json:{ck}"
            break
    result["congestion_overflow"] = congestion_overflow

    # Detect congestion status from available routing data
    has_routing = any(
        result.get(k) for k in ["route_drc_errors", "route_wirelength", "grt_wirelength"]
    )
    if congestion_overflow:
        result["congestion_status"] = "overflow_data_available"
    elif has_routing:
        result["congestion_status"] = "routing_data_found"
    else:
        result["congestion_status"] = NO_CONGESTION

    # Multi-corner parsing
    corners = _extract_corners(data)
    result["_corners"] = corners
    result["timing_corners_count"] = str(len(corners)) if corners else ""

    if corners:
        worst_setup, worst_hold = _find_worst_corners(corners)
        result["worst_setup_corner"] = worst_setup or ""
        result["worst_hold_corner"] = worst_hold or ""
    else:
        result["worst_setup_corner"] = ""
        result["worst_hold_corner"] = ""

    # Compute normalized slack
    setup_slack = compute_setup_slack(result.get("setup_wns"), result.get("setup_ws"))
    hold_slack = compute_hold_slack(result.get("hold_wns"), result.get("hold_ws"))
    result["setup_slack"] = _normalise(setup_slack) if setup_slack is not None else ""
    result["hold_slack"] = _normalise(hold_slack) if hold_slack is not None else ""

    result["_field_sources"] = field_sources
    result["_parser_warnings"] = warnings
    return result


# ---------------------------------------------------------------------------
# RPT summary table parsing
# ---------------------------------------------------------------------------

def parse_summary_rpt(path: Path) -> dict:
    """Parse the tabular summary .rpt format produced by OpenROAD.

    Validates header before relying on positional columns.
    Returns parsed metrics and parser warnings.
    """
    text = _safe_read_text(path)
    if not text.strip():
        return {}

    warnings = []
    field_sources = {}

    # Validate header presence
    header_keywords = ["Hold", "Setup", "Slack", "TNS"]
    lines = text.splitlines()
    has_valid_header = any(
        sum(1 for kw in header_keywords if kw in line) >= 2
        for line in lines[:10]
    )
    if not has_valid_header:
        warnings.append("RPT header not recognized; parsing may be unreliable")

    # Find the Overall row
    for line in lines:
        if "Overall" not in line or not re.search(r"\d", line):
            continue

        nums = re.findall(r"-?\d+\.\d+|-?\d+", line)
        if len(nums) < 12:
            warnings.append(f"Overall row has fewer than 12 numbers ({len(nums)} found)")
            continue

        # Table columns from the .rpt format:
        # Col[0]: Hold WS | Col[1]: Hold Reg2Reg | Col[2]: Hold TNS | Col[3]: Hold Vio Count
        # Col[4]: Hold r2r vio | Col[5]: Setup WS | Col[6]: Setup Reg2Reg | Col[7]: Setup TNS
        # Col[8]: Setup Vio Count | Col[9]: Setup r2r vio | Col[10]: Max Cap Vio | Col[11]: Max Slew Vio

        # Note: The "Worst Slack" column in RPT is actually WS (positive margin),
        # not WNS. The RPT reports WS values; WNS is implicit (0 if no violations).
        result = {
            "hold_ws": nums[0],
            "setup_ws": nums[5],
            "hold_tns": nums[2],
            "setup_tns": nums[7],
            # WNS: If TNS is 0 and no violations, WNS is 0
            "hold_wns": "0" if float(nums[2]) == 0 else "",
            "setup_wns": "0" if float(nums[7]) == 0 else "",
            "slew_violations": nums[11] if len(nums) >= 12 else "",
            "cap_violations": nums[10] if len(nums) >= 12 else "",
            "congestion_status": "routing_data_found" if "route" in text.lower() else NO_CONGESTION,
            "_field_sources": {
                "hold_ws": "rpt:Overall",
                "setup_ws": "rpt:Overall",
                "hold_tns": "rpt:Overall",
                "setup_tns": "rpt:Overall",
                "slew_violations": "rpt:Overall",
                "cap_violations": "rpt:Overall",
            },
            "_parser_warnings": warnings,
        }
        return result

    if warnings:
        return {"_parser_warnings": warnings}
    return {"_parser_warnings": ["No Overall row found in RPT"]}


# ---------------------------------------------------------------------------
# Run mapping logic
# ---------------------------------------------------------------------------

def load_explicit_mapping() -> dict:
    """Load the explicit mapping from run_mapping.json.

    Returns a dict: run_id -> {metrics_json: Path, summary_rpt: Path, ...}
    """
    mapping = {}
    if not MAPPING_FILE.exists():
        return mapping

    try:
        data = json.loads(MAPPING_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return mapping

    for entry in data.get("mappings", []):
        run_id = entry.get("run_id", "")
        if not run_id:
            continue
        info = {}
        if entry.get("metrics_json"):
            info["metrics_json"] = IMPORTANT / entry["metrics_json"]
        if entry.get("summary_rpt"):
            info["summary_rpt"] = IMPORTANT / entry["summary_rpt"]
        info["note"] = entry.get("note", "")
        mapping[run_id] = info

    return mapping


def _token_match(path: Path, text: str, run_id: str, clock_period: str) -> bool:
    """Fallback: try to match a report file to a run by filename/content tokens."""
    merged = f"{str(path).replace(chr(92), '/').lower()}\n{text.lower()}"
    tokens = [
        run_id.lower(),
        run_id.lower().replace("_", ""),
        f"clock_{clock_period}",
        f"clock{clock_period}",
        f"config_clock{clock_period}",
        f'"clock_period": {clock_period}',
        f'"clock_period":{clock_period}',
    ]
    return any(t in merged for t in tokens)


def find_metrics_for_run(run_id: str, clock_period: str, explicit_map: dict) -> tuple:
    """Find and parse metrics for a given run.

    Returns (metrics_dict, source_files_list, field_sources_dict, warnings_list).
    """
    sources = []
    combined_metrics = {}
    all_field_sources = {}
    all_warnings = []

    # 1. Try explicit mapping first
    if run_id in explicit_map:
        info = explicit_map[run_id]

        # Parse the JSON metrics file (preferred source)
        json_path = info.get("metrics_json")
        if json_path and json_path.exists():
            m = parse_metrics_json(json_path)
            if m:
                # Extract internal fields
                corners = m.pop("_corners", {})
                field_sources = m.pop("_field_sources", {})
                warnings = m.pop("_parser_warnings", [])

                combined_metrics.update(
                    {k: v for k, v in m.items() if v not in ("", None)}
                )
                all_field_sources.update(field_sources)
                all_warnings.extend(warnings)
                sources.append(_to_relative_posix(json_path))

                # Store corners for JSON output
                combined_metrics["_corners"] = corners

        # Parse the RPT file (fill gaps only)
        rpt_path = info.get("summary_rpt")
        if rpt_path and rpt_path.exists():
            m = parse_summary_rpt(rpt_path)
            if m:
                field_sources = m.pop("_field_sources", {})
                warnings = m.pop("_parser_warnings", [])
                all_warnings.extend(warnings)

                # Only fill genuinely missing fields from RPT
                for k, v in m.items():
                    if k.startswith("_"):
                        continue
                    if k not in combined_metrics or not combined_metrics[k]:
                        combined_metrics[k] = v
                        if k in field_sources:
                            all_field_sources[k] = field_sources[k]

                sources.append(_to_relative_posix(rpt_path))

    # 2. If no explicit mapping or no metrics found, scan important_reports only
    if not _has_any_metric(combined_metrics):
        search_roots = [IMPORTANT]
        for root in search_roots:
            if not root.exists():
                continue
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                if path.suffix.lower() not in {".json", ".rpt", ".txt", ".log"}:
                    continue
                if path == MAPPING_FILE:
                    continue

                text = _safe_read_text(path)
                if not _token_match(path, text, run_id, clock_period):
                    continue

                if path.suffix.lower() == ".json":
                    m = parse_metrics_json(path)
                else:
                    m = parse_summary_rpt(path)

                if m:
                    m.pop("_corners", None)
                    m.pop("_field_sources", None)
                    m.pop("_parser_warnings", None)

                    if _has_any_metric(m):
                        for k, v in m.items():
                            if not k.startswith("_") and (k not in combined_metrics or not combined_metrics[k]):
                                combined_metrics[k] = v
                        sources.append(_to_relative_posix(path))
                        break

    # Ensure normalized slack is computed if raw fields exist
    if "setup_slack" not in combined_metrics or not combined_metrics.get("setup_slack"):
        s = compute_setup_slack(combined_metrics.get("setup_wns"), combined_metrics.get("setup_ws"))
        if s is not None:
            combined_metrics["setup_slack"] = _normalise(s)

    if "hold_slack" not in combined_metrics or not combined_metrics.get("hold_slack"):
        h = compute_hold_slack(combined_metrics.get("hold_wns"), combined_metrics.get("hold_ws"))
        if h is not None:
            combined_metrics["hold_slack"] = _normalise(h)

    return combined_metrics, sources, all_field_sources, all_warnings


def _has_any_metric(metrics: dict) -> bool:
    """Check if we have at least one real numeric metric."""
    for key in METRIC_KEYS:
        val = metrics.get(key, "")
        if val not in ("", None):
            return True
    return False


# ---------------------------------------------------------------------------
# Decision logic
# ---------------------------------------------------------------------------

def decide_from_metrics(metrics: dict) -> str:
    """Generate a brief decision string from parsed metrics."""
    # Use normalized setup_slack for decisions
    setup_slack = _safe_float(metrics.get("setup_slack"))

    if setup_slack is None:
        return "Cannot determine: setup slack unavailable"

    if setup_slack < 0:
        return "Timing FAIL: increase CLOCK_PERIOD or optimize critical path"
    if setup_slack > 10:
        return "Timing MET with large margin: CLOCK_PERIOD can be reduced"
    if setup_slack > 0.5:
        return "Timing MET with reasonable margin"
    if setup_slack >= 0:
        return "Timing MET with very small margin"
    return "Timing MET"


# ---------------------------------------------------------------------------
# Missing fields and confidence
# ---------------------------------------------------------------------------

def compute_missing_fields(metrics: dict) -> str:
    """Return comma-separated list of metric fields that are empty."""
    missing = []
    for key in METRIC_KEYS:
        if metrics.get(key, "") in ("", None):
            missing.append(key)
    return ",".join(missing)


def compute_confidence(metrics: dict, field_sources: dict, warnings: list) -> str:
    """
    Return a confidence label based on evidence quality.

    Criteria:
    - high: structured JSON parsed, timing fields available, no parser errors.
    - medium: data from RPT or partial JSON, important metrics available.
    - low: few metrics parsed, heuristic matching, or parser warnings.
    - none: no valid metrics found.
    """
    total = len(METRIC_KEYS)
    filled = sum(1 for k in METRIC_KEYS if metrics.get(k, "") not in ("", None))

    if filled == 0:
        return "none"

    # Check if timing fields are available
    has_timing = (
        metrics.get("setup_wns", "") != "" or
        metrics.get("setup_ws", "") != "" or
        metrics.get("setup_slack", "") != ""
    )

    # Check source quality
    has_json_source = any("json:" in v for v in field_sources.values())
    has_errors = any("error" in w.lower() or "malformed" in w.lower() for w in warnings)

    if has_json_source and has_timing and not has_errors and filled >= total * 0.7:
        return "high"
    elif has_timing and filled >= total * 0.4:
        return "medium"
    elif filled > 0:
        return "low"
    return "none"


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def read_expected_runs() -> list:
    """Read runs from qor_runs.csv (dynamic discovery) with fixed defaults as fallback.

    Supports both planned fixed runs and dynamically generated auto-tune runs.
    When qor_runs.csv exists, uses it as the source of truth.
    Falls back to EXPECTED_RUNS only when metadata is unavailable.
    """
    runs = []
    seen_ids = set()

    # 1. Try loading from qor_runs.csv (supports dynamic run IDs)
    if QOR_RUNS.exists():
        try:
            with QOR_RUNS.open(newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    run_id = row.get("run_id", "").strip()
                    clock_period = row.get("clock_period", "").strip()
                    if run_id and clock_period and run_id not in seen_ids:
                        runs.append({
                            "run_id": run_id,
                            "clock_period": clock_period,
                            "report_paths": row.get("report_paths", ""),
                            "status": row.get("status", ""),
                        })
                        seen_ids.add(run_id)
        except (OSError, csv.Error):
            pass

    # 2. Ensure all EXPECTED_RUNS are present (fallback defaults)
    for run_id, clock_period in EXPECTED_RUNS:
        if run_id not in seen_ids:
            runs.append({"run_id": run_id, "clock_period": clock_period})
            seen_ids.add(run_id)

    return runs


def build_output_row(run: dict, explicit_map: dict) -> dict:
    """Build one output row for a run."""
    run_id = run["run_id"]
    clock_period = run["clock_period"]

    # If run has report_paths from qor_runs.csv, add them to explicit map dynamically
    report_paths_str = run.get("report_paths", "")
    if report_paths_str and run_id not in explicit_map:
        info = {}
        for rp in report_paths_str.split(";"):
            rp = rp.strip()
            if not rp:
                continue
            full_path = ROOT / rp
            if full_path.exists():
                if rp.endswith(".json"):
                    info["metrics_json"] = full_path
                elif rp.endswith(".rpt"):
                    info["summary_rpt"] = full_path
        if info:
            explicit_map[run_id] = info

    metrics, sources, field_sources, warnings = find_metrics_for_run(
        run_id, clock_period, explicit_map
    )
    has_metrics = _has_any_metric(metrics)

    # Remove internal fields
    corners = metrics.pop("_corners", {})

    row = {
        "run_id": run_id,
        "design": "pm32",
        "clock_period": clock_period,
        # Raw timing fields
        "setup_wns": metrics.get("setup_wns", ""),
        "setup_ws": metrics.get("setup_ws", ""),
        "setup_tns": metrics.get("setup_tns", ""),
        "hold_wns": metrics.get("hold_wns", ""),
        "hold_ws": metrics.get("hold_ws", ""),
        "hold_tns": metrics.get("hold_tns", ""),
        # Normalized slack
        "setup_slack": metrics.get("setup_slack", ""),
        "hold_slack": metrics.get("hold_slack", ""),
        # Area
        "area": metrics.get("area", ""),
        "utilization": metrics.get("utilization", ""),
        # Power
        "power_total": metrics.get("power_total", ""),
        "power_internal": metrics.get("power_internal", ""),
        "power_switching": metrics.get("power_switching", ""),
        "power_leakage": metrics.get("power_leakage", ""),
        # Violations
        "slew_violations": metrics.get("slew_violations", ""),
        "cap_violations": metrics.get("cap_violations", ""),
        "fanout_violations": metrics.get("fanout_violations", ""),
        # Routing
        "route_drc_errors": metrics.get("route_drc_errors", ""),
        "route_wirelength": metrics.get("route_wirelength", ""),
        "route_vias": metrics.get("route_vias", ""),
        "grt_wirelength": metrics.get("grt_wirelength", ""),
        "grt_vias": metrics.get("grt_vias", ""),
        "antenna_violations": metrics.get("antenna_violations", ""),
        # Congestion
        "congestion_overflow": metrics.get("congestion_overflow", ""),
        "congestion_status": metrics.get("congestion_status", NO_CONGESTION),
        # Multi-corner
        "worst_setup_corner": metrics.get("worst_setup_corner", ""),
        "worst_hold_corner": metrics.get("worst_hold_corner", ""),
        "timing_corners_count": metrics.get("timing_corners_count", ""),
        # Metadata
        "decision": decide_from_metrics(metrics) if has_metrics else "No metrics available",
        "source_files": ";".join(sources) if sources else "",
        "field_sources": json.dumps(field_sources) if field_sources else "",
        "missing_fields": compute_missing_fields(metrics),
        "confidence": compute_confidence(metrics, field_sources, warnings),
        "parser_warnings": ";".join(warnings) if warnings else "",
    }

    # Store corners for JSON output (not in CSV)
    row["_corners"] = corners

    return row


def main():
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)

    runs = read_expected_runs()
    explicit_map = load_explicit_mapping()
    rows = [build_output_row(run, explicit_map) for run in runs]

    # Write CSV (without internal _corners field)
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            csv_row = {k: v for k, v in row.items() if not k.startswith("_")}
            writer.writerow(csv_row)

    # Write JSON summary with multi-corner data
    json_runs = []
    for row in rows:
        corners = row.pop("_corners", {})
        json_run = {k: v for k, v in row.items() if not k.startswith("_")}
        if corners:
            json_run["timing_corners"] = corners
        json_runs.append(json_run)

    json_output = {
        "description": "Parsed QoR metrics for PM32 LibreLane experiments",
        "design": "pm32",
        "schema_version": "2.0",
        "notes": {
            "setup_wns": "Raw OpenROAD setup WNS (0 when no violation exists)",
            "setup_ws": "Raw OpenROAD setup worst slack (positive margin value)",
            "setup_slack": "Normalized: WNS if negative, else WS, else WNS (used for diagnosis)",
            "hold_wns": "Raw OpenROAD hold WNS (0 when no violation exists)",
            "hold_ws": "Raw OpenROAD hold worst slack (positive margin value)",
            "hold_slack": "Normalized: WNS if negative, else WS, else WNS (used for diagnosis)",
        },
        "runs": json_runs,
        "total_runs": len(json_runs),
        "runs_with_metrics": sum(1 for r in json_runs if r["confidence"] != "none"),
        "runs_without_metrics": sum(1 for r in json_runs if r["confidence"] == "none"),
    }

    with OUT_JSON.open("w", encoding="utf-8") as f:
        json.dump(json_output, f, indent=2)

    # Print summary
    print(f"Saved CSV: {OUT_CSV}")
    print(f"Saved JSON: {OUT_JSON}")
    print(f"Total rows: {len(rows)}")

    for row in rows:
        status = "METRICS FOUND" if row.get("confidence", "none") != "none" else "metrics not found"
        print(f"  {row['run_id']} (clock={row['clock_period']}): {status} [confidence={row.get('confidence', 'none')}]")


if __name__ == "__main__":
    main()
