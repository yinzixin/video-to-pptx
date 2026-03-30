"""Extract video frames at segment timestamps or fixed intervals using ffmpeg."""

from __future__ import annotations

import base64
import io
import json
import os
import random
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Literal

FrameStrategy = Literal["segment", "interval"]

_BLACK_SKIP_STEP = 1.0  # seconds to advance when a black frame is detected
_BLACK_SKIP_MAX = 5.0   # max total seconds to advance looking for non-black frame
_EDGE_MARGIN = 0.05     # keep sampled times inside segment / file bounds


def _jitter_timestamp(
    base: float,
    jitter: float,
    t_min: float,
    t_max: float,
) -> float:
    """Add uniform random jitter in ``[-jitter, jitter]`` and clamp to ``[t_min, t_max]``."""
    if jitter <= 0:
        return base
    t = base + random.uniform(-jitter, jitter)
    if t_max < t_min:
        return max(0.0, 0.5 * (t_min + t_max))
    return max(t_min, min(t_max, t))


@dataclass
class FrameEntry:
    path: str
    timestamp_seconds: float
    segment_index: int | None


def _run_ffmpeg_ss(video_path: str, t_sec: float, out_path: str) -> None:
    """Seek before decode (-ss before -i) for speed; single frame output."""
    t_sec = max(0.0, float(t_sec))
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{t_sec:.3f}",
        "-i",
        video_path,
        "-frames:v",
        "1",
        "-q:v",
        "2",
        "-y",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed at {t_sec}s: {r.stderr or r.stdout}")


def _is_black_frame(
    image_path: str,
    pixel_threshold: int = 20,
    black_ratio: float = 0.97,
) -> bool:
    """Return True if the image is predominantly black (e.g. video intro)."""
    try:
        from PIL import Image
    except ImportError:
        return False
    if not os.path.isfile(image_path):
        return False
    img = Image.open(image_path).convert("L")  # greyscale
    pixels = list(img.getdata())
    if not pixels:
        return True
    dark_count = sum(1 for p in pixels if p < pixel_threshold)
    return dark_count / len(pixels) >= black_ratio


def _extract_skip_black(
    video_path: str,
    t_sec: float,
    out_path: str,
    *,
    duration: float | None = None,
    verbose: bool = True,
) -> float:
    """Extract a frame; if it's black, advance up to *_BLACK_SKIP_MAX* seconds.

    Returns the actual timestamp used.
    """
    _run_ffmpeg_ss(video_path, t_sec, out_path)
    if not _is_black_frame(out_path):
        return t_sec

    original_t = t_sec
    advanced = 0.0
    while advanced < _BLACK_SKIP_MAX:
        advanced += _BLACK_SKIP_STEP
        candidate = original_t + advanced
        if duration is not None and candidate >= duration:
            break
        _run_ffmpeg_ss(video_path, candidate, out_path)
        if not _is_black_frame(out_path):
            if verbose:
                print(
                    f"    [skip-black] skipped black frame at {original_t:.2f}s, "
                    f"using {candidate:.2f}s instead",
                    flush=True,
                )
            return candidate

    if verbose:
        print(
            f"    [skip-black] could not find non-black frame near {original_t:.2f}s, "
            f"keeping best attempt at {original_t + advanced:.2f}s",
            flush=True,
        )
    return original_t + advanced


def _count_interval_extractions(
    duration: float,
    interval_seconds: float,
    max_frames: int | None,
) -> int:
    n = 0
    t = 0.0
    while t < duration and (max_frames is None or n < max_frames):
        n += 1
        t += interval_seconds
    return n


