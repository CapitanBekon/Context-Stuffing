"""
analyze.py — Results Analysis & IRS Statistics

Loads one or more result files produced by runner.py and computes:
  - Instruction Retention Score (IRS) per fill level and attack type
  - Token consumption summary (total, per fill level, estimated cost)
  - Injection success rate breakdown
  - Optional: exports a summary CSV ready to paste into the report

USAGE
─────
  # Analyse the most recent results file
  python analyze.py --input results/results_20260401_120000.json

  # Analyse all result files in the results/ folder
  python analyze.py --input results/

  # Export summary table to CSV (for report tables / charts)
  python analyze.py --input results/ --export-summary results/summary.csv
"""

import json
import csv
import os
import argparse
from pathlib import Path
from collections import defaultdict


# ─── Loading ──────────────────────────────────────────────────────────────────

def load_results(path: str) -> list[dict]:
    """
    Load result records from a JSON file or all JSON files in a directory.
    Skips any records with errors (error field is not None).
    """
    p = Path(path)
    files = []

    if p.is_dir():
        files = sorted(p.glob("results_*.json"))
        if not files:
            raise FileNotFoundError(f"No results_*.json files found in {path}")
    elif p.suffix == ".json":
        files = [p]
    elif p.suffix == ".csv":
        return _load_csv(p)
    else:
        raise ValueError(f"Unsupported path: {path}")

    records = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            data = json.load(fh)
        records.extend(data)

    print(f"Loaded {len(records)} records from {len(files)} file(s).")

    # Filter out error records for analysis
    valid = [r for r in records if r.get("irs") is not None and r.get("error") is None]
    errors = len(records) - len(valid)
    if errors:
        print(f"  Skipped {errors} records with errors (included in token totals).")
    return valid


def _load_csv(path: Path) -> list[dict]:
    records = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Coerce numeric fields
            for field in ("fill_pct", "irs", "actual_input_tokens", "actual_output_tokens",
                          "total_tokens_used", "classifier_in_tokens", "classifier_out_tokens",
                          "trial_num", "target_fill_tokens", "est_noise_tokens"):
                if row.get(field) not in (None, "", "None"):
                    try:
                        row[field] = float(row[field]) if "." in str(row[field]) else int(row[field])
                    except (ValueError, TypeError):
                        pass
            for field in ("injection_success",):
                if row.get(field) == "True":
                    row[field] = True
                elif row.get(field) == "False":
                    row[field] = False
            records.append(row)
    valid = [r for r in records if r.get("irs") is not None and not r.get("error")]
    return valid


# ─── IRS computation ──────────────────────────────────────────────────────────

def compute_irs_by_fill(records: list[dict]) -> dict:
    """
    Returns a dict mapping fill_pct (float) → {irs, success_rate, n_trials}.
    IRS = mean of per-trial IRS values (1 = held, 0 = bypassed).
    """
    grouped = defaultdict(list)
    for r in records:
        grouped[float(r["fill_pct"])].append(int(r["irs"]))

    result = {}
    for fill_pct in sorted(grouped.keys()):
        vals = grouped[fill_pct]
        irs = sum(vals) / len(vals)
        result[fill_pct] = {
            "irs":          round(irs, 4),
            "success_rate": round(1 - irs, 4),
            "n_trials":     len(vals),
            "n_bypassed":   sum(1 for v in vals if v == 0),
        }
    return result


def compute_irs_by_attack_type(records: list[dict]) -> dict:
    """Returns IRS broken down by attack_type and fill_pct."""
    grouped = defaultdict(lambda: defaultdict(list))
    for r in records:
        grouped[r["attack_type"]][float(r["fill_pct"])].append(int(r["irs"]))

    result = {}
    for atype, fill_data in grouped.items():
        result[atype] = {}
        for fill_pct in sorted(fill_data.keys()):
            vals = fill_data[fill_pct]
            result[atype][fill_pct] = {
                "irs":      round(sum(vals) / len(vals), 4),
                "n_trials": len(vals),
            }
    return result


def compute_irs_by_attack_id(records: list[dict]) -> dict:
    """Returns per-attack IRS aggregated across all fill levels."""
    grouped = defaultdict(list)
    for r in records:
        grouped[r["attack_id"]].append(int(r["irs"]))
    return {
        aid: {
            "irs":      round(sum(vals) / len(vals), 4),
            "n_trials": len(vals),
            "type":     next(r["attack_type"] for r in records if r["attack_id"] == aid),
        }
        for aid, vals in grouped.items()
    }


# ─── Token accounting ─────────────────────────────────────────────────────────

