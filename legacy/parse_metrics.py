from pathlib import Path
import csv
import re


def parse_summary_rpt(path: Path) -> dict:
    text = path.read_text(errors="ignore")

    result = {
        "hold_wns": "",
        "hold_tns": "",
        "setup_wns": "",
        "setup_tns": "",
        "max_cap_violations": "",
        "max_slew_violations": "",
    }

    for line in text.splitlines():
        if "Overall" in line:
            numbers = re.findall(r"-?\d+\.\d+|-?\d+", line)
            if len(numbers) >= 7:
                result["hold_wns"] = numbers[0]
                result["hold_tns"] = numbers[2]
                result["setup_wns"] = numbers[5]
                result["setup_tns"] = numbers[7] if len(numbers) > 7 else ""
                result["max_cap_violations"] = numbers[-2]
                result["max_slew_violations"] = numbers[-1]
            break

    return result


def append_metrics(csv_path: Path, row: dict) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "run_id",
        "clock_period",
        "setup_wns",
        "hold_wns",
        "setup_tns",
        "hold_tns",
        "max_cap_violations",
        "max_slew_violations",
        "area",
        "comment",
    ]

    exists = csv_path.exists()

    with csv_path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", required=True, help="Path to LibreLane summary.rpt")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--clock-period", required=True)
    parser.add_argument("--output", default="results/metrics.csv")
    args = parser.parse_args()

    metrics = parse_summary_rpt(Path(args.summary))

    row = {
        "run_id": args.run_id,
        "clock_period": args.clock_period,
        "setup_wns": metrics["setup_wns"],
        "hold_wns": metrics["hold_wns"],
        "setup_tns": metrics["setup_tns"],
        "hold_tns": metrics["hold_tns"],
        "max_cap_violations": metrics["max_cap_violations"],
        "max_slew_violations": metrics["max_slew_violations"],
        "area": "",
        "comment": "parsed automatically",
    }

    append_metrics(Path(args.output), row)
    print(row)
