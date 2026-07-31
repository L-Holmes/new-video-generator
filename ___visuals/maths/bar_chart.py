"""
The `bar_chart` media type: labelled bars grow up from a baseline one after
another, then their values pop on top. Compares a few quantities — the bars
all wear the one accent colour, because they are one measure (many-coloured
bars would claim an identity difference the data doesn't have).

Writes the two artefacts every maths type writes (see ___visuals/maths and
AI_READ_THIS.txt): the transition mp4, and its last frame as a still.
"""
from __future__ import annotations

import hashlib

from CONFIG import (
    CHART_ACCENT,
    CHART_ANIM_SEC,
    CHART_BACKGROUND,
    CHART_BASELINE,
    CHART_INK,
    CHART_LABEL_SEC,
    CHART_SETTLE_SEC,
    chart_look,
    chart_min_playable_seconds,
    chart_transition_seconds,
    format_chart_value,
    parse_series,
)
from ___visuals.maths._runner import MathsRender, run_cached_maths_render

# The plot area: bars live between the baseline and the headroom the value
# labels and the title need.
_SPAN_W = 10.4
_BASE_Y, _TOP_Y = -2.3, 1.7


def _build_scene(pairs: list[tuple[str, float]], title: str):
    """The manim Scene class, built here so importing this costs nothing."""
    from manim import (
        DOWN, LEFT, RIGHT, UP, FadeIn, LaggedStart, Line, Rectangle, Restore,
        Scene, Text, VGroup,
    )

    class BarChartScene(Scene):
        def construct(self) -> None:
            slot = _SPAN_W / len(pairs)
            bar_w = min(1.5, slot * 0.62)
            top = max(v for _, v in pairs)
            xs = [-_SPAN_W / 2 + slot * (i + 0.5) for i in range(len(pairs))]

            baseline = Line(
                LEFT * (_SPAN_W / 2 + 0.35) + UP * _BASE_Y,
                RIGHT * (_SPAN_W / 2 + 0.35) + UP * _BASE_Y,
                color=CHART_BASELINE, stroke_width=2.5,
            )

            bars, cat_labels, value_labels = VGroup(), VGroup(), VGroup()
            for x, (label, value) in zip(xs, pairs):
                # a zero bar keeps a sliver so its labels still have an anchor
                h = max(value / top * (_TOP_Y - _BASE_Y), 0.02)
                bar = Rectangle(
                    width=bar_w, height=h,
                    fill_color=CHART_ACCENT, fill_opacity=1.0, stroke_width=0,
                )
                bar.move_to(RIGHT * x + UP * (_BASE_Y + h / 2))
                bars.add(bar)

                cat = Text(label, font_size=30, color=CHART_INK)
                if cat.width > slot * 0.95:
                    cat.scale_to_fit_width(slot * 0.95)
                cat.move_to(RIGHT * x + UP * (_BASE_Y - 0.35))
                cat_labels.add(cat)

                val = Text(format_chart_value(value), font_size=32,
                           color=CHART_INK)
                val.move_to(RIGHT * x + UP * (_BASE_Y + h + 0.28))
                value_labels.add(val)

            self.add(baseline, cat_labels)
            if title:
                self.add(Text(title, font_size=40, color=CHART_INK)
                         .move_to(UP * 3.1))

            # grow each bar up from the baseline, staggered left to right
            grows = []
            for bar in bars:
                bar.save_state()
                bar.stretch(1e-3, 1, about_edge=DOWN)
                grows.append(Restore(bar))
            self.add(bars)
            self.play(LaggedStart(*grows, lag_ratio=0.15),
                      run_time=CHART_ANIM_SEC)
            self.play(FadeIn(value_labels, shift=UP * 0.2),
                      run_time=CHART_LABEL_SEC)
            self.wait(CHART_SETTLE_SEC)

    return BarChartScene


def _cache_key(bars: str, title: str) -> str:
    """A name covering EVERY input to the render — the data and the look."""
    digest = hashlib.md5(
        repr((bars, title, chart_look())).encode()
    ).hexdigest()[:10]
    return f"bar_chart_{digest}"


def render_bar_chart(bars: str, out_dir: str, title: str = "") -> MathsRender:
    """Render the bar chart for `bars` ('label: value, …' — the canonical
    spelling CONFIG's shares kind stores), into `out_dir`. Cached on disk
    under a key covering every input, like every maths type."""
    pairs = parse_series(bars)
    return run_cached_maths_render(
        kind="bar_chart",
        cache_key=_cache_key(bars, title),
        out_dir=out_dir,
        scene_factory=_build_scene(pairs, title),
        background_colour=CHART_BACKGROUND,
        essential_ratio=chart_min_playable_seconds() / chart_transition_seconds(),
    )
