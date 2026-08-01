from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import create_db_and_tables
from app.routers.focus import router as focus_router
from app.routers.games import router as games_router
from app.routers.play import router as play_router
from app.routers.practice import router as practice_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    yield


app = FastAPI(title="chess-coach backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(games_router)
app.include_router(play_router)
app.include_router(focus_router)
app.include_router(practice_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
