"""
MEDIA_CATALOG.py — the ONE catalog of media types, shared by BOTH codebases.

  - the video builder reads it through ___visuals/CONFIG.py (which builds the
    MediaType enum FROM these names and re-exports everything)
  - the tagging tool (___splitting_and_labelling/MEDIA_TYPES.py) imports it
    directly, so the buttons/colours/info in MANUAL_TAGGING and the renderer
    can never drift apart

Dependency-free (stdlib enum only), no side effects.

The model (there is no legacy layer any more):
  - media_type: exactly ONE base name from MEDIA_TYPE_CATALOG. this IS the
    renderer's dispatch key (CONFIG turns it into the MediaType enum).
  - modifiers:  stackable extras from MODIFIERS:
      group    — this line is one cell of a group with its neighbours
                 (rule of n). grouping is a fact about the line, not a type.
      decorate — open the scene's finished image in the decorate editor,
                 whose clickable tools are: draw, text/caption, zoom/crop,
                 and stamp (place pictures on it). (zoom_previous,
                 decorate_previous, the auto caption type and the manual
                 stock stamping are all tools of this ONE editor now.)
      collage  — several review picks for one line, composed together
                 (auto scatter, or stamp them yourself in the editor).
  - group_id:   lines sharing an id render as ONE group. null otherwise.

To add a media type: one entry here + its enum row in CONFIG.py's
MEDIA_PROPERTIES (CONFIG refuses to import if the two drift apart).
"""
from __future__ import annotations

from enum import Enum


class Tag(str, Enum):
    NEW = "new"                      # puts brand-new material on screen
    EDIT_PREVIOUS = "edit_previous"  # acts on the image already on screen
    AI = "ai"                        # ai-generated look (red buttons)
    BOARD = "board"                  # sits on the stickman explain board


# ---------------------------------------------------------------------------
# BASE MEDIA TYPES — pick exactly one per line. The NAME is the value the
# renderer dispatches on; there is no separate legacy string.
# ---------------------------------------------------------------------------
MEDIA_TYPE_CATALOG: dict[str, dict] = {
    "stock": {
        "tags": [Tag.NEW], "color": "#2e6da4",
        "info": "footage or an image fetched from the stock library. the "
                "workhorse — most lines are this. stack group for a grid "
                "of them.",
        "example": "examples/stock.png",
    },
    "ai_stock": {
        "tags": [Tag.NEW, Tag.AI], "color": "#c0392b",
        "info": "an ai-generated picture in the channel's stickman style. "
                "the search term is a scene prompt, not a search. stack "
                "group for a grid of them.",
        "example": "examples/ai_stock.png",
    },
    "wikipedia": {
        "tags": [Tag.NEW], "color": "#148f77",
        "info": "the image from a wikipedia article. the search term must "
                "be the exact article name ('Banda Islands').",
        "example": "examples/wikipedia.png",
    },
    "map": {
        "tags": [Tag.NEW], "color": "#1e8449",
        "info": "a rendered map with the place highlighted. the search "
                "term is just the place name ('Indonesia').",
        "example": "examples/map.png",
    },
    "object": {
        "tags": [Tag.NEW], "color": "#7d3c98",
        "info": "a single cut-out object put through the object editor "
                "('one dollar coin', 'gold bar').",
        "example": "examples/object.png",
    },
    "typography": {
        "tags": [Tag.NEW], "color": "#8d6e2f",
        "info": "the line's own words animate on a blank background. good "
                "cold-open when there is nothing to picture yet.",
        "example": "examples/typography.png",
    },
    "stock_on_board": {
        "tags": [Tag.NEW, Tag.AI, Tag.BOARD], "color": "#e74c3c",
        "info": "stock footage shown on the stickman's explain board.",
        "example": "examples/stock_on_board.png",
    },
    "wikipedia_on_board": {
        "tags": [Tag.NEW, Tag.AI, Tag.BOARD], "color": "#e74c3c",
        "info": "a wikipedia image shown on the stickman's explain board.",
        "example": "examples/wikipedia_on_board.png",
    },
    "hold_previous": {
        "tags": [Tag.EDIT_PREVIOUS], "color": "#5c6bc0",
        "info": "keep the previous image on screen, frozen. the default "
                "for quick mid-sentence beats — stack decorate on it to "
                "draw, caption or zoom on the frozen image.",
        "example": "examples/hold_previous.png",
    },
    "add_stock_to_previous": {
        "tags": [Tag.EDIT_PREVIOUS], "color": "#3949ab",
        "info": "composite one new stock element into the previous image "
                "('add jar of nutmeg into the cupboard').",
        "example": "examples/add_stock_to_previous.png",
    },
    "ai_edit_previous": {
        "tags": [Tag.EDIT_PREVIOUS, Tag.AI], "color": "#a93226",
        "info": "ai edits the previous ai image in place. the search term "
                "is the change ('add a second coin').",
        "example": "examples/ai_edit_previous.png",
    },
}

# ---------------------------------------------------------------------------
# STACKABLE MODIFIERS — layer any of these on top of the base type.
# (you cannot stack first: there must be a base to put them on.)
# ---------------------------------------------------------------------------
MODIFIERS: dict[str, dict] = {
    "decorate": {
        "color": "#e6c15a",
        "info": "open the scene's image in the decorate editor and stack "
                "edits with its clickable tools: draw (circles, arrows, "
                "underlines), text/caption (big words on top — the search "
                "term is offered as the starting text), zoom (crop / push "
                "in on part of the image), and stamp (place extra pictures "
                "onto it).",
        "example": "examples/decorate.png",
    },
    "collage": {
        "color": "#e6c15a",
        "info": "pick SEVERAL images for this one line in the review stage, "
                "then choose: auto collage (they're scattered with overlaps "
                "onto a plain background for you) or stamp it yourself (the "
                "picks load into the decorate editor as stamps). stock only.",
        "example": "examples/collage.png",
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

# Which base types accept the group modifier (they have grid layouts).
GROUPABLE_TYPES: set[str] = {"stock", "ai_stock"}

# Which base types accept the collage modifier (multi-pick in review).
COLLAGEABLE_TYPES: set[str] = {"stock"}
