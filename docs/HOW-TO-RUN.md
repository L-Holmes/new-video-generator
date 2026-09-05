# STAGE 1 - script processing
# =============================================
uv run ___splitting_and_labelling/main.py
# =============================================
reads   SCRIPTS/script-<name>.txt
writes  SCRIPTS/<name>_script_to_search_term.json   (the shot list)
caches  .CACHE/<name>-CACHE/split-and-lable/

run on its own like that and it writes to
TESTING-RESOURCES/splitting_and_labelling/TEST_RESULTS/ instead, with a
TESTING_ prefix on everything — the real paths above are what `main.py`
drives it with.

- its currently way off..
- not even getting close to the right output...
    - obiovus things... like rule of 3...
        - aren't being split on (concerning)
        - and hence we aren't getting rule of 3 assigned even...
- just needs so much work...



# STAGE 2  - visuals
# =============================================
uv run main.py --name stickman
# =============================================
reads   SCRIPTS/script-stickman.txt + .wav
        SCRIPTS/stickman_script_to_search_term.json
caches  .CACHE/stickman-CACHE/
writes  OUTPUT/stickman-OUTPUT/output.mp4

no --name at all -> SCRIPTS/script.txt, .CACHE/unnamed-CACHE/,
OUTPUT/unnamed-OUTPUT/.
