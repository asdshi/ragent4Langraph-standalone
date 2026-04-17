"""
测试三位一体的“时间裁剪”回滚功能
"""
import asyncio
import aiohttp
import json
import time

API_BASE = "http://localhost:8000"

async def main():
    async with aiohttp.ClientSession() as session:
        # 1. 创建对话
        resp = await session.post(f"{API_BASE}/api/v1/conversations", json={"title": "Rollback Test"})
        conv = await resp.json()
        conv_id = conv["conversation_id"]
        print(f"[Test] Created conversation: {conv_id}")
        
        # 2. 发送 3 轮消息
        message_ids = []
        for i, query in enumerate(["我叫张三，记住我的名字", "我喜欢 Python", "今天天气怎么样"]):
            payload = {
                "query": query,
                "conversation_id": conv_id,
                "streaming": False,
                "top_k": 5,
            }
            print(f"[Test] Sending turn {i+1}")
            resp = await session.post(f"{API_BASE}/api/v1/chat", json=payload)
            data = await resp.json()
            print(f"[Test] Turn {i+1} answer: {data['answer'][:30]}...")
            await asyncio.sleep(1.5)
        
        # 获取历史
        await asyncio.sleep(1)
        resp = await session.get(f"{API_BASE}/api/v1/history/{conv_id}")
        history_text = await resp.text()
        print(f"[Test] History raw status: {resp.status}")
        try:
            history = json.loads(history_text)
        except Exception:
            print(f"[Test] History raw: {history_text[:500]}")
            return
        
        print(f"[Test] History count before rollback: {history.get('message_count', 'N/A')}")
        for m in history.get("messages", []):
            print(f"  -> [{m['role']}] {m['content'][:30]}... (mid={m.get('message_id')}, turn={m.get('turn_id')})")
        
        assistant_msgs = [m for m in history.get("messages", []) if m["role"] == "assistant"]
        if len(assistant_msgs) < 2:
            print("[Test] ERROR: Not enough assistant messages to rollback")
            return
        
        target_msg = assistant_msgs[1]
        target_msg_id = target_msg["message_id"]
        print(f"[Test] Target message for rollback: {target_msg_id}")
        
        # 3. 调用回滚
        print(f"[Test] Calling rollback...")
        resp = await session.post(
            f"{API_BASE}/api/v1/conversations/{conv_id}/rollback",
            json={"target_message_id": target_msg_id}
        )
        result_text = await resp.text()
        print(f"[Test] Rollback raw status: {resp.status}")
        try:
            result = json.loads(result_text)
            print(f"[Test] Rollback result: {json.dumps(result, indent=2, ensure_ascii=False)}")
        except Exception:
            print(f"[Test] Rollback raw: {result_text[:500]}")
            return
        
        # 4. 验证历史
        await asyncio.sleep(0.5)
        resp = await session.get(f"{API_BASE}/api/v1/history/{conv_id}")
        history_after = await resp.json()
        print(f"[Test] History count after rollback: {history_after.get('message_count', 'N/A')}")
        for m in history_after.get("messages", []):
            print(f"  -> [{m['role']}] {m['content'][:30]}...")
        
        # 5. 验证 memory stats
        resp = await session.get(f"{API_BASE}/api/v1/memory/{conv_id}/stats")
        mem_stats = await resp.json()
        print(f"[Test] Memory stats after rollback: {json.dumps(mem_stats, indent=2, ensure_ascii=False)}")
        
        print("[Test] Done!")

if __name__ == "__main__":
    asyncio.run(main())
