"""
探测 graph_task 是被谁、从哪里 cancel 的。
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
    print("[Test] Starting cancel source test...")
    initial_state = {"query": "你好", "conversation_id": None, "top_k": 5}
    
    # 直接 patch run_stream 内部逻辑到一个独立函数
    thread_id = "test_thread_cancel"
    config = {"configurable": {"thread_id": thread_id}}
    user_message = __import__('langchain_core.messages', fromlist=['HumanMessage']).HumanMessage(content=initial_state["query"])
    initial_state.setdefault("messages", []).append(user_message)
    
    workflow._token_queue = asyncio.Queue()
    workflow._trace_queue = asyncio.Queue()
    
    async def wrapped_ainvoke():
        try:
            return await workflow._compiled.ainvoke(initial_state, config)
        except asyncio.CancelledError:
            print("[CANCEL] wrapped_ainvoke caught CancelledError!")
            import traceback
            traceback.print_stack()
            raise
    
    graph_task = asyncio.create_task(wrapped_ainvoke())
    graph_task.add_done_callback(lambda t: print(f"[CANCEL] graph_task done_callback: cancelled={t.cancelled()}, exc={t.exception()!r}"))
    
    # 手动消费 __anext__，但这里甚至不走 run_stream，直接模拟 run_stream 的最小循环
    count = 0
    try:
        while True:
            if graph_task.done():
                print("[Test] graph_task done, draining...")
                while not workflow._token_queue.empty():
                    print("[Test] draining token:", await workflow._token_queue.get())
                while not workflow._trace_queue.empty():
                    print("[Test] draining trace:", await workflow._trace_queue.get())
                break
            
            token_task = asyncio.create_task(workflow._token_queue.get())
            trace_task = asyncio.create_task(workflow._trace_queue.get())
            
            print(f"[Test] awaiting wait #{count+1}")
            done, pending = await asyncio.wait(
                [graph_task, token_task, trace_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            
            print(f"[Test] wait done: {len(done)} tasks done, {len(pending)} pending")
            for t in done:
                print(f"[Test]   done task: {t.get_name() if hasattr(t, 'get_name') else t}, cancelled={t.cancelled()}")
            for t in pending:
                print(f"[Test]   pending task: {t.get_name() if hasattr(t, 'get_name') else t}, cancelling it...")
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
            
            for t in done:
                if t is graph_task:
                    print("[Test] graph_task completed")
                    continue
                result = t.result()
                count += 1
                print(f"[Test] Event {count}: {result}")
            
            if graph_task in done:
                break
        
        final_state = await graph_task
        print(f"[Test] final_state keys: {final_state.keys()}")
    except Exception as e:
        print(f"[Test] Exception: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
