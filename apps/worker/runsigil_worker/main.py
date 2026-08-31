from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from sqlalchemy import text

from runsigil_worker.service import ActionWorker

worker = ActionWorker()
stop_event = asyncio.Event()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    task = asyncio.create_task(worker.run_forever(stop_event))
    yield
    stop_event.set()
    await task


app = FastAPI(title="RunSigil Action Worker", version="0.1.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "runsigil-action-worker"}


@app.get("/ready")
def ready() -> dict[str, str]:
    with worker.engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "ready", "database": "available"}


def run() -> None:
    uvicorn.run("runsigil_worker.main:app", host="0.0.0.0", port=8010)  # noqa: S104


if __name__ == "__main__":
    run()
