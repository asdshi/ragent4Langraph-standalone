import asyncio

async def gen():
    try:
        yield 1
        yield 2
        print("after yield 2, returning")
    except asyncio.CancelledError as e:
        print(f"[Gen] CancelledError caught: {e}")
        import traceback
        traceback.print_stack()
        raise
    except GeneratorExit as e:
        print(f"[Gen] GeneratorExit caught: {e}")
        import traceback
        traceback.print_stack()
        raise
    finally:
        print("[Gen] finally")

async def main():
    g = gen()
    print(await g.__anext__())
    print(await g.__anext__())
    await g.aclose()
    print("[Main] after aclose")

asyncio.run(main())
