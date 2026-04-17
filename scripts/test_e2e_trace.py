"""
端到端测试 TracePanel + WebSocket
"""
import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1400, "height": 900})
        
        # 收集 console 和 ws 消息
        console_logs = []
        ws_messages = []
        
        page.on("console", lambda msg: console_logs.append(f"[{msg.type}] {msg.text}"))
        page.on("websocket", lambda ws: (
            print(f"[WS] Connected: {ws.url}"),
            ws.on("framereceived", lambda payload: ws_messages.append(payload))
        ))
        
        print("[Test] Navigating to frontend...")
        await page.goto("http://localhost:5173/")
        await page.wait_for_load_state("networkidle")
        await asyncio.sleep(1)
        
        # 找到输入框并发送消息
        print("[Test] Typing message...")
        textarea = page.locator(".message-input textarea")
        await textarea.fill("你好")
        
        print("[Test] Clicking send button...")
        send_btn = page.locator("button.send-btn").first
        await send_btn.click()
        
        # 等待 trace panel 按钮出现（发送后 header 上会出现 Trace 按钮）
        print("[Test] Waiting for Trace button...")
        trace_btn = page.locator("header button:has-text('Trace')")
        await trace_btn.wait_for(state="visible", timeout=15000)
        
        # 点击展开 trace panel
        print("[Test] Clicking Trace button...")
        await trace_btn.click()
        await asyncio.sleep(0.5)
        
        # 等待一些 trace 事件出现
        print("[Test] Waiting for trace events...")
        await page.locator("text=session").first.wait_for(state="visible", timeout=15000)
        
        # 截图
        await page.screenshot(path="e2e_trace_test.png", full_page=True)
        print("[Test] Screenshot saved to e2e_trace_test.png")
        
        # 等流式响应完成
        print("[Test] Waiting for stream to complete...")
        await asyncio.sleep(10)
        
        # 最终截图
        await page.screenshot(path="e2e_trace_test_final.png", full_page=True)
        
        # 检查 WS 消息
        trace_ws_msgs = [m for m in ws_messages if '"type":"trace"' in m or '"trace"' in m]
        print(f"[Test] Total WS messages: {len(ws_messages)}")
        print(f"[Test] Trace WS messages: {len(trace_ws_msgs)}")
        for m in trace_ws_msgs[:5]:
            print(f"[Test] WS trace: {m[:200]}")
        
        await browser.close()
        print("[Test] Done!")

if __name__ == "__main__":
    asyncio.run(main())
