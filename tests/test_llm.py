#!/usr/bin/env python3
"""
Unit tests for scripts/llm_client.py

Tests the optional LLM layer: activation logic, context building,
API failure handling, and data privacy (no raw content sent).
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from llm_client import (
    should_use_llm,
    is_llm_available,
    build_context,
    call_llm,
)


class TestShouldUseLlm(unittest.TestCase):
    """Tests for should_use_llm activation logic."""

    @patch.dict(os.environ, {"USE_LLM": "true", "OPENAI_API_KEY": "sk-test123"})
    @patch("llm_client.sys")
    def test_should_use_llm_env_var(self, mock_sys):
        """USE_LLM=true with API key → returns True."""
        mock_sys.argv = ["assistant.py"]
        # Re-import to pick up patched env
        result = should_use_llm()
        self.assertTrue(result)

    @patch.dict(os.environ, {"USE_LLM": "true"}, clear=False)
    def test_should_use_llm_no_key(self):
        """No API key → returns False (with warning)."""
        # Remove API keys if present
        env_copy = os.environ.copy()
        env_copy.pop("OPENAI_API_KEY", None)
        env_copy.pop("LLM_API_KEY", None)
        with patch.dict(os.environ, env_copy, clear=True):
            result = should_use_llm()
            self.assertFalse(result)


class TestBuildContext(unittest.TestCase):
    """Tests for build_context structured data formatting."""

    def _make_row(self, **kwargs):
        defaults = {
            "run_id": "clock_25",
            "clock_period": "25",
            "setup_wns": "0",
            "hold_wns": "0",
            "setup_tns": "0",
            "hold_tns": "0",
            "area": "15942.8",
            "utilization": "0.85",
            "power_total": "0.00134",
            "slew_violations": "247",
            "cap_violations": "0",
            "fanout_violations": "1",
            "source_files": "results/important_reports/metrics.json",
            "confidence": "high",
        }
        defaults.update(kwargs)
        return defaults

    def test_build_context_structured_only(self):
        """Context contains only structured data, no raw logs."""
        rows = [self._make_row()]
        diagnoses = [
            {
                "run_id": "clock_25",
                "severity": "info",
                "category": "setup_timing",
                "finding": "Setup timing met with large margin",
            }
        ]
        context = build_context(rows, diagnoses, "What is the best run?")
        # Should contain structured metric names
        self.assertIn("clock_25", context)
        self.assertIn("setup_wns=0", context)
        # Should NOT contain raw file contents or log lines
        self.assertNotIn("#!/", context)
        self.assertNotIn("BEGIN", context)

    def test_build_context_missing_values(self):
        """Missing metrics shown as 'No metrics available'."""
        rows = [
            {
                "run_id": "clock_15",
                "clock_period": "15",
                "confidence": "none",
                "setup_wns": "",
                "hold_wns": "",
                "setup_tns": "",
                "hold_tns": "",
                "area": "",
                "utilization": "",
                "power_total": "",
                "slew_violations": "",
                "cap_violations": "",
                "fanout_violations": "",
                "source_files": "",
            }
        ]
        context = build_context(rows, [], "summary")
        self.assertIn("No metrics available", context)

    def test_llm_no_raw_content(self):
        """Verify no file contents are sent directly in context."""
        rows = [self._make_row()]
        diagnoses = []
        context = build_context(rows, diagnoses, "explain timing")
        # Context should not contain typical raw file indicators
        self.assertNotIn(".v", context)  # No verilog file content
        self.assertNotIn("module ", context)  # No RTL
        self.assertNotIn("endmodule", context)
        # Should only have structured key=value pairs
        self.assertIn("source=", context)


class TestCallLlm(unittest.TestCase):
    """Tests for call_llm API interaction."""

    @patch("llm_client.urllib.request.urlopen")
    def test_call_llm_api_failure(self, mock_urlopen):
        """Mock urllib error → returns None gracefully."""
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        result = call_llm(
            "What is the best run?",
            "Some context",
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
        )
        self.assertIsNone(result)


class TestIsLlmAvailable(unittest.TestCase):
    """Tests for is_llm_available check."""

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test123"})
    def test_available_with_key(self):
        """API key present → available."""
        result = is_llm_available()
        self.assertTrue(result)

    def test_not_available_without_key(self):
        """No API key → not available."""
        env_copy = os.environ.copy()
        env_copy.pop("OPENAI_API_KEY", None)
        env_copy.pop("LLM_API_KEY", None)
        with patch.dict(os.environ, env_copy, clear=True):
            result = is_llm_available()
            self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