def extract_frames(
    video_path: str,
    output_dir: str,
    *,
    segments: list[dict[str, Any]] | None = None,
    strategy: FrameStrategy = "segment",
    interval_seconds: float = 30.0,
    time_offset: float = 0.25,
    time_jitter_seconds: float = 0.75,
    max_frames: int | None = None,
    verbose: bool = True,
) -> tuple[list[FrameEntry], str]:
    """
    Extract PNG frames and write manifest JSON next to frames.

    - segment: one frame per segment at start + time_offset (capped by max_frames).
    - interval: every interval_seconds from 0 until duration (capped by max_frames).
    - time_jitter_seconds: uniform random offset (seconds) applied per frame so
      repeated runs can capture different moments; clamped to segment/video bounds.
      Use 0 to disable.

    Returns (frame_entries, manifest_path).
    """
    os.makedirs(output_dir, exist_ok=True)
    entries: list[FrameEntry] = []

    if strategy == "segment":
        if not segments:
            manifest_path = os.path.join(output_dir, "frames_manifest.json")
            _write_manifest(manifest_path, entries, video_path)
            if verbose:
                print(
                    "[extract_frames] segment strategy: no segments; wrote empty manifest.",
                    flush=True,
                )
            return entries, manifest_path
        indices = list(range(len(segments)))
        if max_frames is not None and len(indices) > max_frames:
            step = max(1, len(indices) // max_frames)
            indices = indices[::step][:max_frames]
        total = len(indices)
        duration: float | None = None
        if time_jitter_seconds > 0:
            duration = _probe_duration(video_path)
        if verbose:
            jit_msg = (
                f", jitter ±{time_jitter_seconds}s"
                if time_jitter_seconds > 0
                else ""
            )
            print(
                f"[extract_frames] segment strategy: extracting {total} frame(s) "
                f"(offset +{time_offset}s after each segment start{jit_msg})…",
                flush=True,
            )
        for n, i in enumerate(indices):
            seg = segments[i]
            seg_start = float(seg["start"])
            seg_end = float(seg.get("end", seg_start))
            base = seg_start + time_offset
            t_lo = seg_start
            t_hi = max(seg_start, seg_end - _EDGE_MARGIN)
            if duration is not None:
                t_hi = min(t_hi, duration - _EDGE_MARGIN)
            t = _jitter_timestamp(base, time_jitter_seconds, t_lo, t_hi)
            name = f"frame_{n:05d}.png"
            out_path = os.path.join(output_dir, name)
            if verbose:
                print(
                    f"  [{n + 1}/{total}] t={t:.2f}s seg#{i} -> {name}",
                    flush=True,
                )
            if n == 0:
                t = _extract_skip_black(
                    video_path, t, out_path, verbose=verbose,
                )
            else:
                _run_ffmpeg_ss(video_path, t, out_path)
            entries.append(
                FrameEntry(
                    path=os.path.abspath(out_path),
                    timestamp_seconds=t,
                    segment_index=i,
                )
            )
    else:
        # interval — need duration
        duration = _probe_duration(video_path)
        total = _count_interval_extractions(duration, interval_seconds, max_frames)
        t_hi_global = max(0.0, duration - _EDGE_MARGIN)
        if verbose:
            jit_msg = (
                f", jitter ±{time_jitter_seconds}s"
                if time_jitter_seconds > 0
                else ""
            )
            print(
                f"[extract_frames] interval strategy: duration={duration:.1f}s, "
                f"every {interval_seconds}s{jit_msg}, ~{total} frame(s)…",
                flush=True,
            )
        t = 0.0
        n = 0
        while t < duration and (max_frames is None or n < max_frames):
            name = f"frame_{n:05d}.png"
            out_path = os.path.join(output_dir, name)
            t_use = _jitter_timestamp(t, time_jitter_seconds, 0.0, t_hi_global)
            if verbose:
                print(
                    f"  [{n + 1}/{total}] t={t_use:.2f}s -> {name}",
                    flush=True,
                )
            if n == 0:
                t_use = _extract_skip_black(
                    video_path, t_use, out_path,
                    duration=duration, verbose=verbose,
                )
            else:
                _run_ffmpeg_ss(video_path, t_use, out_path)
            entries.append(
                FrameEntry(
                    path=os.path.abspath(out_path),
                    timestamp_seconds=t_use,
                    segment_index=None,
                )
            )
            t += interval_seconds
            n += 1

    manifest_path = os.path.join(output_dir, "frames_manifest.json")
    _write_manifest(manifest_path, entries, video_path)
    return entries, manifest_path


def _probe_duration(video_path: str) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {r.stderr}")
    return float(r.stdout.strip() or 0)


def _write_manifest(
    manifest_path: str, entries: list[FrameEntry], video_path: str
) -> None:
    data = {
        "video": os.path.abspath(video_path),
        "frames": [
            {
                "path": e.path,
                "timestamp_seconds": e.timestamp_seconds,
                "segment_index": e.segment_index,
            }
            for e in entries
        ],
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_manifest(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Vision API frame preparation
# ---------------------------------------------------------------------------


def prepare_frames_for_vision(
    frames_manifest: dict[str, Any],
    *,
    max_images: int = 8,
    max_width: int = 512,
    verbose: bool = True,
) -> list[dict[str, Any]]:
    """Select representative frames, resize, and encode as base64 JPEG.

    Returns a list of dicts with keys: index, original_index,
    timestamp_seconds, base64.
    """
    try:
        from PIL import Image
    except ImportError as exc:
        raise ImportError(
            "Pillow is required for vision support. "
            "Install it with: pip install Pillow"
        ) from exc

    frames = frames_manifest.get("frames", [])
    if not frames:
        return []

    if len(frames) <= max_images:
        selected_indices = list(range(len(frames)))
    else:
        step = len(frames) / max_images
        selected_indices = [int(i * step) for i in range(max_images)]

    result: list[dict[str, Any]] = []
    for out_i, orig_i in enumerate(selected_indices):
        frame = frames[orig_i]
        path = frame.get("path", "")
        if not os.path.isfile(path):
            if verbose:
                print(
                    f"  [vision] skipping missing frame: {path}",
                    flush=True,
                )
            continue

        img = Image.open(path)
        if img.width > max_width:
            ratio = max_width / img.width
            img = img.resize(
                (max_width, int(img.height * ratio)),
                Image.LANCZOS,
            )

        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=80)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")

        result.append(
            {
                "index": out_i,
                "original_index": orig_i,
                "timestamp_seconds": frame.get("timestamp_seconds"),
                "base64": b64,
            }
        )

    if verbose:
        print(
            f"  [vision] encoded {len(result)} frame(s) as base64 JPEG "
            f"(max_width={max_width}px)",
            flush=True,
        )
    return result


def main() -> None:
    if len(sys.argv) < 3:
        print(
            "Usage: extract_frames.py <video> <out_dir> [segment|interval]",
            file=sys.stderr,
        )
        sys.exit(1)
    video = sys.argv[1]
    out_dir = sys.argv[2]
    strategy = sys.argv[3] if len(sys.argv) > 3 else "segment"
    segs = []
    if strategy == "segment":
        tx = os.path.join(os.path.dirname(video) or ".", "output", "transcript.json")
        if os.path.isfile(tx):
            with open(tx, encoding="utf-8") as f:
                segs = json.load(f).get("segments", [])
    extract_frames(
        video,
        out_dir,
        segments=segs or None,
        strategy=strategy,
    )


if __name__ == "__main__":
    main()
