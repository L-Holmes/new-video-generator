


claude --resume e76cdc3d-27df-45fd-b30c-2b98a03a0c36
you last said:

```

All six plan steps are wired up and verified. Summary:

Step 1 — resolver is now a library (_abstract_term_resolver.py)
resolve_all_abstract_terms(text, models=MODELS, doc=None, …, log=None) returns AbstractTerms — a dict {(start_char, end_char): {surface, resolved, confidence, source, possessive, number}}, with .canonical and .models_run alongside. main() is now argv + printing only; the workings print through log=print. Also added rewrite_with_resolutions() (human-readable output only, documented as never for the extractor) and split possessive() into is_possessive() / possessive_form().
Verified: uv run _abstract_term_resolver.py ../script-whales.txt prints byte-identical output to the pre-change baseline (bar model timings). old_abstract_term_resolver.py deleted (git rm, so it's recoverable).

Step 2 — one text, one coordinate system (_visualisables_pipeline.py)
join_segments() / normalise_segment() lifted out and used by _parse_together(), so there's only one joining rule. Checked the prefix property on the real splitter output for whales/rome/spices — every prefix of every split holds.

Step 3 — one parse or two: decided and written down (resolver docstring)
Measured with the coref models held fixed: 11 pronouns across the three scripts, 11 identical answers off en_core_web_sm vs en_core_web_trf (parse 0.02–0.03 s vs 0.10–0.16 s). So doc= is accepted and myownstuff.py passes the shared sm parse; trf stays the CLI default. Only char offsets and strings cross the boundary.

Steps 4 & 5 — the map feeds the pipeline (_visualisables_pipeline.py, _visualisables_extractor.py)
get_visualisable_data(..., abstract_terms=…) → resolve_visualisable_details → resolve_references, which looks each pronoun up and runs no model. Below CONFIDENCE_THRESHOLD (0.25) the pronoun is left in place as a "hold the picture" cue. Deictics become the new KIND_DEICTIC ("the narrator"/"the viewer"), and possessives rename their slot and record owner. The old model path is untouched for callers with no map (HOW_TO_USE's output is unchanged).
Verified on the tractor narrative: Its windscreen → "tractor's windscreen" (owner=tractor), and the tractor's variant reaches "yellow paint, broken windscreen" — the KNOWN MISS, fixed for the possessive case. I → "the narrator", kind deictic. Spices: your kitchen cupboard → "the viewer's kitchen cupboard".

Step 6 — myownstuff.py runs end to end (uv run myownstuff.py, all 23 whales segments in order). Your comments and docstrings are all still there; step 2)'s call is kept as a comment pointing at 1)i) where it now runs once for the whole script.

Two things I changed beyond straight wiring, both because a test caught them:
- Merging into one {template: slots} dict loses segments — "Whale skulls." and "about $2 million." are both "[1]."; 23 in, 19 out. _build_visualisable_data now returns one entry per segment, in order (each still the pipeline's own map). Reason is written in the code.
- A cluster can hand back a verb, so "the cat didn't like it" resolved to "revved". The old _model_referent guarded against that and the map path had lost it; the noun-only rule now lives in the resolver's vote (MENTION_POS), where the answer is produced. Whales CLI output is unaffected.

Nothing is committed. Your myownstuff-bakcup-incase-ai-messes-up.py is still empty — the pre-change version is in git (git show HEAD:___splitting_and_labelling/ME/myownstuff.py).
```


yeah, create a separate test file to do that. run on those two examples. also create a couple more examples which have lots of abstract things like 'this', 'it', 'her', etc. and run on those two. then make the manual interpretation test put all the results into a single TEST_RESULTS_....txt file that i can look through.




----------------------------

AUTO SEARCH TERM DETECTION:
(do i write this myself?)

- for the logic that auto detects the search term...
    - if we have a prevoius line like: "there is a phone box standing in a field in japan", then...
    - and next/future line: 
        "it is not connected to anything"
    - then we know 'it' is the phone box as:
        - it was the first (and thus main) thing that was introduced.
        --> the other details of '*in* a field' and *in* japan, are just details about the phone...
        --> we also know that 'connected' is related to 'phone box' as they commonly need connections...
        - so basically, think about the logic i just mentioned (identifying the key noun subject (if there is one), and differentiating other nouns / visualisables which are secondary to that / adding detail...
        --> so then we can more accurately give better predictoins for the most likely visaulsiable for when we encounter words like 'in' later on...
    - also, look at the context around 'in' (or words like that relating to other nouns...)
        --> if there are other words like 'connected', then think of the probability of that word being related to each of the candidate nouns/visualisables (e.g. 'phone box' / 'field'), and get scores of all that..
    --> add those as two extra stages for us to get more accurate noun associations
    e.g.2.- same with later on... it says 'locals call it the wind phone'... 'wind phone' is obviously relatded to the phone box... and then because we already know htat phone box is a 'higher up' / bigger 'importance score' (a new metric!) visualisable, we also know its more likely to be referring to that anyway!
    - so yeah, add lots of logic for this


-------------


- I've prepared the tests for testing the auto tagger.  
    - see the readme for how to proceed.
- once the Auto_add_mediatypes.py has been sorted:
    - go through an implement hthe logic myself for determining how to split, and what mediaypes / search terms to use.
- then:
    - start going through lots of example scripts.
    - use the manual tagger to adjust.
    - (basically start building up the list of changes that we have made...)


-------------

TODO;
- bang out each of the below
- then try an optimise the auto tagging...

12) need an actual reliable thing for the words on screen... and perhaps have a flag that we change in config that determines whether it shows like the whole sentence on screen, but one word at once... or just each word one by one as it does now..

