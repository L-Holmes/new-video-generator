"""
The `line_graph` media type: a trend line draws itself left to right across
the scene's labelled points, then the point markers and the FINAL value pop —
the reveal is where the trend ends up. Labels are the x axis (years,
quarters), evenly spaced; the y axis stays implicit — the shape and the end
value are the message, not coordinate look-ups.

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
    CHART_MUTED,
    CHART_SETTLE_SEC,
    chart_look,
    chart_min_playable_seconds,
    chart_transition_seconds,
    format_chart_value,
    parse_series,
)
from ___visuals.maths._runner import MathsRender, run_cached_maths_render

# The plot area: x span of the points, y band the values are scaled into,
# with the baseline + x labels below and title headroom above.
_SPAN_W = 10.0
_LO_Y, _HI_Y = -1.9, 1.6
_BASE_Y = -2.45


def _positions(pairs: list[tuple[str, float]]) -> list[tuple[float, float]]:
    """Each point's (x, y) on the frame: labels evenly spaced across the
    span, values scaled into the y band with padding so neither extreme sits
    on the band's very edge. A flat series draws mid-band."""
    n = len(pairs)
    xs = [-_SPAN_W / 2 + _SPAN_W * i / (n - 1) for i in range(n)]
    values = [v for _, v in pairs]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    lo, hi = lo - span * 0.08, hi + span * 0.08
    return [
        (x, _LO_Y + (v - lo) / (hi - lo) * (_HI_Y - _LO_Y))
        for x, v in zip(xs, values)
    ]


def _build_scene(pairs: list[tuple[str, float]], title: str):
    """The manim Scene class, built here so importing this costs nothing."""
    from manim import (
        DOWN, LEFT, RIGHT, UP, Create, Dot, FadeIn, Line, Scene, Text,
        VGroup, VMobject, rate_functions,
    )

    class LineGraphScene(Scene):
        def construct(self) -> None:
            points = _positions(pairs)

            baseline = Line(
                LEFT * (_SPAN_W / 2 + 0.4) + UP * _BASE_Y,
                RIGHT * (_SPAN_W / 2 + 0.4) + UP * _BASE_Y,
                color=CHART_BASELINE, stroke_width=2.5,
            )
            x_labels = VGroup()
            slot = _SPAN_W / (len(pairs) - 1)
            for (x, _), (label, _v) in zip(points, pairs):
                lab = Text(label, font_size=28, color=CHART_MUTED)
                if lab.width > slot * 0.9:
                    lab.scale_to_fit_width(slot * 0.9)
                lab.move_to(RIGHT * x + UP * (_BASE_Y - 0.35))
                x_labels.add(lab)

            trend = VMobject(stroke_color=CHART_ACCENT, stroke_width=6)
            trend.set_points_as_corners(
                [RIGHT * x + UP * y for x, y in points]
            )
            dots = VGroup(*[
                Dot(RIGHT * x + UP * y, radius=0.07, color=CHART_ACCENT)
                for x, y in points
            ])
            end_x, end_y = points[-1]
            end_value = Text(format_chart_value(pairs[-1][1]), font_size=44,
                             color=CHART_ACCENT)
            end_value.next_to(RIGHT * end_x + UP * end_y,
                              UP + LEFT * 0.3, buff=0.25)

            self.add(baseline, x_labels)
            if title:
                self.add(Text(title, font_size=40, color=CHART_INK)
                         .move_to(UP * 3.1))

            self.play(Create(trend), run_time=CHART_ANIM_SEC,
                      rate_func=rate_functions.ease_in_out_sine)
            self.play(FadeIn(dots), FadeIn(end_value, shift=UP * 0.2),
                      run_time=CHART_LABEL_SEC)
            self.wait(CHART_SETTLE_SEC)

    return LineGraphScene


def _cache_key(points: str, title: str) -> str:
    """A name covering EVERY input to the render — the data and the look."""
    digest = hashlib.md5(
        repr((points, title, chart_look())).encode()
    ).hexdigest()[:10]
    return f"line_graph_{digest}"


def render_line_graph(points: str, out_dir: str, title: str = "") -> MathsRender:
    """Render the trend line through `points` ('label: value, …' — the
    canonical spelling CONFIG's series kind stores), into `out_dir`. Cached
    on disk under a key covering every input, like every maths type."""
    pairs = parse_series(points)
    return run_cached_maths_render(
        kind="line_graph",
        cache_key=_cache_key(points, title),
        out_dir=out_dir,
        scene_factory=_build_scene(pairs, title),
        background_colour=CHART_BACKGROUND,
        essential_ratio=chart_min_playable_seconds() / chart_transition_seconds(),
    )
