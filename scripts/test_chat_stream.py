import aiohttp
import asyncio

async def main():
    async with aiohttp.ClientSession() as session:
        resp = await session.post(
            "http://localhost:8000/api/v1/chat/stream",
            json={"query": "你好", "streaming": True, "top_k": 5}
        )
        print("Status:", resp.status)
        async for line in resp.content:
            print("LINE:", line.decode('utf-8').strip())

asyncio.run(main())
