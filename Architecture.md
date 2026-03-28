# Architecture

Cartoon video → English teaching PPTX slide generator.  
Two entry points: **Web UI** (FastAPI) and **CLI** (argparse). Both drive the same 6-step pipeline.

---

## Project tree

```
.
├── app.py                          # FastAPI web server (SSE progress, project CRUD, asset API)
├── pipeline_runner.py              # Re-entrant pipeline orchestrator (start_from any step)
├── project_manager.py              # File-based project state (projects/<id>/project.json)
├── cartoon_to_slides.py            # CLI entry point wrapping the same pipeline
│
├── transcribe.py                   # Step 1 — faster-whisper audio → transcript.json
├── extract_frames.py               # Step 2 — FFmpeg frame extraction + Vision API prep
├── slide_plan.py                   # Step 3 — OpenAI GPT structured output → SlidePlan (Pydantic)
├── generate_illustrations.py       # Step 4 — DALL-E image generation (SHA-1 dedup + cache)
├── render_slides.py                # Step 5 — Jinja2 HTML templates + Playwright → 1920×1080 PNGs
├── build_pptx.py                   # Step 6 — python-pptx assembles PNGs into .pptx
│
├── templates/                      # Jinja2 slide templates (rendered to HTML → screenshot)
│   ├── base.html                   #   Shared layout, Google Fonts, CSS (1920×1080 canvas)
│   ├── title.html                  #   Lesson title slide
│   ├── overview.html               #   Story summary, moral, objectives
│   ├── story_intro.html            #   Characters & setting
│   ├── plot_summary.html           #   Chronological story steps
│   ├── key_scene.html              #   Highlight moment + dialogue bubbles
│   ├── vocabulary.html             #   Word cards in 2×N grid (DALL-E per-word illustrations)
│   ├── key_phrases.html            #   Fun phrases as colorful boxes
│   ├── comprehension.html          #   Q&A quiz pairs
│   ├── moral_lesson.html           #   Big takeaway card (DALL-E slide illustration)
│   └── discussion.html             #   "What about you?" prompts
│
├── web_templates/                  # Jinja2 pages for the browser-based Web UI
│   ├── layout.html                 #   Shared HTML shell (nav, CSS, JS)
│   ├── projects.html               #   Project dashboard (list / create / delete)
│   ├── project_detail.html         #   Single project: status, run pipeline, preview
│   ├── edit_slide_plan.html        #   JSON editor for slide_plan.json
│   ├── edit_html.html              #   Edit rendered HTML slides + re-screenshot
│   └── manage_frames.html          #   Browse / replace extracted frames
│
├── static/
│   ├── style.css                   # Web UI stylesheet
│   └── app.js                     # Web UI client-side JS (SSE, fetch, DOM)
│
├── test_overflow.py                # Smoke test: key_scene overflow + vocabulary rendering
│
├── requirements.txt                # Python dependencies (pinned lower bounds)
├── Dockerfile                      # Python 3.10 + FFmpeg + Playwright Chromium
├── docker-compose.yaml             # Service definition (web UI on :8000, CLI via run)
├── .gitignore
└── README.md
```

---

## Data flow

```
  video.mp4
      │
      ▼
 ┌────────────┐    transcript.json
 │ transcribe │──────────────────────────────────────┐
 └────────────┘                                      │
      │                                              │
      ▼                                              ▼
 ┌────────────────┐   frames/*.png            ┌─────────────┐  slide_plan.json
 │ extract_frames │─── frames_manifest.json──►│  slide_plan  │───────────────────┐
 └────────────────┘                           └─────────────┘                    │
                                                    │                            │
                                                    ▼                            ▼
                                          ┌───────────────────────┐   ┌──────────────┐
                                          │ generate_illustrations │   │ render_slides │
                                          │   (DALL-E, optional)   │──►│ (Jinja2 +    │
                                          └───────────────────────┘   │  Playwright)  │
                                                                      └──────────────┘
                                                                             │
                                                                    rendered_slides/*.png
                                                                             │
                                                                             ▼
                                                                      ┌────────────┐
                                                                      │ build_pptx │
                                                                      └────────────┘
                                                                             │
                                                                        output.pptx
```

---

## Pipeline steps

