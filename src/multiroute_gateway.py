import threading
import time
import uuid
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from multiroute_core import DemoEngine, DemoError, PROVIDERS


app = FastAPI(
    title="BW Interoperability — Multi-route Simulation",
    description=(
        "Synthetic bank-wallet and wallet-wallet transfers. "
        "All provider contracts are project-defined mocks."
    ),
    version="2.0.0",
)

WEB_FILE = (
    Path(__file__).resolve().parents[1]
    / "web"
    / "multiroute-demo.html"
)

SESSIONS = {}
SESSION_LOCK = threading.RLock()
SESSION_TTL = 4 * 60 * 60
MAX_SESSIONS = 50


class TransferRequest(BaseModel):
    source: str
    destination: str
    reference: str = Field(min_length=1, max_length=80)
    amount: str
    currency: str = "BWP"
    scenario: Literal[
        "SUCCESS",
        "PENDING",
        "DESTINATION_FAILED",
        "INSUFFICIENT_FUNDS",
        "COMPENSATION_FAILED",
        "CANCELLED",
    ] = "SUCCESS"


class ResolutionRequest(BaseModel):
    action: Literal["SUCCESS", "FAILED", "REFUND"]


@app.exception_handler(DemoError)
async def demo_error_handler(request: Request, exc: DemoError):
    return JSONResponse(
        status_code=exc.status,
        content={"detail": str(exc)},
    )


def session_engine(session_id):
    with SESSION_LOCK:
        record = SESSIONS.get(session_id)

        if record is None:
            raise HTTPException(
                status_code=404,
                detail="Demo session not found. Start a new session.",
            )

        if time.monotonic() - record["touched"] > SESSION_TTL:
            del SESSIONS[session_id]
            raise HTTPException(
                status_code=410,
                detail="Demo session expired. Start a new session.",
            )

        record["touched"] = time.monotonic()
        return record["engine"]


@app.get("/")
def interface():
    if not WEB_FILE.is_file():
        raise HTTPException(
            status_code=404,
            detail="Save the frontend as web/multiroute-demo.html.",
        )

    return FileResponse(
        WEB_FILE,
        headers={"Cache-Control": "no-store"},
    )


@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "2.0.0",
        "mode": "SIMULATION ONLY",
        "routes": [
            "BANK_TO_WALLET",
            "WALLET_TO_BANK",
            "WALLET_TO_WALLET",
        ],
    }


@app.post("/api/demo/sessions", status_code=201)
def create_session():
    with SESSION_LOCK:
        current_time = time.monotonic()

        expired = [
            session_id
            for session_id, record in SESSIONS.items()
            if current_time - record["touched"] > SESSION_TTL
        ]

        for session_id in expired:
            del SESSIONS[session_id]

        if len(SESSIONS) >= MAX_SESSIONS:
            raise HTTPException(
                status_code=503,
                detail="Demo capacity reached. Try again later.",
            )

        session_id = uuid.uuid4().hex
        engine = DemoEngine()

        SESSIONS[session_id] = {
            "engine": engine,
            "touched": current_time,
        }

        return {
            "session_id": session_id,
            "providers": PROVIDERS,
            **engine.overview(),
        }


@app.get("/api/demo/sessions/{session_id}")
def get_session(session_id: str):
    return session_engine(session_id).overview()


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
def resolve_transfer(
    session_id: str,
    transfer_id: str,
    body: ResolutionRequest,
):
    return session_engine(session_id).resolve(transfer_id, body.action)
