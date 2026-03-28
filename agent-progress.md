# Rich Visual PPT Pipeline — Implementation Complete

## Summary

Implemented the full Rich Visual PPT Pipeline replacing the plain python-pptx text renderer with an HTML+CSS template pipeline that produces visually rich chalkboard-themed slides.

## Architecture

```
Video → Whisper → Transcript
Video → FFmpeg → Frames
Transcript + Frames → GPT (Vision) → SlidePlan (with illustration_prompts)
SlidePlan → DALL-E 3 → PNG illustrations
SlidePlan + Illustrations + Frames → Jinja2 HTML Templates → Playwright → 1920×1080 PNGs
PNGs → python-pptx → PPTX
```

## Files Modified

- **slide_plan.py**: Added `illustration_prompt` field to `VocabItem` and `SlideSpec`. Updated `SYSTEM_PROMPT` with DALL-E prompt generation instructions. Updated JSON schema in `build_user_payload`.
- **build_pptx.py**: Renamed `build_presentation` → `build_presentation_legacy`. Added `build_presentation_from_images()` for simple PNG embedding.
- **cartoon_to_slides.py**: Added `--no-illustrations`, `--dalle-model`, `--legacy-renderer` flags. Wired pipeline steps 3b (DALL-E), 3c+3d (HTML render + Playwright screenshot), 4 (image PPTX assembly). Lazy-imports `transcribe` inside `main()`.
- **requirements.txt**: Added `Jinja2>=3.1.0`, `playwright>=1.40.0`.
- **Dockerfile**: Added `playwright install --with-deps chromium`.
- **README.md**: Updated documentation.

## Files Created

- **generate_illustrations.py**: DALL-E 3 illustration generator with prompt dedup, caching, and path mapping.
- **render_slides.py**: Jinja2 template renderer + Playwright Chromium screenshotter. Converts frames/illustrations to base64 data URIs for self-contained HTML. Saves debug HTML alongside PNGs.
- **templates/base.html**: Chalkboard theme base template — dark green gradient, wood frame border, chalk texture, gold Fredoka headers, white Quicksand body text.
- **templates/title.html**: Centered lesson title with story summary card.
- **templates/overview.html**: 4-card grid (summary, moral, objectives, rationale).
- **templates/story_intro.html**: Two-column layout with frame image + character bullets.
- **templates/plot_summary.html**: Numbered flow diagram with gold circles and arrows.
- **templates/key_scene.html**: Frame image + alternating left/right speech bubbles.
- **templates/vocabulary.html**: 2-column vocab card grid with DALL-E illustrations and colored left borders.
- **templates/key_phrases.html**: Centered phrase boxes with alternating colors.
- **templates/comprehension.html**: Q&A pair cards with gold questions and italic answers.
- **templates/moral_lesson.html**: Large centered quote with decorative quotation marks.
- **templates/discussion.html**: Chat-bubble style questions alternating left/right.

## Test Results

- Container builds successfully with Playwright Chromium.
- Smoke test renders all 10 slide types (title + overview + 8 content types) as 1920×1080 PNGs.
- PPTX assembly from rendered images works correctly.
- All CLI flags (`--help`) display properly.
