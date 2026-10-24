"""Capability checks and operator-only configuration; no heavyweight imports."""
from __future__ import annotations

import importlib.util
import re
import shutil
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import PROJECT_ROOT, Settings
from app.schemas import IntegrationRead

DETECTOR_PACKAGES = {
    "rfdetr": "1.9.2", "supervision": "0.29.1", "av": "16.0.1",
    "torch": "2.14.1", "torchvision": "0.29.1", "transformers": "5.20.2",
}
DETECTOR_SECURITY_HOLD = "Optional detection requires the audited CPU dependency lock, explicitly reviewed model/data rights and a digest-verified local tensor checkpoint. No model is installed automatically."


class VisionSettings(BaseSettings):
    """Never populated from HTTP requests. Only reviewed tensor checkpoints."""

    model_config = SettingsConfigDict(env_prefix="SCOUT_VISION_", env_file=PROJECT_ROOT / ".env", extra="ignore", hide_input_in_errors=True)
    enabled: bool = False
    model_root: Path | None = None
    checkpoint: str | None = None
    checkpoint_sha256: str | None = None
    license_reviewed: bool = False
    security_reviewed: bool = False
    architecture: Literal["base", "large"] = "base"
    device: Literal["cpu", "cuda"] = "cpu"
    num_classes: int = Field(default=4, ge=1, le=100)
    labels: dict[int, Literal["player", "goalkeeper", "ball", "referee"]] = Field(default_factory=dict)
    threshold: float = Field(default=0.45, ge=0.1, le=0.95)
    tracking_gap_seconds: float = Field(default=0.5, ge=0.05, le=10)
    max_frames: int = Field(default=900, ge=1, le=10000)
    max_observations: int = Field(default=20000, ge=1, le=50000)
    max_detections_per_frame: int = Field(default=100, ge=1, le=200)
    timeout_seconds: int = Field(default=120, ge=5, le=900)

    @model_validator(mode="after")
    def validate_operator_input(self):
        if self.checkpoint is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\.(?:pth|pt)", self.checkpoint):
            raise ValueError("checkpoint must be a local basename, not a URL or path")
        if self.model_root is not None and not self.model_root.is_absolute():
            raise ValueError("model_root must be an operator-controlled absolute directory")
        if self.checkpoint_sha256 is not None and not re.fullmatch(r"[a-f0-9]{64}", self.checkpoint_sha256):
            raise ValueError("checkpoint_sha256 must be a lowercase SHA-256 digest")
        if any(key < 0 or key >= self.num_classes for key in self.labels):
            raise ValueError("class identifiers must fit the checkpoint class mapping")
        return self

    def checkpoint_path(self) -> Path | None:
        if not self.model_root or not self.checkpoint:
            return None
        root = self.model_root.resolve()
        path = root / self.checkpoint
        # Never follow a checkpoint symlink or load models from the upload store.
        if path.is_symlink() or not path.is_file() or path.resolve().parent != root:
            return None
        if not 1 <= path.stat().st_size <= 2 * 1024**3:
            return None
        return path


def installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ValueError, ImportError, ModuleNotFoundError):
        return False


def versioned_status(module: str, expected: str) -> str:
    if not installed(module):
        return "optional_not_installed"
    try:
        return "ready" if version(module) == expected else "requires_model_configuration"
    except PackageNotFoundError:
        return "optional_not_installed"


def detector_readiness(config: VisionSettings, settings: Settings) -> tuple[str, str]:
    if not config.enabled or not config.license_reviewed or not config.security_reviewed:
        return "requires_model_configuration", DETECTOR_SECURITY_HOLD
    if config.device != "cpu":
        return "requires_model_configuration", "Only the audited CPU runtime has been compatibility-tested; GPU inference is not validated."
    checkpoint = config.checkpoint_path()
    if checkpoint is None or not config.checkpoint_sha256 or not config.labels:
        return "requires_model_configuration", "Configure an explicit local checkpoint, SHA-256 digest and reviewed class map. No weights are downloaded."
    if checkpoint.is_relative_to(settings.storage_root.resolve()):
        return "requires_model_configuration", "Model files must be in a separate operator-controlled directory, never uploaded media storage."
    for package, expected in DETECTOR_PACKAGES.items():
        try:
            actual = version(package)
        except PackageNotFoundError:
            return "optional_not_installed", "Install the isolated audited CPU requirements before enabling optional detection."
        allowed = {expected, expected + "+cpu"} if package in {"torch", "torchvision"} else {expected}
        if actual not in allowed:
            return "requires_model_configuration", f"Unsupported {package} version. Use the exact reviewed optional dependency lock."
    return "ready", "Optional offline CPU runtime configured. Checkpoint digest and complete tensor shapes are checked at execution. Anonymous detections still require identity review; no youth accuracy or event analysis is claimed."


