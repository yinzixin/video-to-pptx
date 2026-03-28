"""Call OpenAI to produce a structured slide plan from transcript and frame manifest."""

from __future__ import annotations

import json
import os
from typing import Any, Literal, overload

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Slide‑type taxonomy
# ---------------------------------------------------------------------------

VALID_SLIDE_TYPES = frozenset(
    {
        "story_intro",
        "plot_summary",
        "key_scene",
        "vocabulary",
        "key_phrases",
        "comprehension",
        "moral_lesson",
        "discussion",
    }
)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class VocabItem(BaseModel):
    word: str
    pos: str = Field("", description="Part of speech (noun, verb, adj …)")
    definition: str = ""
    example: str = Field("", description="Sentence from the episode")


class DialogueLine(BaseModel):
    speaker: str = ""
    line: str = ""


class SlideSpec(BaseModel):
    slide_type: str = Field("key_scene", description="Slide category for layout")
    title: str = Field(..., description="Slide title")
    bullets: list[str] = Field(default_factory=list, max_length=14)
    vocab_items: list[VocabItem] | None = None
    scene_dialogue: list[DialogueLine] | None = None
    teacher_notes: str | None = None
    frame_index: int = Field(
        0, ge=0, description="Index into the frames list (0‑based)"
    )

    @field_validator("slide_type", mode="before")
    @classmethod
    def _normalize_type(cls, v: Any) -> str:
        s = str(v).strip().lower() if v else "key_scene"
        return s if s in VALID_SLIDE_TYPES else "key_scene"

    @field_validator("bullets", mode="before")
    @classmethod
    def cap_bullets(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        return list(v)[:14]

    @field_validator("vocab_items", mode="before")
    @classmethod
    def _vocab_coerce(cls, v: Any) -> Any:
        return v if v else None

    @field_validator("scene_dialogue", mode="before")
    @classmethod
    def _dialogue_coerce(cls, v: Any) -> Any:
        return v if v else None


class SlidePlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    lesson_title: str
    story_summary: str = ""
    moral: str = ""
    teaching_rationale: str | None = None
    learning_objectives: list[str] = Field(default_factory=list)
    slides: list[SlideSpec]

    @field_validator("learning_objectives", mode="before")
    @classmethod
    def _objectives_none(cls, v: Any) -> Any:
        return v if v is not None else []


# ---------------------------------------------------------------------------
# System prompt — pedagogically structured slide sequence
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a senior ESL/EFL teacher and materials designer who specialises in \
teaching English through animated stories and cartoons.

You will receive:
1. A transcript of an animated episode (with timestamps).
2. Episode metadata (duration, word count, dialogue density).
3. Optionally, screenshots from the episode.

Your task: design a structured, pedagogically sound slide deck that teaches \
English through this episode. The slides MUST follow the typed sequence below.

─────────────────────────────────────────────────────
MANDATORY SLIDE SEQUENCE
─────────────────────────────────────────────────────

1. `"story_intro"` — 1 slide, REQUIRED
   Introduce the episode: main characters, setting, and premise.
   • `bullets`: character names with brief descriptions; describe the setting.
   • Pick a `frame_index` showing the opening scene or main characters.

2. `"plot_summary"` — 1‑2 slides, REQUIRED
   Retell the key events in chronological order.
   • `bullets`: numbered events — "1. First, …", "2. Then, …", "3. Finally, …"
   • Cover beginning → conflict/problem → resolution.

3. `"key_scene"` — 2‑3 slides, REQUIRED
   Zoom into important moments with actual dialogue from the transcript.
   • `bullets`: brief scene context and any teaching point.
   • `scene_dialogue`: array of {speaker, line} extracted from the transcript.
     The transcript may not label speakers — infer from context or use generic \
labels such as "Narrator", "Character 1", etc.
   • Pick `frame_index` values matching each scene's timestamp.

4. `"vocabulary"` — 1‑2 slides, REQUIRED
   Teach 6‑10 high‑value words/phrases from the episode.
   • `vocab_items`: array of {word, pos, definition, example}
     – `word`: the vocabulary item
     – `pos`: part of speech (noun, verb, adjective, adverb, phrase, idiom …)
     – `definition`: clear, learner‑friendly definition in simple English
     – `example`: the actual sentence from the transcript containing this word
   • `bullets`: may be empty or contain a short intro such as \
"Key words from this episode:".
   • Prioritise words learners will encounter again — not just rare words.

5. `"key_phrases"` — 0‑1 slide, RECOMMENDED
   Useful expressions, collocations, or functional language chunks.
   • `bullets`: each expression with a brief note on when/how to use it.

6. `"comprehension"` — 1‑2 slides, REQUIRED
   Check story understanding with factual and inferential questions.
   • `bullets`: alternate "Q: …" and "A: …" lines.
   • Include 4‑6 questions.
   • `teacher_notes`: how to run the activity (pair work, hands‑up, written …).

7. `"moral_lesson"` — 1 slide, REQUIRED
   What lesson, value, or message does the story teach?
   • `bullets`: state the moral clearly, then 1‑2 supporting points.
   • Make it relatable to learners' lives.

8. `"discussion"` — 0‑1 slide, RECOMMENDED
   Open‑ended questions connecting the story to learners' experiences.
   • `bullets`: 3‑5 discussion questions.
   • `teacher_notes`: suggested format (pair / group / class).

─────────────────────────────────────────────────────
RULES
─────────────────────────────────────────────────────
• Ground ALL content in the provided transcript — never invent dialogue or events.
• Assign each slide a `frame_index` in 0..N‑1. Spread indices across the timeline.
• Provide `teacher_notes` with practical classroom tips where useful.
• Keep definitions and explanations simple and age‑appropriate.

Return **valid JSON only**, matching the schema described in the user message.\
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _episode_context(
    transcript_payload: dict[str, Any],
    frames_manifest: dict[str, Any],
) -> dict[str, Any]:
    segs = transcript_payload.get("segments") or []
    dur = float(transcript_payload.get("duration") or 0.0)
    text = " ".join(str(s.get("text", "")) for s in segs)
    wc = len(text.split())
    n_frames = len(frames_manifest.get("frames") or [])
    spm = (len(segs) / dur * 60.0) if dur > 0 else 0.0
    density = "high" if spm > 8 else ("low" if spm < 2.5 else "moderate")
    return {
        "duration_seconds": round(dur, 2),
        "segment_count": len(segs),
        "approx_word_count": wc,
        "frame_count": n_frames,
        "segments_per_minute": round(spm, 2),
        "dialogue_density": density,
    }


def _is_gpt5_family(model: str) -> bool:
    return (model or "").lower().startswith("gpt-5")


# ---------------------------------------------------------------------------
# Build the user‑message payload (text portion)
# ---------------------------------------------------------------------------


def build_user_payload(
    transcript_payload: dict[str, Any],
    frames_manifest: dict[str, Any],
    max_slides: int,
    audience: str | None,
) -> str:
    frames = frames_manifest.get("frames", [])
    frame_summary = [
        {
            "index": i,
            "timestamp_seconds": f.get("timestamp_seconds"),
            "segment_index": f.get("segment_index"),
        }
        for i, f in enumerate(frames)
    ]
    ctx = _episode_context(transcript_payload, frames_manifest)

    parts = [
        "## Episode context",
        json.dumps(ctx, indent=2),
        "\n## Transcript (segments with start/end in seconds)",
        json.dumps(transcript_payload, ensure_ascii=False)[:120_000],
        "\n## Available frames (assign frame_index 0 to "
        + str(max(0, len(frames) - 1))
        + ")",
        json.dumps(frame_summary, indent=2),
        f"\n## Constraint: at most {max_slides} content slides in `slides`.",
    ]
    if audience:
        parts.append(f"## Target learners\n{audience}")

    parts.append(
        "## Required JSON schema\n"
        "Return a single JSON object:\n"
        "- `lesson_title` (string): engaging lesson title.\n"
        "- `story_summary` (string): 2‑4 sentence plot summary.\n"
        "- `moral` (string): the main lesson/moral of the story.\n"
        "- `teaching_rationale` (string): 2‑5 sentences on what you chose to "
        "teach and why.\n"
        "- `learning_objectives` (array of strings): 2‑6 observable objectives.\n"
        "- `slides` (array): each object has:\n"
        '  - `slide_type`: one of "story_intro", "plot_summary", "key_scene", '
        '"vocabulary", "key_phrases", "comprehension", "moral_lesson", '
        '"discussion"\n'
        "  - `title` (string)\n"
        "  - `bullets` (array of strings)\n"
        "  - `vocab_items` (array, vocabulary slides only): "
        "[{word, pos, definition, example}]\n"
        "  - `scene_dialogue` (array, key_scene slides only): "
        "[{speaker, line}]\n"
        "  - `teacher_notes` (string, optional)\n"
        "  - `frame_index` (int)\n"
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Generate slide plan via OpenAI
# ---------------------------------------------------------------------------


@overload
def generate_slide_plan(
    transcript_payload: dict[str, Any],
    frames_manifest: dict[str, Any],
    *,
    model: str = ...,
    max_slides: int = ...,
    audience: str | None = ...,
    api_key: str | None = ...,
    reasoning_effort: str | None = ...,
    temperature: float = ...,
    vision_frames: list[dict[str, Any]] | None = ...,
    return_usage: Literal[False] = ...,
) -> SlidePlan: ...


@overload
def generate_slide_plan(
    transcript_payload: dict[str, Any],
    frames_manifest: dict[str, Any],
    *,
    model: str = ...,
    max_slides: int = ...,
    audience: str | None = ...,
    api_key: str | None = ...,
    reasoning_effort: str | None = ...,
    temperature: float = ...,
    vision_frames: list[dict[str, Any]] | None = ...,
    return_usage: Literal[True],
) -> tuple[SlidePlan, dict[str, int] | None, str]: ...


def generate_slide_plan(
    transcript_payload: dict[str, Any],
    frames_manifest: dict[str, Any],
    *,
    model: str = "gpt-5.4",
    max_slides: int = 12,
    audience: str | None = None,
    api_key: str | None = None,
    reasoning_effort: str | None = "medium",
    temperature: float = 0.6,
    vision_frames: list[dict[str, Any]] | None = None,
    return_usage: bool = False,
) -> SlidePlan | tuple[SlidePlan, dict[str, int] | None, str]:
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ValueError("OPENAI_API_KEY is not set")

    client = OpenAI(api_key=key)
    user_text = build_user_payload(
        transcript_payload, frames_manifest, max_slides, audience
    )

    # Multi‑modal message when vision frames are supplied
    if vision_frames:
        user_content: Any = [{"type": "text", "text": user_text}]
        for vf in vision_frames:
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{vf['base64']}",
                        "detail": "low",
                    },
                }
            )
        user_content.append(
            {
                "type": "text",
                "text": (
                    f"\n## Visual context\nThe {len(vision_frames)} images above "
                    "are screenshots from the episode spread across the timeline. "
                    "Use them to identify characters, settings, and key visual "
                    "moments for your slide design."
                ),
            }
        )
    else:
        user_content = user_text

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    create_kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"},
    }
    if _is_gpt5_family(model):
        create_kwargs["reasoning_effort"] = reasoning_effort or "medium"
    else:
        create_kwargs["temperature"] = temperature

    try:
        resp = client.chat.completions.create(**create_kwargs)
    except TypeError:
        create_kwargs.pop("reasoning_effort", None)
        if "temperature" not in create_kwargs:
            create_kwargs["temperature"] = temperature
        resp = client.chat.completions.create(**create_kwargs)

    raw = resp.choices[0].message.content
    if not raw:
        raise RuntimeError("Empty response from OpenAI")

    data = json.loads(raw)
    plan = SlidePlan.model_validate(data)

    # Token usage
    usage_info: dict[str, int] | None = None
    u = getattr(resp, "usage", None)
    if u is not None:
        usage_info = {}
        for attr in ("prompt_tokens", "completion_tokens", "total_tokens"):
            val = getattr(u, attr, None)
            if val is not None:
                usage_info[attr] = int(val)

    # Clamp frame_index values to valid range
    n = len(frames_manifest.get("frames", []))
    if n > 0:
        fixed = [
            s.model_copy(update={"frame_index": min(max(0, s.frame_index), n - 1)})
            for s in plan.slides
        ]
        plan = plan.model_copy(update={"slides": fixed})

    if return_usage:
        return plan, usage_info, raw
    return plan
