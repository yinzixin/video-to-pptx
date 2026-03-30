# Cartoon video → teaching slides (PPTX)

Generate pedagogically structured English teaching PowerPoints from animated video episodes. The pipeline transcribes the audio, extracts key frames, sends the transcript to OpenAI, renders beautifully styled HTML slides via Playwright, and assembles them into a PPTX.

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
│   4. illustrations ─► (skipped — no longer used)               │
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
| `generate_illustrations.py` | (Legacy) DALL-E image generation — no longer called by the pipeline |
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
| 6 | `vocabulary.html` | Must have | 4-6 word cards in a 2×N grid | Video frame |
| 7 | `key_phrases.html` | Nice to have | Fun phrases as colorful boxes | Video frame |
| 8 | `comprehension.html` | Must have | Q&A quiz pairs | Video frame |
| 9 | `moral_lesson.html` | Must have | Big takeaway in a decorative card | Video frame |
| 10 | `discussion.html` | Nice to have | "What about you?" prompts | Video frame |

## Pipeline steps

1. **Transcribe** — faster-whisper extracts text with timestamps
2. **Extract frames** — FFmpeg captures key frames from the video
3. **Slide plan** — OpenAI GPT designs the lesson structure (optionally with Vision API)
4. **Render** — Jinja2 templates + Playwright produce 1920×1080 PNGs
5. **Assemble** — python-pptx embeds the PNGs as slides

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

Default slide model is **`gpt-4.1`** with **`--openai-temperature=0.6`** (default). For `gpt-5.*` models, **`--reasoning-effort`** applies instead of temperature.

### Key options

| Flag | Description |
|---|---|
| `--whisper-device` | Whisper device: `auto` (default), `cuda`, or `cpu` |
| `--compute-type` | Compute precision (default: auto — `float16` for cuda, `int8` for cpu) |
| `--legacy-renderer` | Use the original python-pptx text renderer |
| `--use-vision` | Enable Vision API (disabled by default) |
| `--max-vision-frames` | Max frames for Vision API (default: 8) |
| `--audience` | Learner description, e.g. `"kids aged 8-10, A2"` |
| `--max-slides` | Maximum content slides (default: 12) |
| `--skip-transcribe` | Reuse existing transcript.json |
| `--skip-frames` | Reuse existing frames manifest |

### All options

`--whisper-model`, `--whisper-device` (`auto|cuda|cpu`), `--compute-type` (`auto|float16|int8|int8_float16`), `--openai-model`, `--reasoning-effort` (`none|low|medium|high|xhigh`, gpt-5.* only), `--openai-temperature`, `--max-slides`, `--frame-strategy segment|interval`, `--interval-seconds`, `--frame-offset`, `--audience`, `--use-vision / --no-vision`, `--max-vision-frames`, `--legacy-renderer`, `--skip-transcribe`, `--skip-frames`.

## Setup

1. Install [FFmpeg](https://ffmpeg.org/) and Python 3.10+.
2. `pip install -r requirements.txt` (PyTorch: use CPU index from the Dockerfile if needed.)
3. `playwright install chromium` (for the rich renderer)
4. Set your OpenAI API key (required for slide planning):

   ```bash
   # Option A — export directly (Linux / macOS / Git Bash)
   export OPENAI_API_KEY="sk-..."

   # Option A — PowerShell
   $env:OPENAI_API_KEY = "sk-..."

   # Option B — create a .env file in the project root (git-ignored)
   echo OPENAI_API_KEY=sk-... > .env
   ```

   The pipeline reads `OPENAI_API_KEY` from the environment at runtime. A `.env` file in the project root is loaded automatically by Docker Compose and can also be loaded in local runs with a library like `python-dotenv`.

## Container

Both Docker Compose files pass `OPENAI_API_KEY` into the container via `${OPENAI_API_KEY}`. The easiest way to provide it is a `.env` file in the project root (already in `.gitignore`):

```
OPENAI_API_KEY=sk-...
```

Docker Compose reads `.env` automatically — no extra flags needed.

### CPU (default)

```bash
docker compose up --build              # Web UI on :8000
docker compose run --rm whisper \
  python cartoon_to_slides.py --video input/1.mp4 --out output/lesson.pptx
```

### GPU (NVIDIA CUDA)

Uses `Dockerfile.gpu` (NVIDIA CUDA 12.4 + cuDNN base image) and `docker-compose.gpu.yaml` (GPU reservation + `WHISPER_DEVICE=cuda`).

**Prerequisites on the Docker host:**

1. NVIDIA GPU with CUDA support
2. [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) installed
3. Verify with: `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi`

```bash
docker compose -f docker-compose.gpu.yaml up --build     # Web UI on :8000 with GPU
docker compose -f docker-compose.gpu.yaml run --rm whisper \
  python cartoon_to_slides.py --video input/1.mp4 --out output/lesson.pptx
```

GPU acceleration uses `float16` compute by default (vs `int8` on CPU) and provides ~5-10x faster transcription, especially with larger Whisper models like `large-v3`.

### Cost estimate per lesson

- OpenAI GPT (text): ~$0.01–0.05
- Total: ~$0.01–0.05
