"""Complete offline product flow through HTTP and the separate worker CLI.

All actors/actions here are fictional and only inhabit pytest temporary storage.
No model, real minor, browser, external account or persistent app server is used.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import SCHEMA_VERSION
from app.main import create_app
from app.schemas import LeaderboardResponse, PlayerProfile

ORIGIN = "http://localhost:5173"
PASSWORD = "fictional-integration-only-1294"
BACKEND = Path(__file__).resolve().parents[1]


def test_private_product_roundtrip_with_worker_cli_and_restart(tmp_path):
    settings = Settings(
        _env_file=None, environment="test",
        database_url=f"sqlite:///{tmp_path / 'persistent.db'}",
        storage_root=tmp_path / "private", allowed_origins=[ORIGIN],
    )
    # Explicit child environment: never inherit or inspect host credentials.
    environment = {
        "PATH": os.defpath, "LANG": "C.UTF-8",
        "SCOUT_ENVIRONMENT": "test", "SCOUT_DATABASE_URL": settings.database_url,
        "SCOUT_STORAGE_ROOT": str(settings.storage_root),
        "SCOUT_ALLOWED_ORIGINS": json.dumps([ORIGIN]),
        "SCOUT_VISION_ENABLED": "false",
    }

    def cli(module, *args):
        return subprocess.run(
            [sys.executable, "-m", module, *args], cwd=BACKEND,
            env=environment, check=True, capture_output=True, text=True, timeout=45,
        ).stdout

    assert f"version {SCHEMA_VERSION} ready" in cli("app.db")
    video_path = tmp_path / "fictional-fixture.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x240:r=10",
         "-t", "3", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(video_path)],
        check=True, stdin=subprocess.DEVNULL, capture_output=True, timeout=30, env=environment,
    )
    video_bytes = video_path.read_bytes()
    cohort = {"age_group": "U15", "role": "midfielder", "competition": "Fictional test league", "season": "2025", "mode": "real"}

    with TestClient(create_app(settings)) as client, TestClient(create_app(settings)) as outsider:
        client.headers["Origin"] = ORIGIN
        signed = client.post("/api/auth/signup", json={"email": "fictional@example.test", "password": PASSWORD, "workspace_name": "Isolated fictional integration fixture"})
        assert signed.status_code == 201
        csrf = signed.json()["csrf_token"]
        client.headers["X-CSRF-Token"] = csrf
        session_cookie = client.cookies.get("scout_session")
        assert client.get("/api/players").json() == []
        assert client.get("/api/matches").json() == []
        actor = client.post("/api/players", json={"display_name": "Fictional integration actor", "age_group": "U15", "primary_role": "midfielder"}).json()
        match = client.post("/api/matches", json={"title": "Fictional fixture, not a real match", "match_date": "2025-01-01", "competition": cohort["competition"], "season": "2025", "age_group": "U15", "team_name": "Fictional home", "opponent_name": "Fictional away", "duration_seconds": 3}).json()
        mid, pid = match["id"], actor["id"]
        assert client.put(f"/api/matches/{mid}/roster", json={"appearances": [{"player_id": pid, "role": "midfielder", "jersey_number": 7, "played_seconds": 3, "reviewed_seconds": 2}]}).status_code == 200
        assert client.post(f"/api/matches/{mid}/attestation", json={"permission_to_process": True, "guardian_or_club_authorization": True, "opponent_privacy_review": True, "authorized_roster": True, "retention_days": 30}).status_code == 201
        response = client.post(f"/api/matches/{mid}/upload", files={"file": (video_path.name, video_bytes, "video/mp4")})
        assert response.status_code == 201, response.text
        evidence = response.json()
        job_path = f"/api/jobs/{evidence['job']['id']}"
        media_path = f"/api/uploads/{evidence['upload']['id']}/video"
        assert evidence["job"]["status"] == "queued"
        assert evidence["job"]["result"]["analysis_available"] is False
        assert evidence["upload"]["duration_seconds"] == pytest.approx(3)
        assert "Processed one queued job" in cli("app.worker", "--once")
        assert "No queued jobs" in cli("app.worker", "--once")
        job = client.get(job_path).json()
        assert job["status"] == "needs_configuration", job
        assert job["stage"] == "metadata_ready"
        assert job["result"]["metadata"]["width"] == 320
        assert job["result"]["candidate_count"] == 0
        assert job["result"]["observation_count"] == 0
        assert job["result"]["analysis_available"] is False
        assert job["result"]["automated_evidence_available"] is False
        assert client.get(f"/api/matches/{mid}/events").json() == []
        assert client.get(f"/api/players/{pid}/profile").json()["metrics"][0]["pass_rate"]["posterior_mean_pct"] is None
        assert outsider.get(media_path).status_code == 401
        assert outsider.head(media_path).status_code == 401
        partial = client.get(media_path, headers={"Range": "bytes=0-19"})
        assert partial.status_code == 206 and partial.content == video_bytes[:20]
        assert "no-store" in partial.headers["cache-control"]

        body = {"player_id": pid, "kind": "pass", "outcome": "successful", "timestamp_seconds": 1, "status": "draft"}
        saved = client.post(f"/api/matches/{mid}/events", json=body)
        assert saved.status_code == 201
        eid = saved.json()["id"]
        assert client.get(f"/api/players/{pid}/profile").json()["metrics"][0]["pass_attempts"] == 0
        body["status"] = "approved"
        approved = client.put(f"/api/events/{eid}", json=body)
        assert approved.status_code == 200
        assert approved.json()["reviewed_by"] == signed.json()["account"]["id"]
        profile_json = client.get(f"/api/players/{pid}/profile").json()
        profile = PlayerProfile.model_validate(profile_json)
        assert profile.model_dump(mode="json") == profile_json  # No dropped enriched fields.
        assert profile.metrics[0].pass_rate.posterior_mean_pct == pytest.approx(200 / 3)
        board_json = client.get("/api/leaderboard", params=cohort).json()
        board = LeaderboardResponse.model_validate(board_json)
        assert board.model_dump(mode="json") == board_json
        assert board.policy.ranking_metric == "pass_completion_posterior_lower_95"
        assert board.entries[0].rank is None and board.entries[0].comparison_score is None
        assert board.entries[0].eligibility == "provisional"
        assert board.entries[0].coverage_fraction == pytest.approx(2 / 3)
        assert client.get("/api/leaderboard", params={**cohort, "mode": "demo"}).json()["entries"] == []
        assert client.put(f"/api/events/{eid}", json=body, headers={"X-CSRF-Token": "wrong"}).status_code == 403
        assert client.put(f"/api/events/{eid}", json=body, headers={"Origin": "https://untrusted.example"}).status_code == 403
        outsider.headers["Origin"] = ORIGIN
        foreign = outsider.post("/api/auth/signup", json={"email": "unrelated@example.test", "password": PASSWORD, "workspace_name": "Other isolated workspace"})
        assert foreign.status_code == 201
        outsider.headers["X-CSRF-Token"] = foreign.json()["csrf_token"]
        for path in (media_path, job_path, f"/api/players/{pid}/profile", f"/api/matches/{mid}"):
            assert outsider.get(path).status_code == 404
        assert outsider.head(media_path).status_code == 404
        assert outsider.put(f"/api/events/{eid}", json=body).status_code == 404
        assert outsider.get("/api/exports/workspace").json()["events"] == []
        body["outcome"] = "unsuccessful"
        assert client.put(f"/api/events/{eid}", json=body).status_code == 200
        corrected = client.get(f"/api/players/{pid}/profile").json()["metrics"][0]
        assert corrected["successful_passes"] == 0 and corrected["pass_attempts"] == 1
        assert corrected["pass_rate"]["posterior_mean_pct"] == pytest.approx(100 / 3)
        assert client.get("/api/leaderboard", params=cohort).json()["entries"][0]["pass_rate"] == corrected["pass_rate"]

    # New API and worker instances still see the original session and corrected DB.
    with TestClient(create_app(settings)) as restarted:
        restarted.cookies.set("scout_session", session_cookie, path="/api")
        restarted.headers.update({"Origin": ORIGIN, "X-CSRF-Token": csrf})
        assert restarted.get("/api/auth/me").status_code == 200
        assert restarted.get(job_path).json()["status"] == "needs_configuration"
        assert restarted.get(media_path).content == video_bytes
        persisted = restarted.get(f"/api/players/{pid}/profile").json()["metrics"][0]
        assert persisted == corrected
        audits = restarted.get(f"/api/matches/{mid}/audit").json()
        assert any(entry["action"] == "correct" and entry["after"].get("outcome") == "unsuccessful" for entry in audits)
        exported = restarted.get("/api/exports/workspace").json()
        assert exported["mode"] == "real" and exported["synthetic_notice"] is None
        assert exported["events"][0]["outcome"] == "unsuccessful"
        assert restarted.delete(f"/api/events/{eid}").status_code == 204
        assert restarted.get(f"/api/players/{pid}/profile").json()["metrics"][0]["pass_rate"]["posterior_mean_pct"] is None
        assert restarted.delete(f"/api/matches/{mid}").status_code == 204
        assert restarted.get(media_path).status_code == 404
        assert list(settings.storage_root.iterdir()) == []
