"""
The `timeline` media type: a marker sets off from the CURRENT year, travels
along a dated line to the year the scene asked for, and the year it landed on
pops up beneath it.

Writes the two artefacts every maths type writes (see ___visuals/maths and
AI_READ_THIS.txt): the transition mp4, and its last frame as a still.
"""
from __future__ import annotations

import datetime
import hashlib
from pathlib import Path

from CONFIG import (
    TIMELINE_ACCENT,
    TIMELINE_BACKGROUND,
    TIMELINE_FPS,
    TIMELINE_INK,
    TIMELINE_LABEL_SEC,
    TIMELINE_RESOLUTION,
    TIMELINE_SETTLE_SEC,
    TIMELINE_TICKS,
    TIMELINE_TRAVEL_SEC,
    timeline_min_playable_seconds,
    timeline_transition_seconds,
)
from ___visuals.maths._runner import (
    MathsRender,
    extract_last_frame,
    probe_duration,
    render_manim_scene,
)


def current_year() -> int:
    """Where the marker sets off from. Read from the clock, never tagged — the
    tagger only ever asks for the destination."""
    return datetime.date.today().year


def _axis_bounds(start_year: int, target_year: int) -> tuple[float, float]:
    """The line's two ends, with a margin so neither year sits on the very edge.
    A timeline TO the current year would otherwise have zero span."""
    lo, hi = float(min(start_year, target_year)), float(max(start_year, target_year))
    span = hi - lo
    if span < 1.0:  # "back to this year" — draw a decade around it
        lo, hi = lo - 5.0, hi + 5.0
        span = hi - lo
    margin = span * 0.08
    return lo - margin, hi + margin


# Years people actually mark a timeline with. The renderer takes the smallest
# one that keeps the label count near TIMELINE_TICKS, so a 400-year journey is
# marked per century and a 30-year one per decade.
_NICE_STEPS = (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000)


