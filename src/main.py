"""FastAPI application entrypoint."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src import runtime
from src.auth.router import router as auth_router
from src.config import checkpoint_conninfo, configure_tracing, cors_allow_origins, settings
from src.decks.router import router as decks_router
from src.embeddings import embed_texts
from src.pitches.graph import compile_graph
from src.pitches.router import router as pitches_router
from src.rerank import rerank_texts

configure_tracing()

logger = logging.getLogger(__name__)

SHOW_DOCS_IN = {"local", "staging", "test"}


def warm_local_models() -> None:
    """Load embed + rerank models once at startup (download if missing)."""
    logger.info("Warming embedding model %s", settings.EMBEDDING_MODEL)
    embed_texts(["warmup"])
    logger.info("Warming reranker model %s", settings.RERANK_MODEL)
    rerank_texts("warmup", ["warmup"])
    logger.info("Local models ready")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.ENVIRONMENT == "test" or not settings.DATABASE_URL.startswith("postgresql"):
        runtime.graph = None
        yield
        return
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    async with AsyncPostgresSaver.from_conn_string(checkpoint_conninfo()) as saver:
        await saver.setup()
        runtime.graph = compile_graph(saver)
        await asyncio.to_thread(warm_local_models)
        yield
    runtime.graph = None


app_kwargs: dict = {"title": "Pitch Creator API", "version": "0.1.0", "lifespan": lifespan}
if settings.ENVIRONMENT not in SHOW_DOCS_IN:
    app_kwargs["openapi_url"] = None

app = FastAPI(**app_kwargs)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_allow_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=600,
)

api_v1 = APIRouter(prefix="/v1")
api_v1.include_router(auth_router)
api_v1.include_router(decks_router)
api_v1.include_router(pitches_router)
app.include_router(api_v1)


@app.get("/health", summary="Health check")
def health() -> dict[str, str]:
    return {"status": "ok"}
