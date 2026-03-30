"""Call an LLM to produce a structured slide plan from transcript and frame manifest."""

from __future__ import annotations

import json
import os
from typing import Any, Literal, overload

from pydantic import BaseModel, ConfigDict, Field, field_validator

from llm_provider import get_llm_client, get_provider

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
    illustration_prompt: str = Field(
        "", description="(unused) Illustration prompt placeholder"
    )


class DialogueLine(BaseModel):
    speaker: str = ""
    line: str = ""


class SlideSpec(BaseModel):
    slide_type: str = Field("key_scene", description="Slide category for layout")
    title: str = Field(..., description="Slide title")
    bullets: list[str] = Field(default_factory=list, max_length=6)
    vocab_items: list[VocabItem] | None = None
    scene_dialogue: list[DialogueLine] | None = None
    teacher_notes: str | None = None
    illustration_prompt: str = Field(
        "", description="(unused) Illustration prompt placeholder"
    )
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
        return list(v)[:6]

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
# Audience profiles — drive tone, complexity, vocabulary, and slide style
# ---------------------------------------------------------------------------

_AUDIENCE_PROFILES: dict[str, dict[str, Any]] = {
    "young_children": {
        "label": "young kids (ages 6‑9)",
        "tone": "fun, energetic, and playful",
        "persona": "a fun, energetic English teacher for very young kids (ages 6‑9)",
        "visual_weight": (
            "Your slides are PICTURE‑FIRST — the cartoon screenshot or "
            "illustration is the STAR, and text is tiny and minimal. "
            "Imagine a 6‑year‑old looking at it — they should understand "
            "mostly from the PICTURES. Text is just a small helper."
        ),
        "text_rules": (
            "• Every bullet must be ONE short sentence (max 8‑10 words).\n"
            "• Max 2‑3 bullets per slide — never more!\n"
            "• Use only simple, everyday words a 6‑year‑old knows.\n"
            "• No long explanations. If you can say it in 5 words, do NOT use 10."
        ),
        "vocab_guidance": (
            "Teach 4‑6 words (not more!). Keep it visual.\n"
            "  • `vocab_items`: [{word, pos, definition, example, illustration_prompt}]\n"
            "    – `definition`: 4‑6 words max, like talking to a 6‑year‑old\n"
            "    – `example`: short quote from the cartoon\n"
            "  • Pick easy, common words kids will use again."
        ),
        "comprehension_style": (
            "Quick quiz! 3‑4 easy questions.\n"
            "  • `bullets`: \"Q: …\" then \"A: …\"\n"
            "  • Questions should be simple yes/no or one‑word answers.\n"
            "  • `teacher_notes`: how to play (point, shout out, thumbs up …)."
        ),
        "discussion_style": (
            "2‑3 \"What about you?\" questions.\n"
            "  • `bullets`: start with \"Do you …\", \"What would you …\"\n"
            "  • `teacher_notes`: pair / group / class."
        ),
        "title_style": "short, fun, with action words! (max 5‑6 words)",
        "bullet_intro_example": '"Bluey — a playful blue puppy!"',
        "plot_sentence_len": "6‑8 words max",
        "key_phrases_note": "2‑4 fun phrases kids can say!",
    },
    "children": {
        "label": "children (ages 10‑12)",
        "tone": "friendly, encouraging, and lively",
        "persona": "a friendly, encouraging English teacher for children (ages 10‑12)",
        "visual_weight": (
            "Your slides are visually rich — screenshots and illustrations "
            "are central, supported by clear, concise text. A 10‑year‑old "
            "should easily follow the slides with the pictures as the main "
            "anchor and text adding useful detail."
        ),
        "text_rules": (
            "• Every bullet should be ONE clear sentence (max 12‑15 words).\n"
            "• Max 3‑4 bullets per slide.\n"
            "• Use age‑appropriate vocabulary — straightforward but not "
            "baby‑talk.\n"
            "• Be concise. Favor shorter sentences over wordy ones."
        ),
        "vocab_guidance": (
            "Teach 5‑8 words. Include context.\n"
            "  • `vocab_items`: [{word, pos, definition, example, illustration_prompt}]\n"
            "    – `definition`: a clear 1‑sentence explanation a 10‑year‑old gets\n"
            "    – `example`: a sentence from the episode showing the word in use\n"
            "  • Choose words that expand everyday vocabulary, including some "
            "mildly challenging ones."
        ),
        "comprehension_style": (
            "Quiz time! 3‑5 questions mixing recall and simple inference.\n"
            "  • `bullets`: \"Q: …\" then \"A: …\"\n"
            "  • Include a mix: some factual, some \"why do you think …?\"\n"
            "  • `teacher_notes`: how to run the activity (pairs, hands‑up, "
            "written answers …)."
        ),
        "discussion_style": (
            "2‑4 open‑ended questions connecting the episode to real life.\n"
            "  • `bullets`: \"Have you ever …\", \"Why do you think …\", "
            "\"What would you do if …\"\n"
            "  • `teacher_notes`: suggest pair work or small‑group discussion."
        ),
        "title_style": "catchy and descriptive (max 6‑8 words)",
        "bullet_intro_example": '"Bluey — a curious and energetic blue heeler puppy"',
        "plot_sentence_len": "8‑12 words max",
        "key_phrases_note": "3‑5 useful phrases learners can practice!",
    },
    "teenagers": {
        "label": "teenagers (ages 13‑17)",
        "tone": "engaging, slightly informal, and relatable",
        "persona": (
            "an engaging English teacher for teenagers (ages 13‑17) who makes "
            "lessons feel relevant and interesting, not childish"
        ),
        "visual_weight": (
            "Visuals support analysis — use screenshots to anchor discussion "
            "of scenes, character choices, and themes. Text can carry more "
            "weight here; balance visuals with substantive captions and notes."
        ),
        "text_rules": (
            "• Bullets can be full sentences (max 15‑20 words).\n"
            "• Up to 4‑5 bullets per slide when needed.\n"
            "• Use natural, age‑appropriate language — avoid sounding like a "
            "children's show.\n"
            "• Be direct and informative, but keep energy."
        ),
        "vocab_guidance": (
            "Teach 6‑10 words, including idiomatic and nuanced vocabulary.\n"
            "  • `vocab_items`: [{word, pos, definition, example, illustration_prompt}]\n"
            "    – `definition`: a precise 1‑2 sentence definition\n"
            "    – `example`: quote from the episode with context\n"
            "  • Include idioms, phrasal verbs, or register‑specific language "
            "where relevant."
        ),
        "comprehension_style": (
            "4‑6 questions blending factual recall and critical thinking.\n"
            "  • `bullets`: \"Q: …\" then \"A: …\"\n"
            "  • Include inference, opinion, and analysis questions.\n"
            "  • `teacher_notes`: individual reflection, pair debate, or "
            "class poll."
        ),
        "discussion_style": (
            "3‑4 thought‑provoking questions that connect themes to their lives.\n"
            "  • `bullets`: \"Do you agree that …\", \"How would you handle …\", "
            "\"What does this say about …\"\n"
            "  • `teacher_notes`: debate format, think‑pair‑share, or journaling."
        ),
        "title_style": "clear and engaging (max 8 words)",
        "bullet_intro_example": '"Bluey — an energetic blue heeler navigating everyday adventures"',
        "plot_sentence_len": "10‑15 words max",
        "key_phrases_note": "3‑6 expressions worth learning, including informal/idiomatic ones.",
    },
    "adults": {
        "label": "adult learners",
        "tone": "professional, warm, and respectful",
        "persona": (
            "a professional ESL/EFL instructor for adult learners who uses "
            "media content as authentic input for language development"
        ),
        "visual_weight": (
            "Visuals serve as authentic input — use screenshots to contextualize "
            "language, pragmatics, and cultural references. Text is the primary "
            "content carrier; visuals illustrate and anchor discussion."
        ),
        "text_rules": (
            "• Bullets can be detailed sentences (max 20‑25 words).\n"
            "• Up to 5‑6 bullets per slide.\n"
            "• Use standard adult‑level language; no oversimplification.\n"
            "• Prioritize clarity and usefulness."
        ),
        "vocab_guidance": (
            "Teach 6‑12 vocabulary items including collocations, phrasal verbs, "
            "and register variations.\n"
            "  • `vocab_items`: [{word, pos, definition, example, illustration_prompt}]\n"
            "    – `definition`: precise definition with usage notes when helpful\n"
            "    – `example`: quote from the episode with pragmatic context\n"
            "  • Highlight register (formal vs. informal), connotation, and "
            "common collocations."
        ),
        "comprehension_style": (
            "4‑6 questions covering literal comprehension, inference, and pragmatics.\n"
            "  • `bullets`: \"Q: …\" then \"A: …\"\n"
            "  • Include questions about speaker intent, tone, cultural context.\n"
            "  • `teacher_notes`: suggest individual, pair, or group formats."
        ),
        "discussion_style": (
            "3‑5 discussion prompts exploring themes, cultural differences, and "
            "personal connections.\n"
            "  • `bullets`: analytical or reflective prompts like "
            "\"Compare this situation to …\", \"What cultural assumptions …\"\n"
            "  • `teacher_notes`: structured discussion, role‑play, or essay prompt."
        ),
        "title_style": "clear and informative (max 10 words)",
        "bullet_intro_example": '"Bluey — a six‑year‑old Blue Heeler whose everyday adventures explore family dynamics"',
        "plot_sentence_len": "12‑20 words",
        "key_phrases_note": (
            "4‑8 expressions with pragmatic and register notes — when and how "
            "native speakers use them."
        ),
    },
}

