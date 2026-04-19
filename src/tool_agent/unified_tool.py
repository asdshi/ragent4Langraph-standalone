"""
Unified Tool 抽象定义。

核心设计：LLM 对 Function Tool 和 MCP Tool 是无感知的。
它们看到的都是统一的 {name, description, parameters} schema。
"""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class ToolResult(BaseModel):
    """工具执行结果的标准化表示。
    
    无论底层是本地 Function 还是远程 MCP Server，
    执行结果都统一为此格式。
    """
    output: str = Field(
        default="",
        description="文本化执行结果（给 LLM 看的摘要）",
    )
    structured_data: Dict[str, Any] = Field(
        default_factory=dict,
        description="结构化数据（给 summarize_node / dashboard 用）",
    )
    latency_ms: float = Field(
        default=0.0,
        description="执行耗时（毫秒）",
    )
    success: bool = Field(
        default=True,
        description="是否成功执行",
    )
    error: Optional[str] = Field(
        default=None,
        description="错误信息（success=False 时）",
    )

    @classmethod
    def from_error(cls, error: str, latency_ms: float = 0.0) -> "ToolResult":
        """快速创建错误结果。"""
        return cls(
            output=f"工具执行失败: {error}",
            success=False,
            error=error,
            latency_ms=latency_ms,
        )


class ToolDecision(BaseModel):
    """LLM 在 ReAct 循环中的决策输出。
    
    think_node 使用结构化 LLM 调用输出此对象，
    router_node 根据 action 字段决定下一步路由。
    """
    thought: str = Field(
        description="对当前上下文的分析思考",
    )
    action: Literal["call_tool", "finish"] = Field(
        description="下一步动作: call_tool=调用工具, finish=结束并生成摘要",
    )
    tool_calls: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="当 action=call_tool 时，要调用的工具列表。每个元素含 name 和 arguments",
    )
    reasoning: Optional[str] = Field(
        default=None,
        description="决策理由（可观测用）",
    )


class UnifiedTool(BaseModel):
    """统一工具抽象。
    
    对 LLM 暴露的字段只有 name/description/input_schema，
    执行器 _executor 是内部实现细节，LLM 不可见。
    
    Attributes:
        name: 工具唯一标识（驼峰或下划线命名）
        description: 给 LLM 看的自然语言描述（含使用场景和参数说明）
        input_schema: JSON Schema（OpenAI function format）
        tool_type: "function" 本地函数 | "mcp" 远程 MCP Server
        timeout_seconds: 单次调用超时（默认 30s）
    """
    
    name: str = Field(..., description="工具唯一标识")
    description: str = Field(..., description="给 LLM 看的工具描述")
    input_schema: Dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema（OpenAI function format）",
    )
    tool_type: Literal["function", "mcp"] = Field(
        default="function",
        description="工具类型: function=本地, mcp=远程",
    )
    timeout_seconds: float = Field(
        default=30.0,
        description="单次调用超时（秒）",
    )
    
    # 内部执行器 —— 不序列化，不暴露给 LLM
    _executor: Optional[Callable[..., Awaitable[ToolResult]]] = None
    
    class Config:
        arbitrary_types_allowed = True
        # 不序列化 _executor
        exclude = {"_executor"}
    
    def set_executor(self, executor: Callable[..., Awaitable[ToolResult]]) -> None:
        """设置执行器（builder 模式）。"""
        self._executor = executor
    
    async def execute(self, **kwargs: Any) -> ToolResult:
        """执行工具。
        
        Args:
            **kwargs: input_schema 定义的参数。
            
        Returns:
            ToolResult: 标准化执行结果。
            
        Raises:
            RuntimeError: 如果 _executor 未设置。
        """
        if self._executor is None:
            return ToolResult.from_error(
                f"Tool '{self.name}' has no executor bound."
            )
        
        t0 = time.monotonic()
        try:
            result = await self._executor(**kwargs)
            result.latency_ms = (time.monotonic() - t0) * 1000
            return result
        except Exception as e:
            return ToolResult.from_error(
                error=str(e),
                latency_ms=(time.monotonic() - t0) * 1000,
            )
    
    def to_openai_schema(self) -> Dict[str, Any]:
        """转换为 OpenAI function calling schema。
        
        Returns:
            {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema or {"type": "object", "properties": {}}
            }
        }
    
    def to_anthropic_schema(self) -> Dict[str, Any]:
        """转换为 Anthropic tool_use schema（与 OpenAI 兼容）。"""
        return self.to_openai_schema()["function"]


def build_input_schema(
    properties: Dict[str, Any],
    required: Optional[List[str]] = None,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """辅助函数：快速构建符合 OpenAI function format 的 input_schema。
    
    Args:
        properties: 参数字段定义，如 {"query": {"type": "string", "description": "..."}}
        required: 必填字段列表
        description: schema 整体描述（可选）
        
    Returns:
        JSON Schema dict
        
    Example:
        >>> schema = build_input_schema(
        ...     properties={
        ...         "query": {"type": "string", "description": "搜索关键词"},
        ...         "top_k": {"type": "integer", "default": 5},
        ...     },
        ...     required=["query"],
        ... )
    """
    schema: Dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    if description:
        schema["description"] = description
    return schema
