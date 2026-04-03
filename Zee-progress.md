# Session compaction (Zee)

## 2026-04-01 — Dockerfile.gpu: PyTorch was +cpu inside GPU image

- **Symptom:** `torch.cuda.is_available()` false in container; `torch.__version__` was `2.11.0+cpu` despite `nvidia-smi` OK on host and `gpus: all`.
- **Cause:** `pip install -r requirements.txt` after an earlier CUDA torch install can resolve/replace torch from PyPI with the **CPU** wheel (`+cpu`).
- **Fix:** Install CUDA torch **after** requirements (`--force-reinstall` from `download.pytorch.org/whl/cu124`). Rebuild image: `docker compose -f docker-compose.gpu.yaml build --no-cache whisper`.

## 2026-04-01 — GPU compose: CLI ignored WHISPER_DEVICE; compose GPU hint

- **Why CPU:** `cartoon_to_slides.py` defaulted `--whisper-device` to `auto` only; it did not read `WHISPER_DEVICE` (unlike web `PipelineConfig`). With `auto`, no visible CUDA in container → `device=cpu`.
- **Fix:** `--whisper-device` default = `os.environ.get("WHISPER_DEVICE", "auto")`. `docker-compose.gpu.yaml`: added `gpus: all` so non-Swarm Compose still passes NVIDIA GPU (rely on `deploy` alone can leave the container without a GPU).

## 2026-04-01 — Project page actions toolbar

- **Change:** `project_detail.html` — single Actions card above video: row 1 = Run Pipeline + Download PPTX (`space-between`); row 2 = Re-generate Plan, Edit Slide (was Edit HTML Slides), Manage Frames. Removed Re-render Slides, Edit Slide Plan link, duplicate Edit & Refine section.
- **`app.js`:** `runPipeline` targets `#btn-run-pipeline` instead of first `.btn-primary`.
- **CSS:** `.project-actions-toolbar`, `.toolbar-row-split`, `.toolbar-row-end`.

## 2026-03-30 — Frame extraction time jitter

- **Change:** `extract_frames()` applies uniform random jitter `±time_jitter_seconds` per frame (default **0.75**), clamped to transcript segment bounds (segment mode) or `[0, duration]` (interval mode). Set **`0`** to restore deterministic times.
- **Wiring:** `PipelineConfig.time_jitter_seconds`, CLI `--time-jitter-seconds`, `app.py` float config merge, `pipeline_runner` passes through to `extract_frames`.
