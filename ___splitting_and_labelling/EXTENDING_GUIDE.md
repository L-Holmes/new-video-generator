# EXTENDING GUIDE

Three jobs. each is one place.

## add a media type

* open ___visuals/MEDIA_CATALOG.py (the ONE shared catalog - the tagging tool and the video builder both read it)
* copy any entry in the MEDIA_TYPE_CATALOG dict, give it a new name, set its legacy string, its tags (new or edit_previous, plus ai / board if it applies), a colour, and one sentence of info
* give the video builder its enum value and MEDIA_PROPERTIES row in ___visuals/CONFIG.py - CONFIG refuses to import if you forget, so you cannot get this wrong silently
* optionally drop an example picture at examples/<name>.png
* done. it appears in MANUAL_TAGGING as a button, with its info popup and key entry, automatically

## add a stackable extra (like decorate)

* same catalog file, MODIFIERS dict, one entry
* if the renderer has a dedicated combined mode for some base + your extra, add one line to to_legacy in the catalog (the four existing exceptions show how). otherwise the renderer applies it as a layer via ___visuals/MODIFIER_STAGE.py

## add a sentence splitter rule

* open sentence_splitter.py
* write a rule_<name>(doc) function next to the others (copy one as a template; each has fire / don't fire examples in its header)
* add its number and one-line meaning to RULE_DESCRIPTIONS
* add its name and number to _SPLIT_RULE_IDS
* add it to _POSITIVE_PIPELINE
* if its splits must survive merging, add the name to PROTECTED_RULE_NAMES

## where word lists live

* all of them are in sentence_splitter.py: weak verbs, weak adjectives, transitions, sfx words, idioms, era nouns, and so on. word lists are categories of words, never lists of specific famous things.

## after a change

* run the tests in testing/ and then uv run SPLIT_AND_LABEL.py
