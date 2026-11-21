"""API integration tests for sessions, instructions, revisions, idempotency, and error handling.
Verifies all Section 13 API requirements:
- Valid request
- Invalid request
- Missing session
- Applied response
- Clarification response
- Rejected response
- Idempotent repeated request
- Conflicting idempotency key
- Persistence of instruction results
"""

import json
from typing import Any, Dict, Optional

import pytest
from fastapi import HTTPException
from fastapi.responses import Response

from backend.app import models
from backend.app.api.v1.router import (
    InstructionRequest,
    SessionCreate,
    create_session,
    get_instructions,
    get_revisions,
    get_session,
    run_instruction,
)
from backend.app.database import SessionLocal, init_db


class MockResponse:
    def __init__(self, status_code: int, data: Any):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data


class DirectApiClient:
    """Invokes API endpoints directly without relying on OS network sockets."""

    def __init__(self):
        pass

    def post(self, path: str, json_body: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None) -> MockResponse:
        db = SessionLocal()
        headers = headers or {}
        idemp_key = headers.get("Idempotency-Key")

        try:
            # Route: POST /api/v1/sessions
            if path in ("/api/v1/sessions", "/api/v1/projects"):
                payload = SessionCreate(**(json_body or {}))
                res = create_session(payload, db)
                return MockResponse(201, res.model_dump())

            # Route: POST /api/v1/sessions/{session_id}/instructions
            if "/instructions" in path:
                parts = path.strip("/").split("/")
                session_id = parts[3]
                payload = InstructionRequest(**(json_body or {}))
                res = run_instruction(session_id, payload, db, x_idempotency_key=idemp_key)
                if isinstance(res, Response):
                    return MockResponse(res.status_code or 200, json.loads(res.body.decode("utf-8")))
                return MockResponse(200, res.model_dump())

            raise ValueError(f"Unmapped POST route: {path}")

        except HTTPException as exc:
            detail = exc.detail
            if isinstance(detail, dict):
                return MockResponse(exc.status_code, detail)
            return MockResponse(exc.status_code, {"error": {"code": "HTTP_ERROR", "message": str(detail), "retryable": False, "details": {}}})
        finally:
            db.close()

    def get(self, path: str) -> MockResponse:
        db = SessionLocal()
        try:
            parts = path.strip("/").split("/")
            # Route: GET /api/v1/sessions/{session_id}
            if len(parts) == 4 and parts[2] in ("sessions", "projects"):
                session_id = parts[3]
                res = get_session(session_id, db)
                return MockResponse(200, res.model_dump())

            # Route: GET /api/v1/sessions/{session_id}/revisions
            if len(parts) == 5 and parts[4] == "revisions":
                session_id = parts[3]
                res = get_revisions(session_id, db)
                return MockResponse(200, res)

            # Route: GET /api/v1/sessions/{session_id}/instructions
            if len(parts) == 5 and parts[4] == "instructions":
                session_id = parts[3]
                res = get_instructions(session_id, db)
                return MockResponse(200, res)

            raise ValueError(f"Unmapped GET route: {path}")

        except HTTPException as exc:
            detail = exc.detail
            if isinstance(detail, dict):
                return MockResponse(exc.status_code, detail)
            return MockResponse(exc.status_code, {"error": {"code": "HTTP_ERROR", "message": str(detail), "retryable": False, "details": {}}})
        finally:
            db.close()


@pytest.fixture(autouse=True)
def setup_db(tmp_path):
    db_file = tmp_path / "test_api.db"
    db_url = f"sqlite:///{db_file}"
    init_db(db_url)
    yield


@pytest.fixture
def client():
    return DirectApiClient()


