"""
___visuals/decorator — the ONE image-decoration package: everything where
you manually edit a picture is this ONE window.

    draw.py          the window: draw canvas + tabs (STAMP / ZOOM / OBJECT)
                     — stamps drop like text items, zoom crops in place,
                     object opens the extraction editor on top
    api.py           run_decorator(): base pic in -> edited pic out
    auto_collage.py  headless scatter collage (no interaction)

    from ___visuals.decorator import run_decorator, auto_collage

Direct run:  uv run ___visuals/decorator/api.py PIC.png
"""
from ___visuals.decorator.api import run_decorator            # noqa: F401
from ___visuals.decorator.auto_collage import auto_collage    # noqa: F401
