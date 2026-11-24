"""
A local stand-in for the third-party APIs the platform depends on.

The point is narrow and worth stating. Four integrations - WhatsApp, Meta Conversions
API, the Facebook Page lookup and the assistant's LLM - cannot be exercised without
accounts the consultancy has to open. Until then the only evidence that our client code
is correct is that it compiles, which is no evidence at all. The first real proof would
otherwise arrive on the day a student's message silently fails to send.

So this module implements the vendors' documented wire formats closely enough to run the
*real* client code against: the same request builder, the same auth header, the same
response parsing, the same error handling. What it cannot prove is that the credential is
valid or that the vendor's behaviour matches its documentation. It proves our half.

Run standalone for manual poking:
    python3 -m tests.conformance_server          # serves on 127.0.0.1:8099
"""
from __future__ import annotations

import json
from typing import Any, Dict

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Scholara integration conformance server")
router = APIRouter()

# Everything the harness saw, so tests can assert on what we actually sent.
RECEIVED: list[Dict[str, Any]] = []

VALID_TOKEN = "conformance-valid-token"


def _record(kind: str, request: Request, body: Any, headers: Dict[str, str]) -> None:
    RECEIVED.append({"kind": kind, "path": str(request.url.path), "body": body,
                     "authorization": headers.get("authorization", "")})


def _require_bearer(authorization: str | None) -> str:
    """Mimic Meta's behaviour: a missing or wrong token is a 401 with their error shape."""
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, detail={"error": {"message": "An access token is required",
                                                   "type": "OAuthException", "code": 104}})
    if token != VALID_TOKEN:
        raise HTTPException(401, detail={"error": {"message": "Invalid OAuth access token",
                                                   "type": "OAuthException", "code": 190}})
    return token


# ------------------------------------------------------------------ Meta Graph API
@router.post("/{node_id}/messages")
async def whatsapp_send(node_id: str, request: Request,
                        authorization: str | None = Header(default=None)):
    """WhatsApp Cloud API send-message endpoint."""
    _require_bearer(authorization)
    body = await request.json()
    _record("whatsapp_send", request, body, dict(request.headers))

    if "messaging_product" not in body:
        # Meta rejects this specifically, and it is the most common client-side mistake.
        return JSONResponse(status_code=400, content={
            "error": {"message": "(#100) Param messaging_product must be provided",
                      "type": "OAuthException", "code": 100}})
    to = body.get("to") or ""
    return {"messaging_product": "whatsapp",
            "contacts": [{"input": to, "wa_id": to.lstrip("+")}],
            "messages": [{"id": "wamid.CONFORMANCE_TEST_ID"}]}


@router.post("/{node_id}/events")
async def capi_events(node_id: str, request: Request):
    """Conversions API event receipt. The access token travels in the body here."""
    body = await request.json()
    _record("capi_events", request, {**body, "_query": dict(request.query_params)},
            dict(request.headers))

    # Meta accepts the token either in the body or as a query parameter.
    if not (body.get("access_token") or request.query_params.get("access_token")):
        return JSONResponse(status_code=400, content={
            "error": {"message": "An access token is required to request this resource.",
                      "type": "OAuthException", "code": 104}})
    data = body.get("data") or []
    if not isinstance(data, list) or not data:
        return JSONResponse(status_code=400, content={
            "error": {"message": "(#100) param data must be a non-empty array",
                      "type": "OAuthException", "code": 100}})
    return {"events_received": len(data), "messages": [], "fbtrace_id": "CONFORMANCE"}


@router.get("/me")
async def graph_me(request: Request, authorization: str | None = Header(default=None)):
    """Facebook Page / token introspection."""
    _require_bearer(authorization)
    _record("graph_me", request, None, dict(request.headers))
    return {"id": "100000000000001", "name": "Scholara Bangladesh"}


@router.get("/{node_id}")
async def graph_node(node_id: str, request: Request,
                     authorization: str | None = Header(default=None)):
    """Generic node read - used for the WhatsApp phone number and the pixel."""
    _require_bearer(authorization)
    _record("graph_node", request, None, dict(request.headers))
    return {"id": node_id, "name": f"Conformance node {node_id}",
            "verified_name": "Scholara", "display_phone_number": "+8801700000000",
            "quality_rating": "GREEN"}


# -------------------------------------------------------------------- LLM providers
# xAI, Mistral and Gemini's OpenAI-compatible endpoint all speak this shape, which is why
# one handler covers all three.
@router.post("/chat/completions")
async def chat_completions(request: Request,
                           authorization: str | None = Header(default=None)):
    key = (authorization or "").removeprefix("Bearer ").strip()
    body = await request.json()
    _record("chat_completions", request, body, dict(request.headers))

    if not key:
        return JSONResponse(status_code=401,
                            content={"error": {"message": "Incorrect API key provided",
                                               "type": "invalid_request_error"}})
    # A key that is syntactically fine but unauthorised, so failover can be tested.
    if key == "rejected-key":
        return JSONResponse(status_code=401,
                            content={"error": {"message": "Invalid API key",
                                               "type": "authentication_error"}})
    if key == "rate-limited-key":
        return JSONResponse(status_code=429,
                            content={"error": {"message": "Rate limit exceeded",
                                               "type": "rate_limit_error"}})

    messages = body.get("messages") or []
    prompt = messages[-1].get("content", "") if messages else ""
    # Echo enough that a test can prove the prompt arrived intact.
    reply = f"CONFORMANCE REPLY to: {prompt[:120]}"
    return {
        "id": "chatcmpl-conformance",
        "object": "chat.completion",
        "model": body.get("model") or "conformance-model",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": reply}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 12, "total_tokens": 22},
    }


app.include_router(router)


@app.get("/__received")
def received():
    return RECEIVED


@app.post("/__reset")
def reset():
    RECEIVED.clear()
    return {"cleared": True}


if __name__ == "__main__":  # pragma: no cover
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8099, log_level="warning")
