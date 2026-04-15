#!/usr/bin/env python3
"""
RAG 系统全面评估脚本
覆盖：
1. 查询分析（指代消解 + 子查询拆分）
2. Ingestion Pipeline 完整性与 Trace
3. RAG 端到端检索质量
"""

import asyncio
import os
import json
import time
from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from langchain_core.messages import HumanMessage, AIMessage
from langchain_openai import ChatOpenAI

from src.core.settings import load_settings
from src.core.trace.trace_context import TraceContext
from src.ingestion.pipeline import IngestionPipeline, PipelineResult
from src.ragent_backend.intent import analyze_query, detect_intent
from src.mcp_server.tools.query_knowledge_hub import QueryKnowledgeHubTool


@dataclass
class TestResult:
    name: str
    passed: bool
    score: float  # 0-1
    details: Dict[str, Any]
    error: Optional[str] = None


class RAGBenchmark:
    def __init__(self):
        self.settings = load_settings()
        self.results: List[TestResult] = []
        self.test_collection = f"benchmark_{int(time.time())}"

        try:
            self.llm = ChatOpenAI(
                model=os.getenv("RAGENT_LLM_MODEL", "qwen3.5-omni-flash"),
                base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
                api_key=os.environ.get("OPENAI_API_KEY"),
                temperature=0.0,
            )
        except Exception as e:
            print(f"[Init] Failed to create LLM: {e}")
            self.llm = None

    def add_result(self, result: TestResult):
        self.results.append(result)
        status = "[PASS]" if result.passed else "[FAIL]"
        print(f"\n{status} | {result.name} | score={result.score:.2f}")
        if result.error:
            print(f"   Error: {result.error}")
        for k, v in result.details.items():
            print(f"   {k}: {v}")

    # ------------------------------------------------------------------
    # 1. 查询分析（指代消解 + 子查询拆分）
    # ------------------------------------------------------------------
    async def test_query_analysis(self):
        """测试 analyze_query：同时验证指代消解和子查询拆分"""
        if not self.llm:
            self.add_result(TestResult(
                name="Query Analysis (Anaphora + Subquery)",
                passed=False,
                score=0.0,
                details={},
                error="LLM not available"
            ))
            return

        cases = [
            {
                "name": "代词消解 + 无拆分",
                "history": [
                    HumanMessage(content="介绍一下华为Mate 60的芯片"),
                    AIMessage(content="华为Mate 60搭载的是麒麟9000S芯片，采用7nm工艺。"),
                ],
                "query": "它的性能怎么样？",
                "expected_keywords": ["华为", "Mate", "麒麟", "9000S"],
                "expected_subqueries": 1,
            },
            {
                "name": "指代消解 + 并列拆分（有连词）",
                "history": [
                    HumanMessage(content="2024年英伟达财报表现如何"),
                    AIMessage(content="英伟达2024财年营收同比增长126%，数据中心业务占比超过80%。"),
                ],
                "query": "这个季度的毛利率是多少？",
                "expected_keywords": ["英伟达", "毛利率"],
                "expected_subqueries": 1,
            },
            {
                "name": "无历史 + 无拆分",
                "history": [],
                "query": "北京今天的天气怎么样？",
                "expected_keywords": ["北京", "天气"],
                "expected_subqueries": 1,
            },
            {
                "name": "无连词并列城市拆分",
                "history": [],
                "query": "北京上海杭州的天气怎么样",
                "expected_keywords": ["北京", "上海", "杭州", "天气"],
                "expected_subqueries": 3,
            },
            {
                "name": "有连词产品拆分",
                "history": [],
                "query": "华为和苹果的旗舰手机对比",
                "expected_keywords": ["华为", "苹果", "手机"],
                "expected_subqueries": 2,
            },
        ]

        scores = []
        case_details = []

        for case in cases:
            analysis = await analyze_query(case["query"], case["history"], self.llm)
            rewritten = analysis.rewritten_query
            sub_queries = analysis.sub_queries

            # 评分维度 1：指代消解（关键词匹配）
            keyword_hits = sum(
                1 for kw in case["expected_keywords"]
                if kw.lower() in rewritten.lower()
            )
            rewrite_score = keyword_hits / len(case["expected_keywords"])

            # 评分维度 2：子查询拆分数量
            split_score = 1.0 if len(sub_queries) == case["expected_subqueries"] else 0.0

            # 评分维度 3：不应该还有模糊代词（仅限有历史的情况）
            vague = ["它", "这个", "that", "it", "this", "上述", "上面"]
            if case["history"] and any(v in rewritten for v in vague):
                rewrite_score = max(0, rewrite_score - 0.3)

            # 总分：消解占 60%，拆分占 40%
            total_score = rewrite_score * 0.6 + split_score * 0.4
            scores.append(total_score)

            case_details.append({
                "case": case["name"],
                "original": case["query"],
                "rewritten": rewritten,
                "sub_queries": sub_queries,
                "expected_keywords": case["expected_keywords"],
                "expected_subqueries": case["expected_subqueries"],
                "rewrite_score": rewrite_score,
                "split_score": split_score,
                "total_score": total_score,
            })

        avg_score = sum(scores) / len(scores) if scores else 0
        passed = avg_score >= 0.7

        self.add_result(TestResult(
            name="Query Analysis (Anaphora + Subquery)",
            passed=passed,
            score=avg_score,
            details={"cases": case_details, "average_score": avg_score}
        ))

    # ------------------------------------------------------------------
    # 2. Ingestion Pipeline 完整性与 Trace
    # ------------------------------------------------------------------
    def test_ingestion_pipeline_trace(self):
        test_doc_path = Path("data/benchmark_test_doc.txt")
        test_doc_path.parent.mkdir(parents=True, exist_ok=True)
        test_doc_path.write_text(
            "Artificial Intelligence in Healthcare\n\n"
            "Machine learning algorithms are transforming medical diagnosis. "
            "Deep learning models can detect tumors in radiology images with high accuracy. "
            "Natural language processing helps extract insights from clinical notes. "
            "\n\n"
            "Key benefits include improved diagnostic accuracy, reduced workload for clinicians, "
            "and faster drug discovery through computational biology.",
            encoding="utf-8"
        )

        trace = TraceContext(trace_type="ingestion")
        pipeline = IngestionPipeline(self.settings, collection=self.test_collection, force=True)

        try:
            result: PipelineResult = pipeline.run(str(test_doc_path), trace=trace)

            if not result.success:
                self.add_result(TestResult(
                    name="Ingestion Pipeline + Trace",
                    passed=False,
                    score=0.0,
                    details={"stages": result.stages},
                    error=result.error
                ))
                return

            stage_names = [s["stage"] for s in trace.stages]
            required_stages = ["load", "split", "transform", "embed", "upsert"]
            missing = [s for s in required_stages if s not in stage_names]

            transform_stage = next((s for s in trace.stages if s["stage"] == "transform"), None)
            transform_data = transform_stage["data"] if transform_stage else {}

            chunk_refiner_llm_enabled = getattr(
                getattr(self.settings, 'ingestion', None), 'chunk_refiner', {}
            ).get('use_llm', False) if hasattr(self.settings, 'ingestion') else False
            metadata_enricher_llm_enabled = getattr(
                getattr(self.settings, 'ingestion', None), 'metadata_enricher', {}
            ).get('use_llm', False) if hasattr(self.settings, 'ingestion') else False

            details = {
                "chunk_count": result.chunk_count,
                "vector_ids_count": len(result.vector_ids),
                "trace_stages": stage_names,
                "missing_trace_stages": missing,
                "transform_stats": {
                    "refined_by_llm": transform_data.get("refined_by_llm", 0),
                    "refined_by_rule": transform_data.get("refined_by_rule", 0),
                    "enriched_by_llm": transform_data.get("enriched_by_llm", 0),
                    "enriched_by_rule": transform_data.get("enriched_by_rule", 0),
                    "captioned_chunks": transform_data.get("captioned_chunks", 0),
                },
                "settings": {
                    "chunk_refiner_use_llm": chunk_refiner_llm_enabled,
                    "metadata_enricher_use_llm": metadata_enricher_llm_enabled,
                    "vision_llm_enabled": self.settings.vision_llm.enabled if self.settings.vision_llm else False,
                },
                "warnings": []
            }

            warnings = []
            if chunk_refiner_llm_enabled and details["transform_stats"]["refined_by_llm"] == 0:
                warnings.append("chunk_refiner.use_llm=true but 0 LLM-refined chunks")
            if metadata_enricher_llm_enabled and details["transform_stats"]["enriched_by_llm"] == 0:
                warnings.append("metadata_enricher.use_llm=true but 0 LLM-enriched chunks")
            details["warnings"] = warnings

            score = 1.0
            if missing:
                score -= 0.3 * len(missing)
            if result.chunk_count == 0:
                score -= 0.5
            if len(result.vector_ids) == 0:
                score -= 0.5
            if warnings:
                score -= 0.15 * len(warnings)
            score = max(0, score)

            passed = score >= 0.7 and result.success

            self.add_result(TestResult(
                name="Ingestion Pipeline + Trace",
                passed=passed,
                score=score,
                details=details
            ))

        except Exception as e:
            self.add_result(TestResult(
                name="Ingestion Pipeline + Trace",
                passed=False,
                score=0.0,
                details={},
                error=str(e)
            ))
        finally:
            pipeline.close()

    # ------------------------------------------------------------------
    # 3. RAG 检索质量测试
    # ------------------------------------------------------------------
    async def test_retrieval_quality(self):
        tool = QueryKnowledgeHubTool(self.settings)

        queries = [
            {
                "query": "机器学习在医疗诊断中的应用",
                "expected_in_result": ["medical", "diagnosis", "machine learning"],
            },
            {
                "query": "深度学习检测肿瘤",
                "expected_in_result": ["deep learning", "tumor", "radiology"],
            },
            {
                "query": "NLP 临床笔记",
                "expected_in_result": ["natural language processing", "clinical"],
            },
        ]

        scores = []
        case_details = []

        for q in queries:
            try:
                result = await tool.execute(
                    query=q["query"],
                    collection=self.test_collection,
                    top_k=5
                )
                content = result.content.lower()
                hits = sum(1 for exp in q["expected_in_result"] if exp.lower() in content)
                case_score = hits / len(q["expected_in_result"])
                scores.append(case_score)

                case_details.append({
                    "query": q["query"],
                    "expected": q["expected_in_result"],
                    "hit_count": hits,
                    "score": case_score,
                    "result_preview": content[:200],
                })
            except Exception as e:
                scores.append(0)
                case_details.append({
                    "query": q["query"],
                    "error": str(e),
                    "score": 0
                })

        avg_score = sum(scores) / len(scores) if scores else 0
        passed = avg_score >= 0.5

        self.add_result(TestResult(
            name="RAG Retrieval Quality",
            passed=passed,
            score=avg_score,
            details={"cases": case_details, "average_score": avg_score}
        ))

    # ------------------------------------------------------------------
    # 汇总报告
    # ------------------------------------------------------------------
    def print_summary(self):
        print("\n" + "=" * 70)
        print("BENCHMARK SUMMARY")
        print("=" * 70)

        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        avg_score = sum(r.score for r in self.results) / total if total else 0

        print(f"Total Tests: {total}")
        print(f"Passed: {passed}")
        print(f"Failed: {total - passed}")
        print(f"Average Score: {avg_score:.2f}")

        report_path = Path(f"benchmark_report_{int(time.time())}.json")
        report_path.write_text(
            json.dumps({
                "summary": {
                    "total": total,
                    "passed": passed,
                    "average_score": avg_score,
                },
                "results": [asdict(r) for r in self.results]
            }, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print(f"\nDetailed report saved to: {report_path}")


async def main():
    bench = RAGBenchmark()

    print("Running Query Analysis tests...")
    await bench.test_query_analysis()

    print("\nRunning Ingestion Pipeline tests...")
    bench.test_ingestion_pipeline_trace()

    print("\nRunning RAG Retrieval tests...")
    await bench.test_retrieval_quality()

    bench.print_summary()


if __name__ == "__main__":
    asyncio.run(main())
