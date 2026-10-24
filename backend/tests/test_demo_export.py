import json

import pytest

from app.demo_export import PRIVATE_FIELDS, build_demo_snapshot, public_fields


@pytest.fixture(scope="module")
def snapshot():
    return build_demo_snapshot()


def test_export_is_fictional_only_and_uses_engine_outputs(snapshot):
    assert snapshot["is_synthetic"] is True
    assert snapshot["read_only"] is True
    assert len(snapshot["players"]) == 4
    assert len(snapshot["fixtures"]) == 3
    assert snapshot["duels"]["accepted_contests"] == 12
    for player in snapshot["players"]:
        assert player["is_synthetic"] is True
        profile = snapshot["profiles"][player["id"]]
        events = [event for fixture in snapshot["fixtures"] for event in fixture["events"] if event["player_id"] == player["id"]]
        assert profile["metrics"][0]["approved_event_count"] == len(events)
        histories = snapshot["histories"][player["id"]]
        dates = sorted(histories)
        assert len(dates) == 3
        assert histories[dates[-1]]["approved_event_count"] == len(events)
        assert all(row["match_date"] <= dates[0] for row in histories[dates[0]]["match_details"])
    assert all(event["is_synthetic"] and event["provenance"] == "synthetic" for fixture in snapshot["fixtures"] for event in fixture["events"])


def test_no_accounts_credentials_or_private_paths_in_export(snapshot):
    def inspect(value):
        if isinstance(value, dict):
            assert not PRIVATE_FIELDS.intersection(value)
            for item in value.values():
                inspect(item)
        elif isinstance(value, list):
            for item in value:
                inspect(item)
    inspect(snapshot)
    encoded = json.dumps(snapshot)
    assert "example.test" not in encoded
    assert "sqlite:///" not in encoded
    assert "scoutroom-fictional-demo-" not in encoded


def test_nested_privileged_fields_are_removed():
    assert public_fields({"events": [{"id": "fictional", "created_by": "private"}], "csrf_token": "private"}) == {"events": [{"id": "fictional"}]}
