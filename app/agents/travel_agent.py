from collections.abc import AsyncIterator
from typing import Any

from app.agents.travel_graph import run_travel_graph, stream_travel_graph


async def run_travel_agent(message: str, thread_id: str | None = None) -> str:
    return await run_travel_graph(message=message, thread_id=thread_id)


async def stream_travel_agent(
    message: str,
    thread_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    async for item in stream_travel_graph(message=message, thread_id=thread_id):
        yield item
