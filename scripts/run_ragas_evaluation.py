"""端到端 RAG 评估运行脚本。

用法：
    python scripts/run_ragas_evaluation.py
    python scripts/run_ragas_evaluation.py --test-set tests/fixtures/golden_test_set_v2.json --top-k 10

输出：
    reports/eval_report_{timestamp}.json
    reports/error_analysis_{timestamp}.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

# 将项目根目录加入路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from langchain_openai import ChatOpenAI

from src.core.settings import load_settings
from src.observability.evaluation.ragas_evaluator import RagasEvaluator
from src.libs.evaluator.custom_evaluator import CustomEvaluator
from src.observability.evaluation.rag_eval_runner import RAGEvalRunner, analyze_by_tags
from src.observability.evaluation.eval_runner import load_test_set
from src.ragent_backend.workflow import RAGWorkflow
from src.ragent_backend.store import build_archive_store


REPORTS_DIR = Path("reports")


def classify_failure(metrics: dict[str, float]) -> str:
    """根据 Ragas 指标自动分类失败原因。"""
    if metrics.get("context_precision", 1.0) < 0.5:
        return "retrieval_failure"
    if metrics.get("faithfulness", 1.0) < 0.5:
        return "hallucination"
    if metrics.get("answer_relevancy", 1.0) < 0.5:
        return "off_topic"
    return "pass"


async def main():
    parser = argparse.ArgumentParser(description="Run end-to-end RAG evaluation with Ragas")
    parser.add_argument(
        "--test-set",
        default="tests/fixtures/golden_test_set_v2.json",
        help="Path to golden test set",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Retrieval top-k",
    )
    parser.add_argument(
        "--output-dir",
        default="reports",
        help="Directory to save evaluation reports",
    )
    args = parser.parse_args()

    settings = load_settings()
    print(f"[Settings] LLM: {settings.llm.model}, Embedding: {settings.embedding.model}")

    # 初始化 LLM
    try:
        llm_kwargs = {
            "model": settings.llm.model,
            "temperature": 0.0,
        }
        if getattr(settings.llm, "base_url", None):
            llm_kwargs["base_url"] = settings.llm.base_url
        if getattr(settings.llm, "api_key", None):
            llm_kwargs["api_key"] = settings.llm.api_key
        elif os.environ.get("OPENAI_API_KEY"):
            llm_kwargs["api_key"] = os.environ.get("OPENAI_API_KEY")

        llm = ChatOpenAI(**llm_kwargs)
        print("[Init] LLM ready")
    except Exception as e:
        print(f"[Error] Failed to init LLM: {e}")
        return

    # 初始化 Workflow
    archive_store = build_archive_store()
    checkpointer = None  # 评估时不需要持久化 checkpoint
    workflow = RAGWorkflow(
        store=archive_store,
        llm=llm,
        checkpointer=checkpointer,
        max_messages=20,
        keep_recent=4,
    )

    # 初始化 Evaluators
    ragas_evaluator = RagasEvaluator(settings=settings)
    custom_evaluator = CustomEvaluator(settings=settings)

    # 运行评估
    runner = RAGEvalRunner(
        workflow=workflow,
        ragas_evaluator=ragas_evaluator,
        custom_evaluator=custom_evaluator,
    )

    print(f"\n[Run] Loading test set: {args.test_set}")
    report = await runner.run(args.test_set, top_k=args.top_k)

    # 加载 test cases 用于 tag 分析
    test_cases = load_test_set(args.test_set)
    tag_analysis = analyze_by_tags(report, test_cases)

    # 生成失败分析
    failure_counts = {"retrieval_failure": 0, "hallucination": 0, "off_topic": 0, "pass": 0}
    low_score_cases = []
    for qr in report.query_results:
        label = classify_failure(qr.metrics)
        failure_counts[label] = failure_counts.get(label, 0) + 1
        if label != "pass":
            low_score_cases.append({
                "query": qr.query,
                "label": label,
                "metrics": qr.metrics,
            })

    # 保存 JSON 报告
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = int(time.time())

    json_path = output_dir / f"eval_report_{timestamp}.json"
    json_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n[Report] JSON saved to: {json_path}")

    # 保存 Markdown 分析报告
    md_lines = [
        f"# RAG 评估报告 ({time.strftime('%Y-%m-%d %H:%M:%S')})",
        "",
        "## 总体指标",
        "",
        f"- **测试集**: {args.test_set}",
        f"- **总查询数**: {len(report.query_results)}",
        f"- **平均耗时**: {report.total_elapsed_ms / max(len(report.query_results), 1):.1f} ms/query",
        f"- **总耗时**: {report.total_elapsed_ms / 1000:.1f} s",
        "",
        "### 聚合指标",
        "",
        "| 指标 | 平均值 |",
        "|:---|:---:|",
    ]
    for metric_name, value in sorted(report.aggregate_metrics.items()):
        md_lines.append(f"| {metric_name} | {value:.4f} |")

    md_lines.extend(["", "## 按 Tag 分类指标", ""])
    for tag, metrics in sorted(tag_analysis.items()):
        md_lines.append(f"### {tag}")
        md_lines.append("")
        md_lines.append("| 指标 | 平均值 |")
        md_lines.append("|:---|:---:|")
        for metric_name, value in sorted(metrics.items()):
            md_lines.append(f"| {metric_name} | {value:.4f} |")
        md_lines.append("")

    md_lines.extend(["", "## 错误分析", ""])
    md_lines.append("| 类型 | 数量 | 占比 |")
    md_lines.append("|:---|:---:|:---:|")
    total = len(report.query_results)
    for label, count in sorted(failure_counts.items(), key=lambda x: -x[1]):
        pct = count / total * 100 if total else 0
        md_lines.append(f"| {label} | {count} | {pct:.1f}% |")

    if low_score_cases:
        md_lines.extend(["", "### 低分 Case 详情", ""])
        for case in low_score_cases[:20]:  # 最多展示 20 条
            md_lines.append(f"- **[{case['label']}]** `{case['query']}`")
            md_lines.append(f"  - metrics: {json.dumps(case['metrics'], ensure_ascii=False)}")
            md_lines.append("")

    md_path = output_dir / f"error_analysis_{timestamp}.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    print(f"[Report] Markdown saved to: {md_path}")

    # 控制台摘要
    print("\n" + "=" * 60)
    print("EVALUATION SUMMARY")
    print("=" * 60)
    for metric_name, value in sorted(report.aggregate_metrics.items()):
        print(f"  {metric_name:30s}: {value:.4f}")
    print("=" * 60)
    print(f"Failures: {failure_counts}")


if __name__ == "__main__":
    asyncio.run(main())