def compute_token_summary(records: list[dict]) -> dict:
    """
    Aggregate token consumption across all records.
    Includes classifier tokens (separate budget).
    """
    def safe_int(v):
        try:
            return int(v) if v is not None else 0
        except (TypeError, ValueError):
            return 0

    target_in  = sum(safe_int(r.get("actual_input_tokens"))  for r in records)
    target_out = sum(safe_int(r.get("actual_output_tokens")) for r in records)
    clf_in     = sum(safe_int(r.get("classifier_in_tokens")) for r in records)
    clf_out    = sum(safe_int(r.get("classifier_out_tokens"))for r in records)

    # Token breakdown per fill level
    by_fill = defaultdict(lambda: {"input": 0, "output": 0, "n": 0})
    for r in records:
        fp = float(r["fill_pct"])
        by_fill[fp]["input"]  += safe_int(r.get("actual_input_tokens"))
        by_fill[fp]["output"] += safe_int(r.get("actual_output_tokens"))
        by_fill[fp]["n"]      += 1

    return {
        "target_input_total":  target_in,
        "target_output_total": target_out,
        "classifier_input":    clf_in,
        "classifier_output":   clf_out,
        "grand_total_tokens":  target_in + target_out + clf_in + clf_out,
        "by_fill_level":       dict(sorted(by_fill.items())),
        "n_records":           len(records),
    }


# ─── Printers ─────────────────────────────────────────────────────────────────

def print_irs_table(irs_by_fill: dict) -> None:
    print("\n── Instruction Retention Score (IRS) by Fill Level ────────────")
    print(f"  {'Fill %':>8}  {'IRS':>6}  {'Success Rate':>14}  {'Bypassed':>10}  {'Trials':>8}")
    print("  " + "-" * 56)
    for fill_pct, stats in sorted(irs_by_fill.items()):
        print(
            f"  {fill_pct*100:7.0f}%  "
            f"{stats['irs']:6.3f}  "
            f"{stats['success_rate']*100:13.1f}%  "
            f"{stats['n_bypassed']:>10}  "
            f"{stats['n_trials']:>8}"
        )
    print()


def print_token_table(tok: dict) -> None:
    print("── Token Consumption Summary ───────────────────────────────────")
    print(f"  Target model  — Input : {tok['target_input_total']:>12,}")
    print(f"  Target model  — Output: {tok['target_output_total']:>12,}")
    print(f"  Classifier    — Input : {tok['classifier_input']:>12,}")
    print(f"  Classifier    — Output: {tok['classifier_output']:>12,}")
    print(f"  ─────────────────────────────────────────────")
    print(f"  Grand total           : {tok['grand_total_tokens']:>12,}")
    print(f"\n  Tokens by fill level:")
    print(f"  {'Fill %':>8}  {'Input':>12}  {'Output':>10}  {'Trials':>8}  {'Avg Input':>12}")
    print("  " + "-" * 60)
    for fp, d in sorted(tok["by_fill_level"].items()):
        avg_in = d["input"] // d["n"] if d["n"] else 0
        print(f"  {fp*100:7.0f}%  {d['input']:>12,}  {d['output']:>10,}  {d['n']:>8}  {avg_in:>12,}")
    print()


def print_attack_breakdown(irs_by_type: dict) -> None:
    print("── IRS by Attack Type × Fill Level ────────────────────────────")
    for atype, fill_data in sorted(irs_by_type.items()):
        print(f"\n  {atype}:")
        for fp, stats in sorted(fill_data.items()):
            bar = "█" * int((1 - stats["irs"]) * 20) + "░" * int(stats["irs"] * 20)
            print(f"    {fp*100:5.0f}%  IRS {stats['irs']:.3f}  [{bar}]  n={stats['n_trials']}")
    print()


# ─── Export ───────────────────────────────────────────────────────────────────

def export_summary_csv(irs_by_fill: dict, token_summary: dict, path: str) -> None:
    """
    Write a clean summary table to CSV — ready for import into Excel or
    for inclusion as a data table in the report.
    """
    rows = []
    for fill_pct, stats in sorted(irs_by_fill.items()):
        fill_data = token_summary["by_fill_level"].get(fill_pct, {})
        avg_in = fill_data.get("input", 0) // fill_data.get("n", 1)
        rows.append({
            "fill_pct":           f"{fill_pct*100:.0f}%",
            "irs":                stats["irs"],
            "success_rate":       stats["success_rate"],
            "n_trials":           stats["n_trials"],
            "n_bypassed":         stats["n_bypassed"],
            "avg_input_tokens":   avg_in,
        })

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Summary CSV exported → {path}")


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Analyse prompt injection experiment results")
    parser.add_argument(
        "--input", required=True,
        help="Path to a results JSON file or a directory containing results_*.json files"
    )
    parser.add_argument(
        "--export-summary", default=None,
        help="Optional path to export a summary CSV (e.g. results/summary.csv)"
    )
    args = parser.parse_args()

    records = load_results(args.input)
    if not records:
        print("No valid records found.")
        return

    irs_by_fill  = compute_irs_by_fill(records)
    irs_by_type  = compute_irs_by_attack_type(records)
    token_summary = compute_token_summary(records)

    print_irs_table(irs_by_fill)
    print_token_table(token_summary)
    print_attack_breakdown(irs_by_type)

    if args.export_summary:
        export_summary_csv(irs_by_fill, token_summary, args.export_summary)


if __name__ == "__main__":
    main()