| # | Step | Module | Input | Output |
|---|------|--------|-------|--------|
| 1 | Transcribe | [`transcribe.py`](transcribe.py) | `video.mp4` | `transcript.json` |
| 2 | Extract frames | [`extract_frames.py`](extract_frames.py) | `video.mp4` + segments | `frames/*.png`, `frames_manifest.json` |
| 3 | Slide plan | [`slide_plan.py`](slide_plan.py) | transcript + manifest (+ vision frames) | `slide_plan.json` |
| 4 | Illustrations | [`generate_illustrations.py`](generate_illustrations.py) | `SlidePlan` | `illustrations/*.png` |
| 5 | Render | [`render_slides.py`](render_slides.py) | plan + frames + illustrations | `rendered_slides/*.png`, `html_debug/*.html` |
| 6 | Assemble | [`build_pptx.py`](build_pptx.py) | rendered PNGs | `output.pptx` |

---

## Key modules

### Entry points

| File | Role |
|------|------|
| [`app.py`](app.py) | FastAPI web server — project CRUD, video upload, pipeline run/resume, SSE progress, asset serving, slide plan & HTML editors |
| [`cartoon_to_slides.py`](cartoon_to_slides.py) | CLI entry point — same pipeline driven by argparse |

### Orchestration

| File | Role |
|------|------|
| [`pipeline_runner.py`](pipeline_runner.py) | Re-entrant orchestrator — runs steps sequentially, supports `start_from` to resume, calls `project_manager` for state |
| [`project_manager.py`](project_manager.py) | File-based project state under `projects/<id>/project.json` — Pydantic models for `ProjectMeta`, `PipelineConfig`, `StepState`; step status transitions (pending → running → done/error); downstream invalidation on re-run |

### Pipeline modules

| File | Role |
|------|------|
| [`transcribe.py`](transcribe.py) | `faster-whisper` model loading + streaming transcription → `{language, duration, segments[{start, end, text}]}` |
| [`extract_frames.py`](extract_frames.py) | FFmpeg single-frame extraction via `-ss` seek; two strategies: `segment` (one frame per transcript segment) and `interval` (every N seconds); `prepare_frames_for_vision()` resizes + base64-encodes for OpenAI Vision API |
| [`slide_plan.py`](slide_plan.py) | Pydantic models (`SlidePlan`, `SlideSpec`, `VocabItem`, `DialogueLine`); system prompt for pedagogically ordered slides; OpenAI chat completion with JSON mode; Vision API multi-modal messages; frame_index clamping |
| [`generate_illustrations.py`](generate_illustrations.py) | Collects DALL-E prompts only from `vocabulary` and `moral_lesson` slide types; SHA-1 dedup; on-disk caching; `map_illustrations_to_plan()` builds lookup for the renderer |
| [`render_slides.py`](render_slides.py) | Jinja2 `Environment` loading `templates/`; per-slide context builders; image → base64 data URI embedding; Playwright async screenshot loop (1920×1080); HTML debug output |
| [`build_pptx.py`](build_pptx.py) | `build_presentation_from_images()` — embeds pre-rendered PNGs as full-slide pictures; `build_presentation_legacy()` — original python-pptx text-based renderer (fallback) |

### Templates

Slide templates in [`templates/`](templates/) extend [`base.html`](templates/base.html) which provides a 1920×1080 canvas with Google Fonts (Fredoka + Quicksand), gradient backgrounds, and shared CSS.

| Order | Template | Content |
|-------|----------|---------|
| 1 | [`title.html`](templates/title.html) | Lesson title, subtitle, story summary; full-bleed background frame |
| 2 | [`overview.html`](templates/overview.html) | Story summary, moral, objectives, rationale |
| 3 | [`story_intro.html`](templates/story_intro.html) | Characters & setting (2-3 bullets) + frame |
| 4 | [`plot_summary.html`](templates/plot_summary.html) | Chronological steps with arrow flow + frame |
| 5 | [`key_scene.html`](templates/key_scene.html) | Coolest moment + dialogue bubbles + frame |
| 6 | [`vocabulary.html`](templates/vocabulary.html) | 4-6 word cards in 2×N grid; DALL-E per-word illustration + frame |
| 7 | [`key_phrases.html`](templates/key_phrases.html) | Fun phrases as colorful boxes + frame |
| 8 | [`comprehension.html`](templates/comprehension.html) | Q&A quiz pairs + frame |
| 9 | [`moral_lesson.html`](templates/moral_lesson.html) | Big takeaway card; DALL-E slide illustration + frame |
| 10 | [`discussion.html`](templates/discussion.html) | "What about you?" prompts + frame |

### Web UI pages

