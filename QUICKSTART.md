# Quick Start Guide — Diversified Attack Testing

## What Changed

**Old attack set:** 18 variations of "reveal system prompt" (very hard to succeed)
**New attack set:** 20 diverse attacks across 8 categories (more realistic, higher success rates expected)

---

## Test 1: Cheap Smoke Test (Cost: ~$0.01)

See actual model responses without classification noise:

```bash
cd experiment
python test_attacks.py --input attacks/sample_attacks.json --verbose 
```

**What to look for:**
- Attacks BC01, BC02, TH01, ES01 should show some compliance
- Attacks DE01, DE02 might show willingness to share data
- Attacks MI01, MI02 might succeed if language switching works

---

## Test 2: Single Fill Level (Cost: ~$0.05–0.10)

Test all 20 attacks at 0% context fill (baseline):

```bash
python runner.py --input attacks/sample_attacks.json \
                 --fill-levels 0 \
                 --trials 1 \
                 --injection-style separate
```

**Expected output:**
- Some attacks should show IRS < 1 (success)
- Look for: BC* (behavior change), TH* (task hijacking), ES* (escalation)

---

## Test 3: Compare Fill Levels (Cost: ~$0.30–0.50)

Test 0% vs 100% context fill to measure saturation effect:

```bash
python runner.py --input attacks/sample_attacks.json \
                 --fill-levels 0,100 \
                 --trials 2 \
                 --injection-style separate
```

**Expected pattern:**
- IRS should be lower at 100% fill (more attacks succeed)
- Your saturation hypothesis: filling context → diluted attention → weaker guardrails

---

## Test 4: Injection Style Comparison (Cost: ~$0.40–0.60)

Test which message composition is most effective:

```bash
for style in separate mixed embedded assistant; do
  echo "Testing injection_style=$style"
  python runner.py --input attacks/sample_attacks.json \
                   --fill-levels 0,50,100 \
                   --trials 1 \
                   --injection-style $style \
                   --output-dir results/style_${style}
done
```

**Expected findings:**
- `assistant` style should be strongest (role confusion)
- `mixed` should be stronger than `separate` (direct blending)
- `embedded` should vary depending on model

---

## Test 5: Full Experiment (Cost: ~$2.00–3.00)

Measure all effects: attacks × fill levels × injection styles:

```bash
python runner.py --input attacks/sample_attacks.json \
                 --fill-levels 0,25,50,75,100 \
                 --trials 3 \
                 --injection-style mixed
```

---

## Analyzing Results

### View raw results:
```bash
cat results/results_*.json | python3 -m json.tool | head -100
```

### Count successes by attack type:
```python
import json
import glob

for f in glob.glob("results/results_*.json"):
    with open(f) as file:
        results = json.load(file)

    by_type = {}
    for r in results:
        attack_type = r.get("attack_type", "unknown")
        success = r.get("injection_success", False)

        if attack_type not in by_type:
            by_type[attack_type] = {"success": 0, "total": 0}

        by_type[attack_type]["total"] += 1
        if success:
            by_type[attack_type]["success"] += 1

    print(f"\n=== {f} ===")
    for attack_type in sorted(by_type.keys()):
        counts = by_type[attack_type]
        rate = counts["success"] / counts["total"] if counts["total"] > 0 else 0
        print(f"{attack_type:40} {counts['success']:2}/{counts['total']:2} ({rate*100:5.1f}%)")
```

### Compare fill levels:
```python
import json
import glob

for f in glob.glob("results/results_*.json"):
    with open(f) as file:
        results = json.load(file)

    by_fill = {}
    for r in results:
        fill = r.get("fill_pct", 0)
        irs = r.get("irs", 1)

        if fill not in by_fill:
            by_fill[fill] = []
        by_fill[fill].append(irs)

    print(f"\n=== {f} ===")
    print(f"{'Fill %':>10} {'Avg IRS':>10} {'Failures':>10}")
    for fill in sorted(by_fill.keys()):
        irs_list = by_fill[fill]
        avg_irs = sum(irs_list) / len(irs_list)
        failures = sum(1 for x in irs_list if x == 0)
        print(f"{fill*100:10.0f} {avg_irs:10.3f} {failures:10}")
```

---

## Expected Findings

### By Attack Category:
- **Behavior Change (BC):** 10–30% success
- **Task Hijacking (TH):** 5–15% success
- **Data Exfiltration (DE):** 5–10% success
- **Escalation (ES):** 10–20% success
- **Role Confusion (RC):** 8–12% success
- **Multilingual (MI):** 5–15% success
- **Obfuscation (OE):** 3–10% success
- **Compliance Bypass (CB):** 2–8% success

### By Fill Level:
- **0% fill:** IRS ≈ 0.70–0.80 (guardrails mostly hold)
- **100% fill:** IRS ≈ 0.50–0.70 (more failures — saturation effect)

### By Injection Style:
- **separate:** Baseline (most defensive)
- **mixed:** 20–30% improvement in attack success
- **embedded:** 30–50% improvement
- **assistant:** 50%+ improvement (strongest)

---

## Troubleshooting

### No attacks succeed (IRS = 1.0 everywhere)
→ Model is very strong. Try:
- Different injection styles (`--injection-style mixed` or `--embedded`)
- Pre-fill with `many_shot` strategy (`--strategy many_shot`)
- Add context fill (`--fill-levels 100`)

### All attacks succeed (IRS = 0.0 everywhere)
→ Classifier might be too lenient. Manual review needed:
```bash
python test_attacks.py --input attacks/sample_attacks.json --verbose
```

### Results vary wildly between runs
→ Normal for LLMs. Increase `--trials` to 5+ for stable averages

### API rate limiting
→ Increase `REQUEST_DELAY` in config.py or add `--delay 2` flag

---

## Key Files

| File | Purpose |
|------|---------|
| `sample_attacks.json` | 20 attacks across 8 categories |
| `runner.py` | Main experiment orchestrator |
| `classifier.py` | IRS scoring (success/failure) |
| `context_builder.py` | Noise generation for context fill |
| `test_attacks.py` | Cheap smoke test, raw responses |
| `evolve.py` | Auto-iterate on attacks (advanced) |
| `analyze.py` | Post-experiment analysis |

---

## Next Steps After Testing

1. **If attacks succeed at 0% fill:** Run full matrix (0–100% fill) to measure saturation
2. **If attacks succeed at 100% fill:** Test evolve.py to mutate successful attacks
3. **If specific category succeeds:** Focus future research on that technique
4. **If injection style shows effect:** Use strongest style in all future runs

---

## Cost Estimate

| Test | Attacks | Trials | Fill Levels | Styles | Cost |
|------|---------|--------|-------------|--------|------|
| Smoke | 20 | 1 | 1 | 1 | ~$0.01 |
| Test 2 | 20 | 1 | 1 | 1 | ~$0.05 |
| Test 3 | 20 | 2 | 2 | 1 | ~$0.10 |
| Test 4 | 20 | 1 | 3 | 4 | ~$0.40 |
| Test 5 (Full) | 20 | 3 | 5 | 1 | ~$2.00 |

**Total for comprehensive study:** ~$3.00 USD
