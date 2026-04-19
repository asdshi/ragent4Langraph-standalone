"""
Tool Subgraph — 工具子智能体的 LangGraph 实现。

拓扑：
    START → think_node → router_node
                             │
                ┌────────────┼────────────┐
                ▼            ▼            ▼
           tool_node    summarize_node   END (max_iter)
                │            │
                └────→ think_node (循环，最多 N 轮)

关键设计：
- 独立 State Schema，internal_messages 不返回主图
- 只输出 tool_summary + tool_execution_trace
- 主图通过 tool_subgraph 节点调用，像普通节点一样使用
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Literal, Optional

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage
from langgraph.graph import StateGraph, START, END

from src.tool_agent.state import ToolSubgraphState
from src.tool_agent.tool_registry import ToolRegistry
from src.tool_agent.unified_tool import ToolDecision, ToolResult


# =============================================================================
# Builder
# =============================================================================

def build_tool_subgraph(
    tool_registry: ToolRegistry,
    llm: Any,
    max_iterations: int = 5,
) -> Any:
    """构建工具子智能体子图。
    
    Args:
        tool_registry: 工具注册表
        llm: LangChain LLM 实例（需要支持 with_structured_output）
        max_iterations: ReAct 最大迭代次数
        
    Returns:
        编译后的 LangGraph（可作为子图节点加入主图）
    """
    
    # ------------------------------------------------------------------
    # think_node: LLM 决策下一步动作
    # ------------------------------------------------------------------
    async def think_node(state: ToolSubgraphState) -> Dict[str, Any]:
        """分析当前上下文，决定调用工具还是结束。"""
        iteration = state.get("iteration_count", 0)
        query = state["query"]
        target_tool = state.get("target_tool")
        available_tools = state.get("available_tools", [])
        internal_messages = state.get("internal_messages", [])
        
        # 构建 messages（百炼 API 要求必须有 user 角色消息）
        if iteration == 0:
            system_prompt = _build_system_prompt(query, available_tools, target_tool)
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"用户查询：{query}\n\n请根据上述查询和可用工具列表，做出工具调用决策。直接输出 JSON。"),
            ]
        else:
            # 非第一轮：已有工具执行结果，需要 user 消息触发 LLM 响应
            messages = list(internal_messages)
            messages.append(HumanMessage(content="基于上述工具执行结果，请决定下一步行动（继续调用工具或结束）。直接输出 JSON。"))
        
        # LLM 结构化决策
        try:
            structured_llm = llm.with_structured_output(ToolDecision, method="json_mode")
            decision: ToolDecision = await structured_llm.ainvoke(messages)
        except Exception as e:
            # 结构化失败，默认 finish
            decision = ToolDecision(
                thought=f"结构化决策失败: {e}，默认结束",
                action="finish",
                reasoning="fallback due to structured output error",
            )
        
        # 记录 AI 的思考过程到 internal_messages
        ai_msg = AIMessage(content=f"Thought: {decision.thought}\nAction: {decision.action}")
        
        update: Dict[str, Any] = {
            "internal_messages": messages + [ai_msg],
            "iteration_count": iteration + 1,
        }
        
        if decision.action == "call_tool" and decision.tool_calls:
            update["tool_calls"] = decision.tool_calls
            update["next_node"] = "tool"
        else:
            update["next_node"] = "summarize"
        
        return update
    
    # ------------------------------------------------------------------
    # tool_node: 执行工具调用
    # ------------------------------------------------------------------
    async def tool_node(state: ToolSubgraphState) -> Dict[str, Any]:
        """执行 LLM 决定的工具调用。"""
        tool_calls = state.get("tool_calls", [])
        internal_messages = state.get("internal_messages", [])
        failed_tools = list(state.get("failed_tools", []))
        tool_results = list(state.get("tool_results", []))
        tool_execution_trace = list(state.get("tool_execution_trace", []))
        
        new_messages = []
        
        for call in tool_calls:
            name = call.get("name", "")
            args = call.get("arguments") or call.get("args") or {}
            
            t0 = time.monotonic()
            try:
                result: ToolResult = await tool_registry.execute(name, args)
            except Exception as e:
                result = ToolResult.from_error(str(e))
            
            latency_ms = (time.monotonic() - t0) * 1000
            result.latency_ms = latency_ms
            
            # 记录 ToolMessage（LangGraph 格式）
            tool_msg = ToolMessage(
                content=result.output,
                name=name,
                tool_call_id=f"{name}_{int(t0*1000)}",
            )
            new_messages.append(tool_msg)
            
            # 记录结果
            tool_results.append({
                "name": name,
                "args": args,
                "output": result.output,
                "success": result.success,
                "error": result.error,
                "latency_ms": latency_ms,
            })
            
            # 记录 trace
            tool_execution_trace.append({
                "tool_name": name,
                "args": args,
                "result_preview": result.output[:200] if result.output else "",
                "latency_ms": latency_ms,
                "success": result.success,
                "iteration": state.get("iteration_count", 0),
                "timestamp": time.time(),
            })
            
            if not result.success:
                failed_tools.append(name)
        
        return {
            "internal_messages": new_messages,
            "tool_results": tool_results,
            "failed_tools": failed_tools,
            "tool_execution_trace": tool_execution_trace,
        }
    
    # ------------------------------------------------------------------
    # summarize_node: 整理工具执行结果为结构化摘要
    # ------------------------------------------------------------------
    async def summarize_node(state: ToolSubgraphState) -> Dict[str, Any]:
        """将多轮工具执行结果整理为结构化摘要。"""
        tool_results = state.get("tool_results", [])
        query = state["query"]
        failed_tools = state.get("failed_tools", [])
        
        # 如果没有任何工具结果（如 LLM 直接 finish），返回空摘要
        if not tool_results:
            return {
                "tool_summary": "",
                "tool_execution_trace": state.get("tool_execution_trace", []),
            }
        
        # 使用 LLM 整理摘要（如果可用）
        if llm is not None:
            summary_prompt = _build_summary_prompt(query, tool_results, failed_tools)
            try:
                response = await llm.ainvoke([HumanMessage(content=summary_prompt)])
                summary = response.content.strip()
            except Exception as e:
                summary = _build_fallback_summary(tool_results, failed_tools)
        else:
            summary = _build_fallback_summary(tool_results, failed_tools)
        
        return {
            "tool_summary": summary,
            "tool_execution_trace": state.get("tool_execution_trace", []),
        }
    
    # =================================================================
    # 构建图
    # =================================================================
    graph = StateGraph(ToolSubgraphState)
    
    graph.add_node("think", think_node)
    graph.add_node("tool", tool_node)
    graph.add_node("summarize", summarize_node)
    
    # 边：think 直接路由到 tool 或 summarize（根据 think_node 写入的 next_node）
    graph.add_edge(START, "think")
    graph.add_conditional_edges(
        "think",
        lambda state: "summarize" if state.get("iteration_count", 0) >= state.get("max_iterations", max_iterations) else state.get("next_node", "summarize"),
        {"tool": "tool", "summarize": "summarize"}
    )
    graph.add_edge("tool", "think")  # 循环回到 think
    graph.add_edge("summarize", END)
    
    return graph.compile()


# =============================================================================
# Prompt Builders
# =============================================================================

def _build_system_prompt(
    query: str,
    available_tools: List[Dict[str, Any]],
    target_tool: Optional[str] = None,
) -> str:
    """构建 think_node 的系统提示。"""
    
    tools_desc = ""
    if available_tools:
        tools_desc = "\n".join([
            f"- {t.get('name', 'unknown')}: {t.get('description', '无描述')[:120]}"
            for t in available_tools
        ])
    else:
        tools_desc = "（当前无可用工具）"
    
    target_hint = ""
    if target_tool:
        target_hint = f"\n\n【注意】主图已指定目标工具: {target_tool}，请优先使用此工具。"
    
    return f"""你是一个工具调用决策助手。你的任务是根据用户查询，决定是否需要调用工具，以及调用哪些工具。