def get_integrations(settings: Settings) -> list[IntegrationRead]:
    def row(id, name, status, license, purpose, detail, source_url):
        return IntegrationRead(id=id, name=name, status=status, license=license, purpose=purpose, detail=detail, source_url=source_url)

    try:
        config = VisionSettings()
        detector_status, detail = detector_readiness(config, settings)
    except (ValueError, OSError):
        detector_status, detail = "requires_model_configuration", "Operator vision configuration is invalid; consult docs/INTEGRATIONS.md."
    return [
        row("reviewed-memory", "Canonical identity and reviewed player memory", "ready", "Local application implementation", "Reviewer-confirmed track attribution, chronological history and evidence-linked relationships", "Private APIs and review/profile UI recompute current evidence after corrections. Ambiguity stays unresolved; no autonomous visual learning or face recognition.", "https://www.fifa.com/legal/data-protection"),
        row("direct-duel-model", "Regularized Bradley–Terry direct duels", "ready", "Local application implementation", "Explicit contest evidence, graph components, sample gates and approximate uncertainty", "Unvalidated research estimates within one workspace/mode/season/age/competition/role boundary. Mirrored records collapse once. Not general opponent quality, talent or potential.", "https://doi.org/10.1093/biomet/39.3-4.324"),
        row("spatial-action-value", "Spatial Markov action value", "requires_model_configuration", "Local application implementation; training data rights separate", "Optional operator-activated bounded JSON research artifact", "An operator path alone is not readiness. Each request validates exact scope, integrity, current training evidence and support. Without a compatible artifact values remain null; counts/history still work. No live user model upload or client filesystem path.", "https://karun.in/blog/expected-threat.html"),
        row("ffprobe", "Private video metadata probe", "ready" if shutil.which(settings.ffprobe_binary) else "optional_not_installed", "FFmpeg build-dependent LGPL-2.1-or-later / GPL", "Container duration, resolution, frame rate and codec only", "Local ffprobe with format/protocol restrictions and a hard timeout. This does not detect players or events.", "https://ffmpeg.org/legal.html"),
        row("rfdetr", "RF-DETR detector adapter", detector_status, "1.9.2 code: Apache-2.0; checkpoint/data rights require separate review", "Optional CPU anonymous detection; no weights bundled", detail, "https://github.com/roboflow/rf-detr/tree/1.9.2"),
        row("supervision-bytetrack", "Supervision / ByteTrack", versioned_status("supervision", "0.29.1"), "MIT", "Link sampled detections into segment-local anonymous tracks", "Library availability/version only (0.29.1). No face recognition, roster identity, or automatic cross-match identity.", "https://github.com/roboflow/supervision"),
        row("roboflow-sports", "Roboflow Sports reference", "requires_model_configuration" if installed("sports") else "optional_not_installed", "MIT; examples' models have separate terms", "Reference soccer calibration and visualization components", "Not wired into this pipeline. No team assignment or pitch-calibration capability is claimed.", "https://github.com/roboflow/sports"),
        row("kloppy", "Kloppy calibrated tracking adapter", versioned_status("kloppy", "3.18.0"), "BSD-3-Clause", "Convert explicitly calibrated, roster-confirmed observations into Kloppy domain frames", "app.adapters.kloppy_export.to_kloppy requires kloppy==3.18.0. Uncalibrated image boxes are refused, not mislabeled as pitch coordinates.", "https://github.com/PySport/kloppy"),
        row("socceraction", "socceraction", "requires_model_configuration" if installed("socceraction") else "optional_not_installed", "MIT", "Future structured-event SPADL / xT / VAEP analysis", "Not wired through socceraction itself. A separate bounded native spatial Markov research engine accepts explicitly qualified event semantics and operator-activated scoped artifacts. VAEP and xG remain unavailable.", "https://github.com/ML-KULeuven/socceraction"),
        row("sn-gamestate", "SoccerNet game-state pipeline", "license_blocked", "GPL-3.0; dataset rights separate", "Research reference for full game-state reconstruction", "Not imported. GPL adoption and each dependency/dataset require a distribution and rights review; GPL is not a noncommercial license.", "https://github.com/SoccerNet/sn-gamestate"),
        row("broadcast2pitch", "Broadcast2Pitch (GSR 2025 winner)", "license_blocked", "No root code license identified; checkpoint/dependency rights unresolved", "Research reference for advanced broadcast-to-pitch reconstruction", "Not installed. CUDA plus YOLOX/OSNet/keypoint/CLIP/LLaMA checkpoints are required. Review code, model and training-data rights separately before adoption.", "https://github.com/yinmayoo185/SoccernetGSR"),
        row("footpass", "FOOTPASS", "license_blocked", "Apache LICENSE versus CC BY-NC README; gated NDA dataset", "Research player-centric action spotting", "Blocked by default until maintainers resolve the conflicting code/baseline terms and data authorization in writing.", "https://github.com/JeremieOchin/FOOTPASS"),
        row("soccermaster", "SoccerMaster", "license_blocked", "Weights card Apache-2.0; code/dependency licenses unresolved", "Research-only soccer representation pipeline", "No executable integration or downloads. A model-card label does not clear the code, embedded models, or footage rights.", "https://github.com/haolinyang-hlyang/SoccerMaster"),
        row("qwen", "Qwen explanations", "optional_not_installed", "Qwen2.5-VL-7B-Instruct card: Apache-2.0", "Possible future evidence-linked explanation layer", "Not integrated. No hosted LLM, exact-statistics generation or talent scoring is used.", "https://huggingface.co/Qwen/Qwen2.5-VL-7B-Instruct"),
    ]
