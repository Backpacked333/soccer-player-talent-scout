"""Deterministic dependency doubles test adapter wiring, not model accuracy.

These doubles are ONLY in tests. Production never falls back to fake detections.
"""
import contextlib
import hashlib
import socket
import sys
from fractions import Fraction
from types import SimpleNamespace

import pytest

from app.adapters.rfdetr_tracker import _detect_research as detect
from app.integrations import VisionSettings
from app.schemas import VideoMetadata


@pytest.mark.parametrize("defect", [None, "partial", "wrong_head", "wrong_shape", "non_tensor", "unsafe"])
def test_checkpoint_loader_rejects_partial_and_unsafe_states(tmp_path, monkeypatch, defect):
    from app.adapters.rfdetr_tracker import _load_detector

    class Tensor:
        def __init__(self, shape):
            self.shape = shape
    expected = {"class_embed.weight": Tensor((5, 3)), "backbone.weight": Tensor((2, 2)), "_kp_active_mask": Tensor((0,))}
    state = {key: value for key, value in expected.items() if key != "_kp_active_mask"}
    if defect == "partial":
        state.pop("backbone.weight")
    elif defect == "wrong_head":
        state["class_embed.weight"] = Tensor((91, 3))
    elif defect == "wrong_shape":
        state["backbone.weight"] = Tensor((3, 3))
    elif defect == "non_tensor":
        state["backbone.weight"] = "not a tensor"
    calls = []
    def load(path, **kwargs):
        calls.append(kwargs)
        assert kwargs == {"map_location": "cpu", "weights_only": True}
        if defect == "unsafe":
            raise ValueError("unsafe checkpoint must never fall back to unrestricted pickle")
        return {"model": state}
    def model(**kwargs):
        assert kwargs["trust_checkpoint"] is False
        return SimpleNamespace(model=SimpleNamespace(model=SimpleNamespace(state_dict=lambda: expected)))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(load=load, Tensor=Tensor))
    monkeypatch.setitem(sys.modules, "rfdetr", SimpleNamespace(RFDETRBase=model, RFDETRLargeDeprecated=model))
    config = VisionSettings(_env_file=None, architecture="base", num_classes=4)
    if defect:
        with pytest.raises(ValueError):
            _load_detector(tmp_path / "never-loaded.pth", config)
    else:
        assert _load_detector(tmp_path / "never-loaded.pth", config)
    assert len(calls) == 1


class Box(list):
    def astype(self, _):
        return self


class Scores(list):
    def __neg__(self):
        return [-value for value in self]


class Detections:
    xyxy = [Box([32, 48, 96, 120])]
    confidence = Scores([0.8])
    class_id = [0]
    tracker_id = [7]

    def __getitem__(self, _):
        return self


@pytest.mark.parametrize("container_origin,pts_values,expected_times,expected_epoch", [(1000000, (1000, 1043, 1100), (0, 0.043), 0), (500000, (1000, 1043, 1100), (0.5, 0.543), 0), (1000000, (1000, 1643, 1700), (0, 0.643), 1)])
def test_real_adapter_loop_preserves_source_pts_and_budgets(tmp_path, monkeypatch, container_origin, pts_values, expected_times, expected_epoch):
    checkpoint = tmp_path / "test-only.pth"
    checkpoint.write_bytes(b"test-only-checkpoint-never-loaded-by-torch")
    video = tmp_path / "test-only.mp4"
    video.write_bytes(b"test-only-decoder-double")
    captured = {}

    class FakeDetector:
        def __init__(self, **kwargs):
            captured["model"] = kwargs

        def predict(self, frame, **kwargs):
            assert frame == "RGB_TEST_FRAME" and kwargs["threshold"] == 0.45
            return Detections()

    class FakeTracker:
        def __init__(self, **kwargs):
            captured["tracker"] = kwargs

        def update_with_detections(self, detections):
            return detections

    class FakeContainer:
        start_time = container_origin
        streams = SimpleNamespace(video=[SimpleNamespace(start_time=1000)])

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def decode(self, _):
            for pts in pts_values:
                yield SimpleNamespace(pts=pts, time_base=Fraction(1, 1000), width=320, height=240, to_ndarray=lambda **_: "RGB_TEST_FRAME")

    monkeypatch.setitem(sys.modules, "av", SimpleNamespace(open=lambda *_args, **_kwargs: FakeContainer()))
    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace(isin=lambda *_: [True], argsort=lambda _: [0]))
    monkeypatch.setitem(sys.modules, "supervision", SimpleNamespace(ByteTrack=FakeTracker))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(set_num_threads=lambda _: None, inference_mode=contextlib.nullcontext))
    monkeypatch.setitem(sys.modules, "rfdetr", SimpleNamespace(RFDETRBase=FakeDetector, RFDETRLarge=FakeDetector))
    monkeypatch.setattr("app.adapters.rfdetr_tracker._load_detector", lambda checkpoint, config: FakeDetector(pretrain_weights=str(checkpoint)))
    # detect's network deny-list intentionally lasts for its whole isolated child.
    # Register restoration here because this unit test invokes it in-process.
    monkeypatch.setattr(socket, "create_connection", socket.create_connection)
    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, getattr(socket.socket, name))
    config = VisionSettings(_env_file=None, enabled=True, model_root=tmp_path, checkpoint=checkpoint.name, checkpoint_sha256=hashlib.sha256(checkpoint.read_bytes()).hexdigest(), license_reviewed=True, security_reviewed=True, labels={0: "player"}, max_frames=2)
    result = detect(video, VideoMetadata(duration_seconds=1, width=320, height=240, fps=25, codec="test"), config)
    assert captured["model"]["pretrain_weights"] == str(checkpoint)
    assert captured["tracker"] == {"frame_rate": 25}
    assert len(result["observations"]) == 2
    first, second = result["observations"]
    assert abs(first["timestamp_seconds"] - expected_times[0]) < 1e-9
    assert abs(second["timestamp_seconds"] - expected_times[1]) < 1e-9  # source PTS, not index / nominal FPS
    assert first["bbox"] == [0.1, 0.2, 0.3, 0.5]
    assert first["track_id"] == "player-e0-7" and first["player_id"] is None
    assert second["track_id"] == f"player-e{expected_epoch}-7"
    assert hashlib.sha256(checkpoint.read_bytes()).hexdigest() in first["model_version"]
    assert first["pitch_x"] is None and first["calibration_valid"] is False
    assert any("only the first 2 frames" in message for message in result["warnings"])
