"""Optional adapter checks also run in an isolated uv environment with Kloppy."""
from uuid import uuid4

import pytest

from app.adapters.kloppy_export import RosterAssignment, to_kloppy
from app.schemas import ObservationWrite


def observation(**overrides):
    values = dict(track_id="track-1", timestamp_seconds=12.125, label="player", bbox=(0.1, 0.1, 0.2, 0.3), confidence=0.85, model_version="test-only-calibrated-fixture", pitch_x=20, pitch_y=10, calibration_valid=True)
    values.update(overrides)
    return ObservationWrite(**values)


def export(rows, assignments=None):
    return to_kloppy(rows, assignments=assignments or {}, pitch_length=80, pitch_width=50, period_id=1, period_start_seconds=10, period_end_seconds=20, is_synthetic=True)


def test_kloppy_rejects_default_uncalibrated_detector_output():
    with pytest.raises(ValueError, match="uncalibrated"):
        export([observation(pitch_x=None, pitch_y=None, calibration_valid=False)])


def test_kloppy_rejects_unconfirmed_roster_identity():
    with pytest.raises(ValueError, match="reviewer-confirmed"):
        export([observation()])


def test_kloppy_real_domain_conversion_preserves_time_and_no_missing_player_imputation():
    pytest.importorskip("kloppy")
    player_id, reviewer = str(uuid4()), str(uuid4())
    assignments = {"track-1": RosterAssignment(player_id=player_id, side="home", jersey_number=8, confirmed_by=reviewer)}
    result = export([observation(), observation(track_id="ball-1", label="ball", timestamp_seconds=12.2, pitch_x=21, pitch_y=11)], assignments)
    assert result.metadata.provider.value == "other"
    assert result.metadata.frame_rate is None
    assert result.metadata.attributes["is_synthetic"] is True
    assert result.metadata.pitch_dimensions.pitch_length == 80
    first, second = result.frames
    assert first.timestamp.total_seconds() == 2.125
    assert first.other_data["source_timestamp_seconds"] == 12.125
    player, data = next(iter(first.players_data.items()))
    assert player.player_id == player_id
    assert data.coordinates.x == 0.25 and data.coordinates.y == 0.2
    assert data.speed is None and data.distance is None
    assert first.ball_coordinates is None and first.ball_owning_team is None
    assert second.players_data == {}  # absent is not stationary or interpolated
    assert second.ball_coordinates.z is None


def test_kloppy_refuses_ambiguous_player_track_merge():
    pytest.importorskip("kloppy")
    assignment = RosterAssignment(player_id=str(uuid4()), side="away", jersey_number=2, confirmed_by=str(uuid4()))
    with pytest.raises(ValueError, match="multiple tracks"):
        export([observation(), observation(track_id="track-2")], {"track-1": assignment, "track-2": assignment})
