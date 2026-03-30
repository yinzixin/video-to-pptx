# Session compaction (Zee)

## 2026-03-30 — Frame extraction time jitter

- **Change:** `extract_frames()` applies uniform random jitter `±time_jitter_seconds` per frame (default **0.75**), clamped to transcript segment bounds (segment mode) or `[0, duration]` (interval mode). Set **`0`** to restore deterministic times.
- **Wiring:** `PipelineConfig.time_jitter_seconds`, CLI `--time-jitter-seconds`, `app.py` float config merge, `pipeline_runner` passes through to `extract_frames`.
