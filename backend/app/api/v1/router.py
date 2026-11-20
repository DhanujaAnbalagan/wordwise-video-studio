"""API router for VideoFlow AI backend version 1.
Provides minimal CRUD endpoints for projects and instruction execution.
The heavy‐lifting (engine interpreter) is imported from the existing frontend
codebase (src/engine).  This keeps the data model identical to the client.
"""

from fastapi import APIRouter, Depends, HTTPException, Header, Request, status
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

from ...database import SessionLocal, init_db
from ... import models
import datetime

# Ensure DB tables exist on import
init_db()

router = APIRouter()

# Dependency – provide a DB session per request
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ---------- Pydantic schemas ----------
class ProjectCreate(BaseModel):
    id: str = Field(..., description="Project identifier (UUID or slug)")
    name: str
    source_video_path: str
    source_hash: str
    transcript: Dict[str, Any]

class ProjectResponse(BaseModel):
    id: str
    name: str
    created_at: datetime.datetime
    source_video_path: str
    source_hash: str
    transcript: Dict[str, Any]

class InstructionRequest(BaseModel):
    instruction: str
    # Optional hint for UI – ignored by backend for now.
    idempotency_key: Optional[str] = None

class InstructionResult(BaseModel):
    revision_id: str
    status: str
    notes: List[str]
    # Echo back the operation list for debugging.
    ops: Optional[List[Dict[str, Any]]] = None

# ---------- Helper – import engine ----------
# The engine lives in the frontend src/engine package.  We import the interpreter
# and related types directly.  This works because the backend runs in the same
# checkout and the Python path includes the repository root.
import sys, os
# Add repository root to sys.path so that src package can be imported
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../"))
if repo_root not in sys.path:
    sys.path.append(repo_root)
"""Placeholder for the VideoFlow AI editing engine.
The real engine lives in the frontend TypeScript code under `src/engine`.
At this stage we expose a stub `interpret_instruction` that returns a minimal
`applied` result so that the API can be exercised without invoking the TS
interpreter.  The stub will be replaced with a proper bridge in a later phase.
"""
def interpret_instruction(*, instruction: str, state, transcript):
    """Return a minimal applied result.
    This stub satisfies the instruction endpoint without invoking the TS engine.
    """
    return {
        "id": "dummy_rev",
        "status": "applied",
        "notes": [],
        "ops": [],
        "after": state,
    }

from ...src.engine.types import TimelineState, Revision as EngineRevision

# ---------- Project endpoints ----------
@router.post("/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, db: SessionLocal = Depends(get_db)):
    if db.query(models.Project).filter(models.Project.id == payload.id).first():
        raise HTTPException(status_code=409, detail="Project already exists")
    project = models.Project(
        id=payload.id,
        name=payload.name,
        source_video_path=payload.source_video_path,
        source_hash=payload.source_hash,
        transcript=payload.transcript,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return ProjectResponse(**project.__dict__)

@router.get("/projects/{project_id}", response_model=ProjectResponse)
def get_project(project_id: str, db: SessionLocal = Depends(get_db)):
    project = db.query(models.Project).filter(models.Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectResponse(**project.__dict__)

# ---------- Instruction execution ----------
@router.post("/projects/{project_id}/instructions", response_model=InstructionResult)
def run_instruction(
    project_id: str,
    payload: InstructionRequest,
    db: SessionLocal = Depends(get_db),
    request: Request = None,
    x_idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    # Locate project and its latest revision (head).
    project = db.query(models.Project).filter(models.Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    # Find latest revision for this project (by created_at descending).
    latest_rev = (
        db.query(models.Revision)
        .filter(models.Revision.project_id == project_id)
        .order_by(models.Revision.created_at.desc())
        .first()
    )
    # If none, create an initial revision from the transcript duration.
    if not latest_rev:
        # Build initial TimelineState using engine helper.
        from ...src.engine.timeline import initialState
        source_duration = payload.transcript.get("durationMs") if hasattr(payload, "transcript") else 0
        state: TimelineState = initialState(source_duration)
        rev = models.Revision(
            id="initial",
            project_id=project_id,
            parent_id=None,
            kind="initial",
            label="Initial state",
            ops=[],
            state=state.__dict__,
        )
        db.add(rev)
        db.commit()
        db.refresh(rev)
        latest_rev = rev
    # Run the interpreter on the instruction string.
    try:
        result = interpret_instruction(
            instruction=payload.instruction,
            state=latest_rev.state,  # type: ignore[arg-type]
            transcript=project.transcript,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    # Persist a new revision if the instruction was applied.
    if result["status"] == "applied":
        new_rev = models.Revision(
            id=result["id"],
            project_id=project_id,
            parent_id=latest_rev.id,
            kind="edit",
            label=payload.instruction[:50],
            ops=result.get("ops", []),
            state=result.get("after", {}),
        )
        db.add(new_rev)
        db.commit()
        db.refresh(new_rev)
    else:
        new_rev = None
    return InstructionResult(
        revision_id=new_rev.id if new_rev else latest_rev.id,
        status=result["status"],
        notes=result.get("notes", []),
        ops=result.get("ops"),
    )
