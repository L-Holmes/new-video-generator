"""
The `progress_bar` media type: a horizontal bar fills to the percentage the
scene asked for while the number ticks up above it, then a caption fades in
underneath. One quantity out of a whole — the parts of a whole are pie_chart.

Writes the two artefacts every maths type writes (see ___visuals/maths and
AI_READ_THIS.txt): the transition mp4, and its last frame as a still.
"""
from __future__ import annotations

import hashlib

from CONFIG import (
    CHART_ACCENT,
    CHART_ANIM_SEC,
    CHART_BACKGROUND,
    CHART_INK,
    CHART_LABEL_SEC,
    CHART_SETTLE_SEC,
    CHART_TRACK,
    chart_look,
    chart_min_playable_seconds,
    chart_transition_seconds,
)
from ___visuals.maths._runner import MathsRender, run_cached_maths_render

_TRACK_W, _TRACK_H = 9.0, 0.66


def _build_scene(percent: float, label: str):
    """The manim Scene class, built here so importing this costs nothing."""
    from manim import (
        DOWN, RIGHT, UP, ChangeDecimalToValue, DecimalNumber, FadeIn,
        Rectangle, Scene, Text, ValueTracker, always_redraw, rate_functions,
    )

    class ProgressBarScene(Scene):
        def construct(self) -> None:
            track = Rectangle(
                width=_TRACK_W, height=_TRACK_H,
                fill_color=CHART_TRACK, fill_opacity=1.0, stroke_width=0,
            ).move_to(DOWN * 0.4)

            fill = ValueTracker(0.0)
            left = track.get_left()
            bar = always_redraw(
                lambda: Rectangle(
                    width=max(_TRACK_W * percent / 100 * fill.get_value(),
                              1e-3),
                    height=_TRACK_H,
                    fill_color=CHART_ACCENT, fill_opacity=1.0, stroke_width=0,
                ).next_to(left, RIGHT, buff=0)
            )

            # Pango, never LaTeX (AI_READ_THIS.txt point 3): mob_class=Text,
            # and the % sign is its own Text riding the number's live edge.
            number = DecimalNumber(
                0,
                mob_class=Text,
                num_decimal_places=0 if float(percent).is_integer() else 1,
                group_with_commas=False,
                color=CHART_ACCENT,
                font_size=96,
            ).move_to(UP * 0.9)
            sign = Text("%", font_size=64, color=CHART_ACCENT)
            sign.add_updater(
                lambda m: m.next_to(number, RIGHT, buff=0.1, aligned_edge=DOWN)
            )

            self.add(track, bar, number, sign)
            self.play(
                fill.animate.set_value(1.0),
                ChangeDecimalToValue(number, percent),
                run_time=CHART_ANIM_SEC,
                rate_func=rate_functions.ease_out_cubic,
            )
            if label:
                caption = Text(label, font_size=40, color=CHART_INK)
                caption.next_to(track, DOWN, buff=0.5)
                self.play(FadeIn(caption, shift=UP * 0.2),
                          run_time=CHART_LABEL_SEC)
            else:
                self.wait(CHART_LABEL_SEC)
            self.wait(CHART_SETTLE_SEC)

    return ProgressBarScene


def _cache_key(percent: float, label: str) -> str:
    """A name covering EVERY input to the render — the data and the look."""
    digest = hashlib.md5(
        repr((percent, label, chart_look())).encode()
    ).hexdigest()[:10]
    return f"progress_bar_{digest}"


def render_progress_bar(
    percent: float, out_dir: str, label: str = ""
) -> MathsRender:
    """Render the bar that fills to `percent`, into `out_dir`. Cached on disk
    under a key covering every input, like every maths type."""
    return run_cached_maths_render(
        kind="progress_bar",
        cache_key=_cache_key(float(percent), label),
        out_dir=out_dir,
        scene_factory=_build_scene(float(percent), label),
        background_colour=CHART_BACKGROUND,
        essential_ratio=chart_min_playable_seconds() / chart_transition_seconds(),
    )
