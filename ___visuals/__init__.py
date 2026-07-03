"""
___visuals/decorator — the ONE standalone, generic image-decoration package.

Call it from anywhere with a starting picture; it hands back the edited
picture. It knows NOTHING about scenes, rows, or the pipeline — the pipeline
adapters (DECORATE_STAGE, COLLAGE_STAGE) resolve footage, call this, and
bake the result to MP4.

    from ___visuals.decorator import run_decorator, auto_collage

    out = run_decorator(
        base_image_path="frame.png",       # the pic to start with
        out_path="edited.png",
        stamps=["coin.png", "jar.png"],    # pics to stamp on (optional)
        prefill_text="WORTH ITS WEIGHT",   # offered to the text tool
    )                                       # -> path, or None if cancelled

    auto_collage(["a.png", "b.png", "c.png"], "collage.png", seed="scene 4")

Tools (clickable per session): stamp (absorbs the old manual stock
placement), zoom (the old zoom_previous), draw + text (wired once
DECORATE_PREVIOUS.py / MAKE_TEXT_OVERLAY.py are shared — see tools.py).
"""
from ___visuals.decorator.api import run_decorator            # noqa: F401
from ___visuals.decorator.auto_collage import auto_collage    # noqa: F401
