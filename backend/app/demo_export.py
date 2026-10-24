"""Generate public demo data from an isolated, fictional-only API workspace."""
from __future__ import annotations

import argparse
import json
import secrets
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

PRIVATE_FIELDS = frozenset({
    "email", "password", "password_hash", "csrf_token", "session", "sessions",
    "created_by", "reviewed_by", "account_id", "storage_key", "storage_root",
    "original_filename", "source_path", "secret_key",
})


def public_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: public_fields(item) for key, item in value.items() if key not in PRIVATE_FIELDS}
    if isinstance(value, list):
        return [public_fields(item) for item in value]
    return value


def build_demo_snapshot() -> dict[str, Any]:
    # No source/database argument: this command cannot export an existing workspace.
    with TemporaryDirectory(prefix="scoutroom-fictional-demo-") as directory:
        root = Path(directory)
        origin = "http://localhost:5173"
        settings = Settings(_env_file=None, environment="test", database_url=f"sqlite:///{root / 'demo.db'}", storage_root=root / "private", allowed_origins=[origin])
        with TestClient(create_app(settings)) as client:
            client.headers["Origin"] = origin
            response = client.post("/api/auth/signup", json={"email": "fictional-demo@example.test", "password": secrets.token_urlsafe(48), "workspace_name": "Fictional public demonstration"})
            response.raise_for_status()
            client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
            client.post("/api/demo/seed").raise_for_status()

            def get(path: str, **params: Any) -> Any:
                result = client.get(f"/api{path}", params=params)
                result.raise_for_status()
                return result.json()

            players = get("/players", mode="demo")
            matches = sorted(get("/matches", mode="demo"), key=lambda row: row["match_date"])
            if len(players) != 4 or len(matches) != 3 or not all(row["is_synthetic"] for row in players + matches):
                raise ValueError("Refusing to export anything except the known fictional seed.")
            cohort = {"age_group": "U15", "competition": "Synthetic development league", "season": "2025 demo", "mode": "demo"}
            profiles = {row["id"]: get(f"/players/{row['id']}/profile") for row in players}
            histories = {row["id"]: {match["match_date"]: get(f"/players/{row['id']}/knowledge", **cohort, role="all_roles", as_of=match["match_date"]) for match in matches} for row in players}
            fixtures = [{"match": row, "roster": get(f"/matches/{row['id']}/roster"), "events": get(f"/matches/{row['id']}/events")} for row in matches]
            if not all(event["is_synthetic"] and event["provenance"] == "synthetic" for fixture in fixtures for event in fixture["events"]):
                raise ValueError("Non-fictional event encountered; nothing will be published.")
            leaderboards = {role: get("/leaderboard", **cohort, role=role) for role in ("midfielder", "defender")}
            duels = get("/intelligence/duels", **cohort, role="all_roles")
            pairs = {f"{first['id']}:{second['id']}": get("/intelligence/duels", **cohort, role="all_roles", player_id=first["id"], opponent_player_id=second["id"]) for first in players for second in players if first != second}
            snapshot = {"schema_version": 1, "is_synthetic": True, "read_only": True, "notice": "Entirely fictional demonstration. No real players, footage, accounts or predictions. Values are computed from synthetic reviewed events by the application engines.", "players": players, "fixtures": fixtures, "profiles": profiles, "histories": histories, "leaderboards": leaderboards, "duels": duels, "pairs": pairs}
            return public_fields(snapshot)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="Public JSON output; parent directory must exist.")
    args = parser.parse_args()
    snapshot = build_demo_snapshot()
    args.output.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("Exported four fictional players and three fictional matches; no accounts or private media.")


if __name__ == "__main__":
    main()