用户查询：{query}

可用工具列表：
{tools_desc}{target_hint}

决策规则：
1. 如果查询可以通过已有信息直接回答，选择 "finish"
2. 如果查询需要调用工具获取信息，选择 "call_tool"，并指定 tool_calls
3. 每次最多调用 1-2 个工具
4. 如果工具调用失败，可以重试或换用其他工具
5. 思考要简洁，不要重复已知的上下文

输出格式（JSON）：
{{
  "thought": "你的分析思考",
  "action": "call_tool" | "finish",
  "tool_calls": [{{"name": "工具名", "arguments": {{"参数名": "参数值"}}}}],
  "reasoning": "决策理由"
}}"""


def _build_summary_prompt(
    query: str,
    tool_results: List[Dict[str, Any]],
    failed_tools: List[str],
) -> str:
    """构建 summarize_node 的提示。"""
    results_text = ""
    for i, r in enumerate(tool_results, 1):
        status = "✅ 成功" if r.get("success") else "❌ 失败"
        results_text += f"\n[{i}] 工具: {r['name']}\n"
        results_text += f"    状态: {status}\n"
        results_text += f"    结果: {r.get('output', '')[:500]}\n"
        if r.get("error"):
            results_text += f"    错误: {r['error']}\n"
    
    failed_text = ""
    if failed_tools:
        failed_text = f"\n\n执行失败的工具: {', '.join(failed_tools)}"
    
    return f"""请将以下工具执行结果整理为结构化的摘要，供主智能体生成最终回答使用。

原始查询：{query}

工具执行结果：
{results_text}{failed_text}

摘要要求：
1. 简明扼要，突出关键信息
2. 标注数据来源（工具名）
3. 如果工具失败，说明失败原因
4. 使用 Markdown 格式"""


def _build_fallback_summary(
    tool_results: List[Dict[str, Any]],
    failed_tools: List[str],
) -> str:
    """LLM 不可用时的人工摘要 fallback。"""
    lines = ["## 工具执行结果\n"]
    
    for r in tool_results:
        name = r.get("name", "unknown")
        status = "✅" if r.get("success") else "❌"
        lines.append(f"### {status} {name}")
        output = r.get("output", "")
        lines.append(output[:800] if output else "（无输出）")
        lines.append("")
    
    if failed_tools:
        lines.append(f"\n**失败工具**: {', '.join(failed_tools)}")
    
    return "\n".join(lines)
