#!/usr/bin/env python3
"""CLI: cartoon video -> transcript JSON -> frames -> OpenAI slide plan -> .pptx"""

from __future__ import annotations

import argparse
import glob
import json
import os
from collections import Counter
import sys
import time
from pathlib import Path
from typing import Iterable

from build_pptx import build_presentation_from_images, build_presentation_legacy
from extract_frames import (
    FrameStrategy,
    extract_frames,
    load_manifest,
    prepare_frames_for_vision,
)
from render_slides import render_slides
from slide_plan import generate_slide_plan


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build a teaching PowerPoint from cartoon video(s) "
        "(transcribe, frames, LLM, PPTX). Each input is written to "
        "OUTPUT_DIR/<stem>/<stem>.pptx with assets in OUTPUT_DIR/<stem>/."
    )
    p.add_argument(
        "--input",
        nargs="+",
        required=True,
        metavar="PATH_OR_GLOB",
        help="One or more video paths and/or glob patterns (e.g. *.mp4). "
        "Quoted globs work on Windows.",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="DIR",
        help="Base directory for each input's subfolder (default: current directory).",
    )
    p.add_argument(
        "--whisper-model",
        default="base",
        help="faster-whisper model name (default: base)",
    )
    p.add_argument(
        "--whisper-device",
        default="auto",
        help="Device for faster-whisper: auto, cuda, or cpu (default: auto)",
    )
    p.add_argument(
        "--compute-type",
        default=None,
        help="Whisper compute type (default: auto — float16 for cuda, int8 for cpu)",
    )
    p.add_argument(
        "--openai-model",
        default="gpt-4.1",
        help="OpenAI chat model for slide planning (default: gpt-4.1)",
    )
    p.add_argument(
        "--reasoning-effort",
        default="medium",
        choices=("none", "low", "medium", "high", "xhigh"),
        help="For gpt-5.* models: reasoning effort (default: medium). "
        "Ignored for other models.",
    )
    p.add_argument(
        "--openai-temperature",
        type=float,
        default=0.6,
        help="Temperature for non-gpt-5 models only (default: 0.6).",
    )
    p.add_argument(
        "--max-slides",
        type=int,
        default=12,
        help="Max content slides for the LLM (default: 12)",
    )
    p.add_argument(
        "--max-frames",
        type=int,
        default=None,
        help="Cap extracted frames (default: same as max-slides for segment mode)",
    )
    p.add_argument(
        "--frame-strategy",
        choices=("segment", "interval"),
        default="segment",
        help="segment: one frame per (sampled) segment; interval: every N seconds",
    )
    p.add_argument(
        "--interval-seconds",
        type=float,
        default=30.0,
        help="With --frame-strategy interval, seconds between frames (default: 30)",
    )
    p.add_argument(
        "--frame-offset",
        type=float,
        default=0.25,
        help="Seconds after segment start to grab frame (default: 0.25)",
    )
    p.add_argument(
        "--audience",
        default=None,
        help="Optional learner description for the LLM (e.g. 'kids aged 8-10, A2')",
    )

    # Vision API
    vision_grp = p.add_mutually_exclusive_group()
    vision_grp.add_argument(
        "--use-vision",
        dest="use_vision",
        action="store_true",
        default=False,
        help="Send key frames to OpenAI Vision API for richer story understanding "
        "(default: disabled)",
    )
    vision_grp.add_argument(
        "--no-vision",
        dest="use_vision",
        action="store_false",
        help="Disable Vision API — only transcript text is sent to the LLM "
        "(default)",
    )
    p.add_argument(
        "--max-vision-frames",
        type=int,
        default=8,
        help="Max frames to encode for Vision API (default: 8)",
    )

    # Renderer choice
    p.add_argument(
        "--legacy-renderer",
        action="store_true",
        default=False,
        help="Use the legacy python-pptx text renderer instead of the rich "
        "HTML+Playwright pipeline",
    )

    p.add_argument(
        "--skip-transcribe",
        action="store_true",
        help="Reuse existing transcript.json in each input's work folder",
    )
    p.add_argument(
        "--skip-frames",
        action="store_true",
        help="Reuse existing frames_manifest.json in each work folder under frames/",
    )
    return p.parse_args()