def _tick_years(lo: float, hi: float, target_year: int, start_year: int) -> list[int]:
    """The years to LABEL: round numbers inside the axis, clear of both ends.

    The target is what the animation REVEALS — printing it on the axis from
    frame one gives the ending away, and the reveal then lands on top of a
    label already saying the same thing. The start year gets its own label
    under the marker. A round year sitting too CLOSE to either of those prints
    on top of it (2000 and 2026 collide on a four-century line), so it goes
    too."""
    span = hi - lo
    step = next(
        (s for s in _NICE_STEPS if span / s <= TIMELINE_TICKS),
        _NICE_STEPS[-1],
    )
    first = int(-(-lo // step)) * step  # ceil to the step, integer-only
    clearance = step * 0.7
    return [
        y
        for y in range(first, int(hi) + 1, step)
        if lo <= y <= hi
        and abs(y - target_year) > clearance
        and abs(y - start_year) > clearance
    ]


def _build_scene(start_year: int, target_year: int):
    """The manim Scene class, built here so `import timeline` costs nothing."""
    from manim import (
        DOWN, UP, DecimalNumber, Dot, FadeIn, NumberLine, Scene, Text,
        Triangle, VGroup,
    )

    # Every number on screen is rendered with Pango (Text), not LaTeX.
    # DecimalNumber reaches for MathTex by default, which shells out to
    # `latex` — a dependency this repo does not have and does not want. The
    # NumberLine names the same class `label_constructor` and passes it — and
    # `font_size` — down itself, so its tick config must repeat neither key.
    _NUMBER_STYLE = {
        "num_decimal_places": 0,
        "group_with_commas": False,  # years: 1600, never 1,600
    }

    lo, hi = _axis_bounds(start_year, target_year)

    class TimelineScene(Scene):
        def construct(self) -> None:
            labels = _tick_years(lo, hi, target_year, start_year)
            line = NumberLine(
                x_range=[lo, hi],
                length=11.5,
                # Both are ours: an evenly-spaced x_range step would scatter
                # tick marks across the line at years nobody labelled, sitting
                # between the numbers instead of under them.
                include_ticks=False,
                include_numbers=False,
                label_constructor=Text,
                font_size=30,
                decimal_number_config=dict(_NUMBER_STYLE),
                color=TIMELINE_INK,
                stroke_width=3,
            )
            # a mark under every label, and one at each end of the journey so
            # the marker sets off from a tick and lands on one
            for year in sorted({*labels, start_year, target_year}):
                line.add(line.get_tick(year))
            line.add_numbers(labels, font_size=30)
            line.numbers.set_color(TIMELINE_INK)

            def at(year: float):
                return line.number_to_point(year)

            marker = VGroup(
                Triangle(color=TIMELINE_ACCENT, fill_opacity=1.0)
                .scale(0.16)
                .rotate(180 * 3.141592653589793 / 180),  # point it downwards
                Dot(color=TIMELINE_ACCENT, radius=0.07),
            )
            marker[0].next_to(marker[1], UP, buff=0.02)
            marker.move_to(at(start_year))

            # where the journey sets off from — the round ticks skip it (2026
            # is not a round year), so without this the start is unlabelled
            origin = Text(str(start_year), font_size=30, color=TIMELINE_INK)
            origin.next_to(at(start_year), DOWN, buff=0.28)

            # the year that lands under the marker at the end of the journey
            landed = DecimalNumber(
                target_year,
                mob_class=Text,
                **_NUMBER_STYLE,
                color=TIMELINE_ACCENT,
                font_size=64,
            )
            landed.next_to(at(target_year), DOWN, buff=0.55)

            # The line and the marker are on screen from frame one: the whole
            # transition budget belongs to the journey, so the durations below
            # add up to exactly timeline_transition_seconds().
            self.add(line, origin, marker)
            self.play(
                marker.animate.move_to(at(target_year)),
                run_time=TIMELINE_TRAVEL_SEC,
            )
            self.play(FadeIn(landed, shift=UP * 0.3), run_time=TIMELINE_LABEL_SEC)
            self.wait(TIMELINE_SETTLE_SEC)

    return TimelineScene


def _cache_key(start_year: int, target_year: int) -> str:
    """A name covering EVERY input to the render.

    The years, obviously — including the start one, because it comes from the
    clock and a run in January must not reuse December's line. But also the
    look and the timings: shorten TIMELINE_TRAVEL_SEC and the old render is a
    different video that happens to be about the same two years. A cache key
    that misses an input is a cache that serves stale work forever.
    """
    look = (TIMELINE_TRAVEL_SEC, TIMELINE_LABEL_SEC, TIMELINE_SETTLE_SEC,
            TIMELINE_FPS, TIMELINE_RESOLUTION, TIMELINE_TICKS,
            TIMELINE_BACKGROUND, TIMELINE_INK, TIMELINE_ACCENT)
    digest = hashlib.md5(repr(look).encode()).hexdigest()[:8]
    return f"timeline_{start_year}_to_{target_year}_{digest}"


def render_timeline(year: int, out_dir: str) -> MathsRender:
    """Render the timeline that travels back to `year`, into `out_dir`.

    Both artefacts are CACHED on disk under a key covering every input, so two
    scenes asking for the same year reuse one render (manim takes tens of
    seconds) but a config change re-renders.
    """
    target_year = int(year)
    start_year = current_year()
    key = _cache_key(start_year, target_year)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    mp4, png = out / f"{key}.mp4", out / f"{key}.png"

    if mp4.exists() and png.exists():
        print(f"[timeline]   cached: {mp4.name}")
    else:
        print(f"[timeline]   rendering {start_year} → {target_year} (manim)")
        render_manim_scene(
            scene_factory=_build_scene(start_year, target_year),
            out_mp4=str(mp4),
            background_colour=TIMELINE_BACKGROUND,
        )
        extract_last_frame(str(mp4), str(png))
        print(f"[timeline]   ✓ transition {mp4.name} + final still {png.name}")

    # The encoder lands a frame or two off the durations we asked manim for, so
    # measure the file and scale the essential part by the same ratio rather
    # than trusting the config arithmetic.
    natural = probe_duration(str(mp4))
    ratio = timeline_min_playable_seconds() / timeline_transition_seconds()
    return MathsRender(
        transition_mp4=str(mp4),
        still_png=str(png),
        transition_secs=natural,
        min_playable_secs=natural * ratio,
    )
