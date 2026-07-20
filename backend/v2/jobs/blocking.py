from __future__ import annotations

import asyncio


async def owned_to_thread(function, *args, **kwargs):
    """Run blocking work to completion even when the awaiting task is canceled."""
    operation = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    canceled = False
    while not operation.done():
        try:
            await asyncio.shield(operation)
        except asyncio.CancelledError:
            canceled = True
            consume_current_cancellation()
    try:
        result = operation.result()
    except BaseException:
        if canceled:
            raise asyncio.CancelledError from None
        raise
    return canceled, result


async def run_owned_to_thread(function, *args, **kwargs):
    canceled, result = await owned_to_thread(function, *args, **kwargs)
    if canceled:
        raise asyncio.CancelledError
    return result


def consume_current_cancellation() -> None:
    current = asyncio.current_task()
    if current is not None:
        current.uncancel()
