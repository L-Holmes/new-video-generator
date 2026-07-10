"""
___visuals/maths — manim animations built from a scene's `data` column.

One module per maths media type. Each exposes a single render function with
the SAME contract:

    render_<type>(**data, out_mp4: str, out_png: str) -> float

It writes the TRANSITION (the animation) to `out_mp4`, the FINAL STILL (the
animation's last frame, held) to `out_png`, and returns the transition's exact
duration in seconds.

That pair is the house pattern for every fixed-length animation in a
variable-length scene — see AI_READ_THIS.txt at the repo root. The generator in
SCENE_GENERATORS, not the renderer, decides which of the two a given scene
gets: a scene shorter than the transition would be cut off mid-animation, so it
shows the finished still instead; a longer one plays the transition and then
holds the still for the remainder.

Adding a type (a pie chart, a bar race, …):
  1. CONFIG: a MEDIA_TYPE_CATALOG entry tagged Tag.MATHS, and its inputs in
     MEDIA_TYPE_DATA_FIELDS.
  2. Here: a module with a render function following the contract above.
  3. SCENE_GENERATORS.generate_maths_scenes: one line in _MATHS_RENDERERS.
The tagger picks the new type up on its own — the maths tab is built from the
catalog.
"""
from ___visuals.maths.timeline import render_timeline

__all__ = ["render_timeline"]
