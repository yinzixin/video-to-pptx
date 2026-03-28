"""Generate cartoon illustrations via DALL-E for vocabulary items and slides."""

from __future__ import annotations

import hashlib
import os
import time
from typing import Any
from urllib.request import urlretrieve

from openai import OpenAI

from slide_plan import SlidePlan


def _prompt_key(prompt: str) -> str:
    """Stable short key for caching / dedup."""
    return hashlib.sha1(prompt.encode()).hexdigest()[:12]


def _collect_prompts(plan: SlidePlan) -> list[tuple[str, str]]:
    """Return (key, prompt) pairs from the plan, deduplicated."""
    seen: dict[str, str] = {}
    for slide in plan.slides:
        if slide.illustration_prompt:
            k = _prompt_key(slide.illustration_prompt)
            seen.setdefault(k, slide.illustration_prompt)
        for vi in slide.vocab_items or []:
            if vi.illustration_prompt:
                k = _prompt_key(vi.illustration_prompt)
                seen.setdefault(k, vi.illustration_prompt)
    return list(seen.items())


def generate_illustrations(
    plan: SlidePlan,
    output_dir: str,
    *,
    api_key: str | None = None,
    dalle_model: str = "dall-e-3",
    size: str = "1024x1024",
    quality: str = "standard",
    verbose: bool = True,
) -> dict[str, str]:
    """Generate DALL-E illustrations and return a mapping of prompt_key -> local PNG path.

    Existing files are reused (cache by prompt hash).
    """
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ValueError("OPENAI_API_KEY is not set")

    client = OpenAI(api_key=key)
    os.makedirs(output_dir, exist_ok=True)

    prompts = _collect_prompts(plan)
    if not prompts:
        if verbose:
            print("  [illustrations] no illustration prompts found", flush=True)
        return {}

    if verbose:
        print(
            f"  [illustrations] {len(prompts)} unique prompt(s) to generate",
            flush=True,
        )

    result: dict[str, str] = {}
    for idx, (pkey, prompt) in enumerate(prompts):
        out_path = os.path.join(output_dir, f"{pkey}.png")

        if os.path.isfile(out_path):
            if verbose:
                print(
                    f"  [{idx + 1}/{len(prompts)}] cached: {pkey} -> {out_path}",
                    flush=True,
                )
            result[pkey] = os.path.abspath(out_path)
            continue

        if verbose:
            short = prompt[:80] + ("…" if len(prompt) > 80 else "")
            print(
                f"  [{idx + 1}/{len(prompts)}] generating: {short}",
                flush=True,
            )

        try:
            resp = client.images.generate(
                model=dalle_model,
                prompt=prompt,
                n=1,
                size=size,
                quality=quality,
            )
            url = resp.data[0].url
            if not url:
                raise RuntimeError("DALL-E returned empty URL")
            urlretrieve(url, out_path)
            result[pkey] = os.path.abspath(out_path)
        except Exception as exc:
            if verbose:
                print(f"    WARN: generation failed ({exc}), skipping", flush=True)

    if verbose:
        print(
            f"  [illustrations] done: {len(result)}/{len(prompts)} images",
            flush=True,
        )
    return result


def map_illustrations_to_plan(
    plan: SlidePlan,
    illustrations: dict[str, str],
) -> dict[str, Any]:
    """Build a lookup usable by the HTML renderer.

    Returns::

        {
            "slides": {0: path_or_None, 1: path_or_None, ...},
            "vocab": {"word": path_or_None, ...},
        }
    """
    slide_map: dict[int, str | None] = {}
    vocab_map: dict[str, str | None] = {}

    for i, slide in enumerate(plan.slides):
        if slide.illustration_prompt:
            k = _prompt_key(slide.illustration_prompt)
            slide_map[i] = illustrations.get(k)
        else:
            slide_map[i] = None

        for vi in slide.vocab_items or []:
            if vi.illustration_prompt:
                k = _prompt_key(vi.illustration_prompt)
                vocab_map[vi.word] = illustrations.get(k)

    return {"slides": slide_map, "vocab": vocab_map}