_DEFAULT_PROFILE = "young_children"


def _resolve_audience_profile(audience: str | None) -> dict[str, Any]:
    """Map an audience string to a profile dict.

    Accepts exact keys (``"young_children"``, ``"teenagers"`` …) or common
    natural‑language variants (``"kids 6-9"``, ``"teen"``, ``"adult"`` …).
    Unrecognised values are treated as free‑form descriptions and merged into
    the closest matching profile with the raw string appended as extra context.
    """
    if not audience:
        return _AUDIENCE_PROFILES[_DEFAULT_PROFILE]

    key = audience.strip().lower().replace("-", "_").replace(" ", "_")

    if key in _AUDIENCE_PROFILES:
        return _AUDIENCE_PROFILES[key]

    alias_map: dict[str, str] = {
        "young_kids": "young_children",
        "little_kids": "young_children",
        "preschool": "young_children",
        "kindergarten": "young_children",
        "6_9": "young_children",
        "kids": "children",
        "10_12": "children",
        "pre_teens": "children",
        "preteens": "children",
        "tweens": "children",
        "teens": "teenagers",
        "teen": "teenagers",
        "13_17": "teenagers",
        "high_school": "teenagers",
        "middle_school": "teenagers",
        "adult": "adults",
        "grown_ups": "adults",
        "university": "adults",
        "college": "adults",
        "professional": "adults",
        "business": "adults",
    }
    if key in alias_map:
        return _AUDIENCE_PROFILES[alias_map[key]]

    for alias, profile_key in alias_map.items():
        if alias in key or key in alias:
            return _AUDIENCE_PROFILES[profile_key]

    profile = _AUDIENCE_PROFILES[_DEFAULT_PROFILE].copy()
    profile["_custom_note"] = audience.strip()
    return profile


