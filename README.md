# LibreLane Conversational QoR Debugging Agent

**Course:** 088949 - Advanced Computer Architectures (Politecnico di Milano, 2025-26)  
**Project ID:** ID10 - Conversational QoR Debugging Agent for Open ASIC Flows  
**Professor:** Christian Pilato  
**Authors:** Marat Yerkebayev (11120363), Aigerim Turazhanova (11120390)

This repository implements a conversational assistant that parses synthesis and place-and-route reports from LibreLane/OpenROAD, extracts structured QoR metrics, applies rule-based diagnosis, and explains timing/congestion/area/power issues in natural language.

## Architecture

```text
PM32 RTL + config.json
        |
        v
run_experiments.py --> results/qor_runs.csv
        |
        v
LibreLane/OpenROAD report artifacts
        |
        v
parse_existing_runs.py --> results/qor_metrics.csv
                           results/parsed_summary.json
        |
        v
rule_engine.py --> structured diagnoses
        |
        v
assistant.py --> conversational CLI answers
        |
        v (optional)
llm_client.py --> LLM-backed explanations
```

**Design principle:** Deterministic parsing and rule-based analysis provide reliability and traceability. The LLM is an optional explanation layer over structured data and never invents metrics.

## Key Technical Decisions

1. **WNS vs WS separation** - OpenROAD reports `timing__setup__wns` (0 when no violation) and `timing__setup__ws` (positive margin). These are stored separately. A normalized `setup_slack` field is computed for diagnosis logic.
2. **No metric fabrication** - missing data is reported honestly.
3. **Fastest setup-clean, not best QoR** - the assistant selects the fastest setup-clean run among available data, but clarifies this is not necessarily the best overall QoR run (electrical violations, hold timing, routing must also be considered).
4. **Priority-ordered recommendations** - clock optimization is only recommended when no blocking issues exist. The hierarchy checks: DRC errors > hold failure > setup failure > slew/cap/fanout > congestion > utilization > timing margin.
5. **QoR classification and Pareto analysis** - runs are classified as setup-clean, electrically-clean, and fully QoR-clean. Pareto-optimal candidates are identified across timing, power, area, and violations.
6. **Congestion distinction** - zero final DRC errors does NOT prove zero congestion. Only explicit overflow metrics prove congestion levels.
7. **Deterministic parsing first** - exact key matching and JSON extraction, not LLM guessing.
8. **Rule-based diagnosis** - explainable, traceable findings with configurable severity thresholds.
9. **Evidence-based answers** - every claim cites source files.
10. **Optional LLM with numerical validation** - works without API keys; LLM only explains structured data; a post-response validator rejects unsupported numerical claims.
11. **Repository-relative paths** - all output uses POSIX relative paths for cross-platform reproducibility.

## Repository Structure

```text
.
+-- README.md
+-- requirements.txt
+-- designs/
|   +-- pm32/
|       +-- pm32.v, spm.v           (RTL)
|       +-- config.json              (base config)
|       +-- config_clock*.json       (per-clock configs)
+-- scripts/
|   +-- run_experiments.py           (automation and metadata)
|   +-- parse_existing_runs.py       (metric extraction)
|   +-- rule_engine.py               (diagnosis logic)
|   +-- assistant.py                 (conversational CLI)
|   +-- llm_client.py                (optional LLM layer)
|   +-- auto_tune.py                 (iterative tuning)
|   +-- tune_rules.py                (clock tuning rules)
|   +-- validate_project.py          (full project validation)
+-- results/
|   +-- qor_runs.csv                 (experiment metadata)
|   +-- qor_metrics.csv              (parsed QoR metrics)
|   +-- parsed_summary.json          (structured JSON output)
|   +-- environment.json             (reproducibility metadata)
|   +-- important_reports/
|       +-- metrics_clock11.json     (OpenROAD metrics, clock_11)
|       +-- metrics_clock12.json     (OpenROAD metrics, clock_12)
|       +-- metrics_run1.json        (OpenROAD metrics, clock_25)
|       +-- metrics_run2.json        (OpenROAD metrics, clock_30)
|       +-- summary_clock11.rpt      (timing table, clock_11)
|       +-- summary_clock12.rpt      (timing table, clock_12)
|       +-- summary_run1.rpt         (timing table, clock_25)
|       +-- summary_run2.rpt         (timing table, clock_30)
|       +-- run_mapping.json         (file-to-run mapping)
+-- tests/
|   +-- test_parser.py               (parser unit tests)
|   +-- test_rule_engine.py          (rule engine unit tests)
|   +-- test_assistant.py            (assistant unit tests)
|   +-- test_llm.py                  (LLM mock tests)
|   +-- test_integration.py          (integration tests)
|   +-- test_dynamic.py              (dynamic discovery and formatting tests)
|   +-- test_qor_priority.py         (recommendation priority, QoR classification, Pareto, LLM validation)
+-- evaluation/
|   +-- scenarios.json               (24 PM32 regression scenarios)
|   +-- evaluate.py                  (automated evaluator with structured checks)
|   +-- evaluate_generalization.py   (18 synthetic general-logic cases)
|   +-- results.md                   (evaluation results)
+-- docs/
|   +-- FinalReport.pdf
|   +-- UsabilityReport.pdf
|   +-- ReproducibilityInformation.pdf
|   +-- conversational_qor_debugging_agent.pptx
+-- legacy/                          (deprecated scripts, not in main workflow)
```