def expand_input_paths(tokens: Iterable[str]) -> list[str]:
    """Resolve globs and literal paths to a deduplicated ordered list of files."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in tokens:
        token = os.path.expanduser(raw)
        if glob.has_magic(token):
            matches = sorted(glob.glob(token))
            files = [m for m in matches if os.path.isfile(m)]
            if not files:
                raise ValueError(f"No files matched pattern: {raw!r}")
            for m in files:
                ap = os.path.abspath(m)
                if ap not in seen:
                    seen.add(ap)
                    out.append(ap)
        else:
            ap = os.path.abspath(token)
            if not os.path.isfile(ap):
                raise ValueError(f"Video not found: {ap}")
            if ap not in seen:
                seen.add(ap)
                out.append(ap)
    return out


def _fmt_secs(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s}s"


def run_pipeline(video: str, work: str, out_pptx: str, args: argparse.Namespace) -> int:
    from transcribe import save_transcript_json, transcribe_video

    t0 = time.perf_counter()
    os.makedirs(work, exist_ok=True)
    transcript_path = os.path.join(work, "transcript.json")
    frames_dir = os.path.join(work, "frames")
    manifest_path = os.path.join(frames_dir, "frames_manifest.json")

    renderer_name = "legacy (python-pptx)" if args.legacy_renderer else "rich (HTML+Playwright)"

    print("[cartoon_to_slides] Starting pipeline", flush=True)
    print(f"  Video:     {video}", flush=True)
    print(f"  Work dir:  {work}", flush=True)
    print(f"  Output:    {out_pptx}", flush=True)
    print(
        f"  Options:   whisper={args.whisper_model!r}, openai={args.openai_model!r}, "
        f"max_slides={args.max_slides}, frame_strategy={args.frame_strategy!r}",
        flush=True,
    )
    if args.openai_model.lower().startswith("gpt-5"):
        print(
            f"  GPT-5:     reasoning_effort={args.reasoning_effort!r}",
            flush=True,
        )
    else:
        print(
            f"  OpenAI:    temperature={args.openai_temperature}",
            flush=True,
        )
    print(
        f"  Vision:    {'enabled' if args.use_vision else 'disabled'}"
        f" (max_frames={args.max_vision_frames})",
        flush=True,
    )
    print(f"  Renderer:  {renderer_name}", flush=True)
    print(
        f"  Skip:      transcribe={args.skip_transcribe}, frames={args.skip_frames}",
        flush=True,
    )
    if args.audience:
        print(f"  Audience:  {args.audience}", flush=True)
    print(flush=True)

    # --- 1. Transcribe ---
    step = "[1/4] Transcript"
    if args.skip_transcribe:
        if not os.path.isfile(transcript_path):
            print(
                f"--skip-transcribe but missing {transcript_path}", file=sys.stderr
            )
            return 1
        print(f"{step}: loading existing file…", flush=True)
        t_step = time.perf_counter()
        with open(transcript_path, encoding="utf-8") as f:
            payload = json.load(f)
        print(
            f"{step}: done in {_fmt_secs(time.perf_counter() - t_step)} "
            f"({len(payload.get('segments', []))} segments, "
            f"duration {payload.get('duration', 0):.1f}s, "
            f"lang={payload.get('language', '?')!r})",
            flush=True,
        )
    else:
        print(f"{step}: running faster-whisper…", flush=True)
        t_step = time.perf_counter()
        payload = transcribe_video(
            video,
            whisper_model=args.whisper_model,
            device=args.whisper_device,
            compute_type=args.compute_type,
        )
        save_transcript_json(payload, transcript_path)
        print(
            f"{step}: wrote {transcript_path} in "
            f"{_fmt_secs(time.perf_counter() - t_step)} "
            f"({len(payload.get('segments', []))} segments, "
            f"duration {payload.get('duration', 0):.1f}s, "
            f"lang={payload.get('language', '?')!r})",
            flush=True,
        )

    segments = payload.get("segments", [])
    max_frames = args.max_frames
    if max_frames is None and args.frame_strategy == "segment":
        max_frames = args.max_slides

    # --- 2. Frames ---
    step = "[2/4] Frames"
    if args.skip_frames:
        if not os.path.isfile(manifest_path):
            print(f"--skip-frames but missing {manifest_path}", file=sys.stderr)
            return 1
        print(f"{step}: loading existing manifest…", flush=True)
        t_step = time.perf_counter()
        manifest = load_manifest(manifest_path)
        nfr = len(manifest.get("frames", []))
        print(
            f"{step}: done in {_fmt_secs(time.perf_counter() - t_step)} "
            f"({nfr} frames from {manifest_path})",
            flush=True,
        )
    else:
        print(
            f"{step}: extracting with ffmpeg "
            f"(strategy={args.frame_strategy!r}, max_frames={max_frames})…",
            flush=True,
        )
        t_step = time.perf_counter()
        strategy: FrameStrategy = args.frame_strategy
        _, manifest_path = extract_frames(
            video,
            frames_dir,
            segments=segments,
            strategy=strategy,
            interval_seconds=args.interval_seconds,
            time_offset=args.frame_offset,
            max_frames=max_frames,
        )
        manifest = load_manifest(manifest_path)
        nfr = len(manifest.get("frames", []))
        print(
            f"{step}: wrote {manifest_path} in "
            f"{_fmt_secs(time.perf_counter() - t_step)} ({nfr} frames)",
            flush=True,
        )

    # --- 2b. Prepare vision frames ---
    vision_frames = None
    if args.use_vision and manifest.get("frames"):
        print("[2b/4] Preparing vision frames…", flush=True)
        t_step = time.perf_counter()
        vision_frames = prepare_frames_for_vision(
            manifest,
            max_images=args.max_vision_frames,
        )
        print(
            f"[2b/4] done in {_fmt_secs(time.perf_counter() - t_step)} "
            f"({len(vision_frames)} images encoded)",
            flush=True,
        )

    # --- 3. OpenAI ---
    step = "[3/4] Slide plan (OpenAI)"
    mode_str = "vision + text" if vision_frames else "text only"
    print(f"{step}: requesting {args.openai_model!r} ({mode_str})…", flush=True)
    t_step = time.perf_counter()
    plan, usage_info, raw_response = generate_slide_plan(
        payload,
        manifest,
        model=args.openai_model,
        max_slides=args.max_slides,
        audience=args.audience,
        reasoning_effort=args.reasoning_effort,
        temperature=args.openai_temperature,
        vision_frames=vision_frames,
        return_usage=True,
    )
    elapsed_llm = time.perf_counter() - t_step
    usage_str = ""
    if usage_info:
        pt = usage_info.get("prompt_tokens")
        ct = usage_info.get("completion_tokens")
        tt = usage_info.get("total_tokens")
        if tt is not None:
            usage_str = f", tokens prompt={pt} completion={ct} total={tt}"
    obj_n = len(plan.learning_objectives)
    print(
        f"{step}: done in {_fmt_secs(elapsed_llm)}{usage_str} — "
        f"lesson_title={plan.lesson_title!r}, {len(plan.slides)} content slides, "
        f"{obj_n} objectives",
        flush=True,
    )

    raw_path = os.path.join(work, "openai_raw_response.json")
    with open(raw_path, "w", encoding="utf-8") as f:
        try:
            f.write(json.dumps(json.loads(raw_response), indent=2, ensure_ascii=False))
        except (json.JSONDecodeError, TypeError):
            f.write(raw_response)
    print(f"{step}: raw OpenAI response saved to {raw_path}", flush=True)

    plan_path = os.path.join(work, "slide_plan.json")
    with open(plan_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(plan.model_dump(), indent=2, ensure_ascii=False))
    print(f"{step}: validated slide plan saved to {plan_path}", flush=True)

    # --- 4. Build PPTX ---
    base = os.path.basename(video)

    if args.legacy_renderer:
        step = "[4/4] PowerPoint (legacy)"
        print(f"{step}: building .pptx with text renderer…", flush=True)
        t_step = time.perf_counter()
        build_presentation_legacy(plan, manifest, base, out_pptx)
        print(
            f"{step}: saved {out_pptx} in "
            f"{_fmt_secs(time.perf_counter() - t_step)}",
            flush=True,
        )
    else:
        # --- 3c+3d. Render HTML slides + Playwright screenshots ---
        step = "[3c/4] Render slides"
        print(f"{step}: rendering HTML + Playwright screenshots…", flush=True)
        t_step = time.perf_counter()
        rendered_dir = os.path.join(work, "rendered_slides")
        slide_images = render_slides(
            plan,
            manifest,
            None,
            base,
            rendered_dir,
        )
        print(
            f"{step}: done in {_fmt_secs(time.perf_counter() - t_step)} "
            f"({len(slide_images)} slides rendered)",
            flush=True,
        )

        # --- 4. Assemble PPTX from images ---
        step = "[4/4] PowerPoint (rich)"
        print(f"{step}: assembling .pptx from rendered images…", flush=True)
        t_step = time.perf_counter()
        build_presentation_from_images(slide_images, plan.lesson_title, out_pptx)
        print(
            f"{step}: saved {out_pptx} in "
            f"{_fmt_secs(time.perf_counter() - t_step)}",
            flush=True,
        )

    print(
        f"\n[cartoon_to_slides] Finished in {_fmt_secs(time.perf_counter() - t0)} total.",
        flush=True,
    )
    return 0


def main() -> int:
    args = parse_args()
    output_base = os.path.abspath(args.output if args.output is not None else ".")

    try:
        video_paths = expand_input_paths(args.input)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1

    stems = [Path(p).stem for p in video_paths]
    dup = {s for s, n in Counter(stems).items() if n > 1}
    if dup:
        print(
            "Duplicate input basename(s) under the same --output — "
            f"each folder must be unique: {sorted(dup)}",
            file=sys.stderr,
        )
        return 1

    n = len(video_paths)
    print(
        f"[cartoon_to_slides] {n} input file(s) → base directory: {output_base}",
        flush=True,
    )
    print(flush=True)

    for i, video in enumerate(video_paths):
        stem = Path(video).stem
        work = os.path.join(output_base, stem)
        out_pptx = os.path.join(work, f"{stem}.pptx")
        if n > 1:
            print(
                f"========== [{i + 1}/{n}] {video} → {work} ==========",
                flush=True,
            )
        rc = run_pipeline(video, work, out_pptx, args)
        if rc != 0:
            return rc

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
