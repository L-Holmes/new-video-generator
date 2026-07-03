# FORMAT — what every field in the output json means

The file is a map: each key is one narration line, each value describes its
shot. Plain english, field by field:

* search_term - what to search for / the ai prompt / the caption text. you write this (MANUAL_TAGGING, or the ai prompts in prompts/). starts empty.
* search_type - the string the current renderer reads. you never edit this: it is derived automatically from media_type + modifiers. the derivation is simple: it is the media type's legacy string, except four combos the old renderer has special modes for: stock + group becomes joint_3_row, ai_stock + group becomes stickman_joint_3_row, hold_previous + decorate becomes decorate_previous, hold_previous + caption becomes stickman_text_overlay.
* media_type - the ONE base type you picked, from MEDIA_TYPES.py (stock, ai_stock, wikipedia, map, object, typography, the boards, hold_previous, zoom_previous, add_stock_to_previous, ai_edit_previous). starts empty.
* modifiers - a list of extras stacked on top of the base: decorate (draw on it), caption (text on it), group (this line is one cell of a multi-cell group). can be empty.
* group_id - lines sharing the same number render as one group (rule of n: three grouped lines in a row = three cells). null when the line is not in a group. MANUAL_TAGGING sets this for you when you toggle the group modifier.
* position - which cell this line is inside its group (1, 2, 3...). always "1" for ungrouped lines. recomputed automatically, don't edit.
* sfx, sfx_timing, music, music_trim_seconds, music_fade_out - audio defaults the renderer reads as-is.
* rule_ids - the sentence splitter's tags: which rules cut this line (list item, quote, sfx beat, idiom, and so on). useful context for you and for the ai prompts. the id meanings live in RULE_DESCRIPTIONS inside sentence_splitter.py.

That's the whole format. the old template / shot / tier / why fields are
gone: they belonged to the removed auto-tagging engine.
