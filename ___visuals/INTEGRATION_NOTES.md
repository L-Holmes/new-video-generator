# INTEGRATION NOTES - the legacy layer is gone

files below are labelled ___visuals/ or ___splitting_and_labelling/ or root.

## the model (no legacy, no conversion at runtime)

* MediaType is now BUILT from the shared catalog names: stock, ai_stock, wikipedia, map, object, typography, stock_on_board, wikipedia_on_board, hold_previous, add_stock_to_previous, ai_edit_previous. eleven members, value == name.
* the old combined and derived types are deleted: joint_3_row, stickman_joint_3_row, decorate_previous, stickman_text_overlay, zoom_prev_img, static_of_previous, stickman, ai_edit, read_out, object_generate do not exist anywhere.
* grouping is the group modifier plus group_id. a group OF stock, a group OF ai_stock. only those two bases can group (GROUPABLE_TYPES); the tagging tool greys the group chip for anything else and the loader rejects it.
* decorate is ONE modifier that opens ONE editor with clickable tools: draw, text/caption, zoom. "zoom into the previous image" is hold_previous + decorate (hold makes the previous image this scene's footage, the zoom tool crops it) - the stay-same-plus-edit default you described. captions are the text tool, prefilled from the search_term.
* the json has NO search_type column. the renderer dispatches on media_type + modifiers. MANUAL_TAGGING purges search_type from any row it touches; SPLIT_AND_LABEL never writes it.
* old flat files convert ONCE with: uv run UPGRADE_OLD_JSON.py <file> (root). it maps every old string, folds the old zoom/caption/decorate types into hold_previous + decorate, assigns group_ids to joint runs, keeps a .pre-upgrade.bak, and can be deleted afterwards.

## running package files directly

* every ___visuals module we touched now starts with a 4-line bootstrap so `uv run ___visuals/<file>.py` works from the repo root. paste the same 4 lines (they are at the top of ___visuals/CONFIG.py) into DECORATE_PREVIOUS.py and any other package file you run directly.

## what changed where

* ___visuals/MEDIA_CATALOG.py - the one catalog (names, tags, colours, info, GROUPABLE_TYPES). no legacy field, no to_legacy.
* ___visuals/CONFIG.py - MediaType built from the catalog; new MediaProperties (needs_external_candidates, uses_wikipedia, image_only, is_ai_base, is_ai_edit, is_on_board, is_object, acts_on_previous, is_hold_previous, is_manual_stock_add); normalise_scene_row (rejects legacy files, strips stale columns, validates modifiers and grouping); scene_type / scene_is_grouped / scene_wants_decorate / group_scene_rows; joint layouts and sfx keyed by MediaType.STOCK / MediaType.AI_STOCK; TYPOGRAPHY_ENABLE and TYPOGRAPHY_RENDER_SAFETY_PAD_SEC (renamed from READ_OUT_*); DECORATE_OUTPUT_DIR.
* ___visuals/DECORATE_STAGE.py - the merged editor's stage (replaces MODIFIER_STAGE.py - delete that file). wiring pending: see "files needed" below. until wired it prints which scenes are waiting and changes nothing, so the pipeline still runs.
* ___visuals/SCENE_GENERATORS.py - joint generator runs on grouped rows (group modifier + group_id, base decides stock vs ai tiles); typography rename; the text-overlay generator is deleted (caption = decorate tool).
* ___visuals/AI_GENERATION.py - plain ai_stock vs grouped ai_stock filters; ai_edit_previous; generator called with grouped=True/False instead of process_type.
* ___visuals/PIXELLATE_STAGE.py - pixellates MediaType.AI_STOCK + MediaType.AI_EDIT_PREVIOUS (grouped tiles are the same ai_stock type).
* ___visuals/COLOUR_GRADE_STAGE.py, ___visuals/ADD_RELEVANT_OVERLAYS.py - read media_type / the decorate modifier.
* ___visuals/MANUAL_STOCK_PLACEMENT.py - bootstrap + header comments; its crop_and_zoom / _SizeBox / extract_frame are the zoom building blocks the merged editor will reuse.
* main.py (root) - loader normalises the new schema only; stage-1 review exclusions are property-driven; the text-overlay stage is gone; stage 2.645 is the decorate editor.
* ai_generate_stickman_images.py (root) - filters raw rows by media_type == "ai_stock" + grouped-ness; process_type and _search_type_str are gone.
* ___splitting_and_labelling/MEDIA_TYPES.py - shim over the shared catalog (Tag, MEDIA_TYPES, MODIFIERS, GROUPABLE_TYPES).
* ___splitting_and_labelling/MANUAL_TAGGING.py - no derived column (purges search_type on save); group chip greyed unless the base can group; server rejects invalid grouping.
* ___splitting_and_labelling/SPLIT_AND_LABEL.py - emits rows without search_type.
* UPGRADE_OLD_JSON.py (root) - the one-time converter.
* stickman_script_to_search_term.json (root) - your test file, upgraded through that exact script (plus the Indonesa -> Indonesia typo fix so the map stage doesn't geocode-fail).

## files needed to finish (please share)

these dispatch on the old types or hold the editor code, and were not part of this session:

* ___visuals/DECORATE_PREVIOUS.py - the draw/text editor to merge zoom + caption into (becomes the DECORATE_STAGE editor)
* ___visuals/STATIC_RENDER.py - drives the hold/zoom/decorate/manual-placement stages and still-to-mp4; must switch to the property flags + per-row modifiers
* ___visuals/MAKE_TEXT_OVERLAY.py - the caption renderer the editor's text tool reuses
* ___visuals/TIMING_MERGE.py and ___visuals/AUDIO_EVENTS.py - joint timing + per-type sfx; likely small changes (JOINT_TYPE_SFX_MAP keys changed)
* ___visuals/STOCK_FOOTAGE_REVIEW.py - only if it inspects search_type anywhere
* ___visuals/CACHE_IO.py and ___visuals/OBJECT_GENERATE_STAGE.py - to confirm they are type-free (expected)


## addendum - the standalone decorator + collage

* ___visuals/decorator/ is the standalone generic package: run_decorator(base pic, out path, stamps=[pics], prefill_text=...) -> edited pic. it knows nothing about scenes; the pipeline adapters call it.
* its tools: stamp and zoom are LIVE today - they reuse the proven MANUAL_STOCK_PLACEMENT GUIs (the old "add stock to previous" stamping is now a generic tool of this editor, exactly your integration idea). draw and text are two one-line hooks at the top of ___visuals/decorator/tools.py, waiting on ___visuals/DECORATE_PREVIOUS.py and ___visuals/MAKE_TEXT_OVERLAY.py.
* ___visuals/DECORATE_STAGE.py is now the real adapter: resolves the scene's footage (first frame if video), opens the editor, bakes a static mp4.
* collage is a MODIFIER (same reasoning as your group insight: it is still stock, just several of them on one line). stock only; cannot combine with group - the tagging tool auto-swaps whichever you toggle last.
* ___visuals/COLLAGE_STAGE.py (stage 2.646): per collage scene you choose auto collage (___visuals/decorator/auto_collage.py scatters the picks with tilts, borders and shadows onto a plain background - deterministic per scene, headless-tested) or stamp it yourself (the picks load into the decorator as stamps on a blank card). COLLAGE_BACKGROUND in CONFIG sets the backdrop (image path, #hex, or the default card).
* review side still needed: collage rows want MULTIPLE picks, so ___visuals/DOWNLOADS.py should set num_clips_needed for collage rows and ___visuals/STOCK_FOOTAGE_REVIEW.py must allow multi-select - both files are on the request list already.
