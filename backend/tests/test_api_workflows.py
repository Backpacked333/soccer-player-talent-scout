"""Isolated synthetic-only API/security integration tests; no external services."""
from __future__ import annotations

import hashlib
import os
import subprocess
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import Settings
from app.db import get_session_factory, utcnow
from app.main import create_app
from app.models import AuthSession, ProcessingJob, Upload

ORIGIN = "http://localhost:5173"
PASSWORD = "synthetic-test-password-0123"


@pytest.fixture
def api(tmp_path):
    settings = Settings(_env_file=None, environment="test", database_url=f"sqlite:///{tmp_path / 'test.db'}", storage_root=tmp_path / "private", allowed_origins=[ORIGIN], max_upload_bytes=1024 * 1024)
    app = create_app(settings)
    with TestClient(app, raise_server_exceptions=False) as first, TestClient(app, raise_server_exceptions=False) as second:
        yield app, first, second, settings


@pytest.fixture(scope="session")
def tiny_video(tmp_path_factory):
    path = tmp_path_factory.mktemp("synthetic-video") / "synthetic-black.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x240:r=10", "-t", "3", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)], check=True, timeout=30, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    return path.read_bytes()


def signup(client, label="one"):
    client.headers["Origin"] = ORIGIN
    result = client.post("/api/auth/signup", json={"email": f"{label}@example.test", "password": PASSWORD, "workspace_name": f"Synthetic test workspace {label}"})
    assert result.status_code == 201, result.text
    client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    return result.json()


def player(client, label="Synthetic actor", **extra):
    response = client.post("/api/players", json={"display_name": label, "age_group": "U15", "primary_role": "midfielder", **extra})
    assert response.status_code == 201, response.text
    return response.json()


def match_setup(client, *, attest=True):
    actor = player(client)
    opponent = player(client, "Synthetic opposing actor", primary_role="defender")
    response = client.post("/api/matches", json={"title": "Synthetic test fixture, not real footage", "match_date": "2025-03-01", "competition": "Test league", "season": "2025", "age_group": "U15", "team_name": "Test home", "opponent_name": "Test away", "duration_seconds": 600})
    assert response.status_code == 201, response.text
    match = response.json()
    roster = {"appearances": [{"player_id": actor["id"], "jersey_number": 7, "role": "midfielder", "side": "home", "played_seconds": 600, "reviewed_seconds": 2}, {"player_id": opponent["id"], "jersey_number": 4, "role": "defender", "side": "away", "played_seconds": 600, "reviewed_seconds": 2}]}
    assert client.put(f"/api/matches/{match['id']}/roster", json=roster).status_code == 200
    if attest:
        assert client.post(f"/api/matches/{match['id']}/attestation", json=rights()).status_code == 201
    return actor, opponent, match, roster


def rights():
    return {"permission_to_process": True, "guardian_or_club_authorization": True, "opponent_privacy_review": True, "authorized_roster": True, "retention_days": 30}


def upload(client, match, tiny_video):
    response = client.post(f"/api/matches/{match['id']}/upload", files={"file": ("synthetic-black.mp4", tiny_video, "video/mp4")})
    assert response.status_code == 201, response.text
    return response.json()


def event_body(actor, **extra):
    return {"player_id": actor["id"], "kind": "pass", "outcome": "successful", "timestamp_seconds": 1, "status": "approved", **extra}


