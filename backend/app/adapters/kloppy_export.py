"""Optional real Kloppy-domain adapter, deliberately not a fake SPADL export.

Caller must authorize workspace/match access before loading observations. Supply
one explicitly identified period, metric top-left pitch coordinates and reviewer-
confirmed roster assignments. Default detector output is intentionally rejected.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import timedelta
from importlib.metadata import version
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas import Id, ObservationWrite


class RosterAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    player_id: Id
    side: Literal["home", "away"]
    jersey_number: int = Field(ge=0, le=99)
    confirmed_by: Id


def to_kloppy(observations: list[ObservationWrite], *, assignments: dict[str, RosterAssignment], pitch_length: float, pitch_width: float, period_id: int, period_start_seconds: float, period_end_seconds: float, is_synthetic: bool = False):
    """Return TrackingDataset without resampling or assuming unseen positions.

    Units in input are metres: x along pitch length, y along pitch width, origin
    top-left in the declared fixed camera-independent pitch coordinate system.
    Frame timestamps remain exact presentation times relative to supplied period.
    Frame IDs are export sequence IDs, not claimed original video frame numbers.
    No full-match coverage, orientation, possession, speed, or ball height inferred.
    """
    if not all(math.isfinite(x) for x in (pitch_length, pitch_width, period_start_seconds, period_end_seconds)):
        raise ValueError("finite pitch dimensions and source times required")
    if not (10 <= pitch_length <= 130 and 10 <= pitch_width <= 100 and 0 <= period_start_seconds < period_end_seconds <= 14400 and 1 <= period_id <= 8):
        raise ValueError("explicit valid pitch dimensions and period bounds required")
    if not observations or len(observations) > 100000:
        raise ValueError("a bounded nonempty set of calibrated observations is required")
    for observation in observations:
        if not observation.calibration_valid or observation.pitch_x is None or observation.pitch_y is None:
            raise ValueError("uncalibrated image boxes cannot be exported as pitch tracking")
        if not (math.isfinite(observation.pitch_x) and math.isfinite(observation.pitch_y) and 0 <= observation.pitch_x <= pitch_length and 0 <= observation.pitch_y <= pitch_width):
            raise ValueError("pitch coordinates must be finite metres within the declared pitch")
        if not period_start_seconds <= observation.timestamp_seconds <= period_end_seconds:
            raise ValueError("observations must lie within the declared period")
        if observation.label in {"player", "goalkeeper"}:
            assignment = assignments.get(observation.track_id)
            if assignment is None or observation.player_id not in {None, assignment.player_id}:
                raise ValueError("each player track needs an explicit consistent reviewer-confirmed roster assignment")
        elif observation.label != "ball":
            raise ValueError("referee export is not yet supported")
    if version("kloppy") != "3.18.0":
        raise ValueError("this adapter is validated for kloppy==3.18.0")
    from kloppy.domain import DatasetFlag, Frame, Ground, KloppyCoordinateSystem, Metadata, Orientation, Period, Player, PlayerData, Point, Point3D, Provider, Team, TrackingDataset

    teams = {side: Team(team_id=side, name=side.title(), ground=Ground(side)) for side in ("home", "away")}
    players = {}
    jersey_owners: dict[tuple[str, int], str] = {}
    player_specs: dict[str, tuple[str, int]] = {}
    for assignment in assignments.values():
        spec = (assignment.side, assignment.jersey_number)
        if assignment.player_id in player_specs and player_specs[assignment.player_id] != spec:
            raise ValueError("a roster player has contradictory team or jersey assignments")
        if spec in jersey_owners and jersey_owners[spec] != assignment.player_id:
            raise ValueError("a team jersey is assigned to multiple players")
        player_specs[assignment.player_id] = spec
        jersey_owners[spec] = assignment.player_id
        if assignment.player_id not in players:
            player = Player(player_id=assignment.player_id, team=teams[assignment.side], jersey_no=assignment.jersey_number)
            teams[assignment.side].players.append(player)
            players[assignment.player_id] = player
    period = Period(id=period_id, start_timestamp=timedelta(seconds=period_start_seconds), end_timestamp=timedelta(seconds=period_end_seconds))
    grouped = defaultdict(list)
    for observation in observations:
        grouped[observation.timestamp_seconds].append(observation)
    frames = []
    for index, (timestamp, rows) in enumerate(sorted(grouped.items())):
        player_data = {}
        ball = None
        provenance = []
        for observation in rows:
            assert observation.pitch_x is not None and observation.pitch_y is not None
            x, y = observation.pitch_x / pitch_length, observation.pitch_y / pitch_width
            if observation.label == "ball":
                if ball is not None:
                    raise ValueError("ambiguous multiple ball observations at the same source time")
                ball = Point3D(x=x, y=y, z=None)
            else:
                player = players[assignments[observation.track_id].player_id]
                if player in player_data:
                    raise ValueError("multiple tracks assigned to the same player at one source time")
                player_data[player] = PlayerData(coordinates=Point(x=x, y=y), other_data={"track_id": observation.track_id, "confidence": observation.confidence, "confirmed_by": assignments[observation.track_id].confirmed_by})
            provenance.append({"track_id": observation.track_id, "model_version": observation.model_version, "confidence": observation.confidence})
        frames.append(Frame(period=period, timestamp=timedelta(seconds=timestamp - period_start_seconds), statistics=[], ball_owning_team=None, ball_state=None, frame_id=index, players_data=player_data, other_data={"source_timestamp_seconds": timestamp, "provenance": provenance, "is_synthetic": is_synthetic}, ball_coordinates=ball))
    coordinates = KloppyCoordinateSystem(pitch_length=pitch_length, pitch_width=pitch_width)
    metadata = Metadata(periods=[period], teams=list(teams.values()), coordinate_system=coordinates, pitch_dimensions=coordinates.pitch_dimensions, orientation=Orientation.NOT_SET, flags=DatasetFlag(0), provider=Provider.OTHER, frame_rate=None, attributes={"schema": "scout.calibrated-tracking.v1", "is_synthetic": is_synthetic, "coverage": "observed_frames_only", "identity": "reviewer_confirmed", "source_coordinate_units": "metres_top_left"})
    return TrackingDataset(records=frames, metadata=metadata)
