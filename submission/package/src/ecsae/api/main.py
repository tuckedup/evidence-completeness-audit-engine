from __future__ import annotations

import os
import hashlib
import time
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from prometheus_fastapi_instrumentator import Instrumentator
from starlette.concurrency import run_in_threadpool

from ecsae import __version__
from ecsae.cache import TieredCache, encode_json
from ecsae.config import load_config
from ecsae.engine import AuditEngine
from ecsae.extract import RegexBiomedicalExtractor
from ecsae.extract.offline import GLiNERBiomedicalExtractor
from ecsae.models import AuditResponse, StudyRecord


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    records: list[StudyRecord] = Field(min_length=1, max_length=500)


class ExtractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=500_000)
    study_id: str | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    app.state.engine = AuditEngine(config)
    app.state.cache = TieredCache(
        config.memory_cache_entries,
        config.cache_ttl_seconds,
        os.environ.get("ECSAE_REDIS_URL"),
    )
    await app.state.cache.connect()
    app.state.transformer = None
    transformer_path = os.environ.get("ECSAE_TRANSFORMER_MODEL_PATH")
    if transformer_path:
        revision = os.environ.get("ECSAE_TRANSFORMER_MODEL_REVISION")
        app.state.transformer = GLiNERBiomedicalExtractor(transformer_path, revision=revision)
    yield
    await app.state.cache.close()


app = FastAPI(
    title="Evidence Completeness Statistical Audit Engine",
    version=__version__,
    default_response_class=ORJSONResponse,
    lifespan=lifespan,
)
Instrumentator(excluded_handlers=["/metrics"]).instrument(app).expose(
    app, endpoint="/metrics", include_in_schema=False
)


@app.middleware("http")
async def server_timing(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-ECSAE-Server-Ms"] = f"{(time.perf_counter() - started) * 1000:.6f}"
    return response


def _etag(key: str) -> str:
    return f'"{key}"'


def _raw_cache_key(raw: bytes, config_version: str, model_revision: str) -> str:
    digest = hashlib.sha256(raw).hexdigest()
    return f"raw-v1:{config_version}:{model_revision}:{digest}"


@app.post(
    "/audit",
    response_model=AuditResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": StudyRecord.model_json_schema()}},
        }
    },
)
async def audit(
    request: Request,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    engine: AuditEngine = request.app.state.engine
    cache: TieredCache = request.app.state.cache
    raw = await request.body()
    if len(raw) > 5_000_000:
        raise HTTPException(status_code=413, detail="audit payload exceeds 5 MB")
    key = _raw_cache_key(raw, engine.config.version, engine.config.model_revision)
    etag = _etag(key)
    if if_none_match == etag:
        return Response(status_code=304, headers={"ETag": etag, "X-ECSAE-Cache": "validated"})
    payload = await cache.get(key)
    cache_status = "hit"
    if payload is None:
        async with cache.lock(key):
            payload = await cache.get(key)
            if payload is None:
                try:
                    record = await run_in_threadpool(StudyRecord.model_validate_json, raw)
                except ValidationError as exc:
                    errors = exc.errors(include_input=False)
                    for error in errors:
                        error.pop("ctx", None)  # may contain a non-JSON ValueError instance
                    raise HTTPException(status_code=422, detail=errors) from exc
                result = await run_in_threadpool(engine.audit, record)
                payload = encode_json(result)
                await cache.set(key, payload)
                cache_status = "miss"
            else:
                cache_status = "hit-after-wait"
    return Response(
        content=payload,
        media_type="application/json",
        headers={"ETag": etag, "X-ECSAE-Cache": cache_status},
    )


@app.post("/audit/batch", response_model=list[AuditResponse])
async def audit_batch(batch: BatchRequest, request: Request) -> list[AuditResponse]:
    engine: AuditEngine = request.app.state.engine
    return await run_in_threadpool(lambda: [engine.audit(record) for record in batch.records])


@app.post("/extract")
async def extract(request_body: ExtractRequest) -> dict:
    """Deterministic numeric fallback; transformer extraction remains an offline batch job."""
    return RegexBiomedicalExtractor().extract(request_body.text, study_id=request_body.study_id)


@app.post("/extract/transformer")
async def extract_transformer(request_body: ExtractRequest, request: Request) -> dict:
    """Optional pinned GLiNER spans; excluded from the latency-critical audit benchmark."""
    extractor = request.app.state.transformer
    if extractor is None:
        raise HTTPException(
            status_code=503,
            detail="transformer is offline; use the transformer Docker profile or offline batch job",
        )
    spans = await run_in_threadpool(extractor.spans, request_body.text)
    return {
        "study_id": request_body.study_id,
        "model_revision": extractor.revision,
        "spans": spans,
    }


@app.get("/health")
async def health(request: Request) -> dict[str, str]:
    engine: AuditEngine = request.app.state.engine
    return {"status": "ok", "config_version": engine.config.version}


@app.get("/ready")
async def ready(request: Request) -> dict[str, str]:
    if not getattr(request.app.state, "engine", None):
        raise HTTPException(status_code=503, detail="engine is not loaded")
    return {
        "status": "ready",
        "transformer": "ready" if getattr(request.app.state, "transformer", None) else "offline",
    }


@app.get("/rules")
async def rules(request: Request) -> list[dict[str, str]]:
    return request.app.state.engine.rule_manifest()


@app.get("/version")
async def version(request: Request) -> dict[str, str]:
    engine: AuditEngine = request.app.state.engine
    return {
        "service": __version__,
        "config": engine.config.version,
        "operations_config": engine.config.operations_version,
        "model": engine.config.model_revision,
        "rules": engine.config.rules_version,
        "transformer": "ready" if getattr(request.app.state, "transformer", None) else "offline",
    }
