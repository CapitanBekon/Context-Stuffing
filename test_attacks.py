"""
test_attacks.py — Lightweight Attack Smoke Test

Runs every attack in your input file ONCE, with NO context fill, using
Claude Haiku (the cheapest model). No classifier call is made — the raw
response is printed to the terminal so you can read it yourself and decide
whether the injection worked.

Use this to:
  1. Verify your attack file loads correctly
  2. Sanity-check that your API key and system prompt work
  3. Read the model's raw responses before running the full experiment
  4. Estimate how effective each attack type is at baseline (0% fill)

COST ESTIMATE
─────────────
  Haiku pricing (April 2026): ~$0.80 / million input tokens
                               ~$4.00 / million output tokens
  A single attack at 0% fill: ~200–400 input tokens + ~200 output tokens
  10 attacks:                  < $0.01 total

USAGE
─────
  python test_attacks.py --input attacks/sample_attacks.json
  python test_attacks.py --input attacks/sample_attacks.csv --verbose
"""

import anthropic
import json
import csv
import argparse
import time
from pathlib import Path
from datetime import datetime, timezone

import config


def load_attacks(filepath: str) -> list[dict]:
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"Attack file not found: {filepath}")
    if path.suffix == ".json":
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    elif path.suffix == ".csv":
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    raise ValueError(f"Unsupported file type: {path.suffix}")


def run_test(attacks_file: str, verbose: bool = False):
    attacks = load_attacks(attacks_file)
    client  = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    # Use Haiku for lowest cost — change to config.MODEL to test with Sonnet
    model = config.CLASSIFIER_MODEL  # claude-haiku-4-5-20251001

    print("\n" + "═" * 68)
    print("  ATTACK SMOKE TEST  (0% fill · 1 trial · no classifier)")
    print(f"  Model  : {model}")
    print(f"  Attacks: {len(attacks)}")
    print(f"  System prompt preview: {config.SYSTEM_PROMPT[:80].strip()}…")
    print("═" * 68)

    total_in = total_out = 0
    results = []

    for i, attack in enumerate(attacks, 1):
        attack_id   = attack.get("attack_id", f"#{i}")
        attack_type = attack.get("attack_type", "unknown")
        attack_text = attack["attack_text"]

        print(f"\n[{i}/{len(attacks)}] {attack_id} — {attack_type}")
        if verbose:
            print(f"  Attack: {attack_text[:120]}{'…' if len(attack_text) > 120 else ''}")

        try:
            response = client.messages.create(
                model=model,
                max_tokens=300,
                system=config.SYSTEM_PROMPT,
                messages=[{"role": "user", "content": attack_text}],
            )

            reply        = response.content[0].text.strip()
            in_tokens    = response.usage.input_tokens
            out_tokens   = response.usage.output_tokens
            total_in    += in_tokens
            total_out   += out_tokens

            print(f"  Tokens : {in_tokens} in · {out_tokens} out")
            print(f"  Response:")
            # Indent and wrap the response for readability
            for line in reply.splitlines():
                print(f"    {line}")

            results.append({
                "attack_id":   attack_id,
                "attack_type": attack_type,
                "tokens_in":   in_tokens,
                "tokens_out":  out_tokens,
                "response":    reply,
            })

        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({
                "attack_id":   attack_id,
                "attack_type": attack_type,
                "error":       str(e),
            })

        time.sleep(0.3)

    # ── Summary ───────────────────────────────────────────────────────────────
    print("\n" + "─" * 68)
    print(f"  Done. {len(attacks)} attacks tested.")
    print(f"  Total tokens used : {total_in:,} in · {total_out:,} out · "
          f"{total_in + total_out:,} combined")

    # Rough cost estimate (Haiku April 2026 pricing)
    est_cost = (total_in / 1_000_000 * 0.80) + (total_out / 1_000_000 * 4.00)
    print(f"  Estimated cost    : ~${est_cost:.4f} USD")
    print("─" * 68)
    print("\n  Review the responses above and mark which attacks you consider")
    print("  successful before running the full experiment with runner.py.\n")

    # Save a lightweight log
    ts       = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_path = Path(config.RESULTS_DIR) / f"smoke_test_{ts}.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": ts,
            "model": model,
            "fill_pct": 0,
            "total_in_tokens": total_in,
            "total_out_tokens": total_out,
            "attacks": results,
        }, f, indent=2, ensure_ascii=False)
    print(f"  Log saved → {log_path}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Smoke-test attack prompts cheaply with no context fill"
    )
    parser.add_argument("--input", required=True,
                        help="Path to attack prompts file (.json or .csv)")
    parser.add_argument("--verbose", action="store_true",
                        help="Also print the attack text before each response")
    args = parser.parse_args()
    run_test(args.input, verbose=args.verbose)
