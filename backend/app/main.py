"""FastAPI application entry point for VideoFlow AI backend.
The backend provides REST API under `/api/v1/` for project, revision, and export management.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.v1.router import router as api_v1_router

app = FastAPI(title="VideoFlow AI Backend", version="0.1.0")

# CORS – allow the frontend (served from the same origin or localhost) to talk to the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production restrict to specific origins.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API router with a prefix.
app.include_router(api_v1_router, prefix="/api/v1")

# Health endpoints
@app.get("/healthz", tags=["health"])
async def healthz() -> dict:
    return {"status": "ok"}

@app.get("/readyz", tags=["health"])
async def readyz() -> dict:
    # Could add DB connection check here.
    return {"status": "ready"}
