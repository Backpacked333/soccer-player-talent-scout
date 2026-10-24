"""Bounded, local-only video validation and optional anonymous detection.

The default result is metadata_ready / needs_configuration, never "analyzed".
The command-line interface is internal to the durable worker, not an HTTP API.
"""
from __future__ import annotations

import json
import math
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

from pydantic import ValidationError

from app.config import Settings
from app.media_geometry import display_geometry
from app.integrations import VisionSettings, detector_readiness
from app.integrations import get_integrations as get_integrations
from app.schemas import VideoMetadata, VisionResult

MAX_RESULT_BYTES = 32 * 1024 * 1024
ALLOWED_FORMATS = {"mov", "mp4", "m4a", "3gp", "3g2", "mj2", "matroska", "webm", "avi"}


class MediaError(Exception):
    """Only the constant public message and code may cross the API boundary."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.public_message = code, message


def child_environment() -> dict[str, str]:
    # Deliberately do not pass credentials or arbitrary caller environment values.
    return {"PATH": os.defpath, "LANG": "C.UTF-8", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2"}


def open_video(path: Path, settings: Settings):
    try:
        if path.is_symlink() or path.resolve().parent != settings.storage_root.resolve():
            raise MediaError("unsafe_media_path", "Media storage validation failed. Re-upload the video through the private upload form.")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= settings.max_upload_bytes:
            os.close(fd)
            raise MediaError("invalid_media_size", "The stored video is empty or exceeds the configured upload limit.")
        return os.fdopen(fd, "rb")
    except OSError as exc:
        raise MediaError("media_unavailable", "The private video is unavailable. Re-upload it or ask the operator to check storage.") from exc


def probe_video(video_path: Path, *, settings: Settings) -> VideoMetadata:
    with open_video(video_path, settings) as media:
        # An inherited descriptor pins the already-validated regular file. Whitelist
        # excludes HLS/playlists and HTTP/crypto/concat/subfile network protocols.
        command = [settings.ffprobe_binary, "-v", "error"]
        command += ["-protocol_whitelist", "file", "-format_whitelist", "mov,matroska,webm,avi", "-max_alloc", "67108864", "-probesize", "10000000", "-analyzeduration", "10000000", "-select_streams", "v:0", "-show_entries", "stream=codec_name,width,height,avg_frame_rate,r_frame_rate,duration,sample_aspect_ratio:stream_tags=rotate:stream_side_data_list:format=duration,format_name", "-of", "json", "-i", f"/proc/self/fd/{media.fileno()}"]
        try:
            with tempfile.TemporaryFile() as output:
                completed = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL, pass_fds=(media.fileno(),), timeout=settings.probe_timeout_seconds, check=False, env=child_environment())
                output.seek(0)
                raw = output.read(65537)
            if completed.returncode or len(raw) > 65536:
                raise MediaError("invalid_video", "This file could not be validated as an MP4/MOV, WebM/MKV or AVI video. Export a standard video and upload it again.")
        except FileNotFoundError as exc:
            raise MediaError("probe_unavailable", "ffprobe is not installed. Ask the operator to install FFmpeg before processing videos.") from exc
        except subprocess.TimeoutExpired as exc:
            raise MediaError("probe_timeout", "Video validation exceeded its time limit. Try a shorter standard-format export.") from exc
        except OSError as exc:
            raise MediaError("probe_unavailable", "The video validator could not start. Ask the operator to check the local FFmpeg installation.") from exc
    try:
        data = json.loads(raw)
        stream = data["streams"][0]
        container = data["format"]
        if not set(container["format_name"].split(",")).issubset(ALLOWED_FORMATS):
            raise ValueError("unsupported container")
        duration = float(container.get("duration") or stream["duration"])
        if stream.get("duration") is not None and (not math.isfinite(float(stream["duration"])) or float(stream["duration"]) <= 0):
            raise ValueError("invalid video stream duration")
        fps = float(Fraction(stream.get("avg_frame_rate") or "0/1"))
        if fps <= 0:
            fps = float(Fraction(stream["r_frame_rate"]))
        width, height = int(stream["width"]), int(stream["height"])
        codec = str(stream["codec_name"])
        if not math.isfinite(duration) or duration <= 0 or duration > settings.max_video_seconds:
            raise MediaError("duration_limit", "Video duration is missing, invalid or longer than the configured limit. Upload a shorter clip.")
        if max(width, height) > settings.max_video_dimension:
            raise MediaError("resolution_limit", "Video resolution exceeds the configured limit. Export a lower-resolution copy.")
        if not re.fullmatch(r"[A-Za-z0-9_]{1,80}", codec):
            raise ValueError("invalid codec")
        return VideoMetadata(duration_seconds=duration, width=width, height=height, fps=fps, codec=codec, display_geometry=display_geometry(stream))
    except (ValueError, KeyError, IndexError, TypeError, ZeroDivisionError, OverflowError) as exc:
        raise MediaError("invalid_video_metadata", "Video timing or dimensions are invalid. Export a standard video with a valid video track.") from exc


def run_json_child(module: str, payload: dict, *, timeout: float) -> dict:
    """Fixed internal module, bounded output and execution; never user executables."""
    if module not in {"app.vision", "app.adapters.rfdetr_tracker"}:
        raise ValueError("unsupported internal worker module")
    with tempfile.TemporaryFile() as output:
        try:
            with subprocess.Popen([sys.executable, "-m", module], stdin=subprocess.PIPE, stdout=output, stderr=subprocess.DEVNULL, cwd=Path(__file__).resolve().parents[1], env=child_environment(), start_new_session=(module == "app.vision")) as process:
                try:
                    process.communicate(input=json.dumps(payload).encode(), timeout=timeout)
                except subprocess.TimeoutExpired as exc:
                    if module == "app.vision":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    process.communicate()
                    raise MediaError("processing_timeout", "Processing exceeded its time limit. Use a shorter clip or reduce the detector frame budget.") from exc
                returncode = process.returncode
        except OSError as exc:
            raise MediaError("processing_unavailable", "The isolated processor could not start. Ask the operator to check the worker installation.") from exc
        output.seek(0)
        raw = output.read(MAX_RESULT_BYTES + 1)
    if returncode or len(raw) > MAX_RESULT_BYTES:
        raise MediaError("processor_failed", "The isolated processor failed. Check the local installation; no player statistics were generated.")
    try:
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("expected object")
        return result
    except (ValueError, TypeError) as exc:
        raise MediaError("processor_failed", "The processor returned invalid output. No player statistics were generated.") from exc


def analyze_video(video_path: Path, *, settings: Settings, config: VisionSettings | None = None) -> VisionResult:
    metadata = probe_video(video_path, settings=settings)
    try:
        config = config or VisionSettings()
        status, detail = detector_readiness(config, settings)
    except (ValueError, OSError):
        config = None
        status, detail = "requires_model_configuration", "Operator vision settings are invalid. Consult docs/INTEGRATIONS.md."
    if status != "ready" or config is None:
        return VisionResult(status="needs_configuration", stage="metadata_ready", message="Video metadata verified. " + detail, metadata=metadata, warnings=["No player detection, identity, events, or performance metrics were inferred. Manual event review remains available."])
    try:
        raw = run_json_child("app.adapters.rfdetr_tracker", {"path": str(video_path), "metadata": metadata.model_dump(), "config": config.model_dump(mode="json")}, timeout=min(config.timeout_seconds, settings.worker_lease_seconds - settings.probe_timeout_seconds - 10))
        if raw.get("error"):
            return VisionResult(status="needs_configuration", stage="detector_configuration", message="The trusted detector could not initialize. Verify checkpoint digest, class map and pinned offline dependencies; no statistics were generated.", metadata=metadata)
        return VisionResult(status="needs_review", stage="anonymous_tracks_ready", message="Anonymous detection evidence is ready for review. Confirm roster identities and annotate events manually; detections are not player statistics.", metadata=metadata, observations=raw["observations"], warnings=raw["warnings"])
    except (MediaError, ValidationError, KeyError):
        return VisionResult(status="needs_configuration", stage="detector_configuration", message="Metadata is valid but optional detection failed or exceeded its budget. Check local model configuration or use a shorter clip; manual review remains available.", metadata=metadata, warnings=["No automated events or approved player statistics were produced."])


def _main() -> None:
    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise ValueError("input too large")
        payload = json.loads(raw)
        import resource
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_RESULT_BYTES, MAX_RESULT_BYTES))
        result = analyze_video(Path(payload["path"]), settings=Settings(_env_file=None, **payload["settings"]), config=VisionSettings(_env_file=None, **payload.get("vision", {})))
        print(json.dumps({"result": result.model_dump(mode="json")}))
    except MediaError as exc:
        print(json.dumps({"error": exc.code, "message": exc.public_message}))
    except Exception:
        # Never serialize tracebacks, decoder output, model paths or exception text.
        print(json.dumps({"error": "processor_failed", "message": "Processing could not complete. Ask the operator to check the worker installation; no statistics were generated."}))


if __name__ == "__main__":
    _main()
