"""
直接测试 graph_task = asyncio.create_task(workflow._compiled.ainvoke(...))
看 task 是否会自动被 cancel。
"""
import asyncio
import os
from dotenv import load_dotenv
load_dotenv()

from src.ragent_backend.workflow import RAGWorkflow
from src.ragent_backend.store import build_archive_store
from langchain_openai import ChatOpenAI

archive_store = build_archive_store()
llm = ChatOpenAI(
    model=os.getenv("RAGENT_LLM_MODEL", "gpt-4o"),
    temperature=0.7,
)

workflow = RAGWorkflow(
    store=archive_store,
    llm=llm,
    checkpointer=None,
    max_messages=20,
    keep_recent=4,
)

async def main():
    print("[Test] Starting direct ainvoke test...")
    initial_state = {"query": "你好", "conversation_id": None, "top_k": 5}
    config = {"configurable": {"thread_id": "test_thread_456"}}
    
    # 直接创建 task
    coro = workflow._compiled.ainvoke(initial_state, config)
    print(f"[Test] ainvoke returns: {type(coro)}")
    
    task = asyncio.create_task(coro)
    
    def _cb(t):
        print(f"[Test] task callback: cancelled={t.cancelled()}, done={t.done()}")
        if t.exception():
            print(f"[Test] task exception: {t.exception()}")
    task.add_done_callback(_cb)
    
    try:
        result = await task
        print(f"[Test] ainvoke success! final_answer={result.get('final_answer', '')[:50]}...")
    except asyncio.CancelledError:
        print("[Test] task was CancelledError!")
    except Exception as e:
        print(f"[Test] task raised {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
