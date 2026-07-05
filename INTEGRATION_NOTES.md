# INTEGRATION NOTES - the ONE editor window

## install: unzip + one script (this round)

* put decorator_update.zip in the repo root, then:
    unzip decorator_update.zip
    uv run UPDATE_DROP/SETUP.py
    rm -r UPDATE_DROP decorator_update.zip
* SETUP.py installs the whole tree into the right places, backs up what it replaces to .bak, deletes the dead files AND the stray duplicates sitting in your repo ROOT (CONFIG.py, DECORATE_STAGE.py, DOWNLOADS.py, STATIC_RENDER.py, COLLAGE_STAGE.py, draw.py, api.py, auto_collage.py, INSTALL_DECORATOR.py, test_pipeline_fixture.py - those were leftovers from earlier manual copying; APPLY_UPDATE never wrote them there), and clears __pycache__. re-run with --clean-baks later to delete the backups. one stray it deliberately leaves: a _MAP_DATA folder in the root - compare it with ___visuals/_MAP_DATA yourself before deleting.

## fixes from this test round

* zoom: the gold box FOLLOWS the cursor the moment you open the tab, exactly like the old zoom. click freezes it in place (so you can travel to the sidebar), click again to move it; drag also moves it; - / + sizes it; Enter or the green button applies; the picture updates and you carry on.
* the sidebar is now FULLY per tab: zoom shows only the zoom panel; the item controls (Edit text / Size / item count) show for draw and stamp; the draw instructions live inside the draw panel.
* object: the "pyimage doesn't exist" blank window and the status crash were the smoking gun for the real cause - TWO tk interpreters in one process (your editor makes its own tk.Tk() while the decorator's is alive; its images bind to the wrong interpreter). the editor now runs in a SEPARATE PROCESS with a clean interpreter of its own, writing its result to a file the decorator reads back. no more corruption, no stray after-callbacks. it is still its own window on the screen - making it a tab INSIDE the decorator window physically requires editing OBJECT_SEPERATION.py, which has never been shared. send ___visuals/OBJECT_SEPERATION.py (and ___visuals/REMOVE_BACKGROUND.py) and I will embed it for real.


## where the new files go / how you replace old ones

* download APPLY_UPDATE.py into the repo root (next to main.py)
* run: uv run APPLY_UPDATE.py
* that is the whole job. it backs up every file it touches to <name>.bak, writes all 32 files into the right places itself (___visuals/, ___visuals/decorator/, ___splitting_and_labelling/, the root files, and the updated stickman_script_to_search_term.json), deletes the dead files (OBJECT_GENERATE_STAGE.py, decorator/tools.py, the stray decorator/MEDIA_TYPES.py and decorator/INTEGRATION_NOTES.md that ended up in your decorator folder, and the older leftovers), and clears __pycache__
* when you are happy: uv run APPLY_UPDATE.py --clean-baks (re-applies and deletes every .bak littering your tree)
* then delete APPLY_UPDATE.py itself. done - no manual file placement at all

## fixes from your test run (this round)

* the crash cascade (pack after destroy, _poll_model, PIL choking on an mp4) is gone at the root: the window is NEVER destroyed mid-session any more. the object tab HIDES the window, runs your extraction editor on top, and shows the window again with the result - no reopen, sidebar intact
* the sidebar now SWAPS with the tab: STAMP shows a preview of the passed-in pictures with ◀ ▶ arrows (and a pick-a-file button), and you stamp the selected picture as many times as you like - every click drops another copy, + / − resizes, undo removes, white-background keying is a toggle right there
* ZOOM shows a live gold crop box the moment you open the tab: click/drag the image to move it, − / + in the sidebar sizes it, and NOTHING happens until you press ✓ complete zoom - which applies the crop and drops you back on the canvas to carry on
* an animated MP4 exported from the object editor no longer ends or breaks anything: it is captured as the SESSION result, the canvas keeps the still, and the mp4 is applied when you press FINISH (any further edit discards it, with a printed note). the pipeline stage uses such an mp4 directly - no still-baking, ken burns skips it

## ONE window now - no more window-per-tab

* the decorator IS the draw window. tabs sit at the TOP RIGHT of that window (DRAW / STAMP / ZOOM / OBJECT, click or Ctrl+Left / Ctrl+Right); the copyable session title sits top left
* STAMP is a native canvas item now, exactly like text and arrows: pick a picture (queued stamps first, else a file picker), it floats on the cursor, click to drop, + / - resizes, arrow keys nudge, undo works, and a side-panel toggle keys out white backgrounds. no separate window
* ZOOM is native too: press-drag-release a box on the canvas, confirm, and the base is cropped/zoomed in place (what you had drawn is baked first). no separate window
* OBJECT is your big cut-out extraction editor (OBJECT_SEPERATION) - that one opens on top of the window and you land straight back on the canvas with the result. it stays a separate program because you have never shared its file; share OBJECT_SEPERATION.py + REMOVE_BACKGROUND.py if you want it truly embedded
* the instructions text and the status line in the window are selectable/copyable now
* FINISH bakes and saves; closing the window abandons and the pipeline keeps the original footage

## the types are gone - everything manual is decorate

* MediaType is down to NINE: stock, ai_stock, wikipedia, map, typography, stock_on_board, wikipedia_on_board, hold_previous, ai_edit_previous
* object and add_stock_to_previous are DELETED as types. cutting an object out = any base + decorate, then the OBJECT tab. stamping a picture into the previous image = hold_previous + decorate, then the STAMP tab
* so when main runs and a scene needs ANY manual editing, exactly one thing happens: the decorator window opens (stage 2.645). the hold stage just freezes frames (no GUI); collage's stamp-yourself mode opens the same window with the picks queued
* OBJECT_GENERATE_STAGE.py is deleted; STATIC_RENDER's manual stage is hold-only now; DOWNLOADS no longer special-cases the dead types
* stickman_script_to_search_term.json is regenerated for this model (jar-of-nutmeg row = hold_previous + decorate, the ship object row = stock + decorate). UPGRADE_OLD_JSON.py maps old files the same way - run it once per other old json you have

## tests

both suites pass: uv run test_integration.py (root) covers the 9-type model, the one-window session loop incl. the object round-trip, the automatic caption path, grouping, collage; the tagging suite lives at ___splitting_and_labelling/testing/test_pipeline_fixture.py

## honesty note

the tab/stamp/zoom GUI changes are careful surgical additions to your proven draw editor, but I cannot open Tk windows here - the logic is tested, the widgets are not. if anything looks off on first open, tell me exactly what and I will fix it against the real behaviour.