def test_authentication_csrf_origin_and_empty_signup(api):
    app, first, second, settings = api
    assert first.get("/api/auth/me").status_code == 401
    assert first.post("/api/auth/signup", json={}).status_code == 403
    session = signup(first)
    assert first.get("/api/players").json() == []
    assert first.get("/api/matches").json() == []
    assert "no-store" in first.get("/api/auth/me").headers["cache-control"]
    me = first.get("/api/auth/me").json()
    assert me == session
    assert first.post("/api/players", json={"display_name": "x", "age_group": "U15"}, headers={"Origin": "https://evil.test"}).status_code == 403
    assert first.post("/api/players", json={"display_name": "x", "age_group": "U15"}, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    with get_session_factory(settings.database_url)() as db:
        stored = db.scalar(select(AuthSession))
        raw = first.cookies.get("scout_session")
        assert stored.token_hash == hashlib.sha256(raw.encode()).hexdigest()
        assert stored.token_hash != raw
    assert first.post("/api/auth/logout").status_code == 204
    assert first.get("/api/auth/me").status_code == 401
    first.cookies.set("scout_session", raw, path="/api")
    assert first.get("/api/auth/me").status_code == 401


def test_login_rotation_cookie_and_expiry(api):
    app, first, second, settings = api
    signup(first)
    old = first.cookies.get("scout_session")
    response = first.post("/api/auth/login", json={"email": "one@example.test", "password": PASSWORD})
    assert response.status_code == 200
    assert first.cookies.get("scout_session") != old
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "path=/api" in cookie
    second.cookies.set("scout_session", old, path="/api")
    assert second.get("/api/auth/me").status_code == 401
    with get_session_factory(settings.database_url)() as db:
        row = db.scalar(select(AuthSession))
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert first.get("/api/auth/me").status_code == 401


def test_login_generic_errors_and_throttle(api):
    _app, first, _second, _settings = api
    signup(first)
    bodies = []
    for email in ("one@example.test", "absent@example.test"):
        response = first.post("/api/auth/login", json={"email": email, "password": "wrong"})
        assert response.status_code == 401
        bodies.append(response.json())
    assert bodies[0] == bodies[1]
    for _ in range(10):
        response = first.post("/api/auth/login", json={"email": "one@example.test", "password": "wrong"})
    assert response.status_code == 429
    assert response.headers["retry-after"]


def test_strict_inputs_and_safe_error_bodies(api):
    _app, first, _second, _settings = api
    signup(first)
    assert first.post("/api/players", json={"display_name": "X", "age_group": "U15", "workspace_id": "foreign"}).status_code == 422
    assert first.post("/api/players", json={"display_name": "X", "age_group": "U15", "is_synthetic": True}).status_code == 422
    assert first.post("/api/auth/login", json={"email": "bad", "password": "PRIVATE-NEVER-REFLECT"}).status_code == 422
    response = first.post("/api/auth/login", json={"email": "bad", "password": "PRIVATE-NEVER-REFLECT"})
    assert "PRIVATE-NEVER-REFLECT" not in response.text
    assert first.get("/api/players?mode=invalid").status_code == 422
    assert first.get("/api/leaderboard").status_code == 422


def test_two_account_relationship_and_direct_id_isolation(api, tiny_video):
    _app, first, second, _settings = api
    signup(first)
    signup(second, "two")
    actor, opponent, match, roster = match_setup(first)
    evidence = upload(first, match, tiny_video)
    created = first.post(f"/api/matches/{match['id']}/events", json=event_body(actor)).json()
    other_actor, _other_opponent, other_match, other_roster = match_setup(second)
    for path in (f"/players/{actor['id']}", f"/players/{actor['id']}/profile", f"/matches/{match['id']}", f"/matches/{match['id']}/roster", f"/matches/{match['id']}/attestation", f"/matches/{match['id']}/upload", f"/matches/{match['id']}/events", f"/matches/{match['id']}/audit", f"/matches/{match['id']}/metrics", f"/jobs/{evidence['job']['id']}", f"/uploads/{evidence['upload']['id']}/video"):
        assert second.get("/api" + path).status_code == 404, path
    assert second.post(f"/api/jobs/{evidence['job']['id']}/retry").status_code == 404
    assert second.post(f"/api/matches/{match['id']}/upload", files={"file": ("a.mp4", tiny_video, "video/mp4")}).status_code == 404
    assert second.put(f"/api/events/{created['id']}", json=event_body(other_actor)).status_code == 404
    assert second.delete(f"/api/events/{created['id']}").status_code == 404
    assert second.delete(f"/api/players/{actor['id']}").status_code == 404
    assert second.delete(f"/api/matches/{match['id']}").status_code == 404
    assert first.put(f"/api/matches/{match['id']}/roster", json={"appearances": [{"player_id": other_actor["id"]}]}).status_code == 404
    assert first.post(f"/api/matches/{match['id']}/events", json=event_body(other_actor)).status_code == 404
    assert first.post(f"/api/matches/{match['id']}/events", json=event_body(actor, opponent_player_id=other_actor["id"])).status_code == 404
    exported = second.get("/api/exports/workspace").json()
    assert actor["id"] not in str(exported)
    assert second.get("/api/jobs").json() == []


def test_upload_requires_current_attestation_and_no_events_without_video(api, tiny_video):
    _app, first, _second, settings = api
    signup(first)
    actor, opponent, match, roster = match_setup(first, attest=False)
    assert first.post(f"/api/matches/{match['id']}/upload", files={"file": ("a.mp4", tiny_video, "video/mp4")}).status_code == 409
    assert first.post(f"/api/matches/{match['id']}/events", json=event_body(actor)).status_code == 409
    assert first.post(f"/api/matches/{match['id']}/attestation", json={**rights(), "opponent_privacy_review": False}).status_code == 422
    assert list(settings.storage_root.iterdir()) == []


def test_upload_probe_is_not_analysis_and_duplicate_preserves_video(api, tiny_video):
    _app, first, _second, settings = api
    signup(first)
    actor, opponent, match, roster = match_setup(first)
    evidence = upload(first, match, tiny_video)
    assert evidence["upload"]["duration_seconds"] == pytest.approx(3)
    assert evidence["upload"]["status"] == "probed"
    assert evidence["job"]["status"] == "queued"
    assert evidence["job"]["result"]["analysis_available"] is False
    assert "storage_key" not in str(evidence)
    assert first.get(f"/api/matches/{match['id']}/events").json() == []
    assert first.get(f"/api/players/{actor['id']}/profile").json()["metrics"][0]["pass_completion_pct"] is None
    response = first.post(f"/api/matches/{match['id']}/upload", files={"file": ("other.mp4", b"broken", "video/mp4")})
    assert response.status_code == 409
    assert first.get(f"/api/uploads/{evidence['upload']['id']}/video").content == tiny_video
    assert len(list(settings.storage_root.iterdir())) == 1
    assert os.stat(next(settings.storage_root.iterdir())).st_mode & 0o077 == 0


@pytest.mark.parametrize("filename,content_type,payload,expected", [("../escape.mp4", "video/mp4", b"bad", 422), ("bad.webm", "video/mp4", b"bad", 415), ("a.mp4", "video/mp4", b"", 422), ("a.mp4", "video/mp4", b"#EXTM3U\nhttp://evil.test", 422), ("a.txt", "text/plain", b"bad", 415), ("a.mp4", "video/mp4", b"\x00\x00\x00\x18ftypisom" + b"x" * 100, 422)])
def test_malformed_upload_cleanup(api, filename, content_type, payload, expected):
    _app, first, _second, settings = api
    signup(first)
    actor, opponent, match, roster = match_setup(first)
    response = first.post(f"/api/matches/{match['id']}/upload", files={"file": (filename, payload, content_type)})
    assert response.status_code == expected, response.text
    assert list(settings.storage_root.iterdir()) == []
    assert first.get(f"/api/matches/{match['id']}/upload").status_code == 404
    assert first.get("/api/jobs").json() == []


def test_oversized_upload_cleanup_and_request_limit(api):
    _app, first, _second, settings = api
    signup(first)
    actor, opponent, match, roster = match_setup(first)
    data = b"\x00\x00\x00\x18ftypisom" + b"x" * settings.max_upload_bytes
    assert first.post(f"/api/matches/{match['id']}/upload", files={"file": ("a.mp4", data, "video/mp4")}).status_code == 413
    assert list(settings.storage_root.iterdir()) == []
    assert first.post("/api/players", content=b"x" * (256 * 1024 + 1), headers={"Content-Type": "application/json"}).status_code == 413


def test_probe_timeout_cleanup(api, tiny_video, monkeypatch):
    _app, first, _second, settings = api
    signup(first)
    actor, opponent, match, roster = match_setup(first)
    def timeout(command, **kwargs):
        assert isinstance(command, list)
        assert "-protocol_whitelist" in command
        assert kwargs["timeout"] == settings.probe_timeout_seconds
        assert not kwargs.get("shell")
        raise subprocess.TimeoutExpired(command, 1)
    monkeypatch.setattr("app.services.subprocess.run", timeout)
    response = first.post(f"/api/matches/{match['id']}/upload", files={"file": ("a.mp4", tiny_video, "video/mp4")})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "probe_timeout"
    assert list(settings.storage_root.iterdir()) == []


def test_private_byte_ranges_and_unauthenticated_access(api, tiny_video):
    _app, first, second, _settings = api
    signup(first)
    actor, opponent, match, roster = match_setup(first)
    evidence = upload(first, match, tiny_video)
    path = f"/api/uploads/{evidence['upload']['id']}/video"
    assert second.get(path).status_code == 401
    for header, expected in (("bytes=0-19", tiny_video[:20]), ("bytes=-9", tiny_video[-9:]), ("bytes=10-", tiny_video[10:])):
        response = first.get(path, headers={"Range": header})
        assert response.status_code == 206
        assert response.content == expected
        assert int(response.headers["Content-Length"]) == len(expected)
        assert response.headers["Accept-Ranges"] == "bytes"
    assert first.head(path).content == b""
    assert int(first.head(path).headers["content-length"]) == len(tiny_video)
    for header in ("bytes=999999-", "bytes=10-5", "bytes=0-1,3-4", "bytes=-0", "things=1-2"):
        assert first.get(path, headers={"Range": header}).status_code == 416


def test_private_path_symlink_rejected(api, tiny_video, tmp_path):
    _app, first, _second, settings = api
    signup(first)
    actor, opponent, match, roster = match_setup(first)
    evidence = upload(first, match, tiny_video)
    with get_session_factory(settings.database_url)() as db:
        row = db.get(Upload, evidence["upload"]["id"])
        path = settings.storage_root / row.storage_key
    other = tmp_path / "unrelated-private-data"
    other.write_bytes(tiny_video)
    path.unlink()
    path.symlink_to(other)
    assert first.get(f"/api/uploads/{evidence['upload']['id']}/video").status_code == 404
    assert other.read_bytes() == tiny_video


def test_review_timestamp_validation_identity_correction_and_audit(api, tiny_video):
    _app, first, _second, _settings = api
    signup(first)
    actor, opponent, match, roster = match_setup(first)
    upload(first, match, tiny_video)
    endpoint = f"/api/matches/{match['id']}/events"
    for fields in ({"timestamp_seconds": 4}, {"timestamp_seconds": -1}, {"end_timestamp_seconds": 0.5}, {"outcome": "unknown"}, {"start_x": 0.5}, {"opponent_player_id": actor["id"]}):
        assert first.post(endpoint, json=event_body(actor, **fields)).status_code == 422
    created = first.post(endpoint, json=event_body(actor, opponent_player_id=opponent["id"])).json()
    assert created["provenance"] == "manual" and created["confidence"] is None
    assert created["reviewed_by"] and created["reviewed_at"]
    metric = first.get(f"/api/players/{actor['id']}/profile").json()["metrics"][0]
    assert metric["pass_attempts"] == 1 and metric["rank"] is None
    assert metric["pass_rate"]["posterior_mean_pct"] == pytest.approx(200/3)
    assert first.put(f"/api/events/{created['id']}", json=event_body(opponent, outcome="unsuccessful")).status_code == 200
    assert first.get(f"/api/players/{actor['id']}/profile").json()["metrics"][0]["pass_attempts"] == 0
    assert first.get(f"/api/players/{opponent['id']}/profile").json()["metrics"][0]["pass_attempts"] == 1
    assert first.delete(f"/api/events/{created['id']}").status_code == 204
    assert first.get(f"/api/players/{opponent['id']}/profile").json()["metrics"][0]["pass_attempts"] == 0
    audits = first.get(f"/api/matches/{match['id']}/audit").json()
    assert {entry["action"] for entry in audits} >= {"create", "correct", "delete", "replace", "confirm"}
    correction = next(entry for entry in audits if entry["action"] == "correct")
    assert correction["before"]["player_id"] == actor["id"]
    assert correction["after"]["player_id"] == opponent["id"]


def test_roster_rules_and_attestation_invalidation(api, tiny_video):
    _app, first, _second, _settings = api
    signup(first)
    actor, opponent, match, roster = match_setup(first)
    upload(first, match, tiny_video)
    response = first.post(f"/api/matches/{match['id']}/events", json=event_body(actor))
    assert response.status_code == 201
    assert first.put(f"/api/matches/{match['id']}/roster", json={"appearances": []}).status_code == 409
    assert first.delete(f"/api/players/{actor['id']}").status_code == 409
    bad = {"appearances": [{**roster["appearances"][0], "played_seconds": 601}]}
    assert first.put(f"/api/matches/{match['id']}/roster", json=bad).status_code == 422
    additional = player(first, "Added authorized test actor")
    roster["appearances"].append({"player_id": additional["id"]})
    assert first.put(f"/api/matches/{match['id']}/roster", json=roster).status_code == 200
    assert first.get(f"/api/matches/{match['id']}/attestation").status_code == 404
    assert first.post(f"/api/matches/{match['id']}/attestation", json=rights()).status_code == 201


def test_persistent_job_status_sanitization_and_retry_bounds(api, tiny_video):
    _app, first, _second, settings = api
    signup(first)
    actor, opponent, match, roster = match_setup(first)
    evidence = upload(first, match, tiny_video)
    job_id = evidence["job"]["id"]
    assert first.post(f"/api/jobs/{job_id}/retry").status_code == 409
    with get_session_factory(settings.database_url)() as db:
        job = db.get(ProcessingJob, job_id)
        job.status = "needs_configuration"
        job.stage = "/private/secret/path"
        job.message = "sensitive traceback /private/secret/path"
        job.result_json = {"private_path": "/private/secret/path", "candidate_count": 0}
        job.attempts = 1
        db.commit()
    response = first.get(f"/api/jobs/{job_id}")
    assert response.json()["status"] == "needs_configuration"
    assert "/private/secret" not in response.text and "traceback" not in response.text
    assert first.post(f"/api/jobs/{job_id}/retry").json()["status"] == "queued"
    with get_session_factory(settings.database_url)() as db:
        job = db.get(ProcessingJob, job_id)
        job.status = "failed"
        job.attempts = settings.max_job_attempts
        db.commit()
    assert first.post(f"/api/jobs/{job_id}/retry").status_code == 409


def test_match_deletion_cascades_private_evidence(api, tiny_video):
    _app, first, _second, settings = api
    signup(first)
    actor, opponent, match, roster = match_setup(first)
    evidence = upload(first, match, tiny_video)
    created = first.post(f"/api/matches/{match['id']}/events", json=event_body(actor)).json()
    assert first.delete(f"/api/matches/{match['id']}").status_code == 204
    assert first.get(f"/api/jobs/{evidence['job']['id']}").status_code == 404
    assert first.get(f"/api/uploads/{evidence['upload']['id']}/video").status_code == 404
    assert first.delete(f"/api/events/{created['id']}").status_code == 404
    assert list(settings.storage_root.iterdir()) == []
    assert first.delete(f"/api/players/{actor['id']}").status_code == 204


def test_explicit_demo_and_export_mode_isolation(api):
    _app, first, second, _settings = api
    signup(first)
    signup(second, "two")
    assert first.get("/api/players?mode=demo").json() == []
    seeded = first.post("/api/demo/seed")
    assert seeded.status_code == 200, seeded.text
    assert seeded.json()["players_created"] == 4
    assert first.post("/api/demo/seed").json()["players_created"] == 0
    assert first.get("/api/players").json() == []
    assert first.get("/api/matches").json() == []
    assert second.get("/api/players?mode=demo").json() == []
    exported = first.get("/api/exports/workspace?mode=demo")
    assert exported.status_code == 200
    assert exported.json()["synthetic_notice"].startswith("Synthetic demonstration")
    assert all(row["is_synthetic"] for key in ("players", "matches", "events", "appearances") for row in exported.json()[key])
    for sensitive in ("password_hash", "token_hash", "csrf_token", "storage_key", "secret.key"):
        assert sensitive not in exported.text
    assert first.get("/api/exports/workspace").json()["events"] == []
    demo = first.get("/api/matches?mode=demo").json()[0]
    real_actor = player(first)
    assert first.put(f"/api/matches/{demo['id']}/roster", json={"appearances": [{"player_id": real_actor["id"]}]}).status_code == 422


def test_cors_does_not_allow_unknown_origin(api):
    _app, first, _second, _settings = api
    allowed = first.options("/api/auth/login", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Content-Type"})
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == ORIGIN
    rejected = first.options("/api/auth/login", headers={"Origin": "https://evil.test", "Access-Control-Request-Method": "POST"})
    assert rejected.status_code == 400
    assert "access-control-allow-origin" not in rejected.headers
