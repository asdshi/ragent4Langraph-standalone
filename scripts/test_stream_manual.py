"""
手动驱动 run_stream() 的 __anext__()，看是不是 async for 的问题。
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
    print("[Test] Starting manual anext test...")
    initial_state = {"query": "你好", "conversation_id": None, "top_k": 5}
    stream = workflow.run_stream(initial_state, thread_id="test_thread_manual")
    count = 0
    try:
        while True:
            print(f"[Test] Calling __anext__() #{count+1}")
            event = await stream.__anext__()
            count += 1
            print(f"[Test] Event {count}: type={event.get('type')}, node={event.get('node')}, step={event.get('step')}")
            if event.get("type") == "done":
                print(f"[Test] Done!")
                break
    except StopAsyncIteration:
        print("[Test] StopAsyncIteration")
    except Exception as e:
        print(f"[Test] Exception: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
    print(f"[Test] Total events: {count}")

if __name__ == "__main__":
    asyncio.run(main())
