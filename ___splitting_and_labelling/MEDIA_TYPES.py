"""
MEDIA_TYPES.py  —  the ONE place that defines every media type.

To add a media type: add ONE entry to MEDIA_TYPES below. That's the whole
job. It appears in MANUAL_TAGGING automatically (button, colour, info
popup, key). Optionally drop an example image at examples/<name>.png for
its info popup.

How a line is described (see FORMAT.md for the full output format):
  - media_type: exactly ONE base type from MEDIA_TYPES ("stock", "map"...)
  - modifiers:  any number of STACKABLE extras from MODIFIERS
                ("decorate", "caption", "group")
  - group:      "group" means this line is one cell of a multi-cell group
                with its neighbours (rule of n). It is a modifier, not a
                media type — a group OF stock, a group OF ai stock.

Tags say how a type behaves and how it's grouped/coloured in the UI.
"""
from enum import Enum


class Tag(str, Enum):
    NEW = "new"                      # puts brand-new material on screen
    EDIT_PREVIOUS = "edit_previous"  # acts on the image already on screen
    AI = "ai"                        # ai-generated look (red buttons)
    BOARD = "board"                  # sits on the stickman explain board


# ---------------------------------------------------------------------------
# BASE MEDIA TYPES — pick exactly one per line.
#   legacy = the string the current renderer reads (search_type column)
#   info   = shown in the (i) popup and the key
#   example= image for the info popup (grey placeholder until you add one)
# ---------------------------------------------------------------------------
MEDIA_TYPES = {
    "stock": {
        "legacy": "stock", "tags": [Tag.NEW], "color": "#2e6da4",
        "info": "footage or an image fetched from the stock library. the "
                "workhorse — most lines are this.",
        "example": "examples/stock.png",
    },
    "ai_stock": {
        "legacy": "stickman", "tags": [Tag.NEW, Tag.AI], "color": "#c0392b",
        "info": "an ai-generated picture in the channel's stickman style. "
                "the search term is a scene prompt, not a search.",
        "example": "examples/ai_stock.png",
    },
    "wikipedia": {
        "legacy": "wikipedia", "tags": [Tag.NEW], "color": "#148f77",
        "info": "the image from a wikipedia article. the search term must "
                "be the exact article name ('Banda Islands').",
        "example": "examples/wikipedia.png",
    },
    "map": {
        "legacy": "map", "tags": [Tag.NEW], "color": "#1e8449",
        "info": "a rendered map with the place highlighted. the search "
                "term is just the place name ('Indonesia').",
        "example": "examples/map.png",
    },
    "object": {
        "legacy": "object_generate", "tags": [Tag.NEW], "color": "#7d3c98",
        "info": "a single cut-out object put through the object editor "
                "('one dollar coin', 'gold bar').",
        "example": "examples/object.png",
    },
    "typography": {
        "legacy": "read_out", "tags": [Tag.NEW], "color": "#8d6e2f",
        "info": "the line's own words animate on a blank background. good "
                "cold-open when there is nothing to picture yet.",
        "example": "examples/typography.png",
    },
    "stock_on_board": {
        "legacy": "stickman_explain_stock",
        "tags": [Tag.NEW, Tag.AI, Tag.BOARD], "color": "#e74c3c",
        "info": "stock footage shown on the stickman's explain board.",
        "example": "examples/stock_on_board.png",
    },
    "wikipedia_on_board": {
        "legacy": "stickman_explain_wikipedia",
        "tags": [Tag.NEW, Tag.AI, Tag.BOARD], "color": "#e74c3c",
        "info": "a wikipedia image shown on the stickman's explain board.",
        "example": "examples/wikipedia_on_board.png",
    },
    "hold_previous": {
        "legacy": "static_of_previous", "tags": [Tag.EDIT_PREVIOUS],
        "color": "#5c6bc0",
        "info": "keep the previous image on screen, frozen. the default "
                "for quick mid-sentence beats.",
        "example": "examples/hold_previous.png",
    },
    "zoom_previous": {
        "legacy": "zoom_prev_img", "tags": [Tag.EDIT_PREVIOUS],
        "color": "#7986cb",
        "info": "push in on part of the previous image.",
        "example": "examples/zoom_previous.png",
    },
    "add_stock_to_previous": {
        "legacy": "manual_stock_add_to_previous",
        "tags": [Tag.EDIT_PREVIOUS], "color": "#3949ab",
        "info": "composite one new stock element into the previous image "
                "('add jar of nutmeg into the cupboard').",
        "example": "examples/add_stock_to_previous.png",
    },
    "ai_edit_previous": {
        "legacy": "ai_edit", "tags": [Tag.EDIT_PREVIOUS, Tag.AI],
        "color": "#a93226",
        "info": "ai edits the previous ai image in place. the search term "
                "is the change ('add a second coin').",
        "example": "examples/ai_edit_previous.png",
    },
}

# ---------------------------------------------------------------------------
# STACKABLE MODIFIERS — layer any of these on top of the base type.
# (you cannot stack first: there must be a base to put them on.)
# ---------------------------------------------------------------------------
MODIFIERS = {
    "decorate": {
        "color": "#e6c15a",
        "info": "draw on top of it by hand — circles, arrows, underlines.",
        "example": "examples/decorate.png",
    },
    "caption": {
        "color": "#e6c15a",
        "info": "big on-screen text on top of it — punch words, quotes, "
                "numbers.",
        "example": "examples/caption.png",
    },
    "group": {
        "color": "#e6c15a",
        "info": "this line is one cell of a group with its neighbours "
                "(rule of n: mark 3 lines in a row and they render as 3 "
                "cells side by side). a group OF stock, a group OF ai "
                "stock — the base type stays whatever you picked.",
        "example": "examples/group.png",
    },
}


def to_legacy(media_type: str, modifiers) -> str:
    """The old renderer's search_type string for a base type + modifiers.

    Plain rule: the base type's legacy string. Three exceptions where the
    old renderer has a dedicated combined mode:
      - stock + group        -> joint_3_row
      - ai_stock + group     -> stickman_joint_3_row
      - hold_previous + decorate -> decorate_previous
      - hold_previous + caption  -> stickman_text_overlay
    Every other combination keeps the base string (the renderer just won't
    show the extra layer until it learns the new columns — see FORMAT.md).
    """
    if not media_type:
        return ""
    mods = set(modifiers or [])
    if "group" in mods and media_type == "stock":
        return "joint_3_row"
    if "group" in mods and media_type == "ai_stock":
        return "stickman_joint_3_row"
    if media_type == "hold_previous" and "decorate" in mods:
        return "decorate_previous"
    if media_type == "hold_previous" and "caption" in mods:
        return "stickman_text_overlay"
    return MEDIA_TYPES[media_type]["legacy"]
