"""No real children's footage or downloaded models: generated color clips only."""
import json
import shutil
import subprocess

import pytest
from pydantic import ValidationError

from app import integrations, vision
from app.config import Settings
from app.integrations import VisionSettings, detector_readiness, get_integrations
from app.schemas import VideoMetadata


@pytest.fixture
def clip(tmp_path):
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("FFmpeg is required for real decoder integration tests")
    path = tmp_path / "synthetic-color.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x240:r=25", "-t", "1", "-c:v", "mpeg4", "-y", str(path)], check=True, timeout=10, env=vision.child_environment())
    settings = Settings(_env_file=None, environment="test", storage_root=tmp_path, probe_timeout_seconds=3, worker_lease_seconds=30)
    return path, settings


def test_actual_metadata_probe_is_not_analysis(clip):
    path, settings = clip
    result = vision.analyze_video(path, settings=settings, config=VisionSettings(_env_file=None, enabled=False))
    assert result.status == "needs_configuration" and result.stage == "metadata_ready"
    assert result.observations == [] and result.candidates == []
    assert result.metadata.duration_seconds == pytest.approx(1, abs=0.05)
    assert (result.metadata.width, result.metadata.height, result.metadata.fps) == (320, 240, 25)
    assert result.metadata.codec == "mpeg4"
    assert str(path) not in result.model_dump_json()


def test_worker_isolated_child_preserves_metadata(clip):
    from app.worker import _execute
    path, settings = clip
    result = _execute(path, settings)
    assert result.status == "needs_configuration"
    assert result.metadata.fps == 25


def test_corrupt_and_playlist_inputs_cannot_be_probed(tmp_path):
    settings = Settings(_env_file=None, environment="test", storage_root=tmp_path)
    for content in (b"not video", b"#EXTM3U\n#EXTINF:1,\nhttp://127.0.0.1:9/private\n", b"ffconcat version 1.0\nfile '/etc/passwd'\n"):
        path = tmp_path / "not-a-video.mp4"
        path.write_bytes(content)
        with pytest.raises(vision.MediaError) as error:
            vision.probe_video(path, settings=settings)
        assert error.value.code == "invalid_video"
        assert str(path) not in error.value.public_message


def test_probe_rejects_limits_and_symlinks(clip, tmp_path):
    path, settings = clip
    with pytest.raises(vision.MediaError, match="duration"):
        vision.probe_video(path, settings=settings.model_copy(update={"max_video_seconds": 0.5}))
    with pytest.raises(vision.MediaError, match="resolution"):
        vision.probe_video(path, settings=settings.model_copy(update={"max_video_dimension": 100}))
    link = tmp_path / "linked.mp4"
    link.symlink_to(path)
    with pytest.raises(vision.MediaError) as error:
        vision.probe_video(link, settings=settings)
    assert error.value.code == "unsafe_media_path"


def test_probe_missing_dependency_is_actionable(clip):
    path, settings = clip
    with pytest.raises(vision.MediaError) as error:
        vision.probe_video(path, settings=settings.model_copy(update={"ffprobe_binary": "/missing-operator-install/ffprobe"}))
    assert error.value.code == "probe_unavailable"
    assert "/missing" not in error.value.public_message


def test_probe_invocation_uses_protocol_whitelist_and_timeout(clip, monkeypatch):
    path, settings = clip
    captured = {}
    def timeout(args, **kwargs):
        captured.update(args=args, **kwargs)
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])
    monkeypatch.setattr(vision.subprocess, "run", timeout)
    with pytest.raises(vision.MediaError) as error:
        vision.probe_video(path, settings=settings)
    assert error.value.code == "probe_timeout"
    assert isinstance(captured["args"], list)
    assert captured["args"][captured["args"].index("-protocol_whitelist") + 1] == "file"
    assert "http" not in captured["args"][captured["args"].index("-format_whitelist") + 1]
    assert captured["timeout"] == settings.probe_timeout_seconds
    assert not captured.get("shell", False)
    assert captured["stdin"] == subprocess.DEVNULL
    assert set(captured["env"]) == {"PATH", "LANG", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "OMP_NUM_THREADS", "MKL_NUM_THREADS"}


