import base64
import os
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from multiroute_core import DemoEngine, DemoError
from registry import FEE_POLICIES, capability_matrix, participants_view

app = FastAPI(
    title="BW Interoperability: Multi-route Simulation",
    description="Synthetic transfers. All provider contracts are project-defined mocks.",
    version="3.0.0",
)

WEB_FILE = Path(__file__).resolve().parents[1] / "web" / "multiroute-demo.html"
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD")

SESSIONS = {}
SESSION_LOCK = threading.RLock()
SESSION_TTL = 4 * 60 * 60
MAX_SESSIONS = 50


class QuoteRequest(BaseModel):
    source: str
    destination: str
    amount: str
    fee_policy: str = "NONE"


class TransferRequest(BaseModel):
    source: str
    destination: str
    reference: str = Field(min_length=1, max_length=80)
    amount: str
    currency: str = "BWP"
    quote_id: str = Field(min_length=1, max_length=40)
    scenario: Literal[
        "SUCCESS", "PENDING", "DESTINATION_FAILED",
        "INSUFFICIENT_FUNDS", "COMPENSATION_FAILED", "CANCELLED",
    ] = "SUCCESS"


class ResolutionRequest(BaseModel):
    action: Literal["SUCCESS", "FAILED", "REFUND"]


@app.middleware("http")
async def require_password(request: Request, call_next):
    if not DEMO_PASSWORD or request.url.path == "/health":
        return await call_next(request)

    header = request.headers.get("authorization", "")
    if header.startswith("Basic "):
        try:
            supplied = base64.b64decode(header[6:]).decode("utf-8").partition(":")[2]
        except Exception:
            supplied = ""
        if secrets.compare_digest(supplied.encode(), DEMO_PASSWORD.encode()):
            return await call_next(request)

    return Response(
        content="Authentication required.",
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="BW Interop Lab"'},
    )


@app.exception_handler(DemoError)
async def demo_error_handler(request: Request, exc: DemoError):
    return JSONResponse(status_code=exc.status, content={"detail": str(exc)})


def session_engine(session_id):
    with SESSION_LOCK:
        record = SESSIONS.get(session_id)
        if record is None:
            raise HTTPException(404, "Demo session not found. Start a new session.")
        if time.monotonic() - record["touched"] > SESSION_TTL:
            del SESSIONS[session_id]
            raise HTTPException(410, "Demo session expired. Start a new session.")
        record["touched"] = time.monotonic()
        return record["engine"]


@app.get("/")
def interface():
    if not WEB_FILE.is_file():
        raise HTTPException(404, "Save the frontend as web/multiroute-demo.html.")
    return FileResponse(WEB_FILE, headers={"Cache-Control": "no-store"})


@app.get("/health")
def health():
    return {"status": "ok", "version": "3.0.0", "mode": "SIMULATION ONLY"}


@app.post("/api/demo/sessions", status_code=201)
def create_session():
    with SESSION_LOCK:
        current = time.monotonic()
        for sid in [
            s for s, r in SESSIONS.items() if current - r["touched"] > SESSION_TTL
        ]:
            del SESSIONS[sid]

        if len(SESSIONS) >= MAX_SESSIONS:
            raise HTTPException(503, "Demo capacity reached. Try again later.")

        session_id = uuid.uuid4().hex
        engine = DemoEngine()
        SESSIONS[session_id] = {"engine": engine, "touched": current}

        return {
            "session_id": session_id,
            "providers": participants_view(),
            "capabilities": capability_matrix(),
            "fee_policies": FEE_POLICIES,
            **engine.overview(),
        }


@app.get("/api/demo/sessions/{session_id}")
def get_session(session_id: str):
    return session_engine(session_id).overview()


@app.post("/api/demo/sessions/{session_id}/quotes")
def create_quote(session_id: str, body: QuoteRequest):
    return session_engine(session_id).quote(body.model_dump())


@app.post("/api/demo/sessions/{session_id}/transfers")
def create_transfer(session_id: str, body: TransferRequest):
    return session_engine(session_id).create(body.model_dump())


@app.get("/api/demo/sessions/{session_id}/transfers/{transfer_id}")
def get_transfer(session_id: str, transfer_id: str):
    return session_engine(session_id).get(transfer_id)


@app.post("/api/demo/sessions/{session_id}/transfers/{transfer_id}/advance")
def advance_transfer(session_id: str, transfer_id: str):
    return session_engine(session_id).advance(transfer_id)


@app.post("/api/demo/sessions/{session_id}/transfers/{transfer_id}/resolve")
def resolve_transfer(session_id: str, transfer_id: str, body: ResolutionRequest):
    return session_engine(session_id).resolve(transfer_id, body.action)
