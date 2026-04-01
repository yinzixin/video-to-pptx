# Cartoon video → teaching slides (PPTX)

Generate pedagogically structured English teaching PowerPoints from animated video episodes. The pipeline transcribes the audio, extracts key frames, sends the transcript to an LLM (OpenAI, Xiaomi MiMo V2, or other OpenAI-compatible providers), renders beautifully styled HTML slides via Playwright, and assembles them into a PPTX.

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
│   6. pptx        ─► <stem>.pptx (per project)                    │
├──────────────────────────────────────────────────────────────────┤
│  CLI (cartoon_to_slides.py)                                      │
│  --input / --output — per-input folder & <stem>.pptx             │
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
| `llm_provider.py` | LLM provider abstraction — registry of providers (OpenAI, MiMo V2, ...) with `get_llm_client()` factory |
| `slide_plan.py` | LLM structured output → `SlidePlan` (Pydantic models); provider-agnostic via `llm_provider` |
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
| 7 | `key_phrases.html` | Must have | Fun phrases as colorful boxes | Video frame |
| 8 | `comprehension.html` | Must have | Q&A quiz pairs | Video frame |
| 9 | `moral_lesson.html` | Must have | Big takeaway in a decorative card | Video frame |
| 10 | `discussion.html` | Nice to have | "What about you?" prompts | Video frame |

## Pipeline steps

1. **Transcribe** — faster-whisper extracts text with timestamps
2. **Extract frames** — FFmpeg captures key frames from the video
3. **Slide plan** — LLM designs the lesson structure (OpenAI, MiMo V2, etc.; optionally with Vision API)
4. **Render** — Jinja2 templates + Playwright produce 1920×1080 PNGs
5. **Assemble** — python-pptx embeds the PNGs as slides

## Web UI

The FastAPI web interface (`app.py`) provides:

- **Project dashboard** — create, list, and delete projects
- **Video upload** — upload video files to a project
- **Pipeline configuration** — configure whisper model, LLM provider/model, audience, etc.
- **Live progress** — SSE-based real-time pipeline progress tracking
- **Slide plan editor** — view and edit the generated slide plan JSON
- **HTML slide editor** — edit rendered HTML slides and re-screenshot
- **Frame manager** — browse and manage extracted frames
- **Preview & download** — view rendered slides and download the final PPTX

```bash
python -m uvicorn app:app --reload
```

## CLI

Each input file is processed into **`OUTPUT_DIR/<stem>/<stem>.pptx`**, with transcript, frames, and rendered assets in **`OUTPUT_DIR/<stem>/`**. **`--output`** defaults to the **current directory**.

```bash
# All .mp4 files in the current directory → ./<stem>/<stem>.pptx
python cartoon_to_slides.py --input "*.mp4"

# Explicit base directory → some_folder/<stem>/<stem>.pptx
python cartoon_to_slides.py --input "*.mp4" --output some_folder

# One file, outputs to ./lesson/lesson.pptx (when the file is lesson.mp4)
python cartoon_to_slides.py --input input/lesson.mp4

# Multiple paths or patterns
python cartoon_to_slides.py --input a.mp4 other/b.mp4 "archive/*.mp4"
```

On Windows, quote glob patterns so the shell does not expand them before Python: `--input "*.mp4"`.

Default LLM provider is **OpenAI** with model **`gpt-4.1`** and **`--llm-temperature=0.6`** (default). Use **`--llm-provider mimo`** to switch to Xiaomi MiMo V2 (default model: `mimo-v2-pro`). For providers that support it, **`--reasoning-effort`** applies instead of temperature. The legacy flags `--openai-model` and `--openai-temperature` still work as aliases.

### Key options

| Flag | Description |
|---|---|
| `--input` | One or more video paths and/or glob patterns (required) |
| `--output` | Base directory for per-input subfolders (default: current directory) |
| `--whisper-device` | Whisper device: `auto` (default), `cuda`, or `cpu` |
| `--compute-type` | Compute precision (default: auto — `float16` for cuda, `int8` for cpu) |
| `--legacy-renderer` | Use the original python-pptx text renderer |
| `--use-vision` | Enable Vision API (disabled by default) |
| `--max-vision-frames` | Max frames for Vision API (default: 8) |
| `--audience` | Learner description, e.g. `"kids aged 8-10, A2"` |
| `--max-slides` | Maximum content slides (default: 12) |
| `--skip-transcribe` | Reuse existing `transcript.json` in each input folder |
| `--skip-frames` | Reuse existing frames manifest in each input folder |

### All options

`--input`, `--output`, `--whisper-model`, `--whisper-device` (`auto|cuda|cpu`), `--compute-type` (`auto|float16|int8|int8_float16`), `--llm-provider` (`openai|mimo`), `--llm-model` (alias `--openai-model`), `--reasoning-effort` (`none|low|medium|high|xhigh`), `--llm-temperature` (alias `--openai-temperature`), `--max-slides`, `--frame-strategy segment|interval`, `--interval-seconds`, `--frame-offset`, `--audience`, `--use-vision / --no-vision`, `--max-vision-frames`, `--legacy-renderer`, `--skip-transcribe`, `--skip-frames`, `skip_intro_seconds`.

## Setup

1. Install [FFmpeg](https://ffmpeg.org/) and Python 3.10+.
2. `pip install -r requirements.txt` (PyTorch: use CPU index from the Dockerfile if needed.)
3. `playwright install chromium` (for the rich renderer)
4. Set your LLM API key(s) (at least one is required for slide planning):

   ```bash
   # Option A — export directly (Linux / macOS / Git Bash)
   export OPENAI_API_KEY="sk-..."      # for OpenAI provider
   export MIMO_API_KEY="your-key"      # for Xiaomi MiMo V2 provider

   # Option A — PowerShell
   $env:OPENAI_API_KEY = "sk-..."
   $env:MIMO_API_KEY = "your-key"

   # Option B — create a .env file in the project root (git-ignored)
   echo OPENAI_API_KEY=sk-... > .env
   echo MIMO_API_KEY=your-key >> .env
   ```

   The pipeline reads the appropriate API key based on the selected provider. A `.env` file in the project root is loaded automatically by Docker Compose and can also be loaded in local runs with a library like `python-dotenv`.

## Container

Both Docker Compose files pass LLM API keys into the container. The easiest way to provide them is a `.env` file in the project root (already in `.gitignore`):

```
OPENAI_API_KEY=sk-...
MIMO_API_KEY=your-key
```

Docker Compose reads `.env` automatically — no extra flags needed. Only the key for your chosen provider is required.

### CPU (default)

```bash
docker compose up --build              # Web UI on :8000
docker compose run --rm whisper \
  python cartoon_to_slides.py --input input/1.mp4 --output output
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
  python cartoon_to_slides.py --input input/1.mp4 --output output
```

GPU acceleration uses `float16` compute by default (vs `int8` on CPU) and provides ~5-10x faster transcription, especially with larger Whisper models like `large-v3`.

### Cost estimate per lesson

- OpenAI GPT (text): ~$0.01–0.05; MiMo V2: ~$0.01–0.03
- Total: ~$0.01–0.05
