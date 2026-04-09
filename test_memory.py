"""
测试滑动窗口记忆功能

运行方式：
1. 确保 MySQL 和 PostgreSQL/SQLite 已启动
2. 配置 .env 文件
3. python test_memory.py
"""

import asyncio
import os
from uuid import uuid4

# 设置测试环境变量
os.environ.setdefault("RAGENT_SQLITE_PATH", "test_checkpoints.sqlite")
os.environ.setdefault("RAGENT_MYSQL_HOST", "127.0.0.1")
os.environ.setdefault("RAGENT_MYSQL_PORT", "3306")
os.environ.setdefault("RAGENT_MYSQL_USER", "root")
os.environ.setdefault("RAGENT_MYSQL_PASSWORD", "")
os.environ.setdefault("RAGENT_MYSQL_DATABASE", "ragent")

from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite import SqliteSaver

from src.ragent_backend.workflow import RAGWorkflow
from src.ragent_backend.store import build_archive_store


async def test_rolling_memory():
    """测试滚动记忆功能"""
    
    print("=" * 60)
    print("测试滚动窗口记忆管理")
    print("=" * 60)
    
    # 初始化组件
    checkpointer = SqliteSaver.from_conn_string("test_checkpoints.sqlite")
    archive_store = build_archive_store()
    
    try:
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.7)
    except Exception as e:
        print(f"警告：无法初始化 LLM: {e}")
        print("使用 Mock LLM 继续测试...")
        llm = None
    
    # 创建工作流，设置较小的阈值方便测试
    workflow = RAGWorkflow(
        store=archive_store,
        llm=llm,
        checkpointer=checkpointer,
        max_messages=6,      # 超过 6 条就压缩
        keep_recent=2,       # 保留最近 2 条
    )
    
    conversation_id = str(uuid4())
    print(f"\n对话 ID: {conversation_id}")
    
    # 模拟多轮对话
    queries = [
        "你好，我是张三，在做一个人工智能项目",
        "我们项目用 Python 和 LangGraph",
        "能介绍一下 RAG 的最佳实践吗？",
        "具体如何设计记忆管理？",
        "滑动窗口和摘要哪个更好？",
        "帮我总结一下刚才的讨论",
        "那长期记忆怎么实现？",
        "用户画像如何构建？",
    ]
    
    for i, query in enumerate(queries, 1):
        print(f"\n--- 第 {i} 轮 ---")
        print(f"用户: {query}")
        
        initial_state = {
            "query": query,
            "conversation_id": conversation_id,
        }
        
        # 运行工作流
        final_state = await workflow.run(initial_state, thread_id=conversation_id)
        
        answer = final_state.get("final_answer", "[无回答]")
        print(f"助手: {answer[:100]}...")
        
        # 打印记忆统计
        stats = workflow.get_memory_stats(final_state)
        print(f"[记忆状态] 消息数: {stats['message_count']}/{stats['max_messages']}, "
              f"摘要长度: {stats['summary_length']}, 需压缩: {stats['need_compact']}")
    
    # 检查 MySQL 归档
    print("\n" + "=" * 60)
    print("检查 MySQL 归档的完整历史")
    print("=" * 60)
    
    try:
        full_history = await archive_store.load_full_history(conversation_id)
        print(f"MySQL 中归档了 {len(full_history)} 条消息:")
        for msg in full_history:
            role = msg['role']
            content = msg['content'][:50] + "..." if len(msg['content']) > 50 else msg['content']
            print(f"  [{role}] {content}")
    except Exception as e:
        print(f"无法加载归档历史: {e}")
    
    # 检查 Checkpoint
    print("\n" + "=" * 60)
    print("检查 Checkpoint 中的精简状态（给模型用的）")
    print("=" * 60)
    
    config = {"configurable": {"thread_id": conversation_id}}
    checkpoint = checkpointer.get(config)
    
    if checkpoint:
        state = checkpoint.get("channel_values", {})
        messages = state.get("messages", [])
        summary = state.get("summary", "")
        
        print(f"Checkpoint 中的消息数: {len(messages)}")
        print(f"摘要长度: {len(summary)}")
        print(f"\n摘要内容:\n{summary[:500]}...")
        print(f"\n最近消息:")
        for m in messages:
            role = "用户" if m.type == "human" else "助手"
            content = m.content[:50] + "..." if len(m.content) > 50 else m.content
            print(f"  [{role}] {content}")
    else:
        print("未找到 checkpoint")
    
    # 清理
    await archive_store.close()
    print("\n测试完成！")


if __name__ == "__main__":
    asyncio.run(test_rolling_memory())
