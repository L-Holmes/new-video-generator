"""
SPLIT_AND_LABEL_CONFIG.py  —  THE single place to define/tune media types
==========================================================================
Separate from ___visuals/CONFIG.py on purpose: the renderer keeps its own
MediaType untouched; the decision side thinks in this vocabulary and the
legacy bridge translates on emit.

>>> ADDING / CHANGING A MEDIA TYPE = EDIT *ONE* TemplateDef ENTRY BELOW <<<
Everything else (legacy bridge, requirements, lock-only sets, pacing
priors, AI gating, derived groupings) is computed from TEMPLATE_DEFS.
See EXTENDING_GUIDE.md for the step-by-step.

NAMING CONVENTION  (strategy prefix, double underscore)
-------------------------------------------------------
    new__*        brand-new material reaches the screen
                  (stock and AI generation are BOTH "brand new" — the
                  prefix groups them, the material axis separates them)
    editprev__*   the previous scene's image is edited / annotated / held
    editgroup__*  a run of related cells built as one visual group — the
                  first cell is unique, the rest build alongside it.
                  RULE OF N: the cell count comes from the detected list
                  size at emit time, not a hardcoded 3.  (The legacy
                  renderer still draws 3-cell rows — see
                  TODO_LEGACY_SWITCHOVER.md.)

THE FIVE AXES (every template is a point in this space)
-------------------------------------------------------
    STRATEGY   new / edit_previous
    MATERIAL   stock / stock_image / wikipedia / map / ai_stock / none
    BASE       none / stickman_board
    LAYOUT     single / grid(n, same_term?) / composite_onto_previous /
               crop_zoom / freeze / edit_in_place
    OVERLAY    none / auto_text / draw / object_edit

Overlays are a MODIFIER available to any shot, not a media type: "new stock
AND decorate it" is new__stock with overlay=draw — the axes compose.

EXPECTED PACING RATIO  (the "mostly small edits" doctrine)
----------------------------------------------------------
prior_opener / prior_cont on each TemplateDef ARE the expected-ratio knobs:
  • OPENERS (a line starting a sentence): mostly fresh material — stock
    (or ai stock) dominates.
  • CONTINUATIONS (mid-sentence lines): the video is fast-paced, so most
    lines should be SUBTLE EDITS of what's already on screen — hold, zoom,
    add-onto, caption — with fresh fetches the minority.
Tune the mix by editing those two numbers per template; CONT_FRESH_DAMP
below keeps per-rule weights from swamping the continuation mix.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Dict, Optional


class Strategy(str, Enum):
    NEW = "new"
    EDIT_PREVIOUS = "edit_previous"


class Material(str, Enum):
    STOCK = "stock"
    STOCK_IMAGE = "stock_image"
    WIKIPEDIA = "wikipedia"
    MAP = "map"
    AI_STOCK = "ai_stock"          # AI-generated art (the stickman look)
    NONE = "none"


class Base(str, Enum):
    NONE = "none"
    STICKMAN_BOARD = "stickman_board"


class LayoutKind(str, Enum):
    SINGLE = "single"
    GRID = "grid"
    COMPOSITE_ONTO_PREVIOUS = "composite_onto_previous"
    CROP_ZOOM = "crop_zoom"
    FREEZE = "freeze"
    EDIT_IN_PLACE = "edit_in_place"


@dataclass(frozen=True)
class Layout:
    kind: LayoutKind = LayoutKind.SINGLE
    n: int = 1                 # editgroup cell count — overridden at emit
    same_term: bool = False    # editgroup: same search term for every cell


class Overlay(str, Enum):
    NONE = "none"
    AUTO_TEXT = "auto_text"
    DRAW = "draw"
    OBJECT_EDIT = "object_edit"


@dataclass(frozen=True)
class ShotSpec:
    strategy: Strategy
    material: Material
    layout: Layout = field(default_factory=Layout)
    overlay: Overlay = Overlay.NONE
    base: Base = Base.NONE

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["strategy"] = self.strategy.value
        d["material"] = self.material.value
        d["layout"]["kind"] = self.layout.kind.value
        d["overlay"] = self.overlay.value
        d["base"] = self.base.value
        return d


# =============================================================================
# >>> THE MASTER TABLE — the ONE place a dev edits <<<
# =============================================================================
@dataclass(frozen=True)
class TemplateDef:
    spec: ShotSpec
    legacy: str                       # renderer string (AI off)
    legacy_ai: Optional[str] = None   # renderer string when AI is on
    requires: Optional[str] = None    # "named_thing_entity"/"place_entity"/"list"
    lock_only: bool = False           # only reachable via a tier-1 lock
    prior_opener: float = 0.02        # expected-ratio weight, sentence starts
    prior_cont: float = 0.02          # expected-ratio weight, mid-sentence


GRID_CELLS = 3   # legacy renderer cell count; real n comes from the list

_G = Layout(LayoutKind.GRID, GRID_CELLS, False)
_GS = Layout(LayoutKind.GRID, GRID_CELLS, True)
_N, _E = Strategy.NEW, Strategy.EDIT_PREVIOUS

TEMPLATE_DEFS: Dict[str, TemplateDef] = {
    # ---- new__* : brand-new material ---------------------------------------
    "new__stock": TemplateDef(
        ShotSpec(_N, Material.STOCK), legacy="stock",
        prior_opener=0.50, prior_cont=0.14),
    "new__ai_stock": TemplateDef(
        ShotSpec(_N, Material.AI_STOCK), legacy="stickman",
        prior_opener=0.45, prior_cont=0.12),
    "new__object": TemplateDef(
        ShotSpec(_N, Material.STOCK_IMAGE, overlay=Overlay.OBJECT_EDIT),
        legacy="object_generate", prior_opener=0.12, prior_cont=0.06),
    "new__wikipedia": TemplateDef(
        ShotSpec(_N, Material.WIKIPEDIA), legacy="wikipedia",
        requires="named_thing_entity", prior_opener=0.06, prior_cont=0.03),
    "new__map": TemplateDef(
        ShotSpec(_N, Material.MAP), legacy="map",
        requires="place_entity", prior_opener=0.05, prior_cont=0.03),
    "new__typography": TemplateDef(
        ShotSpec(_N, Material.NONE, overlay=Overlay.AUTO_TEXT),
        legacy="read_out", prior_opener=0.02, prior_cont=0.01),
    "new__stock_on_board": TemplateDef(
        ShotSpec(_N, Material.STOCK, base=Base.STICKMAN_BOARD),
        legacy="stickman_explain_stock", prior_opener=0.05, prior_cont=0.02),
    "new__wikipedia_on_board": TemplateDef(
        ShotSpec(_N, Material.WIKIPEDIA, base=Base.STICKMAN_BOARD),
        legacy="stickman_explain_wikipedia", requires="named_thing_entity",
        prior_opener=0.03, prior_cont=0.02),
    # ---- editprev__* : act on the previous image ---------------------------
    "editprev__hold": TemplateDef(
        ShotSpec(_E, Material.NONE, Layout(LayoutKind.FREEZE)),
        legacy="static_of_previous", prior_opener=0.02, prior_cont=0.28),
    "editprev__zoom": TemplateDef(
        ShotSpec(_E, Material.NONE, Layout(LayoutKind.CROP_ZOOM)),
        legacy="zoom_prev_img", prior_opener=0.02, prior_cont=0.16),
    "editprev__add_stock": TemplateDef(
        ShotSpec(_E, Material.STOCK, Layout(LayoutKind.COMPOSITE_ONTO_PREVIOUS)),
        legacy="manual_stock_add_to_previous",
        prior_opener=0.02, prior_cont=0.16),
    "editprev__ai_edit": TemplateDef(
        ShotSpec(_E, Material.AI_STOCK, Layout(LayoutKind.EDIT_IN_PLACE)),
        legacy="ai_edit", prior_opener=0.02, prior_cont=0.14),
    "editprev__caption": TemplateDef(
        ShotSpec(_E, Material.NONE, Layout(LayoutKind.FREEZE),
                 overlay=Overlay.AUTO_TEXT),
        legacy="decorate_previous", legacy_ai="stickman_text_overlay",
        prior_opener=0.02, prior_cont=0.10),
    "editprev__draw": TemplateDef(
        ShotSpec(_E, Material.NONE, Layout(LayoutKind.FREEZE),
                 overlay=Overlay.DRAW),
        legacy="decorate_previous", prior_opener=0.01, prior_cont=0.04),
    # ---- editgroup__* : related cells, rule of N ----------------------------
    "editgroup__stock": TemplateDef(
        ShotSpec(_N, Material.STOCK, _G), legacy="joint_3_row",
        requires="list", lock_only=True),
    "editgroup__same_stock": TemplateDef(
        ShotSpec(_N, Material.STOCK, _GS), legacy="joint_3_row",
        requires="list", lock_only=True),
    "editgroup__ai": TemplateDef(
        ShotSpec(_N, Material.AI_STOCK, _G), legacy="stickman_joint_3_row",
        requires="list", lock_only=True),
}

# Mid-sentence damp on fresh (strategy-new) templates after per-rule weights
# apply — keeps a loud rule from overturning the "mostly small edits" mix.
CONT_FRESH_DAMP = 0.40


# =============================================================================
# EVERYTHING BELOW IS DERIVED — never hand-edit
# =============================================================================
SHOT_TEMPLATES: Dict[str, ShotSpec] = {n: d.spec for n, d in TEMPLATE_DEFS.items()}
PREVIOUS_FAMILY = {n for n, d in TEMPLATE_DEFS.items()
                   if d.spec.strategy is Strategy.EDIT_PREVIOUS}
GRID_TEMPLATES = {n for n, d in TEMPLATE_DEFS.items()
                  if d.spec.layout.kind is LayoutKind.GRID}
FRESH_MATERIAL_TEMPLATES = {n for n, d in TEMPLATE_DEFS.items()
                            if d.spec.material is not Material.NONE}
AI_TEMPLATES = {n for n, d in TEMPLATE_DEFS.items()
                if d.spec.material is Material.AI_STOCK
                or d.spec.base is Base.STICKMAN_BOARD}
TEMPLATE_REQUIREMENTS = {n: d.requires for n, d in TEMPLATE_DEFS.items()
                         if d.requires}
LOCK_ONLY_TEMPLATES = {n for n, d in TEMPLATE_DEFS.items() if d.lock_only}
PRIOR_OPENER = {n: d.prior_opener for n, d in TEMPLATE_DEFS.items()}
PRIOR_CONT = {n: d.prior_cont for n, d in TEMPLATE_DEFS.items()}

LEGACY_TO_TEMPLATE: Dict[str, str] = {}
for _n, _d in TEMPLATE_DEFS.items():           # first definition wins
    LEGACY_TO_TEMPLATE.setdefault(_d.legacy, _n)
    if _d.legacy_ai:
        LEGACY_TO_TEMPLATE.setdefault(_d.legacy_ai, _n)


def to_legacy(template: str, ai_enabled: bool = False) -> str:
    d = TEMPLATE_DEFS[template]
    return d.legacy_ai if (ai_enabled and d.legacy_ai) else d.legacy


def from_legacy(legacy: str) -> str:
    return LEGACY_TO_TEMPLATE[legacy]


assert all("__" in n for n in TEMPLATE_DEFS), \
    "template names must follow the strategy__name convention"
assert set(TEMPLATE_REQUIREMENTS) <= set(TEMPLATE_DEFS)
