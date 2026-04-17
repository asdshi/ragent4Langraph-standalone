"""
直接测试 workflow.run_stream()，绕过 HTTP 层，看是不是 app.py 的问题。
注意：这里不 break，让 async for 自然结束，模拟 app.py 的 _pump_stream 行为。
"""
import asyncio
import os
from dotenv import load_dotenv
load_dotenv()

from src.ragent_backend.workflow import RAGWorkflow
from src.ragent_backend.store import build_archive_store
from langchain_openai import ChatOpenAI

checkpointer = None  # 不用 checkpointer，简化测试
archive_store = build_archive_store()

llm = ChatOpenAI(
    model=os.getenv("RAGENT_LLM_MODEL", "gpt-4o"),
    temperature=0.7,
)

workflow = RAGWorkflow(
    store=archive_store,
    llm=llm,
    checkpointer=checkpointer,
    max_messages=20,
    keep_recent=4,
)

async def main():
    print("[Test] Starting direct run_stream test...")
    initial_state = {"query": "你好", "conversation_id": None, "top_k": 5}
    count = 0
    final_answer = ""
    try:
        async for event in workflow.run_stream(initial_state, thread_id="test_thread_123"):
            count += 1
            print(f"[Test] Event {count}: type={event.get('type')}, node={event.get('node')}, step={event.get('step')}")
            if event.get("type") == "done":
                final_answer = event.get("state", {}).get("final_answer", "")
                print(f"[Test] Done! final_answer={final_answer[:50]}...")
        print(f"[Test] Stream exhausted naturally.")
    except Exception as e:
        print(f"[Test] Exception: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
    print(f"[Test] Total events: {count}")

if __name__ == "__main__":
    asyncio.run(main())
