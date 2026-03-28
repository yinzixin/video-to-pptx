# Cartoon video → teaching slides (PPTX)

Generate pedagogically structured English teaching PowerPoints from animated video episodes. The pipeline transcribes the audio, extracts key frames, sends both transcript and images to OpenAI (Vision API), and builds a richly formatted PPTX with typed slide layouts.

## Generated slide types

| Slide type | Purpose |
|---|---|
| Story Introduction | Characters, setting, and premise |
| Plot Summary | Key events in chronological order |
| Key Scene | Important moments with actual dialogue |
| Vocabulary | Word table with POS, definition, and example sentence |
| Key Phrases | Useful expressions and functional language |
| Comprehension Check | Q&A pairs for understanding verification |
| Moral / Lesson | The message or value the story teaches |
| Discussion | Open-ended questions for class conversation |

## Setup

- Install [FFmpeg](https://ffmpeg.org/) and Python 3.10+.
- `pip install -r requirements.txt` (PyTorch: use CPU index from the Dockerfile if needed.)
- Set `OPENAI_API_KEY` in the environment.

## Run

```bash
python cartoon_to_slides.py --video input/1.mp4 --out output/lesson.pptx
```

Default slide model is **`gpt-5.4`** with **`reasoning_effort=medium`**. For other models, **`--openai-temperature`** applies (default `0.6`).

### Vision API

By default, key frames are sent to OpenAI as images so the model can identify characters, settings, and visual context. Disable with `--no-vision`. Control how many frames are sent with `--max-vision-frames` (default: 8).

### All options

`--whisper-model`, `--openai-model`, `--reasoning-effort` (`none|low|medium|high|xhigh`, gpt-5.* only), `--openai-temperature`, `--max-slides`, `--frame-strategy segment|interval`, `--interval-seconds`, `--audience`, `--use-vision / --no-vision`, `--max-vision-frames`, `--skip-transcribe`, `--skip-frames`.

## Container

Build the image, pass `OPENAI_API_KEY`, mount the project, run `python cartoon_to_slides.py` with the same arguments. Works with Docker or Podman:

```bash
podman compose run --rm whisper python cartoon_to_slides.py --video input/1.mp4 --out output/lesson.pptx
```

Legacy transcription only: `python transcribe.py`.
