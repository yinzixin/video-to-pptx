#!/usr/bin/env python3
"""Test key_scene with many dialogue lines + long teacher note."""

import os
from slide_plan import SlidePlan
from render_slides import render_slides
from build_pptx import build_presentation_from_images


def main() -> int:
    plan = SlidePlan.model_validate({
        "lesson_title": "Bluey: Frozen",
        "story_summary": "Bluey and Bingo play a game of freeze tag.",
        "moral": "Taking turns is important.",
        "learning_objectives": ["Understand feelings vocabulary"],
        "slides": [
            {
                "slide_type": "key_scene",
                "title": "Key Scene 3: Bingo Says Her Feelings",
                "bullets": [
                    "Bingo finally explains the real problem clearly: Bluey takes all the turns.",
                    "She uses feeling language to say the game is unfair.",
                    "Teaching point: expressing feelings can help solve a conflict.",
                ],
                "scene_dialogue": [
                    {"speaker": "Bingo", "line": "Bluey, you always never take tons with me."},
                    {"speaker": "Bingo", "line": "You just take all of the tons."},
                    {"speaker": "Bingo", "line": "And it makes me feel sad."},
                    {"speaker": "Bluey", "line": "I will unfreeze you if you promise me you'll let me have times too."},
                    {"speaker": "Bingo", "line": "I promise!"},
                    {"speaker": "Bluey", "line": "OK, then let's play together properly this time."},
                ],
                "teacher_notes": "Because the transcript is noisy, paraphrase the meaning after reading. Bingo says Bluey takes all the turns, and Bingo feels sad. Ask students to make a cleaner sentence: 'You take all the turns.'",
                "frame_index": 0,
            },
            {
                "slide_type": "vocabulary",
                "title": "Key Vocabulary 2",
                "vocab_items": [
                    {"word": "splendid", "pos": "adjective", "definition": "very good, beautiful, or impressive", "example": "What a splendid look that you have!"},
                    {"word": "head start", "pos": "phrase", "definition": "an early advantage before others begin", "example": "That way you can get a head start."},
                    {"word": "freeze", "pos": "verb", "definition": "to stop moving completely", "example": "I'll freeze my boss."},
                    {"word": "promise", "pos": "verb", "definition": "to say you will definitely do something", "example": "I will unpear you if you promise me you'll let me have times too."},
                ],
                "teacher_notes": "Ask students to make one simple sentence with each word. For lower levels, let them point and choose the best definition first.",
                "frame_index": 1,
            },
        ],
    })

    manifest = {"frames": []}
    out_dir = os.path.join("output", "test_overflow")
    os.makedirs("output", exist_ok=True)

    slide_images = render_slides(plan, manifest, None, "bluey.mp4", out_dir)
    out_pptx = os.path.join("output", "test_overflow.pptx")
    build_presentation_from_images(slide_images, plan.lesson_title, out_pptx)
    print(f"Done: {len(slide_images)} slides -> {out_pptx}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
