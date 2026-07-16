#!/usr/bin/env python3
"""Clean Unicode characters from Markdown files for Windows compatibility."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FILES = [
    "README.md",
    "docs/final_report.md",
    "docs/final_presentation.md",
    "docs/course_evidence_map.md",
    "docs/usability_report.md",
    "docs/evaluation_scenarios.md",
    "docs/assistant_demo.md",
    "docs/week1_results.md",
    "evaluation/results.md",
]

REPLACEMENTS = [
    ("\u2014", "-"),    # em dash
    ("\u2013", "-"),    # en dash
    ("\u2012", "-"),    # figure dash
    ("\u00d7", "x"),    # multiplication sign
    ("\u2192", "->"),   # right arrow
    ("\u2190", "<-"),   # left arrow
    ("\u21d2", "=>"),   # double right arrow
    ("\u00a7", "Section "),  # section sign
    ("\u201c", '"'),    # left double curly quote
    ("\u201d", '"'),    # right double curly quote
    ("\u2018", "'"),    # left single curly quote
    ("\u2019", "'"),    # right single curly quote
    ("\u00a0", " "),    # non-breaking space
    ("\u25bc", "v"),    # down triangle
    ("\u25b2", "^"),    # up triangle
    ("\u25b6", ">"),    # right triangle
    ("\u25ba", ">"),    # right pointer
    ("\u2502", "|"),    # box drawing vertical
    ("\u2500", "-"),    # box drawing horizontal
    ("\u250c", "+"),    # box drawing corner
    ("\u2510", "+"),    # box drawing corner
    ("\u2514", "+"),    # box drawing corner
    ("\u2518", "+"),    # box drawing corner
    ("\u251c", "+"),    # box drawing tee
    ("\u2524", "+"),    # box drawing tee
    ("\u252c", "+"),    # box drawing tee
    ("\u2534", "+"),    # box drawing tee
    ("\u253c", "+"),    # box drawing cross
    ("\u2022", "-"),    # bullet
    ("\u2265", ">="),   # greater than or equal
    ("\u2264", "<="),   # less than or equal
    ("\u00b5m\u00b2", "um^2"),  # micrometer squared (combined)
    ("\u00b5", "u"),    # micro sign
    ("\u00b2", "^2"),   # superscript 2
    ("\u00b3", "^3"),   # superscript 3
]


def main():
    for filepath in FILES:
        p = ROOT / filepath
        if not p.exists():
            print(f"SKIP (not found): {filepath}")
            continue

        text = p.read_text(encoding="utf-8")
        original = text

        for old, new in REPLACEMENTS:
            text = text.replace(old, new)

        if text != original:
            p.write_text(text, encoding="utf-8")
            print(f"CLEANED: {filepath}")
        else:
            print(f"OK (no changes needed): {filepath}")


if __name__ == "__main__":
    main()
