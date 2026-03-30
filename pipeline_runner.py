"""Re-entrant pipeline runner with progress callbacks.

Wraps the existing CLI modules so that each step can be invoked
independently, with a ``start_from`` parameter to skip earlier steps
and reuse artifacts already on disk.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Callable

from project_manager import (
    PipelineStep,
    ProjectMeta,
    project_dir,
    set_project_completed,
    set_step_done,
    set_step_error,
    set_step_running,
)

ProgressCallback = Callable[[PipelineStep, str, str], None]
"""(step, status, message) — ``status`` is one of running / done / error."""


def _fmt(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s}s"


# ---- individual steps ----------------------------------------------------


def _step_transcribe(
    meta: ProjectMeta,
    work: str,
    video: str,
    cb: ProgressCallback,
) -> dict[str, Any]:
    from transcribe import save_transcript_json, transcribe_video

    transcript_path = os.path.join(work, "transcript.json")

    cb(PipelineStep.TRANSCRIBE, "running", "Running faster-whisper…")
    set_step_running(meta, PipelineStep.TRANSCRIBE, "Running faster-whisper…")
    t0 = time.perf_counter()

    payload = transcribe_video(
        video,
        whisper_model=meta.config.whisper_model,
        device=meta.config.whisper_device,
        compute_type=meta.config.compute_type,
    )
    save_transcript_json(payload, transcript_path)

    n_seg = len(payload.get("segments", []))
    dur = payload.get("duration", 0)
    msg = f"{n_seg} segments, {dur:.1f}s duration in {_fmt(time.perf_counter() - t0)}"
    set_step_done(meta, PipelineStep.TRANSCRIBE, msg)
    cb(PipelineStep.TRANSCRIBE, "done", msg)
    return payload


def _step_frames(
    meta: ProjectMeta,
    work: str,
    video: str,
    segments: list[dict[str, Any]],
    cb: ProgressCallback,
) -> dict[str, Any]:
    from extract_frames import FrameStrategy, extract_frames, load_manifest

    frames_dir = os.path.join(work, "frames")
    cfg = meta.config

    max_frames = cfg.max_frames
    if max_frames is None and cfg.frame_strategy == "segment":
        max_frames = cfg.max_slides

    cb(PipelineStep.FRAMES, "running", "Extracting frames with ffmpeg…")
    set_step_running(meta, PipelineStep.FRAMES, "Extracting frames…")
    t0 = time.perf_counter()

    strategy: FrameStrategy = cfg.frame_strategy  # type: ignore[assignment]
    _, manifest_path = extract_frames(
        video,
        frames_dir,
        segments=segments,
        strategy=strategy,
        interval_seconds=cfg.interval_seconds,
        time_offset=cfg.frame_offset,
        max_frames=max_frames,
    )
    manifest = load_manifest(manifest_path)
    nfr = len(manifest.get("frames", []))
    msg = f"{nfr} frames in {_fmt(time.perf_counter() - t0)}"
    set_step_done(meta, PipelineStep.FRAMES, msg)
    cb(PipelineStep.FRAMES, "done", msg)
    return manifest


def _step_plan(
    meta: ProjectMeta,
    work: str,
    transcript: dict[str, Any],
    manifest: dict[str, Any],
    cb: ProgressCallback,
) -> Any:
    from extract_frames import prepare_frames_for_vision
    from slide_plan import SlidePlan, generate_slide_plan

    cfg = meta.config
    cb(PipelineStep.PLAN, "running", f"Requesting {cfg.openai_model}…")
    set_step_running(meta, PipelineStep.PLAN, f"Calling {cfg.openai_model}…")
    t0 = time.perf_counter()

    vision_frames = None
    if cfg.use_vision and manifest.get("frames"):
        vision_frames = prepare_frames_for_vision(
            manifest, max_images=cfg.max_vision_frames
        )

    plan, usage_info, raw_response = generate_slide_plan(
        transcript,
        manifest,
        model=cfg.openai_model,
        max_slides=cfg.max_slides,
        audience=cfg.audience,
        reasoning_effort=cfg.reasoning_effort,
        temperature=cfg.openai_temperature,
        vision_frames=vision_frames,
        return_usage=True,
    )

    raw_path = os.path.join(work, "openai_raw_response.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        try:
            f.write(json.dumps(json.loads(raw_response), indent=2, ensure_ascii=False))
        except (json.JSONDecodeError, TypeError):
            f.write(raw_response)

    plan_path = os.path.join(work, "slide_plan.json")
    with open(plan_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(plan.model_dump(), indent=2, ensure_ascii=False))

    msg = (
        f"{len(plan.slides)} slides, title={plan.lesson_title!r} "
        f"in {_fmt(time.perf_counter() - t0)}"
    )
    set_step_done(meta, PipelineStep.PLAN, msg)
    cb(PipelineStep.PLAN, "done", msg)
    return plan


def _step_illustrations(
    meta: ProjectMeta,
    work: str,
    plan: Any,
    cb: ProgressCallback,
) -> None:
    set_step_done(meta, PipelineStep.ILLUSTRATIONS, "Skipped (illustrations removed)")
    cb(PipelineStep.ILLUSTRATIONS, "done", "Skipped")


def _step_render(
    meta: ProjectMeta,
    work: str,
    plan: Any,
    manifest: dict[str, Any],
    video_basename: str,
    cb: ProgressCallback,
) -> list[str]:
    from render_slides import render_slides

    cb(PipelineStep.RENDER, "running", "Rendering HTML + Playwright screenshots…")
    set_step_running(meta, PipelineStep.RENDER, "Rendering slides…")
    t0 = time.perf_counter()

    rendered_dir = os.path.join(work, "rendered_slides")
    slide_images = render_slides(
        plan, manifest, None, video_basename, rendered_dir
    )

    msg = f"{len(slide_images)} slides in {_fmt(time.perf_counter() - t0)}"
    set_step_done(meta, PipelineStep.RENDER, msg)
    cb(PipelineStep.RENDER, "done", msg)
    return slide_images


def _step_pptx(
    meta: ProjectMeta,
    work: str,
    slide_images: list[str],
    lesson_title: str,
    cb: ProgressCallback,
) -> str:
    from build_pptx import build_presentation_from_images

    out_path = os.path.join(work, "output.pptx")
    cb(PipelineStep.PPTX, "running", "Assembling PPTX…")
    set_step_running(meta, PipelineStep.PPTX, "Assembling PPTX…")
    t0 = time.perf_counter()

    build_presentation_from_images(slide_images, lesson_title, out_path)

    msg = f"Saved in {_fmt(time.perf_counter() - t0)}"
    set_step_done(meta, PipelineStep.PPTX, msg)
    cb(PipelineStep.PPTX, "done", msg)
    return out_path


# ---- orchestrator --------------------------------------------------------


def run_pipeline(
    meta: ProjectMeta,
    *,
    start_from: PipelineStep = PipelineStep.TRANSCRIBE,
    progress: ProgressCallback | None = None,
) -> None:
    """Run the pipeline for *meta*, starting from *start_from*.

    Earlier steps must have completed artifacts on disk when skipped.
    Calls *progress(step, status, message)* for SSE streaming.
    """

    def cb(step: PipelineStep, status: str, message: str) -> None:
        if progress:
            progress(step, status, message)

    work = project_dir(meta.id)
    video = os.path.join(work, "video.mp4")
    if not os.path.isfile(video):
        set_step_error(meta, PipelineStep.UPLOAD, "No video file found")
        cb(PipelineStep.UPLOAD, "error", "No video file found")
        return

    video_basename = meta.video_filename or "video.mp4"
    from extract_frames import load_manifest
    from slide_plan import SlidePlan

    steps = list(PipelineStep)
    start_idx = steps.index(start_from)

    # --- Load existing artifacts for skipped steps ---
    transcript: dict[str, Any] | None = None
    manifest: dict[str, Any] | None = None
    plan: SlidePlan | None = None
    slide_images: list[str] | None = None

    transcript_path = os.path.join(work, "transcript.json")
    manifest_path = os.path.join(work, "frames", "frames_manifest.json")
    plan_path = os.path.join(work, "slide_plan.json")

    try:
        # Transcribe
        if start_idx <= steps.index(PipelineStep.TRANSCRIBE):
            transcript = _step_transcribe(meta, work, video, cb)
        else:
            with open(transcript_path, encoding="utf-8") as f:
                transcript = json.load(f)

        # Frames
        if start_idx <= steps.index(PipelineStep.FRAMES):
            segments = transcript.get("segments", [])
            manifest = _step_frames(meta, work, video, segments, cb)
        else:
            manifest = load_manifest(manifest_path)

        # Plan
        if start_idx <= steps.index(PipelineStep.PLAN):
            plan = _step_plan(meta, work, transcript, manifest, cb)
        else:
            with open(plan_path, encoding="utf-8") as f:
                plan = SlidePlan.model_validate(json.load(f))

        # Illustrations
        if start_idx <= steps.index(PipelineStep.ILLUSTRATIONS):
            _step_illustrations(meta, work, plan, cb)
        else:
            set_step_done(meta, PipelineStep.ILLUSTRATIONS, "Reusing existing")
            cb(PipelineStep.ILLUSTRATIONS, "done", "Reusing existing")

        # Render
        if start_idx <= steps.index(PipelineStep.RENDER):
            slide_images = _step_render(
                meta, work, plan, manifest, video_basename, cb
            )
        else:
            rendered_dir = os.path.join(work, "rendered_slides")
            slide_images = sorted(
                os.path.join(rendered_dir, f)
                for f in os.listdir(rendered_dir)
                if f.endswith(".png")
            )

        # PPTX
        if start_idx <= steps.index(PipelineStep.PPTX):
            _step_pptx(meta, work, slide_images, plan.lesson_title, cb)

        set_project_completed(meta)
        cb(PipelineStep.PPTX, "done", "Pipeline complete")

    except Exception as exc:
        current_step = PipelineStep.TRANSCRIBE
        for s in reversed(steps):
            state = meta.pipeline.get(s.value)
            if state and state.status == "running":
                current_step = s
                break
        set_step_error(meta, current_step, str(exc))
        cb(current_step, "error", str(exc))
        raise


def rerender_from_html(meta: ProjectMeta, progress: ProgressCallback | None = None) -> None:
    """Re-screenshot existing HTML debug files and rebuild the PPTX.

    Used after the user edits slide HTML directly.
    """
    from render_slides import _screenshot_slides
    from slide_plan import SlidePlan

    def cb(step: PipelineStep, status: str, message: str) -> None:
        if progress:
            progress(step, status, message)

    work = project_dir(meta.id)
    html_dir = os.path.join(work, "rendered_slides", "html_debug")
    rendered_dir = os.path.join(work, "rendered_slides")
    plan_path = os.path.join(work, "slide_plan.json")

    html_files = sorted(
        f for f in os.listdir(html_dir) if f.endswith(".html")
    )
    html_slides: list[str] = []
    for hf in html_files:
        with open(os.path.join(html_dir, hf), encoding="utf-8") as f:
            html_slides.append(f.read())

    cb(PipelineStep.RENDER, "running", "Re-screenshotting HTML slides…")
    set_step_running(meta, PipelineStep.RENDER, "Re-rendering from HTML")

    slide_images = asyncio.run(_screenshot_slides(html_slides, rendered_dir))

    msg = f"{len(slide_images)} slides re-rendered"
    set_step_done(meta, PipelineStep.RENDER, msg)
    cb(PipelineStep.RENDER, "done", msg)

    with open(plan_path, encoding="utf-8") as f:
        plan = SlidePlan.model_validate(json.load(f))

    _step_pptx(meta, work, slide_images, plan.lesson_title, cb)
    set_project_completed(meta)