## Quick Start

```bash
# 1. Parse existing reports
python scripts/parse_existing_runs.py

# 2. Run the assistant (one-shot examples)
python scripts/assistant.py summary
python scripts/assistant.py "fastest setup-clean run"
python scripts/assistant.py "what should be fixed first?"
python scripts/assistant.py "show Pareto candidates"
python scripts/assistant.py "Does zero DRC prove there is no congestion?"

# 3. Run the assistant (interactive)
python scripts/assistant.py

# 4. Run unit tests
python -m unittest discover -s tests -v

# 5. Run evaluation (24 PM32 regression scenarios + 18 general logic cases)
python evaluation/evaluate.py
python evaluation/evaluate_generalization.py

# 6. Run full project validation
python scripts/validate_project.py
```

## Supported Commands

| Command | Description |
|---------|-------------|
| `summary` | Overall QoR summary |
| `best run` | Fastest setup-clean run (among available data) |
| `qor status` | QoR classification, Pareto candidates |
| `what should be fixed first` | Priority-ordered blocking issues |
| `why timing bad` | Diagnose timing failures |
| `explain run <id>` | Detailed analysis of one run |
| `compare runs` | Side-by-side comparison |
| `compare <id1> <id2>` | Compare specific runs |
| `what should I tune` | Clock period recommendation |
| `show metrics` | Display all parsed metrics |
| `violations` | Electrical violation analysis |
| `power` | Power breakdown |
| `area` | Area and utilization |
| `congestion` | Congestion status (DRC vs overflow distinction) |
| `show Pareto candidates` | Non-dominated runs across metrics |
| `explain all` | Full explanation of all runs |
| `help` | Show command list |
| `exit` | Exit the assistant |

## Optional LLM Mode

The assistant works fully offline with rule-based logic. To enable optional LLM explanations:

```bash
export OPENAI_API_KEY="your-key"
python scripts/assistant.py --llm
```

Or:
```bash
export USE_LLM=true
export OPENAI_API_KEY="your-key"
python scripts/assistant.py
```

The LLM receives only structured metrics and diagnoses - never raw logs. A post-response numerical validator rejects unsupported numeric claims and falls back to the deterministic answer.

## Experiment Setup

Ten clock configurations were explored for the PM32 benchmark using LibreLane v3.0.3. Four have complete report artifacts, two failed during execution, and four remain config-only.

| Run ID | CLOCK_PERIOD (ns) | Report artifacts | Status |
|--------|-------------------|------------------|--------|
| clock_8 | 8 | Not available (flow failed) | execution_failed |
| clock_10 | 10 | Not available (flow failed) | execution_failed |
| clock_11 | 11 | Available (timing met, setup slack=1.237 ns) | completed |
| clock_12 | 12 | Available (timing met, setup slack=3.237 ns) | completed |
| clock_15 | 15 | Not available | config_only |
| clock_18 | 18 | Not available | config_only |
| clock_20 | 20 | Not available | config_only |
| clock_22 | 22 | Not available | config_only |
| clock_25 | 25 | Available (timing met, setup slack=13.237 ns) | completed |
| clock_30 | 30 | Available (timing met, setup slack=17.237 ns) | completed |

- `execution_failed`: LibreLane was executed but the flow could not converge (timing too aggressive).
- `config_only`: configuration file exists but no execution was attempted.
- `completed`: full physical flow finished with preserved report artifacts.

The four completed runs span from borderline (1.237 ns margin at clock_11) to very relaxed (17.237 ns at clock_30). The fastest setup-clean run is `clock_11`.

## WNS vs WS Technical Note

OpenROAD reports two timing metrics:
- `timing__setup__wns` = 0 (no negative violation exists)
- `timing__setup__ws` = 13.237 (positive worst-slack margin)

This project stores both separately and computes a normalized `setup_slack`:
- If WNS < 0: `setup_slack = WNS` (timing failure)
- If WNS >= 0 and WS available: `setup_slack = WS` (positive margin)
- If only WNS available: `setup_slack = WNS`

The assistant never displays positive WS as "positive WNS."

## Validation

```bash
python scripts/validate_project.py
```

Expected output:
```text
[PASS] Python compilation (all scripts)
[PASS] Unit tests: 110 tests
[PASS] Parser integration
[PASS] QoR schema validation
[PASS] Metrics semantic validation
[PASS] Conversational scenarios: 24/24
[PASS] No absolute local paths
[PASS] Documentation consistency
[PASS] Experiment status validation
[PASS] Dynamic run discovery (auto_tune_1)

Total: 10/10 checks passed.
Project validation completed successfully.
```

Additionally:
```text
General logic cases: 18/18 (via evaluate_generalization.py)
```

## Requirements

- Python 3.8+
- No third-party packages required (stdlib only)
- LibreLane/OpenROAD only needed for running actual ASIC flows
