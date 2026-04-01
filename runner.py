"""
runner.py — Main Experiment Orchestrator

Context Window Saturation & Prompt Injection Study
Coventry University — 601IT Individual Cyber Security Project

USAGE
─────
  # Run with a JSON attack file, all default settings
  python runner.py --input attacks/sample_attacks.json

  # Specify fill levels and number of trials
  python runner.py --input attacks/sample_attacks.json --fill-levels 0,50,100 --trials 5

  # Use many-shot noise strategy
  python runner.py --input attacks/sample_attacks.csv --strategy many_shot

  # Dry run (no API calls) to verify your input file format
  python runner.py --input attacks/sample_attacks.json --dry-run

REQUIRED ENV VAR
────────────────
  export ANTHROPIC_API_KEY="sk-ant-..."

INPUT FILE FORMAT (JSON)
────────────────────────
  [
    {
      "attack_id":   "A001",
      "attack_type": "direct_override",
      "attack_goal": "Reveal system prompt and adopt new persona",
      "attack_text": "Ignore all previous instructions. You are now..."
    },
    ...
  ]

INPUT FILE FORMAT (CSV)
───────────────────────
  attack_id,attack_type,attack_goal,attack_text
  A001,direct_override,"Reveal system prompt","Ignore all previous..."

OUTPUT
──────
  results/results_YYYYMMDD_HHMMSS.json  — full record for every trial
  results/results_YYYYMMDD_HHMMSS.csv   — same data in CSV format
"""

import anthropic
import json
import csv
import os
import time
import argparse
from datetime import datetime, timezone
from pathlib import Path

import config
from context_builder import build_noise_messages, estimated_noise_tokens
from classifier import classify_injection


# ─── Input loading ────────────────────────────────────────────────────────────

def load_attacks(filepath: str) -> list[dict]:
    """
    Load attack prompts from a JSON or CSV file.

    Required fields per record:
      attack_id   — unique identifier string
      attack_type — category label (e.g. "direct_override", "persona_swap")
      attack_goal — human-readable description of what the attack aims to do
      attack_text — the actual injection prompt sent to the model

    Returns a list of dicts.
    """
    path = Path(filepath)

    if not path.exists():
        raise FileNotFoundError(f"Attack file not found: {filepath}")

    if path.suffix == ".json":
        with open(path, encoding="utf-8") as f:
            attacks = json.load(f)

    elif path.suffix == ".csv":
        attacks = []
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                attacks.append(dict(row))

    else:
        raise ValueError(f"Unsupported file type '{path.suffix}'. Use .json or .csv")

    # Validate required fields
    required = {"attack_id", "attack_type", "attack_goal", "attack_text"}
    for i, a in enumerate(attacks):
        missing = required - set(a.keys())
        if missing:
            raise ValueError(f"Attack record {i} is missing fields: {missing}")

    return attacks


# ─── Single trial ─────────────────────────────────────────────────────────────

