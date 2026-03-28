# Cartoon video → teaching slides (PPTX)

Generate pedagogically structured English teaching PowerPoints from animated video episodes. The pipeline transcribes the audio, extracts key frames, sends both transcript and images to OpenAI (Vision API), generates DALL-E cartoon illustrations, renders beautifully styled HTML slides via Playwright, and assembles them into a PPTX.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Web UI (FastAPI)                                                │
│  app.py ─► SSE progress ─► project dashboard                    │
│   ├── web_templates/   (Jinja2 pages for the browser UI)         │
│   └── static/          (CSS + JS)                                │
├──────────────────────────────────────────────────────────────────┤
│  Project Manager                                                 │
│  project_manager.py ─► projects/<id>/project.json                │
│   • create / list / delete projects                              │
│   • track per-step state (pending → running → done / error)      │
│   • invalidate downstream steps on re-run                        │
├──────────────────────────────────────────────────────────────────┤
│  Pipeline Runner (pipeline_runner.py)                             │
│  Re-entrant runner — can resume from any step                    │
│   1. transcribe  ─► transcript.json                              │
│   2. frames      ─► frames/ + frames_manifest.json               │
│   3. plan        ─► slide_plan.json                              │
│   4. illustrations ─► illustrations/*.png (DALL-E)               │
│   5. render      ─► slides/*.png + html_debug/*.html             │
│   6. pptx        ─► lesson.pptx                                 │
├──────────────────────────────────────────────────────────────────┤
│  CLI (cartoon_to_slides.py)                                      │
│  Same pipeline steps, driven by argparse                         │
└──────────────────────────────────────────────────────────────────┘
```

### Key modules

| Module | Role |
|---|---|
| `app.py` | FastAPI web server — upload videos, configure & run pipeline, preview/download results via SSE progress |
| `project_manager.py` | File-based project state under `projects/` — metadata, config, per-step status tracking |
| `pipeline_runner.py` | Orchestrates pipeline steps with progress callbacks; supports `start_from` to skip completed steps |
| `cartoon_to_slides.py` | CLI entry point wrapping the same pipeline |
| `transcribe.py` | Audio → text via faster-whisper |
| `extract_frames.py` | FFmpeg frame extraction (segment or interval strategy) + Vision API prep |
| `slide_plan.py` | OpenAI GPT structured output → `SlidePlan` (Pydantic models) |
| `generate_illustrations.py` | DALL-E image generation with prompt dedup and caching |
| `render_slides.py` | Jinja2 HTML templates + Playwright screenshots → 1920×1080 PNGs |
| `build_pptx.py` | Assembles rendered PNGs into a PowerPoint file |

## Slide templates

All templates extend `base.html` (shared layout, fonts, and CSS). The presentation is assembled in this order:

| Order | Template | Required | Content | Images |
|---|---|---|---|---|
| 1 | `title.html` | Always | Lesson title, subtitle, story summary | Video frame (full-bleed background) |
| 2 | `overview.html` | Conditional | Story summary, moral, objectives, rationale | None |
| 3 | `story_intro.html` | Must have | Characters and setting (2-3 bullets) | Video frame |
| 4 | `plot_summary.html` | Must have | Chronological steps with arrow flow | Video frame |
| 5 | `key_scene.html` | Must have (1-2) | Coolest moment + dialogue bubbles | Video frame |
| 6 | `vocabulary.html` | Must have | 4-6 word cards in a 2×N grid | DALL-E per-word illustration + video frame |
| 7 | `key_phrases.html` | Nice to have | Fun phrases as colorful boxes | Video frame |
| 8 | `comprehension.html` | Must have | Q&A quiz pairs | Video frame |
| 9 | `moral_lesson.html` | Must have | Big takeaway in a decorative card | DALL-E slide illustration + video frame |
| 10 | `discussion.html` | Nice to have | "What about you?" prompts | Video frame |

### Illustration generation

DALL-E illustrations are only generated for slide types that actually render them — `vocabulary` (per-word images) and `moral_lesson` (slide-level image). Other slide types rely exclusively on extracted video frames. This avoids unnecessary DALL-E API calls for templates that would discard the result.

Prompts are deduplicated by SHA-1 hash and cached on disk, so re-runs reuse existing images.

## Pipeline steps

1. **Transcribe** — faster-whisper extracts text with timestamps
2. **Extract frames** — FFmpeg captures key frames from the video
3. **Slide plan** — OpenAI GPT (+ Vision API) designs the lesson structure
4. **Illustrations** — DALL-E 3 generates cartoon images for vocabulary words and moral lesson slides
5. **Render** — Jinja2 templates + Playwright produce 1920×1080 PNGs
6. **Assemble** — python-pptx embeds the PNGs as slides

## Web UI

The FastAPI web interface (`app.py`) provides:

- **Project dashboard** — create, list, and delete projects
- **Video upload** — upload video files to a project
- **Pipeline configuration** — configure whisper model, OpenAI model, audience, illustration settings, etc.
- **Live progress** — SSE-based real-time pipeline progress tracking
- **Slide plan editor** — view and edit the generated slide plan JSON
- **HTML slide editor** — edit rendered HTML slides and re-screenshot
- **Frame manager** — browse and manage extracted frames
- **Preview & download** — view rendered slides and download the final PPTX

```bash
python -m uvicorn app:app --reload
```

## CLI

```bash
python cartoon_to_slides.py --video input/1.mp4 --out output/lesson.pptx
```

Default slide model is **`gpt-5.4`** with **`reasoning_effort=medium`**. For other models, **`--openai-temperature`** applies (default `0.6`).

### Key options

| Flag | Description |
|---|---|
| `--no-illustrations` | Skip DALL-E image generation (faster, cheaper) |
| `--dalle-model` | DALL-E model to use (default: `dall-e-3`) |
| `--legacy-renderer` | Use the original python-pptx text renderer |
| `--no-vision` | Disable Vision API (text-only slide planning) |
| `--max-vision-frames` | Max frames for Vision API (default: 8) |
| `--audience` | Learner description, e.g. `"kids aged 8-10, A2"` |
| `--max-slides` | Maximum content slides (default: 12) |
| `--skip-transcribe` | Reuse existing transcript.json |
| `--skip-frames` | Reuse existing frames manifest |

### All options

`--whisper-model`, `--openai-model`, `--reasoning-effort` (`none|low|medium|high|xhigh`, gpt-5.* only), `--openai-temperature`, `--max-slides`, `--frame-strategy segment|interval`, `--interval-seconds`, `--frame-offset`, `--audience`, `--use-vision / --no-vision`, `--max-vision-frames`, `--no-illustrations`, `--dalle-model`, `--legacy-renderer`, `--skip-transcribe`, `--skip-frames`.

## Setup

- Install [FFmpeg](https://ffmpeg.org/) and Python 3.10+.
- `pip install -r requirements.txt` (PyTorch: use CPU index from the Dockerfile if needed.)
- `playwright install chromium` (for the rich renderer)
- Set `OPENAI_API_KEY` in the environment.

## Container

Build the image (includes Playwright Chromium), pass `OPENAI_API_KEY`, mount the project:

```bash
podman compose run --rm whisper python cartoon_to_slides.py --video input/1.mp4 --out output/lesson.pptx
```

### Cost estimate per lesson

- OpenAI GPT (text + vision): ~$0.05–0.15
- DALL-E 3 illustrations: ~$0.08–0.16 (2-4 images for vocabulary + moral lesson)
- Total: ~$0.15–0.30

Use `--no-illustrations` to skip DALL-E and reduce cost to ~$0.05–0.15.
