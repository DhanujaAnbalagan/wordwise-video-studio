"""FastAPI application entry point for VideoFlow AI backend.
The backend provides REST API under `/api/v1/` for project, revision, and export management.
"""

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

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

# Exception handlers ensuring structured error response format (Section 12)
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": "HTTP_ERROR",
                "message": str(exc.detail),
                "retryable": False,
                "details": {},
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Invalid request parameters.",
                "retryable": False,
                "details": {"errors": [str(e) for e in exc.errors()]},
            }
        },
    )


# Include API router with a prefix.
app.include_router(api_v1_router, prefix="/api/v1")


# Health endpoints
@app.get("/healthz", tags=["health"])
async def healthz() -> dict:
    return {"status": "ok"}


@app.get("/readyz", tags=["health"])
async def readyz() -> dict:
    return {"status": "ready"}