@pytest.fixture
def session_with_transcript(client):
    transcript = {
        "provider": "whisper",
        "durationMs": 60000,
        "speakers": {},
        "words": [
            {"id": "w1", "index": 0, "text": "Intro", "start": 500, "end": 2000},
            {"id": "w2", "index": 1, "text": "is", "start": 2100, "end": 2500},
            {"id": "w3", "index": 2, "text": "here.", "start": 2600, "end": 3500},
            {"id": "w4", "index": 3, "text": "Later", "start": 20000, "end": 21000},
            {"id": "w5", "index": 4, "text": "it", "start": 21100, "end": 21500},
            {"id": "w6", "index": 5, "text": "is", "start": 21600, "end": 22000},
            {"id": "w7", "index": 6, "text": "confidential", "start": 22100, "end": 23000},
            {"id": "w8", "index": 7, "text": "and", "start": 30000, "end": 30500},
            {"id": "w9", "index": 8, "text": "also", "start": 30600, "end": 31000},
            {"id": "w10", "index": 9, "text": "confidential", "start": 31100, "end": 32000},
            {"id": "w11", "index": 10, "text": "again.", "start": 32100, "end": 33000},
        ],
    }
    resp = client.post(
        "/api/v1/sessions",
        json_body={
            "id": "test_session_1",
            "name": "Test Session",
            "duration_ms": 60000,
            "transcript": transcript,
        },
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def test_valid_request_applied(client, session_with_transcript):
    resp = client.post(
        f"/api/v1/sessions/{session_with_transcript}/instructions",
        json_body={"instruction": "Remove the first 10 seconds"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "APPLIED"
    assert data["revision_id"] == "r1"
    assert "instruction_id" in data
    assert len(data["operations"]) == 1
    assert data["operations"][0]["type"] == "cut"
    assert data["operations"][0]["parameters"]["ranges"] == [{"start": 0, "end": 10000}]


def test_invalid_request_empty(client, session_with_transcript):
    resp = client.post(
        f"/api/v1/sessions/{session_with_transcript}/instructions",
        json_body={"instruction": "   "},
    )
    assert resp.status_code == 400
    data = resp.json()
    assert "error" in data
    assert data["error"]["code"] == "EMPTY_INSTRUCTION"


def test_missing_session(client):
    resp = client.post(
        "/api/v1/sessions/non_existent_session_id/instructions",
        json_body={"instruction": "Remove the first 10 seconds"},
    )
    assert resp.status_code == 404
    data = resp.json()
    assert "error" in data
    assert data["error"]["code"] == "SESSION_NOT_FOUND"


def test_clarification_response(client, session_with_transcript):
    # Word "confidential" appears twice -> needs clarification
    resp = client.post(
        f"/api/v1/sessions/{session_with_transcript}/instructions",
        json_body={"instruction": "Cut the word confidential"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "NEEDS_CLARIFICATION"
    assert data["clarification"] is not None
    assert "2 times" in data["clarification"]["question"]
    assert len(data["clarification"]["options"]) >= 2
    assert "revision_id" not in data or data["revision_id"] is None


def test_rejected_response(client, session_with_transcript):
    resp = client.post(
        f"/api/v1/sessions/{session_with_transcript}/instructions",
        json_body={"instruction": "Make the lighting cinematic teal and orange"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "REJECTED"
    assert data["reason"] is not None
    assert "revision_id" not in data or data["revision_id"] is None


def test_idempotent_repeated_request(client, session_with_transcript):
    headers = {"Idempotency-Key": "key_12345"}
    payload = {"instruction": "Remove the first 10 seconds"}

    # First request
    resp1 = client.post(
        f"/api/v1/sessions/{session_with_transcript}/instructions",
        json_body=payload,
        headers=headers,
    )
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["status"] == "APPLIED"
    assert data1["revision_id"] == "r1"

    # Repeated request with same idempotency key
    resp2 = client.post(
        f"/api/v1/sessions/{session_with_transcript}/instructions",
        json_body=payload,
        headers=headers,
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    # Must return exact same result
    assert data2["instruction_id"] == data1["instruction_id"]
    assert data2["revision_id"] == data1["revision_id"]
    assert data2["status"] == data1["status"]

    # Verify that only ONE new revision was created (r0 and r1, not r2)
    revs_resp = client.get(f"/api/v1/sessions/{session_with_transcript}/revisions")
    assert revs_resp.status_code == 200
    revs = revs_resp.json()
    assert len(revs) == 2
    assert [r["id"] for r in revs] == ["r0", "r1"]


def test_conflicting_idempotency_key(client, session_with_transcript):
    headers = {"Idempotency-Key": "conflict_key"}

    # First request
    resp1 = client.post(
        f"/api/v1/sessions/{session_with_transcript}/instructions",
        json_body={"instruction": "Remove the first 10 seconds"},
        headers=headers,
    )
    assert resp1.status_code == 200

    # Second request with SAME key but DIFFERENT payload
    resp2 = client.post(
        f"/api/v1/sessions/{session_with_transcript}/instructions",
        json_body={"instruction": "Make the section after 1:20 play at 2x speed"},
        headers=headers,
    )
    assert resp2.status_code == 409
    data = resp2.json()
    assert "error" in data
    assert data["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_persistence_of_instruction_results(client, session_with_transcript):
    # 1. Successful instruction
    r1 = client.post(
        f"/api/v1/sessions/{session_with_transcript}/instructions",
        json_body={"instruction": "Remove the first 10 seconds"},
    )
    assert r1.status_code == 200
    d1 = r1.json()

    # 2. Clarification instruction
    r2 = client.post(
        f"/api/v1/sessions/{session_with_transcript}/instructions",
        json_body={"instruction": "Cut the word confidential"},
    )
    assert r2.status_code == 200
    d2 = r2.json()

    # 3. Rejected instruction
    r3 = client.post(
        f"/api/v1/sessions/{session_with_transcript}/instructions",
        json_body={"instruction": "Speed up to 10x"},
    )
    assert r3.status_code == 200
    d3 = r3.json()

    # Retrieve all instruction audit rows
    ins_resp = client.get(f"/api/v1/sessions/{session_with_transcript}/instructions")
    assert ins_resp.status_code == 200
    ins_list = ins_resp.json()
    assert len(ins_list) == 3

    assert ins_list[0]["instruction_id"] == d1["instruction_id"]
    assert ins_list[0]["status"] == "APPLIED"

    assert ins_list[1]["instruction_id"] == d2["instruction_id"]
    assert ins_list[1]["status"] == "NEEDS_CLARIFICATION"

    assert ins_list[2]["instruction_id"] == d3["instruction_id"]
    assert ins_list[2]["status"] == "REJECTED"

    # Only 1 revision was committed across all 3 instructions (plus initial r0)
    revs_resp = client.get(f"/api/v1/sessions/{session_with_transcript}/revisions")
    assert len(revs_resp.json()) == 2