# ---------------------------------------------------------------------------
# System prompt builder — adapts pedagogy to the audience
# ---------------------------------------------------------------------------


def build_system_prompt(audience: str | None = None) -> str:
    p = _resolve_audience_profile(audience)

    custom_note = ""
    if "_custom_note" in p:
        custom_note = (
            f"\n\nADDITIONAL AUDIENCE CONTEXT (from the teacher):\n"
            f"{p['_custom_note']}\n"
            "Adapt your language, examples, and complexity accordingly."
        )

    return f"""\
You are {p['persona']}. \
You teach English using video content (cartoons, shows, clips). \
{p['visual_weight']}

You will receive:
1. A transcript of an episode (with timestamps).
2. Episode info (length, word count, how much talking).
3. Maybe some screenshots from the episode.

Your job: create an effective, visually grounded slide deck for \
{p['label']}. Your tone should be {p['tone']}.

CRITICAL RULES FOR TEXT:
{p['text_rules']}

The slides MUST follow this order:

─────────────────────────────────────────────────────
SLIDE ORDER
─────────────────────────────────────────────────────

1. `"story_intro"` — 1 slide, MUST HAVE
   WHO is in the story? WHERE does it happen?
   • `bullets`: max 2‑3 bullets introducing characters and setting.
     Example: {p['bullet_intro_example']}
   • Pick `frame_index` with the main characters visible.

2. `"plot_summary"` — 1 slide, MUST HAVE
   What happens? Summarize in 3‑4 steps.
   • `bullets`: "1. First, …", "2. Then, …", "3. In the end, …"
   • Each step = ONE sentence ({p['plot_sentence_len']}).

3. `"vocabulary"` — 1 slide, MUST HAVE
   {p['vocab_guidance']}

4. `"key_scene"` — 1+ slides, MUST HAVE
   A pivotal moment — strong visual, relevant dialogue.
   • `bullets`: a sentence describing what happens.
   • `scene_dialogue`: max 3‑4 lines of [{{speaker, line}}].
   • Pick `frame_index` of the most impactful frame.
   • See LONG TRANSCRIPT RULE below for how many key_scene slides to use.

5. `"key_phrases"` — 0‑1 slide, NICE TO HAVE
   {p['key_phrases_note']}
   • `bullets`: the phrase + brief usage note.

6. `"comprehension"` — 1 slide, MUST HAVE
   {p['comprehension_style']}

7. `"moral_lesson"` — 1 slide, MUST HAVE
   The central message or takeaway.
   • `bullets`: the lesson in ONE clear sentence + 1 supporting line.

8. `"discussion"` — 0‑1 slide, NICE TO HAVE
   {p['discussion_style']}

─────────────────────────────────────────────────────
LONG TRANSCRIPT RULE
─────────────────────────────────────────────────────

When the transcript is LONG (high word count, many segments, or high \
dialogue density), follow this rule strictly:

• Every NON‑key_scene slide type gets AT MOST 1 slide.
  That means: 1 story_intro, 1 plot_summary, 1 vocabulary, 0‑1 key_phrases,
  1 comprehension, 1 moral_lesson, 0‑1 discussion.
• ALL remaining slide slots (up to the max_slides limit) should be \
  filled with additional `"key_scene"` slides, each covering a different \
  important moment from the episode.
• Spread key_scene slides across the timeline — pick varied frame indices \
  and different dialogue excerpts so the whole story is covered visually.

─────────────────────────────────────────────────────
RULES
─────────────────────────────────────────────────────
• ONLY use words and events from the transcript — never invent content.
• Every slide gets a `frame_index` in 0..N‑1. Spread them out.
• `teacher_notes`: practical tips for the teacher.
• Visuals first, text second. Less is more!
• Titles: {p['title_style']}
{custom_note}
Return **valid JSON only**, matching the schema in the user message.\
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


def _supports_reasoning_effort(provider_name: str) -> bool:
    try:
        return get_provider(provider_name).supports_reasoning_effort
    except ValueError:
        return False


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
        parts.append(
            f"## Target learners\n{audience}\n"
            "The system prompt has already been adapted for this audience — "
            "ensure all content (vocabulary difficulty, sentence complexity, "
            "question depth, tone) is appropriate for them."
        )

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
        "[{word, pos, definition, example, illustration_prompt}]\n"
        "  - `scene_dialogue` (array, key_scene slides only): "
        "[{speaker, line}]\n"
        "  - `teacher_notes` (string, optional)\n"
        "  - `frame_index` (int)\n"
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Generate slide plan via LLM
# ---------------------------------------------------------------------------


@overload
def generate_slide_plan(
    transcript_payload: dict[str, Any],
    frames_manifest: dict[str, Any],
    *,
    provider: str = ...,
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
    provider: str = ...,
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
    provider: str = "openai",
    model: str = "gpt-4.1",
    max_slides: int = 12,
    audience: str | None = None,
    api_key: str | None = None,
    reasoning_effort: str | None = "medium",
    temperature: float = 0.6,
    vision_frames: list[dict[str, Any]] | None = None,
    return_usage: bool = False,
) -> SlidePlan | tuple[SlidePlan, dict[str, int] | None, str]:
    client = get_llm_client(provider, api_key=api_key)
    provider_info = get_provider(provider)

    user_text = build_user_payload(
        transcript_payload, frames_manifest, max_slides, audience
    )

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
        {"role": "system", "content": build_system_prompt(audience)},
        {"role": "user", "content": user_content},
    ]

    create_kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "response_format": {"type": "json_object"},
    }
    if provider_info.supports_reasoning_effort and _supports_reasoning_effort(provider):
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
        raise RuntimeError(f"Empty response from {provider_info.display_name}")

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

    plan = split_large_vocabulary_slides(plan)

    if return_usage:
        return plan, usage_info, raw
    return plan


# ---------------------------------------------------------------------------
# Post-processing: split oversized vocabulary slides
# ---------------------------------------------------------------------------

_MAX_VOCAB_PER_SLIDE = 8


def split_large_vocabulary_slides(plan: SlidePlan) -> SlidePlan:
    """Split any vocabulary slide with >_MAX_VOCAB_PER_SLIDE items into
    multiple consecutive slides, each holding at most _MAX_VOCAB_PER_SLIDE words.
    """
    new_slides: list[SlideSpec] = []
    changed = False

    for spec in plan.slides:
        items = spec.vocab_items or []
        if spec.slide_type != "vocabulary" or len(items) <= _MAX_VOCAB_PER_SLIDE:
            new_slides.append(spec)
            continue

        changed = True
        chunks = [
            items[i : i + _MAX_VOCAB_PER_SLIDE]
            for i in range(0, len(items), _MAX_VOCAB_PER_SLIDE)
        ]
        total_parts = len(chunks)
        for part_idx, chunk in enumerate(chunks, 1):
            title = (
                f"{spec.title} ({part_idx}/{total_parts})"
                if total_parts > 1
                else spec.title
            )
            new_slides.append(
                spec.model_copy(
                    update={"title": title, "vocab_items": chunk}
                )
            )

    if not changed:
        return plan
    return plan.model_copy(update={"slides": new_slides})
