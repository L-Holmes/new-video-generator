# FORMAT - what every field in the output json means

The file is a map: each key is one narration line, each value describes its
shot. Plain english, field by field:

* search_term - what to search for / the ai prompt / the caption or map text. you write this (MANUAL_TAGGING, or the ai prompts in prompts/). starts empty.
* media_type - the ONE base type you picked, from the shared catalog (stock, ai_stock, wikipedia, map, object, typography, stock_on_board, wikipedia_on_board, hold_previous, add_stock_to_previous, ai_edit_previous). this is what the renderer dispatches on. starts empty.
* modifiers - extras stacked on the base. decorate (open the scene's image in the ONE decorate editor - tools: draw, text/caption, zoom, stamp), group (this line is one cell of a multi-cell group; stock and ai_stock only), and collage (several review picks composed on this ONE line; stock only; cannot combine with group). can be empty.
* group_id - lines sharing the same number render as one group (rule of n). null when the line is not in a group. MANUAL_TAGGING sets this when you toggle group.
* position - which cell this line is inside its group (1, 2, 3...). always "1" for ungrouped lines. recomputed automatically, don't edit.
* sfx, sfx_timing, music, music_trim_seconds, music_fade_out - audio defaults the renderer reads as-is.
* rule_ids - the sentence splitter's tags: which rules cut this line. useful context for you and the ai prompts. meanings live in RULE_DESCRIPTIONS inside
  0-sentence-splitter/sentence_splitter.py.

There is no search_type column any more - the old derived string is gone.

The media type names, colours and info all come from ONE shared catalog:
the MEDIA_CATALOG block in CONFIG.py at the repo root - the same file the
video builder reads.

That's the whole format.
