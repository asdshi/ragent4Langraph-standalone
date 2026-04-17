"""评估指标阈值检查脚本，用于 CI 门禁。

用法：
    python scripts/check_metrics_threshold.py \
        --report reports/eval_report_1713331200.json \
        --faithfulness 0.75 \
        --answer-relevancy 0.70 \
        --context-precision 0.65

如果任一指标低于阈值，脚本返回非零退出码。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Check evaluation metrics against thresholds")
    parser.add_argument("--report", required=True, help="Path to eval_report JSON")
    parser.add_argument("--faithfulness", type=float, default=0.0, help="Minimum faithfulness")
    parser.add_argument("--answer-relevancy", type=float, default=0.0, help="Minimum answer relevancy")
    parser.add_argument("--context-precision", type=float, default=0.0, help="Minimum context precision")
    parser.add_argument("--reference-similarity", type=float, default=0.0, help="Minimum reference similarity")
    parser.add_argument("--hit-rate", type=float, default=0.0, help="Minimum hit rate")
    parser.add_argument("--mrr", type=float, default=0.0, help="Minimum MRR")
    args = parser.parse_args()

    report_path = Path(args.report)
    if not report_path.exists():
        print(f"[Error] Report not found: {report_path}")
        sys.exit(1)

    data = json.loads(report_path.read_text(encoding="utf-8"))
    metrics = data.get("aggregate_metrics", {})

    thresholds = {
        "faithfulness": args.faithfulness,
        "answer_relevancy": args.answer_relevancy,
        "context_precision": args.context_precision,
        "reference_similarity": args.reference_similarity,
        "hit_rate": args.hit_rate,
        "mrr": args.mrr,
    }

    failed = []
    passed = []

    print(f"Checking report: {report_path.name}")
    print("-" * 50)

    for name, threshold in thresholds.items():
        if threshold <= 0:
            continue
        actual = metrics.get(name, 0.0)
        status = "PASS" if actual >= threshold else "FAIL"
        print(f"  {name:25s}: {actual:.4f} >= {threshold:.4f}  [{status}]")
        if actual < threshold:
            failed.append(name)
        else:
            passed.append(name)

    print("-" * 50)

    if failed:
        print(f"FAILED: {len(failed)} metric(s) below threshold: {', '.join(failed)}")
        sys.exit(1)
    else:
        checked = len(passed)
        print(f"ALL PASS: {checked} metric(s) meet threshold requirements.")
        sys.exit(0)


if __name__ == "__main__":
    main()
