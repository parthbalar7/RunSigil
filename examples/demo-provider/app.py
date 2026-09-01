from __future__ import annotations

import asyncio
import hashlib
import secrets
from typing import Annotated, Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from runsigil_contracts import canonical_digest
from runsigil_contracts.canonical import canonical_bytes
from runsigil_gateway.tokens import verify_audience_token


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RUNSIGIL_", env_file=".env", extra="ignore")

    demo_provider_audience: str = "runsigil-demo-provider"
    demo_provider_signing_key: str = Field(min_length=32)


class EffectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipient: str
    amount_cents: int = Field(gt=0)
    description: str
    simulate_outcome: str = "committed"


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    input: dict[str, Any]
    max_output_tokens: int = Field(ge=1, le=32_768)


settings = Settings()
app = FastAPI(title="RunSigil development provider", version="0.2.0")
effects: dict[str, dict[str, Any]] = {}
model_responses: dict[str, dict[str, Any]] = {}
lock = asyncio.Lock()


def authorize(authorization: str | None) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="audience-bound token required")
    try:
        return verify_audience_token(
            authorization.removeprefix("Bearer "),
            signing_key=settings.demo_provider_signing_key,
            audience=settings.demo_provider_audience,
        )
    except (ValueError, KeyError):
        raise HTTPException(status_code=401, detail="audience-bound token invalid") from None


@app.post("/effects")
async def create_effect(
    request: EffectRequest,
    authorization: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    claims = authorize(authorization)
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="idempotency key required")
    async with lock:
        existing = effects.get(idempotency_key)
        if existing is not None:
            return existing
        if request.simulate_outcome == "failed":
            raise HTTPException(status_code=422, detail="simulated provider rejection")
        receipt = {
            "effect_id": f"eff_{secrets.token_hex(8)}",
            "status": "committed",
            "credential_audience": claims["aud"],
            "credential_subject": claims["sub"],
            "action_id": claims["action_id"],
        }
        effects[idempotency_key] = receipt
    if request.simulate_outcome == "ambiguous_after_commit":
        await asyncio.sleep(5)
    return receipt


@app.get("/effects/{idempotency_key}")
async def get_effect(
    idempotency_key: str,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    authorize(authorization)
    effect = effects.get(idempotency_key)
    if effect is None:
        raise HTTPException(status_code=404, detail="effect not found")
    return effect


def _model_response(request: ModelRequest, claims: dict[str, Any]) -> dict[str, Any]:
    request_digest = canonical_digest(request.input)
    output = {
        "status": "completed",
        "model": request.model,
        "input_digest": request_digest,
    }
    reference_digest = hashlib.sha256(
        canonical_bytes(
            {
                "model": request.model,
                "input_digest": request_digest,
                "model_call_id": claims["model_call_id"],
            }
        )
    ).hexdigest()
    return {
        "output": output,
        "provider_reference": f"mdl_{reference_digest[:16]}",
        "input_tokens": max(1, (len(canonical_bytes(request.input)) + 3) // 4),
        "output_tokens": min(
            request.max_output_tokens,
            max(1, (len(canonical_bytes(output)) + 3) // 4),
        ),
        "cost_minor": 1,
        "credential_audience": claims["aud"],
        "credential_subject": claims["sub"],
        "model_call_id": claims["model_call_id"],
    }


@app.post("/models")
async def execute_model(
    request: ModelRequest,
    authorization: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    claims = authorize(authorization)
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="idempotency key required")
    if request.model != "demo-governed-model":
        raise HTTPException(status_code=422, detail="model is unsupported")
    if not isinstance(claims.get("model_call_id"), str):
        raise HTTPException(status_code=401, detail="model-call credential required")
    async with lock:
        existing = model_responses.get(idempotency_key)
        if existing is not None:
            return existing
        if request.input.get("simulate_outcome") == "failed":
            raise HTTPException(status_code=422, detail="simulated model rejection")
        response = _model_response(request, claims)
        model_responses[idempotency_key] = response
    if request.input.get("simulate_outcome") == "ambiguous_after_commit":
        await asyncio.sleep(5)
    return response


@app.get("/models/{idempotency_key}")
async def get_model_response(
    idempotency_key: str,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    claims = authorize(authorization)
    if not isinstance(claims.get("model_call_id"), str):
        raise HTTPException(status_code=401, detail="model-call credential required")
    response = model_responses.get(idempotency_key)
    if response is None:
        raise HTTPException(status_code=404, detail="model response not found")
    return response


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ready", "service": "runsigil-demo-provider"}


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8090)  # noqa: S104
