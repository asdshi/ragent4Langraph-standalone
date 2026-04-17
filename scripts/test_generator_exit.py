import asyncio

async def gen():
    try:
        yield 1
        yield 2
        print("after yield 2, returning")
    except asyncio.CancelledError as e:
        print(f"[Gen] CancelledError caught: {e}")
        raise
    except GeneratorExit as e:
        print(f"[Gen] GeneratorExit caught: {e}")
        raise
    except BaseException as e:
        print(f"[Gen] BaseException caught: {type(e).__name__}: {e}")
        raise
    finally:
        print("[Gen] finally")

async def main():
    async for item in gen():
        print(f"[Main] got {item}")
        if item == 2:
            break
    print("[Main] after loop")

asyncio.run(main())
