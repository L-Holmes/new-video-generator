# INTEGRATION NOTES - new media types in the video builder

what changed, in plain english.

## one shared catalog

* the media type catalog now lives in ___visuals/MEDIA_CATALOG.py. it has no dependencies and no side effects.
* ___visuals/CONFIG.py re-exports it and adds the MediaType enum plumbing, so renderer modules keep importing everything from CONFIG.
* ___splitting_and_labelling/MEDIA_TYPES.py is now a thin shim that imports the same catalog. the tagging tool and the renderer literally read the same dict, so they cannot drift.
* to add a media type: one entry in MEDIA_CATALOG.py, plus its enum value and MEDIA_PROPERTIES row in CONFIG.py. CONFIG refuses to import if the catalog references a legacy string with no enum, so you cannot forget.

## the json now carries the new columns

* media_type - the base name you picked in MANUAL_TAGGING (stock, hold_previous, ...)
* modifiers - stacked extras (decorate, caption, group)
* group_id - lines sharing a number are one group. null otherwise.
* search_type - still written to the file as the derived legacy string, so tools that read the raw file (ai_generate_stickman_images filters on it) keep working unchanged.
* main.py normalises every row on load with CONFIG.normalise_scene_row: new rows derive their enum from media_type + modifiers (that is authoritative); old flat files with just a search_type string still load exactly as before.

## grouping now uses group_id

* generate_joint_scenes used to sort by the position field. positions restart at 1 for every group, so two groups in one video would interleave and group wrongly.
* it now sorts by script order and groups consecutive scenes that share the same type and the same group_id (CONFIG.group_scene_rows). old files without group_id fall back to the original contiguous-position rule.

## decorate and caption are layers now

* hold_previous + decorate and hold_previous + caption still route through the dedicated legacy types (decorate_previous, stickman_text_overlay) - that is the "stay same as previous + draw on it" default, unchanged and free.
* any OTHER base + decorate/caption reaches the renderer as the base type plus leftover modifiers. the new pass ___visuals/MODIFIER_STAGE.py (stage 2.645 in main, after manual placement, before colour grade and ken burns) applies them to the scene's OWN finished footage:
* caption is fully wired: it composites the caption with the same MAKE_TEXT_OVERLAY renderer the legacy overlay type uses, as a static mp4 so ken burns skips it. the caption text is the row's caption_text field if you add one, otherwise its search_term (add caption_text by hand when the term is needed for fetching, e.g. stock + caption).
* decorate needs one line from you: point DECORATE_LAYER_HOOK in MODIFIER_STAGE.py at your interactive decorate editor (signature in the file). until then a decorate layer prints a clear notice and leaves the footage unchanged - nothing breaks.

## unchanged on purpose

* PIXELLATE_STAGE, COLOUR_GRADE_STAGE, AI_GENERATION, ADD_RELEVANT_OVERLAYS, MANUAL_STOCK_PLACEMENT, WORDS_ON_SCREEN, ai_generate_stickman_images: they dispatch on the MediaType enum or the raw legacy string, and both are preserved.
* old script_to_search_term.json files run exactly as before.

## files in this drop

* main.py (loader + stage 2.645)
* ___visuals/MEDIA_CATALOG.py (new)
* ___visuals/CONFIG.py (re-export, normalise_scene_row, group_scene_rows, scene_residual_modifiers, MODIFIER_LAYER_* paths)
* ___visuals/MODIFIER_STAGE.py (new)
* ___visuals/SCENE_GENERATORS.py (grouping)
* ___splitting_and_labelling/MEDIA_TYPES.py (now the shim)
* testing: test_integration.py (renderer side, runs with stubs), and the existing tagging tests pass unchanged against the shim