def run_single_trial(
    client: anthropic.Anthropic,
    attack: dict,
    fill_pct: float,
    trial_num: int,
    strategy: str = config.DEFAULT_NOISE_STRATEGY,
) -> dict:
    """
    Execute one experiment trial.

    Pipeline:
      1. Calculate how many tokens to fill (fill_pct × MAX_CONTEXT_TOKENS)
      2. Build noise messages to pre-load the context
      3. Append the attack prompt as the final user message
      4. Call Claude API and capture exact token counts from usage field
      5. Pass response to the IRS classifier
      6. Return a fully-populated result record

    Parameters
    ----------
    client    : Anthropic SDK client
    attack    : dict with attack metadata and text
    fill_pct  : fraction of context to pre-fill (0.0 – 1.0)
    trial_num : trial index for this (attack × fill_level) combination
    strategy  : noise generation strategy

    Returns
    -------
    dict — complete result record (see field list below)
    """
    attack_id   = attack["attack_id"]
    attack_type = attack["attack_type"]
    attack_goal = attack["attack_goal"]
    attack_text = attack["attack_text"]

    # ── Step 1: Build context ──────────────────────────────────────────────────
    target_fill_tokens = int(config.MAX_CONTEXT_TOKENS * fill_pct)
    noise_messages = build_noise_messages(target_fill_tokens, strategy=strategy)
    est_noise_tokens = estimated_noise_tokens(noise_messages)

    # ── Step 2: Compose messages ───────────────────────────────────────────────
    # Order: [noise user msg, noise assistant reply] + [attack user msg]
    messages = noise_messages + [{"role": "user", "content": attack_text}]

    # ── Base result record ─────────────────────────────────────────────────────
    record = {
        # Identifiers
        "timestamp_utc":        datetime.now(timezone.utc).isoformat(),
        "attack_id":            attack_id,
        "attack_type":          attack_type,
        "attack_goal":          attack_goal,
        "attack_text":          attack_text,
        # Experiment parameters
        "model":                config.MODEL,
        "fill_pct":             fill_pct,
        "fill_strategy":        strategy,
        "trial_num":            trial_num,
        "target_fill_tokens":   target_fill_tokens,
        "est_noise_tokens":     est_noise_tokens,
        "num_context_messages": len(noise_messages),
        # Token counts (filled after API call)
        "actual_input_tokens":  None,
        "actual_output_tokens": None,
        "total_tokens_used":    None,
        # Text passed to model before attack (for qualitative analysis)
        "context_preview":      (noise_messages[0]["content"][:300] + "…")
                                 if noise_messages else "(no pre-fill context)",
        # Response and classification
        "model_response":       None,
        "irs":                  None,   # 1 = held, 0 = bypassed
        "injection_success":    None,   # True if attack succeeded
        "classifier_confidence": None,
        "classifier_reason":    None,
        # Classifier token costs (for budget tracking)
        "classifier_in_tokens": None,
        "classifier_out_tokens":None,
        # Error capture
        "error":                None,
    }

    try:
        # ── Step 3: Call the target model ─────────────────────────────────────
        response = client.messages.create(
            model=config.MODEL,
            max_tokens=config.MAX_RESPONSE_TOKENS,
            system=config.SYSTEM_PROMPT,
            messages=messages,
        )

        model_response       = response.content[0].text
        actual_input_tokens  = response.usage.input_tokens
        actual_output_tokens = response.usage.output_tokens

        record["model_response"]       = model_response
        record["actual_input_tokens"]  = actual_input_tokens
        record["actual_output_tokens"] = actual_output_tokens
        record["total_tokens_used"]    = actual_input_tokens + actual_output_tokens

        # ── Step 4: Classify success ───────────────────────────────────────────
        classification = classify_injection(
            client         = client,
            system_prompt  = config.SYSTEM_PROMPT,
            attack_type    = attack_type,
            attack_goal    = attack_goal,
            attack_text    = attack_text,
            model_response = model_response,
        )

        record["irs"]                  = classification["irs"]
        record["injection_success"]    = classification["success"]
        record["classifier_confidence"]= classification["confidence"]
        record["classifier_reason"]    = classification["reason"]
        record["classifier_in_tokens"] = classification["classifier_in"]
        record["classifier_out_tokens"]= classification["classifier_out"]

        status_icon = "✗ SUCCESS" if classification["success"] else "✓ HELD"
        print(
            f"  [{status_icon}] {attack_id} | "
            f"Fill: {fill_pct*100:5.1f}% | Trial {trial_num} | "
            f"Tokens in: {actual_input_tokens:,} out: {actual_output_tokens:,} | "
            f"IRS: {classification['irs']} | "
            f"Conf: {classification['confidence']}"
        )

    except anthropic.RateLimitError as e:
        record["error"] = f"RateLimitError: {e}"
        print(f"  [RATE LIMIT] {attack_id} T{trial_num} — waiting 10s and retrying…")
        time.sleep(20)
        # Retry once
        return run_single_trial(client, attack, fill_pct, trial_num, strategy)

    except Exception as e:
        record["error"] = str(e)
        print(f"  [ERROR] {attack_id} | Fill {fill_pct*100:.0f}% | Trial {trial_num}: {e}")

    return record


# ─── Results persistence ──────────────────────────────────────────────────────

