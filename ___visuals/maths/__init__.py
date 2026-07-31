"""
___visuals/maths — manim animations built from a scene's `data` column.

One module per maths media type. Each exposes a single render function with
the SAME contract:

    render_<type>(**data, out_mp4: str, out_png: str) -> MathsRender

It writes the TRANSITION (the animation) to `out_mp4` and the FINAL STILL (the
animation's last frame) to `out_png`, and reports both the animation's real
duration and its `min_playable_secs` — the shortest cut that still says
something, everything past which is a trailing settle beat safe to trim.

That pair is the house pattern for every fixed-length animation in a
variable-length scene — see AI_READ_THIS.txt at the repo root. The generator in
SCENE_GENERATORS, not the renderer, decides what a given scene gets: too short
for even min_playable and it shows the finished still; long enough for the
animation but not its settle and it plays the animation trimmed; longer still
and it plays the animation, then holds the still for the remainder.

Keep min_playable_secs HONEST and the animation SHORT. Narration lines are
brief — a median line is around 1.3 seconds — so an animation that takes three
seconds to make its point is one that will almost always be replaced by its own
still.

Adding a type (a pie chart, a bar race, …):
  1. CONFIG: a MEDIA_TYPE_CATALOG entry tagged Tag.MATHS, and its inputs in
     MEDIA_TYPE_DATA_FIELDS.
  2. Here: a module with a render function following the contract above.
  3. SCENE_GENERATORS.generate_maths_scenes: one line in _MATHS_RENDERERS.
The tagger picks the new type up on its own — the maths tab is built from the
catalog.
"""
from ___visuals.maths._runner import MathsRender
from ___visuals.maths.bar_chart import render_bar_chart
from ___visuals.maths.counter import render_counter
from ___visuals.maths.line_graph import render_line_graph
from ___visuals.maths.pie_chart import render_pie_chart
from ___visuals.maths.progress_bar import render_progress_bar
from ___visuals.maths.timeline import render_timeline

__all__ = [
    "MathsRender",
    "render_bar_chart",
    "render_counter",
    "render_line_graph",
    "render_pie_chart",
    "render_progress_bar",
    "render_timeline",
]
