
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from App.config import settings
from App.database import init_db
from App.issues import discover, list_issues, transition


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Patchwork API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        settings.frontend_origin,
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class DiscoverRequest(BaseModel):
    language: str = "Python"
    per_page: int = Field(default=15, ge=1, le=50)


class RejectRequest(BaseModel):
    reason: str = ""


@app.get("/")
def root():
    return {"name": "Patchwork", "status": "running"}


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/issues")
def get_issues(state: str | None = None):
    return {"items": list_issues(state)}


@app.post("/api/issues/discover")
async def run_discovery(payload: DiscoverRequest):
    try:
        items = await discover(payload.language, payload.per_page)
        return {"count": len(items), "items": items}
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                f"GitHub returned HTTP {exc.response.status_code}. "
                "Check your token or rate limit."
            ),
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="Could not reach GitHub.",
        ) from exc


@app.post("/api/issues/{issue_id}/approve-implementation")
def approve(issue_id: int):
    item = transition(issue_id, "in_progress")
    if not item:
        raise HTTPException(status_code=404, detail="Issue not found")
    return item


@app.post("/api/issues/{issue_id}/reject")
def reject(issue_id: int, payload: RejectRequest):
    item = transition(issue_id, "rejected", payload.reason)
    if not item:
        raise HTTPException(status_code=404, detail="Issue not found")
    return item


@app.post("/api/issues/{issue_id}/reconsider")
def reconsider(issue_id: int):
    rejected = list_issues("rejected")
    if not any(item["id"] == issue_id for item in rejected):
        raise HTTPException(
            status_code=409,
            detail="Issue is not rejected",
        )

    return transition(issue_id, "new")