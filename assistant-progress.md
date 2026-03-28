# Teaching PPT Pipeline Improvement - Progress

## Completed

All 6 tasks from the plan are implemented and tested.

### 1. Data Model Redesign (`slide_plan.py`)
- Added `VocabItem` (word, pos, definition, example) and `DialogueLine` (speaker, line) models
- `SlideSpec` now has `slide_type` field (8 valid types with fallback), `vocab_items`, `scene_dialogue`
- `SlidePlan` gained `story_summary` and `moral` top-level fields
- Validators normalize unknown types to `key_scene`

### 2. Prompt Redesign (`slide_plan.py`)
- `SYSTEM_PROMPT` mandates 8-step pedagogical sequence: story_intro -> plot_summary -> key_scene -> vocabulary -> key_phrases -> comprehension -> moral_lesson -> discussion
- `build_user_payload` specifies full JSON schema including `vocab_items` and `scene_dialogue` structures
- Prompt instructs model to ground all content in transcript, infer speakers, spread frame indices

### 3. Vision API Support
- `extract_frames.py`: added `prepare_frames_for_vision()` — selects N frames, resizes via Pillow, encodes as base64 JPEG
- `slide_plan.py`: `generate_slide_plan()` accepts `vision_frames` param, builds multi-modal content array with `image_url` parts
- `requirements.txt`: added `Pillow>=10.0.0`

### 4. PPTX Builder Redesign (`build_pptx.py`)
- 8 distinct renderers: `_render_story_intro`, `_render_plot_summary`, `_render_key_scene`, `_render_vocabulary`, `_render_key_phrases`, `_render_comprehension`, `_render_moral_lesson`, `_render_discussion`
- Each has unique accent color bar, section tag, layout strategy
- Vocabulary uses `add_table()` with header row + alternating colors
- Key scene uses `add_run()` for bold speaker names in dialogue
- Moral lesson uses centered quote-style with colored background rectangle
- Overview slide shows story_summary, moral, objectives, rationale
- Teacher notes bar at slide bottom (light gray, italic)
- Generic fallback renderer for unknown types

### 5. CLI Update (`cartoon_to_slides.py`)
- `--use-vision` / `--no-vision` mutually exclusive group (default: enabled)
- `--max-vision-frames` (default: 8)
- Step [2b/4] prepares vision frames between frame extraction and OpenAI call
- Passes `vision_frames` to `generate_slide_plan()`

### 6. Testing
- All files compile cleanly via podman
- All imports resolve (8 types, 8 palettes, 8 renderers)
- Pydantic model validation passes for all types + edge cases
- PPTX generation succeeds with all 8 slide types (41KB output)
- CLI --help shows all new flags correctly
