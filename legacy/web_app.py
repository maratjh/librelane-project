#!/usr/bin/env python3
"""
Lightweight Streamlit web UI for the QoR Debugging Assistant.

Usage:
    streamlit run scripts/web_app.py

Requires: pip install streamlit pandas
"""

import sys
from pathlib import Path

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import streamlit as st
    import pandas as pd
except ImportError:
    print("Streamlit and pandas are required for the web UI.")
    print("Install with: pip install streamlit pandas")
    print("The CLI assistant (scripts/assistant.py) works without these.")
    sys.exit(1)

from assistant import load_metrics, answer


def main():
    st.set_page_config(page_title="QoR Debugging Assistant", page_icon="🔧")
    st.title("QoR Debugging Assistant")
    st.caption("Domain-specific assistant for LibreLane/OpenROAD QoR analysis")

    # Load metrics
    rows = load_metrics()

    # Sidebar with metrics overview
    with st.sidebar:
        st.header("Parsed Metrics")
        if rows:
            st.metric("Total runs", len(rows))
            has_data = sum(1 for r in rows if r.get("confidence") != "none")
            st.metric("Runs with data", has_data)
            st.metric("Runs missing data", len(rows) - has_data)
        else:
            st.warning("No metrics loaded. Run parse_existing_runs.py first.")

        st.divider()
        st.subheader("Quick Commands")
        quick_cmds = [
            "summary", "best run", "violations",
            "congestion", "power", "area",
        ]
        for cmd in quick_cmds:
            if st.button(cmd, key=f"btn_{cmd}"):
                st.session_state["question"] = cmd

    # Main chat area
    if "question" not in st.session_state:
        st.session_state["question"] = ""

    question = st.text_input(
        "Ask a QoR question:",
        value=st.session_state.get("question", ""),
        placeholder="e.g., Which run is best? / Is routing clean? / What should I tune?",
    )

    if st.button("Ask", type="primary") or (question and question != st.session_state.get("last_q", "")):
        if question.strip():
            st.session_state["last_q"] = question
            response = answer(question, rows)
            if response == "EXIT":
                st.info("Type a question to continue.")
            else:
                st.code(response, language=None)

    # Metrics table
    with st.expander("View parsed metrics table"):
        if rows:
            display_cols = [
                "run_id", "clock_period", "setup_wns", "hold_wns",
                "area", "power_total", "slew_violations", "confidence",
            ]
            df = pd.DataFrame(rows)
            available_cols = [c for c in display_cols if c in df.columns]
            st.dataframe(df[available_cols], use_container_width=True)
        else:
            st.info("No metrics available.")

    # Example questions
    with st.expander("Example questions"):
        st.markdown("""
- Which run is best?
- Why is timing failing?
- Can I make the clock faster?
- Is routing clean?
- Are there slew violations?
- How much power does it consume?
- What is the area?
- Why is clock_15 empty?
- Compare clock_25 and clock_30
        """)


if __name__ == "__main__":
    main()