===========================================


Ideas:
- extend previous
    - take the previous, put it side by side with a second image. 
    e.g. Rome was not built in a day || but in burned in six
        -> rome construction -> rome construction + fire

or almost like 'rearrange and add more elements to a joint image!?!?'

------------------------------------------------------------------------------------------------------------------
------------------------------------------------------------------------------------------------------------------
------------------------------------------------------------------------------------------------------------------
------------------------------------------------------------------------------------------------------------------

find stickman-CACHE -type f -name "*.json" -delete && rm -rf stickman-OUTPUT/output.mp4

rm -rf TESTER-OUTPUT/output.mp4 TESTER-CACHE/output_video_final.mp4 TESTER-CACHE/{joint,blank,map,collage,decorate}_scenes

------------------------------------------------------------------------------------------------------------------
------------------------------------------------------------------------------------------------------------------
------------------------------------------------------------------------------------------------------------------
------------------------------------------------------------------------------------------------------------------


TODO THEN THEN:
- for when a map is shown...
    Add ability to show a map when e.g. two or more place names are mentioned:
    - place names mentioned..
    - show both places on the same map
        - but create two pictures...
            - so actually, this will be a new media type... 'addanothermappoint'... (?)
    - ==> Ideally I want to mark where is is on the map... If it's a country, show world map, if it's a county/town/city, show national map... Etc. and have a marker of the place I'm after...
    - (if it can't be done automatically, ask user to click where it should go, then add a pin and then text of what the place is called...) 
    - ........
    - well.. just make it work for like 'n' name places... 
         up to a reasonable limit like 10 places...
- Add a transtiion for things like the collage???


========================================================================
========================================================================
========================================================================
========================================================================
========================================================================
========================================================================
===============================================================


THEN:
------------------------------------------------------------------------------------------------------------------

Manual TODO;
- Grid background (2 or 3 variations)
    ---> then two, up to five pictures layered on top, like a collage, each slightly rotated
    (Perhaps also with slight box shadow etc. to look like it's been placed there...) 
    --> I THINK THE PAPER MAY BE A BIT.. TACKY? NOT 'TIMELESS'...?


Other ideas:
- 2 to 3 brand colours? (for like labels.. text .. arrows.. stock.. etc.)
- slight rotation for layered things...
    - hmmm . yeah so either layer on prexisting background
    - would this be a variation of the 3 row???
    - i.e. better collage...
- maybe transitions etc. for when I place something in a scene manually?? 
    - (even if i place multiple items in a scene? - e.g. adding 5 coins... could have default it adds them one by one, cutting one after the other... (unless the length of the clip isn't long enough?)

    i.e. custom animations (manim?)
    - e.g. custom animated graph (e.g. bar chart or line going up.. . numbers ticking up...)
- look at powerpoint etc. for ideas on auto generation / arranging
- subjects / people should ideally be cut out and put on a background...????
    - Get picture of the person
    - pipe into remove background
    - compose that on top of the animated background

    --> would i make that a joint scene where i have the background as the first, then the second, user can choose to add 'wikipedia + decorate', where ...
        - would they need a seperate tab/window for them to remove background for second picture? or to click on subject using our extract subject, then make background white, then keyout the background??
        - then use that as the 'stamp' to stamp onto the existing background???


TODO LATER:
- maybe link up the other image providers??!?!?!?!? (or actually... just stick with pexels for now...)


For prod:
- ADD WAY FOR USERS TO EASILY GET AI TO POPULATE THE TAGS;
    - for the manual tagging;
        - add a button 'let AI populate this list for me'
        - at the top left is instructions.
        - once clicked, at the left side, it shows the json with a copy button (and which confirms when you copy)
        - at the right, there is a prompt that the user is expected to copy and paste into an AI agent chat.
        - it also has a clear button.
        - user then pastes the new json into the text box. code confirms whether its different (and perhaps other verification, like checking the keys are all the same...)


----------------------------------------------------------------------------------------------------------------
----------------------------------------------------------------------------------------------------------------
----------------------------------------------------------------------------------------------------------------

Other from better stock notes:
- motion tracking labels?
    - so the user sort of manually tracks a moving thing in a video, and we attach a label to it
- update the object edit, such that we can add text behind a selected subject?

????
- synced music
    - have a theme, switch to different footage on the beats of a song??
- speed ramps:
    - 100% -> 150% for boring middle section, snap back to 100% on beat. Great for walking, clouds, city timelapses.
- character reactions library
- Reuse a small library of stickman poses: thinking, pointing, shocked, shrug. Trigger by sentiment of line.
