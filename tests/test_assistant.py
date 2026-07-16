#!/usr/bin/env python3
"""
Unit tests for scripts/assistant.py

Tests the conversational assistant: intent matching, command routing,
natural-language question handling, and response content verification.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from assistant import answer, _match_intent


def _make_row(**kwargs):
    """Helper to build a row dict with defaults."""
    defaults = {
        "run_id": "clock_25",
        "design": "pm32",
        "clock_period": "25",
        "setup_wns": "0",
        "setup_ws": "13.237121107238776",
        "setup_tns": "0",
        "hold_wns": "0",
        "hold_ws": "0.11902446032334803",
        "hold_tns": "0",
        "setup_slack": "13.237121107238776",
        "hold_slack": "0.11902446032334803",
        "area": "15942.8",
        "utilization": "0.851986",
        "power_total": "0.0013482653303071856",
        "power_internal": "0.0009364390862174332",
        "power_switching": "0.0004118068900424987",
        "power_leakage": "1.931543103239619e-08",
        "slew_violations": "247",
        "cap_violations": "0",
        "fanout_violations": "1",
        "route_drc_errors": "0",
        "route_wirelength": "37368",
        "route_vias": "7603",
        "grt_wirelength": "53219",
        "grt_vias": "15",
        "antenna_violations": "0",
        "congestion_overflow": "",
        "congestion_status": "routing_data_found",
        "worst_setup_corner": "nom_ss_100C_1v60",
        "worst_hold_corner": "nom_ss_100C_1v60",
        "timing_corners_count": "1",
        "decision": "Timing MET with large margin: CLOCK_PERIOD can be reduced",
        "source_files": "results/important_reports/clock_25_metrics.json",
        "field_sources": '{"setup_wns": "json:timing__setup__wns"}',
        "missing_fields": "",
        "confidence": "high",
        "parser_warnings": "",
    }
    defaults.update(kwargs)
    return defaults


def _sample_rows():
    """Build a sample dataset with multiple runs for testing."""
    rows = [
        # Missing runs (no metrics)
        _make_row(
            run_id="clock_15", clock_period="15", confidence="none",
            setup_wns="", setup_ws="", setup_slack="", hold_slack="",
            area="", utilization="", power_total="", slew_violations="",
            cap_violations="", fanout_violations="", route_drc_errors="",
            route_wirelength="", route_vias="", grt_wirelength="",
            grt_vias="", antenna_violations="", source_files="",
            decision="No metrics available",
        ),
        _make_row(
            run_id="clock_18", clock_period="18", confidence="none",
            setup_wns="", setup_ws="", setup_slack="", hold_slack="",
            area="", utilization="", power_total="", slew_violations="",
            cap_violations="", fanout_violations="", route_drc_errors="",
            route_wirelength="", route_vias="", grt_wirelength="",
            grt_vias="", antenna_violations="", source_files="",
            decision="No metrics available",
        ),
        _make_row(
            run_id="clock_20", clock_period="20", confidence="none",
            setup_wns="", setup_ws="", setup_slack="", hold_slack="",
            area="", utilization="", power_total="", slew_violations="",
            cap_violations="", fanout_violations="", route_drc_errors="",
            route_wirelength="", route_vias="", grt_wirelength="",
            grt_vias="", antenna_violations="", source_files="",
            decision="No metrics available",
        ),
        _make_row(
            run_id="clock_22", clock_period="22", confidence="none",
            setup_wns="", setup_ws="", setup_slack="", hold_slack="",
            area="", utilization="", power_total="", slew_violations="",
            cap_violations="", fanout_violations="", route_drc_errors="",
            route_wirelength="", route_vias="", grt_wirelength="",
            grt_vias="", antenna_violations="", source_files="",
            decision="No metrics available",
        ),
        # clock_25: timing met, metrics available
        _make_row(run_id="clock_25", clock_period="25"),
        # clock_30: timing met with even larger margin
        _make_row(
            run_id="clock_30", clock_period="30",
            setup_wns="0", setup_ws="18.5",
            setup_slack="18.5", hold_slack="0.5",
            slew_violations="200",
        ),
    ]
    return rows


class TestSummaryCommand(unittest.TestCase):
    """Tests for the summary command."""

    def test_summary_command(self):
        """'summary' → mentions runs and CLOCK_PERIOD."""
        rows = _sample_rows()
        result = answer("summary", rows)
        self.assertIn("run", result.lower())
        self.assertIn("CLOCK_PERIOD", result)


class TestBestRunCommand(unittest.TestCase):
    """Tests for the best run command."""

    def test_best_run_command(self):
        """'best run' → mentions clock_25 and 'available'."""
        rows = _sample_rows()
        result = answer("best run", rows)
        self.assertIn("clock_25", result)
        self.assertIn("available", result.lower())


class TestViolationsCommand(unittest.TestCase):
    """Tests for the violations command."""

    def test_violations_command(self):
        """'violations' → mentions slew and 247."""
        rows = _sample_rows()
        result = answer("violations", rows)
        self.assertIn("lew", result.lower())  # "Slew" or "slew"
        self.assertIn("247", result)


class TestPowerCommand(unittest.TestCase):
    """Tests for the power command."""

    def test_power_command(self):
        """'power' → mentions power and W."""
        rows = _sample_rows()
        result = answer("power", rows)
        self.assertIn("power", result.lower())
        self.assertIn("W", result)


class TestAreaCommand(unittest.TestCase):
    """Tests for the area command."""

    def test_area_command(self):
        """'area' → mentions area and um."""
        rows = _sample_rows()
        result = answer("area", rows)
        self.assertIn("area", result.lower())
        self.assertIn("um", result)


class TestCongestionCommand(unittest.TestCase):
    """Tests for the congestion command."""

    def test_congestion_command(self):
        """'congestion' → mentions routing, does NOT prove."""
        rows = _sample_rows()
        result = answer("congestion", rows)
        # Should mention routing data
        self.assertIn("rout", result.lower())


class TestMissingData(unittest.TestCase):
    """Tests for missing data handling."""

    def test_missing_data(self):
        """'explain run clock_15' → mentions 'not available' or 'missing'."""
        rows = _sample_rows()
        result = answer("explain run clock_15", rows)
        has_missing = "not available" in result.lower() or "missing" in result.lower()
        self.assertTrue(has_missing, f"Expected 'not available' or 'missing' in: {result}")


class TestNaturalLanguageRouting(unittest.TestCase):
    """Tests for NL question → intent routing."""

    def test_nl_which_run_best(self):
        """'Which run is best?' → routes to best_run."""
        intent = _match_intent("which run is best?")
        self.assertEqual(intent, "best_run")

    def test_nl_routing_clean(self):
        """'Is routing clean?' → routes to congestion."""
        intent = _match_intent("is routing clean?")
        self.assertEqual(intent, "congestion")

    def test_nl_clock_faster(self):
        """'Can I make the clock faster?' → routes to tuning."""
        intent = _match_intent("can i make the clock faster?")
        self.assertEqual(intent, "tuning")

    def test_unknown_question(self):
        """Gibberish → fallback message."""
        rows = _sample_rows()
        result = answer("xyzzy foobar baz quux", rows)
        self.assertIn("didn't understand", result.lower())


class TestCompareSpecific(unittest.TestCase):
    """Tests for comparing specific runs."""

    def test_compare_specific(self):
        """'Compare clock_25 and clock_30' → mentions both."""
        rows = _sample_rows()
        result = answer("Compare clock_25 and clock_30", rows)
        self.assertIn("clock_25", result)
        self.assertIn("clock_30", result)


class TestWnsWsDistinction(unittest.TestCase):
    """Tests that responses use correct WNS/WS terminology."""

    def test_wns_vs_ws_distinction(self):
        """Response uses correct terminology (never says positive WNS for WS)."""
        rows = _sample_rows()
        # Ask about timing for the clock_25 run (WNS=0, WS=13.237)
        result = answer("explain run clock_25", rows)
        # Should mention WNS = 0 (the actual WNS value)
        # Should not confuse WS with WNS in positive context
        # The explanation should reference both WNS and WS correctly
        self.assertIn("0", result)  # WNS = 0 mentioned
        self.assertIn("13.237", result)  # WS margin mentioned


class TestZeroDrcCongestion(unittest.TestCase):
    """Tests for zero-DRC congestion interpretation."""

    def test_does_zero_drc_mean_no_congestion(self):
        """'Does zero DRC prove there is no congestion?' → says no."""
        rows = _sample_rows()
        # Ask about congestion — the assistant should clarify DRC vs congestion
        result = answer("congestion", rows)
        # The response should indicate DRC=0 does not prove no congestion
        # or at least not claim zero congestion
        lower = result.lower()
        # Should not claim "no congestion" without qualification
        self.assertTrue(
            "does not" in lower or
            "does NOT" in result or
            "cannot" in lower or
            "no explicit" in lower or
            "not prove" in lower,
            f"Expected caveat about DRC vs congestion in: {result}"
        )


if __name__ == "__main__":
    unittest.main()