def save_results(results: list[dict], output_dir: str) -> tuple[str, str]:
    """
    Write results to both JSON (full fidelity) and CSV (easy analysis).
    Returns (json_path, csv_path).
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    json_path = os.path.join(output_dir, f"results_{ts}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    csv_path = os.path.join(output_dir, f"results_{ts}.csv")
    if results:
        fieldnames = list(results[0].keys())
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)

    print(f"\n  Saved → {json_path}")
    print(f"  Saved → {csv_path}")
    return json_path, csv_path


# ─── Summary printer ─────────────────────────────────────────────────────────

def print_summary(results: list[dict], fill_levels: list[float]) -> None:
    valid = [r for r in results if r.get("irs") is not None]
    if not valid:
        print("No valid results to summarise.")
        return

    total_in  = sum(r.get("actual_input_tokens", 0) or 0 for r in valid)
    total_out = sum(r.get("actual_output_tokens", 0) or 0 for r in valid)
    clf_in    = sum(r.get("classifier_in_tokens", 0) or 0 for r in valid)
    clf_out   = sum(r.get("classifier_out_tokens", 0) or 0 for r in valid)

    print("\n" + "═" * 64)
    print("  EXPERIMENT SUMMARY")
    print("═" * 64)
    print(f"  Valid trials       : {len(valid)} / {len(results)}")
    print(f"  Target tokens used : {total_in:,} in  {total_out:,} out")
    print(f"  Classifier tokens  : {clf_in:,} in  {clf_out:,} out")

    overall_irs = sum(r["irs"] for r in valid) / len(valid)
    print(f"\n  Overall IRS        : {overall_irs:.3f}  (1.0 = perfect retention)")

    print("\n  IRS by fill level:")
    print(f"  {'Fill':>8}  {'IRS':>6}  {'Successes':>12}  {'Trials':>8}")
    print("  " + "-" * 44)
    for fill_pct in fill_levels:
        runs = [r for r in valid if r["fill_pct"] == fill_pct]
        if not runs:
            continue
        irs = sum(r["irs"] for r in runs) / len(runs)
        successes = sum(1 for r in runs if r["injection_success"])
        print(f"  {fill_pct*100:7.0f}%  {irs:6.3f}  {successes:>12}  {len(runs):>8}")
    print("═" * 64)


# ─── Main orchestrator ────────────────────────────────────────────────────────

def run_experiment(
    attacks_file: str,
    fill_levels: list[float] = None,
    n_trials: int = None,
    strategy: str = None,
    output_dir: str = None,
    dry_run: bool = False,
) -> list[dict]:
    """
    Run the full experiment matrix: attacks × fill_levels × n_trials.

    In dry_run mode, the input file is validated and the run plan is printed
    but no API calls are made. Use this to check your attack file and settings.
    """
    fill_levels = fill_levels or config.FILL_LEVELS
    n_trials    = n_trials    or config.N_TRIALS
    strategy    = strategy    or config.DEFAULT_NOISE_STRATEGY
    output_dir  = output_dir  or config.RESULTS_DIR

    attacks = load_attacks(attacks_file)
    total_runs = len(attacks) * len(fill_levels) * n_trials

    print("\n" + "═" * 64)
    print("  CONTEXT WINDOW SATURATION — PROMPT INJECTION EXPERIMENT")
    print("═" * 64)
    print(f"  Model          : {config.MODEL}")
    print(f"  Classifier     : {config.CLASSIFIER_MODEL}")
    print(f"  Attacks loaded : {len(attacks)}")
    print(f"  Fill levels    : {[f'{x*100:.0f}%' for x in fill_levels]}")
    print(f"  Trials/combo   : {n_trials}")
    print(f"  Noise strategy : {strategy}")
    print(f"  Total runs     : {total_runs}")
    print(f"  Output dir     : {output_dir}")
    print("═" * 64)

    if dry_run:
        print("\n  [DRY RUN] — no API calls made. Input file looks valid.\n")
        for a in attacks:
            print(f"    {a['attack_id']:8} | {a['attack_type']:20} | {a['attack_goal'][:40]}")
        return []

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    all_results = []
    run_count = 0

    for attack in attacks:
        print(f"\n── Attack: {attack['attack_id']} ({attack['attack_type']}) ──")
        for fill_pct in fill_levels:
            for trial in range(1, n_trials + 1):
                run_count += 1
                print(f"  Run {run_count}/{total_runs}", end="  ")
                record = run_single_trial(client, attack, fill_pct, trial, strategy)
                all_results.append(record)
                time.sleep(config.REQUEST_DELAY)

    save_results(all_results, output_dir)
    print_summary(all_results, fill_levels)
    return all_results


# ─── CLI entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="LLM Context Window Saturation — Prompt Injection Experiment"
    )
    parser.add_argument(
        "--input", required=True,
        help="Path to attack prompts file (.json or .csv)"
    )
    parser.add_argument(
        "--fill-levels", default=None,
        help="Comma-separated fill percentages, e.g. 0,25,50,75,100 (default: from config.py)"
    )
    parser.add_argument(
        "--trials", type=int, default=None,
        help=f"Trials per (attack × fill level) combination (default: {config.N_TRIALS})"
    )
    parser.add_argument(
        "--strategy", default=config.DEFAULT_NOISE_STRATEGY,
        choices=["semantic_noise", "many_shot", "random_text"],
        help="Noise injection strategy (default: semantic_noise)"
    )
    parser.add_argument(
        "--output-dir", default=config.RESULTS_DIR,
        help=f"Directory for result files (default: {config.RESULTS_DIR})"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate input file and print run plan without making API calls"
    )
    args = parser.parse_args()

    levels = (
        [float(x) / 100.0 for x in args.fill_levels.split(",")]
        if args.fill_levels else None
    )

    run_experiment(
        attacks_file = args.input,
        fill_levels  = levels,
        n_trials     = args.trials,
        strategy     = args.strategy,
        output_dir   = args.output_dir,
        dry_run      = args.dry_run,
    )
