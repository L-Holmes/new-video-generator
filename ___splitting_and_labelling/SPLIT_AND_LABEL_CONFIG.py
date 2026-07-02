"""
SPLIT_AND_LABEL_CONFIG.py  —  the media vocabulary of the labelling pipeline
============================================================================
This file is deliberately SEPARATE from ___visuals/CONFIG.py.  The renderer
keeps its own MediaType untouched; this file defines the vocabulary the
DECISION side thinks in, plus the bridge that translates back to the
renderer's legacy strings.  Change this file freely — the renderer only ever
sees the output of `to_legacy()`.

THE FOUR AXES
-------------
Every "media type" the old system had is really a point in a 4-axis space:

    STRATEGY   what we do            new / edit_previous
    MATERIAL   what we fetch         stock / stock_image / wikipedia / map / none
    LAYOUT     how it's arranged     single / grid(n, same_term?) /
                                     composite_onto_previous / crop_zoom / freeze
    OVERLAY    what goes on top      none / auto_text / draw / object_edit

TAXONOMY TABLE  (old flat enum -> axis coordinates)
---------------------------------------------------
    old media type                 strategy        material     layout                    overlay
    ---------------------------   -------------   ----------   -----------------------   -----------
    stock                         new             stock        single                    none
    wikipedia                     new             wikipedia    single                    none
    map                           new             map          single                    none
    object_generate               new             stock_image  single                    object_edit
    joint_3_row                   new             stock        grid(3, different)        none
    (NEW) grid_same               new             stock        grid(3, SAME term)        none
    manual_stock_add_to_previous  edit_previous   stock        composite_onto_previous   none
    zoom_prev_img                 edit_previous   none         crop_zoom                 none
    static_of_previous            edit_previous   none         freeze                    none
    decorate_previous             edit_previous   none         freeze                    draw
    read_out                      ***REMOVED***   —            —                         —

WHY read_out IS GONE
--------------------
The narration is read out over EVERY line.  "read_out" as a media type only
ever meant "change nothing on screen" — which is exactly hold_previous
(the old static_of_previous).  Legacy inputs saying "read_out" are folded
into hold_previous by LEGACY_TO_TEMPLATE below.

OVERLAYS ARE A MODIFIER, NOT A TYPE
-----------------------------------
Any shot may carry an overlay.  `decorate_previous` survives as a TEMPLATE
(a convenient named preset = edit_previous + freeze + draw), but nothing
stops a decision attaching `overlay=auto_text` to a fresh stock shot.  The
"do I draw on top by default?" question becomes a per-shot field with a
default, not an architectural fork.

SHOT TEMPLATES
--------------
Tier-3 sampling (and the weights table) doesn't want to reason over a 4-D
space — it wants a small named menu.  SHOT_TEMPLATES is that menu: each name
is a preset ShotSpec.  Adding a new choosable look = add one line here, one
line in TEMPLATE_TO_LEGACY, and (optionally) a column in the weights
generator.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Dict, Optional


# =============================================================================
# AXIS 1 — STRATEGY
# =============================================================================
class Strategy(str, Enum):
    NEW = "new"                        # fetch / render fresh material
    EDIT_PREVIOUS = "edit_previous"    # act on the previous scene's image


# =============================================================================
# AXIS 2 — MATERIAL
# =============================================================================
class Material(str, Enum):
    STOCK = "stock"                # pexels video-or-image
    STOCK_IMAGE = "stock_image"    # image only (feeds the object editor)
    WIKIPEDIA = "wikipedia"        # wikipedia image
    MAP = "map"                    # locally-rendered highlighted map
    NONE = "none"                  # nothing new — reuse the previous scene


# =============================================================================
# AXIS 3 — LAYOUT
# =============================================================================
class LayoutKind(str, Enum):
    SINGLE = "single"
    GRID = "grid"                                   # n cells side by side
    COMPOSITE_ONTO_PREVIOUS = "composite_onto_previous"
    CROP_ZOOM = "crop_zoom"
    FREEZE = "freeze"


@dataclass(frozen=True)
class Layout:
    kind: LayoutKind = LayoutKind.SINGLE
    n: int = 1                 # cell count (grid only)
    same_term: bool = False    # grid only: every cell uses the SAME search term


# =============================================================================
# AXIS 4 — OVERLAY
# =============================================================================
class Overlay(str, Enum):
    NONE = "none"
    AUTO_TEXT = "auto_text"    # auto-added on-screen text
    DRAW = "draw"              # interactive decorate tools
    OBJECT_EDIT = "object_edit"  # bg-separation editor + effects


# =============================================================================
# A SHOT = one point in the 4-axis space
# =============================================================================
@dataclass(frozen=True)
class ShotSpec:
    strategy: Strategy
    material: Material
    layout: Layout = field(default_factory=Layout)
    overlay: Overlay = Overlay.NONE

    def to_dict(self) -> Dict:
        """JSON-friendly view for the output rows / review sheet."""
        d = asdict(self)
        d["strategy"] = self.strategy.value
        d["material"] = self.material.value
        d["layout"]["kind"] = self.layout.kind.value
        d["overlay"] = self.overlay.value
        return d


# =============================================================================
# SHOT TEMPLATES — the named menu the decision engine chooses from
# =============================================================================
GRID_CELLS = 3   # default cell count for grid templates (was JOINT_CELLS)

SHOT_TEMPLATES: Dict[str, ShotSpec] = {
    # ---- strategy NEW -------------------------------------------------------
    "stock":            ShotSpec(Strategy.NEW, Material.STOCK),
    "wikipedia":        ShotSpec(Strategy.NEW, Material.WIKIPEDIA),
    "map":              ShotSpec(Strategy.NEW, Material.MAP),
    "object_generate":  ShotSpec(Strategy.NEW, Material.STOCK_IMAGE,
                                 overlay=Overlay.OBJECT_EDIT),
    "grid_different":   ShotSpec(Strategy.NEW, Material.STOCK,
                                 Layout(LayoutKind.GRID, GRID_CELLS, False)),
    "grid_same":        ShotSpec(Strategy.NEW, Material.STOCK,
                                 Layout(LayoutKind.GRID, GRID_CELLS, True)),
    # ---- strategy EDIT_PREVIOUS --------------------------------------------
    "composite_onto_previous":
                        ShotSpec(Strategy.EDIT_PREVIOUS, Material.STOCK,
                                 Layout(LayoutKind.COMPOSITE_ONTO_PREVIOUS)),
    "zoom_previous":    ShotSpec(Strategy.EDIT_PREVIOUS, Material.NONE,
                                 Layout(LayoutKind.CROP_ZOOM)),
    "hold_previous":    ShotSpec(Strategy.EDIT_PREVIOUS, Material.NONE,
                                 Layout(LayoutKind.FREEZE)),
    "decorate_previous": ShotSpec(Strategy.EDIT_PREVIOUS, Material.NONE,
                                  Layout(LayoutKind.FREEZE),
                                  overlay=Overlay.DRAW),
}

# Derived groupings — always compute from the axes, never hand-maintain lists.
PREVIOUS_FAMILY = {name for name, s in SHOT_TEMPLATES.items()
                   if s.strategy is Strategy.EDIT_PREVIOUS}
GRID_TEMPLATES = {name for name, s in SHOT_TEMPLATES.items()
                  if s.layout.kind is LayoutKind.GRID}
FRESH_MATERIAL_TEMPLATES = {name for name, s in SHOT_TEMPLATES.items()
                            if s.material is not Material.NONE}


# =============================================================================
# LEGACY BRIDGE — translate to/from the renderer's old MediaType strings
# =============================================================================
# The renderer still consumes the OLD strings in the `search_type` column.
# Nothing downstream breaks while the decision side lives in the new model.

TEMPLATE_TO_LEGACY: Dict[str, str] = {
    "stock":                    "stock",
    "wikipedia":                "wikipedia",
    "map":                      "map",
    "object_generate":          "object_generate",
    "grid_different":           "joint_3_row",
    # The renderer has no same-term grid yet; joint_3_row is the closest
    # behaviour (the SAME search term is simply emitted for every cell).
    "grid_same":                "joint_3_row",
    "composite_onto_previous":  "manual_stock_add_to_previous",
    "zoom_previous":            "zoom_prev_img",
    "hold_previous":            "static_of_previous",
    "decorate_previous":        "decorate_previous",
}

LEGACY_TO_TEMPLATE: Dict[str, str] = {
    "stock":                        "stock",
    "wikipedia":                    "wikipedia",
    "map":                          "map",
    "object_generate":              "object_generate",
    "joint_3_row":                  "grid_different",
    "manual_stock_add_to_previous": "composite_onto_previous",
    "zoom_prev_img":                "zoom_previous",
    "static_of_previous":           "hold_previous",
    "decorate_previous":            "decorate_previous",
    # read_out is retired: it never changed the screen, which is exactly
    # what hold_previous means.  (See the module docstring.)
    "read_out":                     "hold_previous",
}


def to_legacy(template: str) -> str:
    """Template name -> the renderer's legacy MediaType string."""
    return TEMPLATE_TO_LEGACY[template]


def from_legacy(legacy: str) -> str:
    """Legacy MediaType string -> template name (folds read_out away)."""
    return LEGACY_TO_TEMPLATE[legacy]


# Sanity: every template must round-trip to a legacy string, and every
# legacy string must resolve to a real template.  Fails loudly at import
# time, which is exactly when you want to find out.
assert set(TEMPLATE_TO_LEGACY) == set(SHOT_TEMPLATES), \
    "TEMPLATE_TO_LEGACY out of sync with SHOT_TEMPLATES"
assert set(LEGACY_TO_TEMPLATE.values()) <= set(SHOT_TEMPLATES), \
    "LEGACY_TO_TEMPLATE points at unknown templates"
