"""
用已存在的对话测试 WebSocket trace。
"""
import asyncio
import aiohttp
import json

API_BASE = "http://localhost:8000"
WS_BASE = "ws://localhost:8000"
CONV_ID = "conv_7d769aeb472849bc"

async def main():
    async with aiohttp.ClientSession() as session:
        trace_msgs = []
        
        async def ws_reader():
            async with session.ws_connect(f"{WS_BASE}/ws/trace/{CONV_ID}") as ws:
                print(f"[Test] WS connected to {CONV_ID}")
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        trace_msgs.append(msg.data)
                        print(f"[Test] WS msg: {msg.data[:150]}")
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
        
        ws_task = asyncio.create_task(ws_reader())
        await asyncio.sleep(0.5)
        
        payload = {
            "query": "测试一下trace",
            "conversation_id": CONV_ID,
            "streaming": True,
            "top_k": 5,
            "temperature": 0.7,
        }
        print(f"[Test] Sending chat request for existing conv...")
        async with session.post(f"{API_BASE}/api/v1/chat/stream", json=payload) as resp:
            async for line in resp.content:
                line = line.decode('utf-8').strip()
                if line.startswith('data:'):
                    data = json.loads(line[5:].strip())
                    if data.get('type') == 'done':
                        print(f"[Test] SSE done")
                        break
        
        await asyncio.sleep(2)
        ws_task.cancel()
        try:
            await ws_task
        except asyncio.CancelledError:
            pass
        
        print(f"[Test] Total WS trace messages: {len(trace_msgs)}")

if __name__ == "__main__":
    asyncio.run(main())
