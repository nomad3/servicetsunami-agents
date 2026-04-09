"""Run the Gemma4 commitment classifier against the gold set, report F1.

Acceptance gate (design doc §11.1 / open Q #2): F1 ≥ 0.7 to ship the classifier.
"""
import json
import os
import sys
import time
from dataclasses import asdict

# Add apps/api to path
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from app.memory.classifiers.commitment import classify_commitment


def main():
    gold_set_path = os.path.join(os.path.dirname(__file__), "../tests/fixtures/commitment_gold_set.jsonl")
    if not os.path.exists(gold_set_path):
        print(f"Gold set not found at {gold_set_path}")
        sys.exit(1)

    gold = []
    with open(gold_set_path) as f:
        for line in f:
            if line.strip():
                gold.append(json.loads(line))

    print(f"Loaded {len(gold)} gold examples")

    tp = fp = tn = fn = 0
    misclassified = []
    t0 = time.perf_counter()
    
    for i, ex in enumerate(gold):
        result = classify_commitment(ex["text"], role=ex.get("role", "user"))
        pred = 1 if result.is_commitment else 0
        actual = ex["label"]
        
        if pred == 1 and actual == 1:
            tp += 1
        elif pred == 1 and actual == 0:
            fp += 1
        elif pred == 0 and actual == 0:
            tn += 1
        elif pred == 0 and actual == 1:
            fn += 1

        if pred != actual:
            misclassified.append({
                "text": ex["text"][:100],
                "actual": actual,
                "predicted": pred,
                "confidence": result.confidence,
            })
        
        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(gold)} processed...")

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    elapsed = time.perf_counter() - t0

    report = {
        "n": len(gold),
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "elapsed_seconds": round(elapsed, 1),
        "elapsed_per_example_ms": round(elapsed / len(gold) * 1000, 0),
        "misclassified_count": len(misclassified),
    }
    print("\n" + json.dumps(report, indent=2))

    output_dir = os.path.join(os.path.dirname(__file__), "../../docs/plans/baselines")
    os.makedirs(output_dir, exist_ok=True)
    
    result_path = os.path.join(output_dir, "commitment-classifier-f1.md")
    with open(result_path, "w") as f:
        f.write("# Commitment Classifier F1 Evaluation\n\n")
        f.write(f"**Date:** {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(f"**Result:** F1 = {f1:.3f}\n\n")
        f.write(f"**Acceptance:** {'✅ PASS' if f1 >= 0.7 else '❌ FAIL'} (target ≥ 0.7)\n\n")
        f.write("## Metrics\n```json\n" + json.dumps(report, indent=2) + "\n```\n\n")
        f.write("## Sample misclassifications (first 20)\n")
        for m in misclassified[:20]:
            f.write(f"- `{m['text']}` actual={m['actual']} predicted={m['predicted']} conf={m['confidence']:.2f}\n")

    print(f"\nReport written to {result_path}")
    sys.exit(0 if f1 >= 0.7 else 1)


if __name__ == "__main__":
    main()