@pytest.mark.parametrize("field,value", [("duration", "nan"), ("duration", "inf"), ("avg_frame_rate", "0/0"), ("width", 0), ("codec_name", "../../private/path")])
def test_malformed_metadata_is_rejected(clip, monkeypatch, field, value):
    path, settings = clip
    stream = {"duration": "1", "avg_frame_rate": "25/1", "width": 320, "height": 240, "codec_name": "mpeg4"}
    stream[field] = value
    def fake(*args, **kwargs):
        kwargs["stdout"].write(json.dumps({"streams": [stream], "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "1"}}).encode())
        return subprocess.CompletedProcess(args, 0)
    monkeypatch.setattr(vision.subprocess, "run", fake)
    with pytest.raises(vision.MediaError):
        vision.probe_video(path, settings=settings)


@pytest.mark.parametrize("kwargs", [{"checkpoint": "https://example.com/model.pth"}, {"checkpoint": "../model.pth"}, {"checkpoint": "/etc/model.pth"}, {"model_root": "relative"}, {"checkpoint_sha256": "wrong"}, {"max_frames": 10001}, {"timeout_seconds": 901}])
def test_operator_model_input_is_restricted(kwargs):
    with pytest.raises(ValidationError):
        VisionSettings(_env_file=None, **kwargs)


def test_registry_license_blocks_are_not_overridden_by_installs(clip, monkeypatch):
    _, settings = clip
    monkeypatch.setattr(integrations, "installed", lambda _: True)
    rows = {item.id: item for item in get_integrations(settings)}
    assert all(rows[name].status == "license_blocked" for name in ("footpass", "soccermaster", "sn-gamestate", "broadcast2pitch"))
    assert rows["rfdetr"].status == "requires_model_configuration"
    assert rows["ffprobe"].status == "ready"
    assert "Not wired" in rows["socceraction"].detail


def test_configured_model_requires_installed_dependencies(clip, tmp_path, monkeypatch):
    _, settings = clip
    root = tmp_path / "models"
    root.mkdir()
    (root / "trusted.pth").write_bytes(b"test-only-placeholder-not-model-weights")
    config = VisionSettings(_env_file=None, enabled=True, license_reviewed=True, model_root=root, checkpoint="trusted.pth", checkpoint_sha256="0" * 64, labels={0: "ball", 1: "player"})
    # Separate storage from model-root in this fixture.
    settings = settings.model_copy(update={"storage_root": tmp_path / "media"})
    monkeypatch.setattr(integrations, "installed", lambda _: False)
    status, detail = detector_readiness(config, settings)
    assert status == "requires_model_configuration" and "reviewed" in detail
    assert detector_readiness(config.model_copy(update={"license_reviewed": False}), settings)[0] == "requires_model_configuration"
    monkeypatch.setattr(integrations, "installed", lambda _: True)
    monkeypatch.setattr(integrations, "version", lambda _: "fully-patched-test-only")
    status, detail = detector_readiness(config, settings)
    assert status == "requires_model_configuration" and "reviewed" in detail
    assert detector_readiness(config.model_copy(update={"security_reviewed": True}), settings)[0] == "requires_model_configuration"
    monkeypatch.setattr(integrations, "version", lambda _: "0.0.0-test-only")
    assert detector_readiness(config.model_copy(update={"security_reviewed": True}), settings)[0] == "requires_model_configuration"
    monkeypatch.setattr(integrations, "version", lambda package: integrations.DETECTOR_PACKAGES[package])
    ready = config.model_copy(update={"security_reviewed": True})
    assert detector_readiness(ready, settings)[0] == "ready"
    assert detector_readiness(ready.model_copy(update={"device": "cuda"}), settings)[0] == "requires_model_configuration"
    assert detector_readiness(ready, settings.model_copy(update={"storage_root": root}))[0] == "requires_model_configuration"


def test_checkpoint_digest_rejection_precedes_optional_imports(tmp_path):
    from app.adapters.rfdetr_tracker import _detect_research as detect
    (tmp_path / "trusted.pth").write_bytes(b"not-a-model-and-never-loaded")
    config = VisionSettings(_env_file=None, enabled=True, license_reviewed=True, security_reviewed=True, model_root=tmp_path, checkpoint="trusted.pth", checkpoint_sha256="0" * 64, labels={0: "player"})
    with pytest.raises(ValueError, match="checksum"):
        detect(tmp_path / "video.mp4", VideoMetadata(duration_seconds=1, width=320, height=240, fps=25, codec="mpeg4"), config)


def test_flags_alone_cannot_enable_unconfigured_detector(clip, monkeypatch):
    from app.adapters.rfdetr_tracker import detect
    path, settings = clip
    for flag in ("ENABLED", "LICENSE_REVIEWED", "SECURITY_REVIEWED"):
        monkeypatch.setenv(f"SCOUT_VISION_{flag}", "true")
    config = VisionSettings(_env_file=None)
    monkeypatch.setattr(integrations, "installed", lambda _: True)
    assert detector_readiness(config, settings)[0] == "requires_model_configuration"
    result = vision.analyze_video(path, settings=settings, config=config)
    assert result.status == "needs_configuration" and result.observations == []
    assert result.candidates == [] and result.metadata.width == 320
    with pytest.raises(ValueError, match="local checkpoint"):
        detect(path, result.metadata, config)
    child = vision.run_json_child("app.adapters.rfdetr_tracker", {
        "video_path": str(path), "metadata": result.metadata.model_dump(),
        "config": config.model_dump(mode="json"),
    }, timeout=10)
    assert child == {"error": "detector_unavailable"}


def test_bounded_child_cannot_take_arbitrary_executable():
    with pytest.raises(ValueError):
        vision.run_json_child("arbitrary.module", {}, timeout=1)


def test_optional_detector_failure_preserves_real_metadata(clip, monkeypatch):
    path, settings = clip
    monkeypatch.setattr(vision, "detector_readiness", lambda *_: ("ready", "Test fake readiness"))
    monkeypatch.setattr(vision, "run_json_child", lambda *_args, **_kwargs: {"error": "detector_unavailable"})
    result = vision.analyze_video(path, settings=settings, config=VisionSettings(_env_file=None))
    assert result.status == "needs_configuration" and result.stage == "detector_configuration"
    assert result.metadata.fps == 25 and result.observations == []


def test_actual_timeout_kills_isolated_processor():
    # The fixed module starts genuine Python; an intentionally tiny runtime makes
    # this a deterministic timeout without introducing any production fake mode.
    with pytest.raises(vision.MediaError) as error:
        vision.run_json_child("app.vision", {}, timeout=0.00001)
    assert error.value.code == "processing_timeout"
