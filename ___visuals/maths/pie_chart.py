"""
The `pie_chart` media type: the pie sweeps itself on clockwise from twelve
o'clock, one slice per share, then every slice's label + computed percentage
fades in around it. Slices are the ONE chart mark that carries identity, so
this is the one renderer on CONFIG.CHART_PALETTE — assigned in its validated
order, with white gaps between slices and an ink label on every slice (the
relief the palette's two low-contrast slots require).

Writes the two artefacts every maths type writes (see ___visuals/maths and
AI_READ_THIS.txt): the transition mp4, and its last frame as a still.
"""
from __future__ import annotations

import hashlib
import math

from CONFIG import (
    CHART_ANIM_SEC,
    CHART_BACKGROUND,
    CHART_INK,
    CHART_LABEL_SEC,
    CHART_PALETTE,
    CHART_SETTLE_SEC,
    chart_look,
    chart_min_playable_seconds,
    chart_transition_seconds,
    parse_series,
)
from ___visuals.maths._runner import MathsRender, run_cached_maths_render

_RADIUS = 2.15
_LABEL_R = 1.42  # of the radius — where the slice labels sit


def _slices(pairs: list[tuple[str, float]]) -> list[tuple[str, float, str]]:
    """(label, fraction, colour) per NON-ZERO share, palette in fixed order.
    A zero share is dropped: a zero-width slice draws nothing and its label
    would only point at a seam."""
    total = sum(v for _, v in pairs)
    return [
        (label, value / total, CHART_PALETTE[i % len(CHART_PALETTE)])
        for i, (label, value) in enumerate(pairs)
        if value > 0
    ]


def _build_scene(pairs: list[tuple[str, float]], title: str):
    """The manim Scene class, built here so importing this costs nothing."""
    from manim import (
        DOWN, PI, RIGHT, TAU, UP, FadeIn, Scene, Sector, Text, ValueTracker,
        VGroup, always_redraw, rate_functions,
    )

    slices = _slices(pairs)
    centre = DOWN * 0.35  # leaves headroom for the title

    class PieChartScene(Scene):
        def construct(self) -> None:
            swept = ValueTracker(0.0)

            def pie_now() -> VGroup:
                """The pie as far as the sweep has got: each slice shows the
                part of its arc the leading edge has passed, so the whole pie
                draws on as ONE clockwise wipe from twelve o'clock."""
                reach = swept.get_value() * TAU
                group = VGroup()
                start = 0.0  # arc already swept before this slice
                for _, fraction, colour in slices:
                    angle = min(max(reach - start, 0.0), fraction * TAU)
                    if angle > 1e-4:
                        group.add(
                            Sector(
                                radius=_RADIUS,
                                start_angle=PI / 2 - start - angle,
                                angle=angle,
                                fill_color=colour,
                                fill_opacity=1.0,
                                # the white gap between slices (mark spec +
                                # colour-vision relief)
                                stroke_color=CHART_BACKGROUND,
                                stroke_width=3,
                            ).shift(centre)
                        )
                    start += fraction * TAU
                return group

            pie = always_redraw(pie_now)
            self.add(pie)
            if title:
                self.add(Text(title, font_size=40, color=CHART_INK)
                         .move_to(UP * 3.1))

            self.play(swept.animate.set_value(1.0),
                      run_time=CHART_ANIM_SEC,
                      rate_func=rate_functions.ease_in_out_sine)
            pie.clear_updaters()

            labels = VGroup()
            start = 0.0
            for label, fraction, _ in slices:
                mid = PI / 2 - (start + fraction / 2) * TAU
                at = (RIGHT * math.cos(mid) + UP * math.sin(mid)) \
                    * _RADIUS * _LABEL_R + centre
                labels.add(
                    Text(f"{label}  {fraction * 100:.0f}%",
                         font_size=30, color=CHART_INK).move_to(at)
                )
                start += fraction
            self.play(FadeIn(labels), run_time=CHART_LABEL_SEC)
            self.wait(CHART_SETTLE_SEC)

    return PieChartScene


def _cache_key(slices: str, title: str) -> str:
    """A name covering EVERY input to the render — the data and the look."""
    digest = hashlib.md5(
        repr((slices, title, chart_look())).encode()
    ).hexdigest()[:10]
    return f"pie_chart_{digest}"


def render_pie_chart(slices: str, out_dir: str, title: str = "") -> MathsRender:
    """Render the pie for `slices` ('label: value, …' — the canonical spelling
    CONFIG's shares kind stores), into `out_dir`. Cached on disk under a key
    covering every input, like every maths type."""
    pairs = parse_series(slices)
    return run_cached_maths_render(
        kind="pie_chart",
        cache_key=_cache_key(slices, title),
        out_dir=out_dir,
        scene_factory=_build_scene(pairs, title),
        background_colour=CHART_BACKGROUND,
        essential_ratio=chart_min_playable_seconds() / chart_transition_seconds(),
    )
