"""Render slide plan to high-res PNG images via Jinja2 HTML templates + Playwright."""

from __future__ import annotations

import asyncio
import base64
import os
import re
from typing import Any

from jinja2 import Environment, FileSystemLoader

from slide_plan import SlidePlan

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

SECTION_TAGS: dict[str, str] = {
    "story_intro": "🌟 Meet the Friends!",
    "plot_summary": "📖 The Story!",
    "key_scene": "🎬 Cool Moment!",
    "vocabulary": "📚 New Words!",
    "key_phrases": "💬 Say This!",
    "comprehension": "🧩 Quiz Time!",
    "moral_lesson": "💡 Big Idea!",
    "discussion": "🗣️ Your Turn!",
}


def _to_data_uri(path: str | None) -> str | None:
    """Convert a local image path to a base64 data URI."""
    if not path or not os.path.isfile(path):
        return None
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
            "webp": "image/webp"}.get(ext, "image/png")
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _parse_qa_pairs(bullets: list[str]) -> list[dict[str, str]]:
    """Parse alternating Q:/A: bullets into structured pairs."""
    pairs: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in bullets:
        stripped = line.strip()
        if re.match(r"^Q\s*[:\.]\s*", stripped, re.IGNORECASE):
            if current:
                pairs.append(current)
            current = {"q": re.sub(r"^Q\s*[:\.]\s*", "", stripped, flags=re.IGNORECASE)}
        elif re.match(r"^A\s*[:\.]\s*", stripped, re.IGNORECASE):
            current["a"] = re.sub(r"^A\s*[:\.]\s*", "", stripped, flags=re.IGNORECASE)
        else:
            if current:
                pairs.append(current)
            current = {"q": stripped}
    if current:
        pairs.append(current)
    return pairs


