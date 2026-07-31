"""
The `counter` media type: a big number ticks up from 0 to the value the scene
asked for, wearing an optional prefix / suffix ('$', '%', ' million'), then a
small caption fades in underneath. The workhorse for any single narrated stat.

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
    chart_look,
    chart_min_playable_seconds,
    chart_transition_seconds,
)
from ___visuals.maths._runner import MathsRender, run_cached_maths_render


def _decimal_places(value: float) -> int:
    """How many decimals the tick-up shows: what the value itself carries,
    capped at 2 — '3.5' ticks with one decimal, a whole number with none.
    (Never inferred from '%g', which spells 1500000 as '1.5e+06'.)"""
    if float(value).is_integer():
        return 0
    return 1 if (value * 10).is_integer() else 2


def _build_scene(value: float, prefix: str, suffix: str, label: str):
    """The manim Scene class, built here so `import counter` costs nothing."""
    from manim import (
        DOWN, LEFT, RIGHT, UP, DecimalNumber, FadeIn, ORIGIN, Scene, Text,
        ValueTracker, rate_functions,
    )

    class CounterScene(Scene):
        def construct(self) -> None:
            # Pango, never LaTeX (AI_READ_THIS.txt point 3): mob_class=Text.
            number = DecimalNumber(
                0,
                mob_class=Text,
                num_decimal_places=_decimal_places(value),
                group_with_commas=True,
                color=CHART_ACCENT,
                font_size=140,
            )
            anchor = ORIGIN + (UP * 0.35 if label else ORIGIN)
            # set_value keeps the LEFT edge, so a growing number would walk
            # off the right of the frame — re-centre on the anchor each frame.
            ticker = ValueTracker(0.0)

            def tick(m) -> None:
                m.set_value(ticker.get_value())
                m.move_to(anchor)

            number.add_updater(tick)
            tick(number)

            # The digits widen as the number grows; the units hang off the
            # live edges so '$' and '%' ride along instead of being overrun.
            fringes = []
            if prefix:
                p = Text(prefix, font_size=112, color=CHART_ACCENT)
                p.add_updater(
                    lambda m: m.next_to(number, LEFT, buff=0.18,
                                        aligned_edge=DOWN)
                )
                fringes.append(p)
            if suffix:
                s = Text(suffix, font_size=112, color=CHART_ACCENT)
                s.add_updater(
                    lambda m: m.next_to(number, RIGHT, buff=0.18,
                                        aligned_edge=DOWN)
                )
                fringes.append(s)

            self.add(number, *fringes)
            self.play(
                ticker.animate.set_value(value),
                run_time=CHART_ANIM_SEC,
                rate_func=rate_functions.ease_out_cubic,
            )
            number.remove_updater(tick)
            if label:
                caption = Text(label, font_size=44, color=CHART_INK)
                caption.next_to(number, DOWN, buff=0.6)
                caption.set_x(anchor[0])
                self.play(FadeIn(caption, shift=UP * 0.25),
                          run_time=CHART_LABEL_SEC)
            else:
                self.wait(CHART_LABEL_SEC)
            self.wait(CHART_SETTLE_SEC)

    return CounterScene


def _cache_key(value: float, prefix: str, suffix: str, label: str) -> str:
    """A name covering EVERY input to the render — the data and the look."""
    digest = hashlib.md5(
        repr((value, prefix, suffix, label, chart_look())).encode()
    ).hexdigest()[:10]
    return f"counter_{digest}"


def render_counter(
    value: float,
    out_dir: str,
    prefix: str = "",
    suffix: str = "",
    label: str = "",
) -> MathsRender:
    """Render the counter that ticks up to `value`, into `out_dir`. Cached on
    disk under a key covering every input, like every maths type."""
    return run_cached_maths_render(
        kind="counter",
        cache_key=_cache_key(float(value), prefix, suffix, label),
        out_dir=out_dir,
        scene_factory=_build_scene(float(value), prefix, suffix, label),
        background_colour=CHART_BACKGROUND,
        essential_ratio=chart_min_playable_seconds() / chart_transition_seconds(),
    )
