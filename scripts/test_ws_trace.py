"""
直接测试 WebSocket trace 事件是否从后端发出。
先创建对话，然后发送消息，同时连接 WebSocket 接收 trace。
"""
import asyncio
import aiohttp
import json

API_BASE = "http://localhost:8000"
WS_BASE = "ws://localhost:8000"

async def main():
    async with aiohttp.ClientSession() as session:
        # 1. 创建对话
        resp = await session.post(f"{API_BASE}/api/v1/conversations", json={"title": "WS Trace Test"})
        conv = await resp.json()
        conv_id = conv["conversation_id"]
        print(f"[Test] Created conversation: {conv_id}")
        
        # 2. 连接 WebSocket
        trace_msgs = []
        ws_task = None
        
        async def ws_reader():
            async with session.ws_connect(f"{WS_BASE}/ws/trace/{conv_id}") as ws:
                print(f"[Test] WS connected")
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        trace_msgs.append(msg.data)
                        print(f"[Test] WS msg: {msg.data[:150]}")
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
        
        ws_task = asyncio.create_task(ws_reader())
        await asyncio.sleep(0.5)  # 等待 WS 建立
        
        # 3. 发送流式请求（不读取 SSE，只触发后端 workflow）
        payload = {
            "query": "你好",
            "conversation_id": conv_id,
            "streaming": True,
            "top_k": 5,
            "temperature": 0.7,
        }
        print(f"[Test] Sending chat request...")
        async with session.post(f"{API_BASE}/api/v1/chat/stream", json=payload) as resp:
            # 简单读取 SSE，确保请求完成
            count = 0
            async for line in resp.content:
                line = line.decode('utf-8').strip()
                if line.startswith('data:'):
                    count += 1
                    data = json.loads(line[5:].strip())
                    if data.get('type') == 'done':
                        print(f"[Test] SSE done received")
                        break
            print(f"[Test] SSE lines received: {count}")
        
        await asyncio.sleep(2)  # 给 WS 一点时间接收消息
        ws_task.cancel()
        try:
            await ws_task
        except asyncio.CancelledError:
            pass
        
        print(f"[Test] Total WS trace messages: {len(trace_msgs)}")
        for m in trace_msgs:
            d = json.loads(m)
            print(f"  -> {d.get('type')} | {d.get('node')} | {d.get('step')} | {d.get('status')}")

if __name__ == "__main__":
    asyncio.run(main())
