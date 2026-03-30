import json
import os
from typing import Any

from faster_whisper import WhisperModel

INPUT_FILE = "input/1.mp4"
OUTPUT_FILE = "output/result1.txt"

_COMPUTE_TYPE_DEFAULTS = {"cuda": "float16", "cpu": "int8"}


def _resolve_device(device: str) -> str:
    """Return 'cuda' or 'cpu'. 'auto' probes for a usable GPU."""
    if device != "auto":
        return device
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        pass
    try:
        import ctranslate2
        if "cuda" in ctranslate2.get_supported_compute_types("cuda"):
            return "cuda"
    except Exception:
        pass
    return "cpu"


def transcribe_video(
    video_path: str,
    *,
    whisper_model: str = "base",
    device: str = "auto",
    compute_type: str | None = None,
    verbose: bool = True,
) -> dict[str, Any]:
    """
    Transcribe audio with faster-whisper and return structured data:
    language, duration, segments[{start, end, text}].

    device: 'auto' (default) probes for CUDA, falls back to CPU.
    compute_type: if None, picks a sensible default for the resolved device
                  (float16 for cuda, int8 for cpu).
    """
    device = _resolve_device(device)
    if compute_type is None:
        compute_type = _COMPUTE_TYPE_DEFAULTS.get(device, "int8")

    if verbose:
        print(
            f"[transcribe] Loading Whisper model {whisper_model!r} "
            f"(device={device}, compute_type={compute_type})…",
            flush=True,
        )
    model = WhisperModel(whisper_model, device=device, compute_type=compute_type)
    if verbose:
        print(
            f"[transcribe] Decoding {video_path!r} (streaming segments)…",
            flush=True,
        )
    segments_iter, info = model.transcribe(video_path)

    segments: list[dict[str, Any]] = []
    for segment in segments_iter:
        segments.append(
            {
                "start": round(float(segment.start), 3),
                "end": round(float(segment.end), 3),
                "text": segment.text.strip(),
            }
        )

    if verbose:
        print(
            f"[transcribe] Finished: {len(segments)} segment(s), "
            f"duration {float(info.duration):.1f}s, language={info.language!r}.",
            flush=True,
        )

    return {
        "language": info.language,
        "duration": float(info.duration),
        "segments": segments,
    }


def save_transcript_json(payload: dict[str, Any], path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main():
    if not os.path.exists(INPUT_FILE):
        print(f"❌ Input file not found: {INPUT_FILE}")
        return

    os.makedirs("output", exist_ok=True)

    print("🚀 Loading model...")
    print("🎧 Transcribing...\n")

    payload = transcribe_video(INPUT_FILE)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        for segment in payload["segments"]:
            line = f"[{segment['start']:.2f} - {segment['end']:.2f}] {segment['text']}"
            print(line)
            f.write(line + "\n")

    json_path = os.path.join("output", "transcript.json")
    save_transcript_json(payload, json_path)

    print(f"\n✅ Done! Output saved to {OUTPUT_FILE} and {json_path}")


if __name__ == "__main__":
    main()
