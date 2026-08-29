from asyncio import Runner
import uvloop



async def async_main() -> None:
    print("Hello from the async main!")


if __name__ == "__main__":
    with Runner(loop_factory=uvloop.new_event_loop) as runner:
        runner.run(async_main())
