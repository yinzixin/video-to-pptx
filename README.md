# Cartoon video → teaching slides (PPTX)

Generate pedagogically structured English teaching PowerPoints from animated video episodes. The pipeline transcribes the audio, extracts key frames, sends both transcript and images to OpenAI (Vision API), generates DALL-E cartoon illustrations, renders beautifully styled HTML slides via Playwright, and assembles them into a PPTX.

## Generated slide types

| Slide type | Purpose |
|---|---|
| Story Introduction | Characters, setting, and premise |
| Plot Summary | Key events in chronological order |
| Key Scene | Important moments with actual dialogue |
| Vocabulary | Word cards with illustrations, POS, definition, and example |
| Key Phrases | Useful expressions and functional language |
| Comprehension Check | Q&A pairs for understanding verification |
| Moral / Lesson | The message or value the story teaches |
| Discussion | Open-ended questions for class conversation |

## Rich visual pipeline (default)

Slides are rendered as styled HTML pages with a chalkboard theme (dark green background, gold headers, decorative borders), then screenshotted at 1920×1080 via Playwright Chromium and embedded into the PPTX as full-slide images.

Optionally, DALL-E 3 generates cartoon illustrations for vocabulary words and key scenes.

### Pipeline steps

1. **Transcribe** — faster-whisper extracts text with timestamps
2. **Extract frames** — FFmpeg captures key frames from the video
3. **Slide plan** — OpenAI GPT (+ Vision API) designs the lesson structure
4. **Illustrations** — DALL-E 3 generates cartoon images for vocab/scenes
5. **Render** — Jinja2 templates + Playwright produce 1920×1080 PNGs
6. **Assemble** — python-pptx embeds the PNGs as slides

## Setup

- Install [FFmpeg](https://ffmpeg.org/) and Python 3.10+.
- `pip install -r requirements.txt` (PyTorch: use CPU index from the Dockerfile if needed.)
- `playwright install chromium` (for the rich renderer)
- Set `OPENAI_API_KEY` in the environment.

## Run

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

## Container

Build the image (includes Playwright Chromium), pass `OPENAI_API_KEY`, mount the project:

```bash
podman compose run --rm whisper python cartoon_to_slides.py --video input/1.mp4 --out output/lesson.pptx
```

### Cost estimate per lesson

- OpenAI GPT (text + vision): ~$0.05–0.15
- DALL-E 3 illustrations: ~$0.40–0.80 (10–20 images)
- Total: ~$0.50–1.00

Use `--no-illustrations` to skip DALL-E and reduce cost to ~$0.05–0.15.
