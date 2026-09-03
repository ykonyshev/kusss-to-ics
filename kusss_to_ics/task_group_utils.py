from asyncio import Task, TaskGroup
from collections.abc import Callable, Coroutine, Iterable
from itertools import chain
from typing import Any, overload


@overload
async def async_map_collect[*Ts, RT](
    func: Callable[[*Ts], Coroutine[None, None, RT]],
    objects: Iterable[tuple[*Ts]]
) -> list[RT]:
    ...

@overload
async def async_map_collect[T, RT](
    func: Callable[[T], Coroutine[None, None, RT]],
    objects: Iterable[T]
) -> list[RT]:
    ...

async def async_map_collect[RT](
    func: Callable[..., Coroutine[None, None, RT]],
    objects: Iterable[Any | tuple[Any, ...]],
) -> list[RT]:
    tasks: list[Task[RT]] = []
    async with TaskGroup() as tg:
        for obj in objects:
            if isinstance(obj, tuple):
                task = tg.create_task(func(*obj))
            else:
                task = tg.create_task(func(obj))

            tasks.append(task)

    return list(map(Task[RT].result, tasks))

@overload
async def async_map_flatten_collect[*Ts, RT](
    func: Callable[[*Ts], Coroutine[None, None, Iterable[RT]]],
    objects: Iterable[tuple[*Ts]]
) -> list[RT]:
    ...

@overload
async def async_map_flatten_collect[T, RT](
    func: Callable[[T], Coroutine[None, None, Iterable[RT]]],
    objects: Iterable[T]
) -> list[RT]:
    ...

async def async_map_flatten_collect[RT](
    func: Callable[..., Coroutine[None, None, Iterable[RT]]],
    objects: Iterable[Any | tuple[Any, ...]],
) -> list[RT]:
    return list(chain.from_iterable(await async_map_collect(func, objects)))
