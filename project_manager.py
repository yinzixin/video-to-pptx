"""File-based project management for the cartoon-to-slides web UI.

Each project lives in its own directory under PROJECTS_ROOT with a
``project.json`` metadata file that tracks name, config, and pipeline state.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

PROJECTS_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "projects")


class PipelineStep(str, Enum):
    UPLOAD = "upload"
    TRANSCRIBE = "transcribe"
    FRAMES = "frames"
    PLAN = "plan"
    ILLUSTRATIONS = "illustrations"
    RENDER = "render"
    PPTX = "pptx"


PIPELINE_STEP_ORDER = list(PipelineStep)


class ProjectStatus(str, Enum):
    CREATED = "created"
    PROCESSING = "processing"
    COMPLETED = "completed"
    ERROR = "error"


class StepState(BaseModel):
    status: str = "pending"  # pending | running | done | error
    started_at: str | None = None
    completed_at: str | None = None
    message: str = ""


class PipelineConfig(BaseModel):
    whisper_model: str = "base"
    compute_type: str | None = None
    whisper_device: str = Field(
        default_factory=lambda: os.environ.get("WHISPER_DEVICE", "auto"),
    )
    llm_provider: str = "mimo"
    llm_model: str = "mimo-v2-flash"
    llm_api_key: str | None = None
    reasoning_effort: str = "medium"
    llm_temperature: float = 0.6
    max_slides: int = 12
    max_frames: int | None = None
    frame_strategy: str = "segment"
    interval_seconds: float = 30.0
    frame_offset: float = 0.25
    time_jitter_seconds: float = 0.75
    skip_intro_seconds: float = 5.0
    audience: str | None = None
    use_vision: bool = False
    max_vision_frames: int = 8

    @model_validator(mode="before")
    @classmethod
    def _migrate_openai_fields(cls, data: Any) -> Any:
        """Accept legacy ``openai_model`` / ``openai_temperature`` from
        existing ``project.json`` files and map them to the new names."""
        if isinstance(data, dict):
            if "openai_model" in data and "llm_model" not in data:
                data["llm_model"] = data.pop("openai_model")
            elif "openai_model" in data:
                data.pop("openai_model")
            if "openai_temperature" in data and "llm_temperature" not in data:
                data["llm_temperature"] = data.pop("openai_temperature")
            elif "openai_temperature" in data:
                data.pop("openai_temperature")
        return data


class ProjectMeta(BaseModel):
    id: str
    name: str
    created_at: str
    video_filename: str | None = None
    status: ProjectStatus = ProjectStatus.CREATED
    pipeline: dict[str, StepState] = Field(default_factory=dict)
    config: PipelineConfig = Field(default_factory=PipelineConfig)
    error_message: str | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _project_dir(project_id: str) -> str:
    return os.path.join(PROJECTS_ROOT, project_id)


def _meta_path(project_id: str) -> str:
    return os.path.join(_project_dir(project_id), "project.json")


def _save_meta(meta: ProjectMeta) -> None:
    path = _meta_path(meta.id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(meta.model_dump(), indent=2, ensure_ascii=False))
    os.replace(tmp, path)


def _load_meta(project_id: str) -> ProjectMeta:
    path = _meta_path(project_id)
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return ProjectMeta.model_validate(data)


# ---- Public API ----------------------------------------------------------


def create_project(name: str, config: dict[str, Any] | None = None) -> ProjectMeta:
    pid = uuid.uuid4().hex[:12]
    pdir = _project_dir(pid)
    os.makedirs(pdir, exist_ok=True)

    cfg = PipelineConfig(**(config or {}))
    pipeline = {step.value: StepState().model_dump() for step in PipelineStep}

    meta = ProjectMeta(
        id=pid,
        name=name,
        created_at=_now_iso(),
        config=cfg,
        pipeline={k: StepState.model_validate(v) for k, v in pipeline.items()},
    )
    _save_meta(meta)
    return meta


def list_projects() -> list[ProjectMeta]:
    os.makedirs(PROJECTS_ROOT, exist_ok=True)
    projects: list[ProjectMeta] = []
    for entry in sorted(os.listdir(PROJECTS_ROOT)):
        meta_file = os.path.join(PROJECTS_ROOT, entry, "project.json")
        if os.path.isfile(meta_file):
            try:
                projects.append(_load_meta(entry))
            except Exception:
                pass
    projects.sort(key=lambda p: p.created_at, reverse=True)
    return projects


def get_project(project_id: str) -> ProjectMeta | None:
    if not os.path.isfile(_meta_path(project_id)):
        return None
    return _load_meta(project_id)


def update_project(meta: ProjectMeta) -> None:
    _save_meta(meta)


def delete_project(project_id: str) -> bool:
    pdir = _project_dir(project_id)
    if not os.path.isdir(pdir):
        return False
    shutil.rmtree(pdir, ignore_errors=True)
    return True


def project_dir(project_id: str) -> str:
    return _project_dir(project_id)


def set_step_running(meta: ProjectMeta, step: PipelineStep, message: str = "") -> None:
    meta.pipeline[step.value] = StepState(
        status="running", started_at=_now_iso(), message=message
    )
    meta.status = ProjectStatus.PROCESSING
    _save_meta(meta)


def set_step_done(meta: ProjectMeta, step: PipelineStep, message: str = "") -> None:
    state = meta.pipeline.get(step.value, StepState())
    state.status = "done"
    state.completed_at = _now_iso()
    state.message = message
    meta.pipeline[step.value] = state
    _save_meta(meta)


def set_step_error(meta: ProjectMeta, step: PipelineStep, message: str = "") -> None:
    state = meta.pipeline.get(step.value, StepState())
    state.status = "error"
    state.completed_at = _now_iso()
    state.message = message
    meta.pipeline[step.value] = state
    meta.status = ProjectStatus.ERROR
    meta.error_message = message
    _save_meta(meta)


def set_project_completed(meta: ProjectMeta) -> None:
    meta.status = ProjectStatus.COMPLETED
    meta.error_message = None
    _save_meta(meta)


def invalidate_steps_from(meta: ProjectMeta, step: PipelineStep) -> None:
    """Reset the given step and all subsequent steps to pending."""
    idx = PIPELINE_STEP_ORDER.index(step)
    for s in PIPELINE_STEP_ORDER[idx:]:
        meta.pipeline[s.value] = StepState()
    _save_meta(meta)


def get_first_frame_path(project_id: str) -> str | None:
    """Return path to the first extracted frame, if available."""
    frames_dir = os.path.join(_project_dir(project_id), "frames")
    manifest_path = os.path.join(frames_dir, "frames_manifest.json")
    if not os.path.isfile(manifest_path):
        return None
    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
        frames = manifest.get("frames", [])
        if frames:
            return frames[0].get("path")
    except Exception:
        pass
    return None
