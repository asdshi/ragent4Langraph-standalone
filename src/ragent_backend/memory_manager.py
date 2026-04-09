"""
滚动窗口记忆管理器

核心职责：
1. 判断是否需要压缩消息（超出最大限制）
2. 执行压缩：将旧消息合并到 summary 中
3. 重写 summary 时保留核心实体
"""

from typing import List, Dict, Tuple, Optional, Any
from langchain_core.messages import HumanMessage, AIMessage, AnyMessage
import time


class RollingMemoryManager:
    """
    滑动窗口记忆管理器
    
    工作流程：
    1. 保持消息列表在 max_messages 以内
    2. 当超出限制时，保留 keep_recent 条最新消息
    3. 其余消息合并到 summary 中
    4. 异步归档被移除的消息到 MySQL
    """
    
    def __init__(
        self, 
        max_messages: int = 20, 
        keep_recent: int = 4,
        summary_max_length: int = 500
    ):
        self.max_messages = max_messages
        self.keep_recent = keep_recent
        self.summary_max_length = summary_max_length
    
    def should_compact(self, messages: List[AnyMessage]) -> bool:
        """判断消息列表是否需要压缩"""
        return len(messages) > self.max_messages
    
    async def compact(
        self,
        messages: List[AnyMessage],
        current_summary: str,
        llm: Optional[Any] = None
    ) -> Tuple[List[AnyMessage], str, List[Dict]]:
        """
        压缩记忆
        
        Args:
            messages: 当前所有消息
            current_summary: 现有摘要
            llm: 用于重写摘要的语言模型
            
        Returns:
            (保留的消息列表, 新摘要, 被归档的消息数据)
        """
        if len(messages) <= self.keep_recent:
            # 消息太少，全部保留
            return messages, current_summary, []
        
        # 保留最近的 keep_recent 条
        to_keep = messages[-self.keep_recent:]
        to_archive_msgs = messages[:-self.keep_recent]
        
        # 重写摘要
        new_summary = await self._rewrite_summary(
            llm, current_summary, to_archive_msgs
        )
        
        # 准备归档数据（用于 MySQL）
        archived_data = [
            {
                "role": "user" if isinstance(m, HumanMessage) else "assistant",
                "content": m.content,
                "message_id": m.id,
                "ts": time.time()
            }
            for m in to_archive_msgs
        ]
        
        return to_keep, new_summary, archived_data
    
    async def _rewrite_summary(
        self, 
        llm: Optional[Any], 
        existing_summary: str, 
        archived_msgs: List[AnyMessage]
    ) -> str:
        """
        重写摘要，保留核心实体
        
        Prompt 设计原则：
        1. 保留专有名词（项目名、人名、技术栈）
        2. 保留具体结论（数字、决策、事实）
        3. 保留用户偏好（喜好、约束、要求）
        4. 只压缩冗余描述和语气词
        """
        
        # 格式化归档消息
        conversation_text = "\n".join([
            f"User: {m.content}" if isinstance(m, HumanMessage) else f"Assistant: {m.content}"
            for m in archived_msgs
        ])
        
        prompt = f"""请更新对话摘要。你必须保留以下信息，只能压缩冗余内容：

现有摘要：
{existing_summary}

新归档的对话：
{conversation_text}

【核心实体保留原则】（绝对不能删除）：
1. 专有名词：项目名称、人名、公司名、技术栈、产品名
2. 具体结论：已经确定的事实、决策、数字、时间
3. 用户偏好：明确表达的喜好、约束、要求、反对意见
4. 上下文关键信息：会影响后续理解的前提条件

【输出要求】：
- 合并现有摘要和新对话的关键信息
- 长度控制在{self.summary_max_length}字以内
- 保留所有核心实体和结论
- 去除冗余描述、语气词、过渡语句
- 只输出摘要内容，不要解释

新摘要："""

        if llm is None:
            # 降级策略：简单拼接
            return self._fallback_summary(existing_summary, archived_msgs)
        
        try:
            # 调用 LLM 重写摘要
            response = await llm.ainvoke([HumanMessage(content=prompt)])
            result = response.content.strip()
            return result if result else self._fallback_summary(existing_summary, archived_msgs)
        except Exception as e:
            # 异常时降级
            print(f"[MemoryManager] Summary rewrite failed: {e}, using fallback")
            return self._fallback_summary(existing_summary, archived_msgs)
    
    def _fallback_summary(
        self, 
        existing_summary: str, 
        archived_msgs: List[AnyMessage]
    ) -> str:
        """
        降级摘要策略：简单拼接
        当 LLM 不可用时使用
        """
        snippets = []
        
        if existing_summary:
            snippets.append(f"[历史摘要] {existing_summary}")
        
        # 只保留最近4条关键信息
        for m in archived_msgs[-4:]:
            role = "用户" if isinstance(m, HumanMessage) else "助手"
            content = m.content[:120] + "..." if len(m.content) > 120 else m.content
            snippets.append(f"[{role}] {content}")
        
        return "\n".join(snippets)
    
    def get_stats(self, messages: List[AnyMessage], summary: str) -> Dict:
        """获取记忆统计信息（用于调试）"""
        return {
            "message_count": len(messages),
            "max_messages": self.max_messages,
            "keep_recent": self.keep_recent,
            "need_compact": self.should_compact(messages),
            "summary_length": len(summary),
        }
