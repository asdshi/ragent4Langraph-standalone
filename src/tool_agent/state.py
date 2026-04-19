"""
Tool Subgraph State — 工具子智能体的独立状态定义。

关键设计：
- internal_messages: 子图内部 ReAct 循环的消息，不返回主图
- tool_summary: 子图结束时产生的结构化摘要，写入主图 State
- tool_execution_trace: 执行轨迹，用于主图的可观测性
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict, Annotated
from langchain_core.messages import AnyMessage, ToolMessage
from langgraph.graph import add_messages


class ToolSubgraphState(TypedDict, total=False):
    """工具子智能体状态。
    
    与主图 RAGState 隔离，避免消息污染。
    子图结束时，只将 tool_summary + tool_execution_trace 写回主图。
    """
    
    # === 输入（从主图传入）===
    query: str
    target_tool: Optional[str]           # 主图指定的目标工具（可选，None 则自主决策）
    available_tools: List[Dict[str, Any]]  # 可用工具 schema（用于 think_node 的 LLM 决策）
    
    # === 内部状态（子图内部循环，不返回主图）===
    internal_messages: Annotated[List[AnyMessage], add_messages]
    tool_calls: List[Dict[str, Any]]     # LLM 决定的 tool calls [{name, arguments}]
    tool_results: List[Dict[str, Any]]   # 工具执行结果
    failed_tools: List[str]              # 执行失败的工具名
    iteration_count: int                 # ReAct 迭代计数（防无限循环）
    max_iterations: int                  # 最大迭代次数（默认 5）
    
    # === 输出（返回主图）===
    tool_summary: str                    # 结构化摘要
    tool_execution_trace: List[Dict[str, Any]]  # 执行轨迹
    
    # === 控制流 ===
    next_node: Optional[str]             # router_node 写入，控制下一个节点
