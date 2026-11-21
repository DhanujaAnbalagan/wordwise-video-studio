"""API router for VideoFlow AI backend version 1.
Provides REST API endpoints for sessions/projects, revisions, and natural-language instruction execution.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import time
import uuid
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from ... import models
from ...database import get_db
from ...engine.executor import Session as EngineSession
from ...engine.executor import run_instruction as executor_run_instruction
from ...engine.history import head
from ...engine.interpreter import (
    ClarificationOptionSchema,
    ClarificationSchema,
    OperationPlan,
    PlannedOperation,
    _op_to_planned,
)
from ...engine.serialize import (
    initial_state,
    op_from_dict,
    ops_to_list,
    state_from_dict,
    state_to_dict,
    timeline_hash,
    transcript_from_dict,
)
from ...engine.types import History as EngineHistory, Revision, Transcript

router = APIRouter()

# Include health endpoints
from backend.app.api.v1.health import router as health_router
router.include_router(health_router, tags=["health"])


# ---------------------------------------------------------------------------
# Pydantic Schemas
# ---------------------------------------------------------------------------

class SessionCreate(BaseModel):
    id: Optional[str] = Field(None, description="Session ID (UUID or slug). Generated if omitted.")
    name: Optional[str] = "Untitled Project"
    duration_ms: Optional[int] = Field(60000, description="Duration in ms when no transcript/media is provided")
    source_video_path: Optional[str] = None
    source_hash: Optional[str] = None
    transcript: Optional[Dict[str, Any]] = None
    media: Optional[Dict[str, Any]] = None


class SessionResponse(BaseModel):
    id: str
    name: str
    status: str
    created_at: datetime.datetime
    updated_at: datetime.datetime
    current_revision_id: Optional[str] = None
    undo_stack: List[str] = Field(default_factory=list)
    redo_stack: List[str] = Field(default_factory=list)
    transcript: Optional[Dict[str, Any]] = None
    media: Optional[Dict[str, Any]] = None


class InstructionRequest(BaseModel):
    instruction: str = Field(..., description="Natural-language instruction, e.g. 'Remove the first 10 seconds'")
    idempotency_key: Optional[str] = Field(None, description="Optional client idempotency key")


class InstructionResponse(BaseModel):
    status: Literal["APPLIED", "NEEDS_CLARIFICATION", "REJECTED"]
    instruction_id: str
    operations: List[PlannedOperation] = Field(default_factory=list)
    revision_id: Optional[str] = None
    message: str
    clarification: Optional[ClarificationSchema] = None
    reason: Optional[str] = None


class ErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool = False
    details: Dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorDetail


# ---------------------------------------------------------------------------
# Helper: Idempotency & Database
# ---------------------------------------------------------------------------

def _raise_error(status_code: int, code: str, message: str, details: Optional[Dict[str, Any]] = None) -> None:
    raise HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message, "retryable": False, "details": details or {}}},
    )


def _check_idempotency_before(
    db: DbSession,
    key: Optional[str],
    method: str,
    path: str,
    payload_str: str,
) -> Optional[Response]:
    if not key or not key.strip():
        return None
    request_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()
    record = (
        db.query(models.IdempotencyRecord)
        .filter(
            models.IdempotencyRecord.key == key,
            models.IdempotencyRecord.method == method,
        )
        .first()
    )
    if record:
        if record.request_hash != request_hash:
            _raise_error(
                status.HTTP_409_CONFLICT,
                "IDEMPOTENCY_CONFLICT",
                "Idempotency key was previously used with a different request payload.",
            )
        if record.state == "done" and record.response_body is not None:
            return Response(
                content=record.response_body,
                status_code=record.status_code or 200,
                media_type=record.response_type or "application/json",
            )
    return None


def _save_idempotency_after(
    db: DbSession,
    key: Optional[str],
    method: str,
    path: str,
    payload_str: str,
    status_code: int,
    response_body: str,
) -> None:
    if not key or not key.strip():
        return
    request_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()
    record = (
        db.query(models.IdempotencyRecord)
        .filter(
            models.IdempotencyRecord.key == key,
            models.IdempotencyRecord.method == method,
        )
        .first()
    )
    if record:
        record.state = "done"
        record.status_code = status_code
        record.response_body = response_body
        record.response_type = "application/json"
    else:
        record = models.IdempotencyRecord(
            key=key,
            method=method,
            path=path,
            request_hash=request_hash,
            state="done",
            status_code=status_code,
            response_body=response_body,
            response_type="application/json",
        )
        db.add(record)


# ---------------------------------------------------------------------------
# Session / Project Endpoints
# ---------------------------------------------------------------------------

@router.post("/sessions", response_model=SessionResponse, status_code=status.HTTP_201_CREATED, tags=["sessions"])
@router.post("/projects", response_model=SessionResponse, status_code=status.HTTP_201_CREATED, tags=["projects"])
def create_session(payload: SessionCreate, db: DbSession = Depends(get_db)):
    session_id = payload.id or f"proj_{uuid.uuid4().hex[:12]}"
    if db.query(models.Project).filter(models.Project.id == session_id).first():
        _raise_error(status.HTTP_409_CONFLICT, "SESSION_ALREADY_EXISTS", f"Session '{session_id}' already exists.")

    duration_ms = payload.duration_ms or 60000
    if payload.transcript and "durationMs" in payload.transcript:
        duration_ms = int(payload.transcript["durationMs"])
    elif payload.media and "durationMs" in payload.media:
        duration_ms = int(payload.media["durationMs"])

    media_dict = dict(payload.media or {})
    if payload.source_video_path and "storageKey" not in media_dict:
        media_dict["storageKey"] = payload.source_video_path
    if payload.source_hash and "hash" not in media_dict:
        media_dict["hash"] = payload.source_hash

    project = models.Project(
        id=session_id,
        name=payload.name or "Untitled Project",
        status="ready",
        media=media_dict or None,
        transcript=payload.transcript,
        undo_stack=["r0"],
        redo_stack=[],
    )
    db.add(project)

    # Create initial revision r0
    init_st = initial_state(duration_ms)
    r0 = models.RevisionRow(
        project_id=session_id,
        rev_id="r0",
        index=0,
        parent_id=None,
        kind="initial",
        label="Original upload",
        ops=[],
        state=state_to_dict(init_st),
        hash=timeline_hash(init_st),
        created_ms=int(time.time() * 1000),
    )
    db.add(r0)
    db.commit()
    db.refresh(project)

    return SessionResponse(
        id=project.id,
        name=project.name,
        status=project.status,
        created_at=project.created_at,
        updated_at=project.updated_at,
        current_revision_id="r0",
        undo_stack=project.undo_stack,
        redo_stack=project.redo_stack,
        transcript=project.transcript,
        media=project.media,
    )


@router.get("/sessions/{session_id}", response_model=SessionResponse, tags=["sessions"])
@router.get("/projects/{session_id}", response_model=SessionResponse, tags=["projects"])
def get_session(session_id: str, db: DbSession = Depends(get_db)):
    project = db.query(models.Project).filter(models.Project.id == session_id).first()
    if not project:
        _raise_error(status.HTTP_404_NOT_FOUND, "SESSION_NOT_FOUND", f"Session '{session_id}' not found.")
    head_rev_id = project.undo_stack[-1] if project.undo_stack else "r0"
    return SessionResponse(
        id=project.id,
        name=project.name,
        status=project.status,
        created_at=project.created_at,
        updated_at=project.updated_at,
        current_revision_id=head_rev_id,
        undo_stack=project.undo_stack or [],
        redo_stack=project.redo_stack or [],
        transcript=project.transcript,
        media=project.media,
    )


# ---------------------------------------------------------------------------
# Instruction Execution Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/sessions/{session_id}/instructions",
    response_model=InstructionResponse,
    status_code=status.HTTP_200_OK,
    tags=["instructions"],
)
@router.post(
    "/projects/{session_id}/instructions",
    response_model=InstructionResponse,
    status_code=status.HTTP_200_OK,
    tags=["instructions"],
)
def run_instruction(
    session_id: str,
    payload: InstructionRequest,
    db: DbSession = Depends(get_db),
    x_idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    idempotency_key = x_idempotency_key if isinstance(x_idempotency_key, str) else (payload.idempotency_key if payload else None)
    path = f"/api/v1/sessions/{session_id}/instructions"
    payload_str = payload.instruction.strip()

    # 1. Idempotency check
    cached = _check_idempotency_before(db, idempotency_key, "POST", path, payload_str)
    if cached is not None:
        return cached

    # 2. Check session existence
    project = db.query(models.Project).filter(models.Project.id == session_id).first()
    if not project:
        _raise_error(status.HTTP_404_NOT_FOUND, "SESSION_NOT_FOUND", f"Session '{session_id}' not found.")

    # 3. Validate instruction input
    if not payload.instruction or not payload.instruction.strip():
        _raise_error(status.HTTP_400_BAD_REQUEST, "EMPTY_INSTRUCTION", "Instruction cannot be empty.")

    # 4. Load revisions
    rev_rows = (
        db.query(models.RevisionRow)
        .filter(models.RevisionRow.project_id == session_id)
        .order_by(models.RevisionRow.index.asc())
        .all()
    )

    if not rev_rows:
        duration_ms = 60000
        if project.transcript and "durationMs" in project.transcript:
            duration_ms = int(project.transcript["durationMs"])
        elif project.media and "durationMs" in project.media:
            duration_ms = int(project.media["durationMs"])
        init_st = initial_state(duration_ms)
        r0 = models.RevisionRow(
            project_id=session_id,
            rev_id="r0",
            index=0,
            parent_id=None,
            kind="initial",
            label="Original upload",
            ops=[],
            state=state_to_dict(init_st),
            hash=timeline_hash(init_st),
            created_ms=int(time.time() * 1000),
        )
        db.add(r0)
        project.undo_stack = ["r0"]
        project.redo_stack = []
        db.commit()
        rev_rows = [r0]

    engine_revs = [
        Revision(
            id=r.rev_id,
            index=r.index,
            parentId=r.parent_id,
            kind=r.kind,
            label=r.label,
            ops=[op_from_dict(o) for o in (r.ops or [])],
            state=state_from_dict(r.state),
            hash=r.hash,
            createdAt=r.created_ms,
        )
        for r in rev_rows
    ]

    if project.transcript:
        transcript = transcript_from_dict(project.transcript)
    else:
        transcript = Transcript(
            provider="none",
            model=None,
            note=None,
            durationMs=rev_rows[0].state["sourceDurationMs"],
            speakers={},
            words=[],
        )

    history = EngineHistory(
        revisions=engine_revs,
        undo=list(project.undo_stack or [r.rev_id for r in rev_rows]),
        redo=list(project.redo_stack or []),
    )

    ins_rows = (
        db.query(models.InstructionRow)
        .filter(models.InstructionRow.project_id == session_id)
        .order_by(models.InstructionRow.seq.asc())
        .all()
    )
    log_entries = [r.result for r in ins_rows]

    engine_session = EngineSession(transcript=transcript, history=history, log=log_entries)

    # 5. Run instruction through deterministic executor
    now_ms = int(time.time() * 1000)
    new_session = executor_run_instruction(engine_session, payload.instruction, now=now_ms)
    result = new_session.log[-1]

    raw_status = result.get("status", "rejected")
    instruction_id = result.get("id", f"ins_{uuid.uuid4().hex[:8]}")
    outcome_status: Literal["APPLIED", "NEEDS_CLARIFICATION", "REJECTED"]

    # 6. Build structured operation list
    planned_ops: List[PlannedOperation] = []
    if result.get("ops"):
        planned_ops = [
            _op_to_planned(op_from_dict(op), f"op_{idx + 1}", notes=result.get("notes"))
            for idx, op in enumerate(result["ops"])
        ]

    # 7. Outcome branching
    if raw_status == "applied":
        outcome_status = "APPLIED"
        head_rev = head(new_session.history)

        # If a new revision was added (e.g. edit), save it to revisions table
        if head_rev.id not in [r.rev_id for r in rev_rows]:
            new_rev_row = models.RevisionRow(
                project_id=session_id,
                rev_id=head_rev.id,
                index=head_rev.index,
                parent_id=head_rev.parentId,
                kind=head_rev.kind,
                label=head_rev.label,
                ops=ops_to_list(head_rev.ops),
                state=state_to_dict(head_rev.state),
                hash=head_rev.hash,
                created_ms=head_rev.createdAt,
            )
            db.add(new_rev_row)

        project.undo_stack = list(new_session.history.undo)
        project.redo_stack = list(new_session.history.redo)

        response_data = InstructionResponse(
            status="APPLIED",
            instruction_id=instruction_id,
            operations=planned_ops,
            revision_id=new_session.history.undo[-1],
            message=result.get("message", "Instruction applied successfully."),
        )

    elif raw_status == "needs_clarification":
        outcome_status = "NEEDS_CLARIFICATION"
        options = [
            ClarificationOptionSchema(
                label=o["label"],
                detail=o.get("detail"),
                operations=[_op_to_planned(op_from_dict(op), f"op_{idx+1}") for idx, op in enumerate(o.get("ops", []))],
            )
            for o in result.get("options", [])
        ]
        clar_schema = ClarificationSchema(
            question=result.get("question") or result.get("message") or "Clarification needed.",
            options=options,
        )
        response_data = InstructionResponse(
            status="NEEDS_CLARIFICATION",
            instruction_id=instruction_id,
            operations=planned_ops,
            clarification=clar_schema,
            message=result.get("message", "The target could not be identified unambiguously."),
        )

    else:
        outcome_status = "REJECTED"
        err_msg = (
            result.get("error", {}).get("message")
            if isinstance(result.get("error"), dict)
            else result.get("message", "Instruction rejected.")
        )
        response_data = InstructionResponse(
            status="REJECTED",
            instruction_id=instruction_id,
            reason=err_msg,
            operations=planned_ops,
            message=result.get("message", "Instruction rejected."),
        )

    # 8. Persist InstructionRow
    ins_record = models.InstructionRow(
        project_id=session_id,
        result_id=instruction_id,
        seq=len(ins_rows) + 1,
        status=outcome_status,
        result=response_data.model_dump(),
    )
    db.add(ins_record)

    # 9. Save Idempotency if provided
    if idempotency_key:
        _save_idempotency_after(
            db,
            idempotency_key,
            "POST",
            path,
            payload_str,
            200,
            response_data.model_dump_json(),
        )

    db.commit()
    return response_data


# ---------------------------------------------------------------------------
# Revisions and History Retrieval Endpoints
# ---------------------------------------------------------------------------

@router.get("/sessions/{session_id}/revisions", tags=["revisions"])
@router.get("/projects/{session_id}/revisions", tags=["revisions"])
def get_revisions(session_id: str, db: DbSession = Depends(get_db)):
    project = db.query(models.Project).filter(models.Project.id == session_id).first()
    if not project:
        _raise_error(status.HTTP_404_NOT_FOUND, "SESSION_NOT_FOUND", f"Session '{session_id}' not found.")
    revs = (
        db.query(models.RevisionRow)
        .filter(models.RevisionRow.project_id == session_id)
        .order_by(models.RevisionRow.index.asc())
        .all()
    )
    return [
        {
            "id": r.rev_id,
            "index": r.index,
            "parentId": r.parent_id,
            "kind": r.kind,
            "label": r.label,
            "ops": r.ops,
            "hash": r.hash,
            "createdAt": r.created_ms,
        }
        for r in revs
    ]


@router.get("/sessions/{session_id}/instructions", tags=["instructions"])
@router.get("/projects/{session_id}/instructions", tags=["instructions"])
def get_instructions(session_id: str, db: DbSession = Depends(get_db)):
    project = db.query(models.Project).filter(models.Project.id == session_id).first()
    if not project:
        _raise_error(status.HTTP_404_NOT_FOUND, "SESSION_NOT_FOUND", f"Session '{session_id}' not found.")
    ins = (
        db.query(models.InstructionRow)
        .filter(models.InstructionRow.project_id == session_id)
        .order_by(models.InstructionRow.seq.asc())
        .all()
    )
    return [i.result for i in ins]