| Template | Route | Purpose |
|----------|-------|---------|
| [`layout.html`](web_templates/layout.html) | — | Shared HTML shell |
| [`projects.html`](web_templates/projects.html) | `GET /` | Dashboard: list, create, delete projects |
| [`project_detail.html`](web_templates/project_detail.html) | `GET /projects/{id}` | Status, run pipeline, preview slides, download PPTX |
| [`edit_slide_plan.html`](web_templates/edit_slide_plan.html) | `GET /projects/{id}/edit/plan` | JSON editor for `slide_plan.json` |
| [`edit_html.html`](web_templates/edit_html.html) | `GET /projects/{id}/edit/html` | Edit rendered HTML slides, re-screenshot |
| [`manage_frames.html`](web_templates/manage_frames.html) | `GET /projects/{id}/edit/frames` | Browse, replace extracted frames |

---

## Pydantic models ([`slide_plan.py`](slide_plan.py), [`project_manager.py`](project_manager.py))

```
SlidePlan
├── lesson_title: str
├── story_summary: str
├── moral: str
├── teaching_rationale: str | None
├── learning_objectives: list[str]
└── slides: list[SlideSpec]
    ├── slide_type: str  (one of VALID_SLIDE_TYPES)
    ├── title: str
    ├── bullets: list[str]
    ├── vocab_items: list[VocabItem] | None
    │   ├── word, pos, definition, example
    │   └── illustration_prompt
    ├── scene_dialogue: list[DialogueLine] | None
    │   ├── speaker
    │   └── line
    ├── teacher_notes: str | None
    ├── illustration_prompt: str
    └── frame_index: int

ProjectMeta
├── id, name, created_at
├── video_filename: str | None
├── status: ProjectStatus  (created | processing | completed | error)
├── config: PipelineConfig
│   ├── whisper_model, compute_type
│   ├── openai_model, reasoning_effort, openai_temperature
│   ├── max_slides, max_frames, frame_strategy, interval_seconds, frame_offset
│   ├── audience, use_vision, max_vision_frames
│   └── no_illustrations, dalle_model
├── pipeline: dict[str, StepState]
│   └── status (pending | running | done | error), started_at, completed_at, message
└── error_message: str | None
```

---

## API routes ([`app.py`](app.py))

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/projects` | Create project |
| `DELETE` | `/api/projects/{id}` | Delete project |
| `POST` | `/api/projects/{id}/upload` | Upload video |
| `POST` | `/api/projects/{id}/run` | Run full pipeline |
| `POST` | `/api/projects/{id}/run-from/{step}` | Resume from step |
| `POST` | `/api/projects/{id}/rerender` | Re-screenshot HTML → PNG → PPTX |
| `GET` | `/api/projects/{id}/progress` | SSE event stream |
| `GET` | `/api/projects/{id}/download` | Download `.pptx` |
| `GET/PUT` | `/api/projects/{id}/assets/plan` | Read/write `slide_plan.json` |
| `GET` | `/api/projects/{id}/assets/raw-response` | OpenAI raw response |
| `GET` | `/api/projects/{id}/assets/slides` | List rendered slides |
| `GET` | `/api/projects/{id}/assets/slides/{idx}` | Slide PNG |
| `GET/PUT` | `/api/projects/{id}/assets/slides/{idx}/html` | Read/write slide HTML |
| `GET` | `/api/projects/{id}/assets/frames` | List frames |
| `GET/POST` | `/api/projects/{id}/assets/frames/{idx}` | Get/replace frame PNG |
| `GET` | `/api/projects/{id}/assets/video` | Stream video |
| `GET` | `/api/projects/{id}/assets/thumbnail` | First frame thumbnail |

---

## Infrastructure

| File | Purpose |
|------|---------|
| [`requirements.txt`](requirements.txt) | Python deps: faster-whisper, openai, python-pptx, pydantic, Pillow, Jinja2, playwright, fastapi, uvicorn, sse-starlette |
| [`Dockerfile`](Dockerfile) | Python 3.10-slim + FFmpeg + PyTorch CPU + Playwright Chromium |
| [`docker-compose.yaml`](docker-compose.yaml) | Web UI on `:8000`; HuggingFace cache volume; `OPENAI_API_KEY` passthrough |
| [`test_overflow.py`](test_overflow.py) | Smoke test for slide overflow rendering |
| [`.gitignore`](.gitignore) | Ignores `input/`, `output/`, `projects/`, `.env`, `__pycache__/` |