def _build_slide_context(
    slide_index: int,
    plan: SlidePlan,
    frames_manifest: dict[str, Any],
    illustration_map: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the Jinja2 template context for a single slide."""
    spec = plan.slides[slide_index]
    frames = frames_manifest.get("frames", [])

    frame_path = None
    if frames and 0 <= spec.frame_index < len(frames):
        frame_path = frames[spec.frame_index].get("path")
    frame_uri = _to_data_uri(frame_path)

    slide_illust = None
    vocab_illustrations: dict[str, str | None] = {}
    if illustration_map:
        slide_illust = _to_data_uri(
            (illustration_map.get("slides") or {}).get(slide_index)
        )
        vocab_lookup = illustration_map.get("vocab") or {}
        for vi in spec.vocab_items or []:
            vocab_illustrations[vi.word] = _to_data_uri(vocab_lookup.get(vi.word))

    vocab_items_ctx = None
    if spec.vocab_items:
        vocab_items_ctx = []
        for vi in spec.vocab_items:
            vocab_items_ctx.append({
                "word": vi.word,
                "pos": vi.pos,
                "definition": vi.definition,
                "example": vi.example,
                "illustration": vocab_illustrations.get(vi.word),
            })

    dialogue_ctx = None
    if spec.scene_dialogue:
        dialogue_ctx = [
            {"speaker": dl.speaker, "line": dl.line}
            for dl in spec.scene_dialogue
        ]

    qa_pairs = _parse_qa_pairs(spec.bullets) if spec.slide_type == "comprehension" else []

    return {
        "title": spec.title,
        "section_tag": SECTION_TAGS.get(spec.slide_type, ""),
        "bullets": spec.bullets,
        "vocab_items": vocab_items_ctx,
        "scene_dialogue": dialogue_ctx,
        "teacher_notes": spec.teacher_notes,
        "frame_image": frame_uri,
        "illustration": slide_illust,
        "qa_pairs": qa_pairs,
    }


def _build_title_context(
    plan: SlidePlan,
    frames_manifest: dict[str, Any],
    video_basename: str,
) -> dict[str, Any]:
    """Context for the title slide."""
    frames = frames_manifest.get("frames", [])
    first_frame_uri = None
    if frames:
        first_frame_uri = _to_data_uri(frames[0].get("path"))
    return {
        "title": plan.lesson_title,
        "section_tag": "",
        "teacher_notes": None,
        "lesson_title": plan.lesson_title,
        "subtitle": f"Source: {video_basename}",
        "story_summary": plan.story_summary,
        "frame_image": first_frame_uri,
    }


def _build_overview_context(plan: SlidePlan) -> dict[str, Any]:
    """Context for the overview slide."""
    return {
        "title": "Lesson Overview",
        "section_tag": "Overview",
        "teacher_notes": None,
        "story_summary": plan.story_summary,
        "moral": plan.moral,
        "learning_objectives": plan.learning_objectives,
        "teaching_rationale": plan.teaching_rationale,
    }


def _render_html_slides(
    plan: SlidePlan,
    frames_manifest: dict[str, Any],
    illustration_map: dict[str, Any] | None,
    video_basename: str,
) -> list[str]:
    """Render all slides as HTML strings."""
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=False,
    )

    html_slides: list[str] = []

    title_tpl = env.get_template("title.html")
    html_slides.append(title_tpl.render(**_build_title_context(
        plan, frames_manifest, video_basename
    )))

    for i, spec in enumerate(plan.slides):
        tpl_name = f"{spec.slide_type}.html"
        try:
            tpl = env.get_template(tpl_name)
        except Exception:
            tpl = env.get_template("story_intro.html")
        ctx = _build_slide_context(i, plan, frames_manifest, illustration_map)
        html_slides.append(tpl.render(**ctx))

    return html_slides


async def _screenshot_slides(
    html_slides: list[str],
    output_dir: str,
    *,
    width: int = 1920,
    height: int = 1080,
    verbose: bool = True,
) -> list[str]:
    """Use Playwright to screenshot each HTML slide to a PNG (parallel)."""
    from playwright.async_api import async_playwright

    os.makedirs(output_dir, exist_ok=True)
    total = len(html_slides)
    paths: list[str | None] = [None] * total
    concurrency = min(total, os.cpu_count() or 4, 6)
    sem = asyncio.Semaphore(concurrency)
    done_count = 0

    async def _render_one(idx: int, html: str, browser: Any) -> None:
        nonlocal done_count
        async with sem:
            page = await browser.new_page(viewport={"width": width, "height": height})
            await page.set_content(html, wait_until="networkidle")
            try:
                await page.evaluate("document.fonts.ready")
            except Exception:
                await page.wait_for_timeout(2000)

            out_path = os.path.join(output_dir, f"slide_{idx:03d}.png")
            await page.screenshot(path=out_path, full_page=False)
            await page.close()
            paths[idx] = os.path.abspath(out_path)
            done_count += 1
            if verbose:
                print(
                    f"    rendered slide {done_count}/{total} "
                    f"(slide_{idx:03d}) -> {out_path}",
                    flush=True,
                )

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        await asyncio.gather(
            *[_render_one(i, h, browser) for i, h in enumerate(html_slides)]
        )
        await browser.close()

    return [p for p in paths if p is not None]


def render_slides(
    plan: SlidePlan,
    frames_manifest: dict[str, Any],
    illustration_map: dict[str, Any] | None,
    video_basename: str,
    output_dir: str,
    *,
    width: int = 1920,
    height: int = 1080,
    verbose: bool = True,
) -> list[str]:
    """Render slide plan to PNG images. Returns list of PNG file paths."""
    if verbose:
        print("  [render] building HTML from templates…", flush=True)

    html_slides = _render_html_slides(
        plan, frames_manifest, illustration_map, video_basename
    )

    if verbose:
        print(
            f"  [render] {len(html_slides)} HTML slides ready, screenshotting…",
            flush=True,
        )

    html_dir = os.path.join(output_dir, "html_debug")
    os.makedirs(html_dir, exist_ok=True)
    for idx, html in enumerate(html_slides):
        with open(os.path.join(html_dir, f"slide_{idx:03d}.html"), "w",
                  encoding="utf-8") as f:
            f.write(html)

    paths = asyncio.run(_screenshot_slides(
        html_slides, output_dir, width=width, height=height, verbose=verbose
    ))

    if verbose:
        print(f"  [render] done: {len(paths)} PNGs in {output_dir}", flush=True)

    return paths
