"""
TODO
- integrate the thing that builds the visualisable data.
    - I thikn visualisables_pipeline.py calls it. so maybe via that.


- bulk out this file - think of logic, what goes where etc.
- continue bulking out, think of exact data structures etc. How we're going to
  return the data...
- Start implementing:
    - Hook up necessary functoins to LLMs
    - Start testing manually, evaluating the output..
    - OR could even write tests with expected outcomes...
"""


"""
--------------------------------------------------------------------------------------
Definitions:
- "Visualisable" = Something you can picture
    - e.g.
        - Dog
        - Peter Robin
        - Hammering
- "Concrete Visualisable" = Something you can picture, excluding actions / verbs
    - e.g. NOT 'hammering'
- "Setting" = A place that usually contains the visualisables
    - e.g. 
        - Field
        - 25th Avenue
        - Planet Earth 
--------------------------------------------------------------------------------------
"""

"""
--------------------------------------------------------------------------------------
=== ULTIMATE GOAL ===
- To know what visualisables to put on screen, AND when!
- input:
    - the split sentences (as split by the sentence splitter)
- output:
    - map:
        - split sentence -> visualisables 
            (may also want to add more info / separate things like 'settings' from 'concrete visualisables' etc.)
            (so then when making the video, we know to put the setting in the background, 
             and then put the [tractor] on the 'dusty track' in the setting...)

    e.g.
        {
            ...
            "broke" -> " <Tractor with yellow paint and broken windscreen> <crashed into a lamppost, from earlier> <cat onlooking, from earlier> "
            ...
        }

        ~~~~
        TODO need to work out how I'm going to output this map...
        depends really on how the code after this visualises stuff...
        ~~~~
--------------------------------------------------------------------------------------
"""
import sys
from pathlib import Path

# The visualisables files and the sentence splitter live here and one up.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "shared"))
# ...and the sentence splitter now has a stage folder of its own
# (0-sentence-splitter). PATHS puts every stage folder on sys.path.
import PATHS  # noqa: F401,E402

from _abstract_term_resolver import resolve_all_abstract_terms

# get_visualisable_data is the name of MY main function below, so the
# pipeline's one comes in under the name of what it actually does: ONE line
# segment, not the script.
from _visualisables_pipeline import (
    get_visualisable_data as get_line_segment_visualisables,
    join_segments,      # the ONE text the abstract terms map is keyed against
    parse_script,       # ...and its ONE spaCy parse, shared with the resolver
)

BUILT_VISUALISABLE_DATA=False
VISUALISABLE_DATA = None

def get_visualisable_data(sentence_splitter_output):
    """
    Main function

    @input sentece_splitter_output = each of the sentences, split into scenes.
    e.g. 
     [
        Chunk(text='In Egypt,', ids=[]),
        Chunk(text="there's a valley filled with whale skeletons.", ids=[]),
        Chunk(text='It was once covered by', ids=[]),
        Chunk(text='the Tethys Sea.', ids=[])
    ]

    @output one entry per line segment, in script order -- each of them the
    {template: {slot: what we know about it}} map for that segment.
    e.g.
     [
        {"In [1],": {"1": {"visualisable": "Egypt", "variant": None,
                           "action": None, "location": None, "kind": "name",
                           "identity": "egypt", "is_setting": True,
                           "amount": 1, "confidence": 1.0}}},
        {"[1] was once covered": {"1": {"visualisable": "valley",  # was "It"
                                        "kind": "reference",
                                        "action": "covered", ...}}},
     ]
    NOTE only THINGS get slots. "covered" is a verb, so it is the valley's
    `action`, not a slot of its own; an adjective is its `variant` the same
    way. See _visualisables_extractor.SLOT_KINDS.
    """
    BUILT_VISUALISABLE_DATA=False
    if not BUILT_VISUALISABLE_DATA:
        input_list = [chunk.text for chunk in sentence_splitter_output] # ["In Egypt", "there's a valley filled with whale skeletons.", "It was once covered by", "the Tethys Sea."]
        VISUALISABLE_DATA = _build_visualisable_data(input_list)
        BUILT_VISUALISABLE_DATA=True
    return VISUALISABLE_DATA

def _build_visualisable_data(line_segments):
    """
    @input The text.
    e.g.
    "
    [
        The tractor and the cat, Molly, went down the lane. 
        They passed a bee.
        It revved really loud. The cat didn't like it.
        So she poured yellow paint onto the tractor. It swished and swerved after having the paint splatter its windscreen.
        It then crashed into the lampost by accident.
        The yellow and black guy flew away.
        Its windscreen broke.
    ]

    @generates:
    (as a pseudo intermediary step)

    [tractor][yellow paint splat, broken window][ploughing]

    [tractor]
    --> [yellow paint splat, broken window]
        --> [ploughing]

    @output
    
    {

        The tractor and the cat, Molly, went down the lane.:{
                "cumulative_abstract_terms":["tractor", "cat", "lane", "bee"],


                --------------------------------------------------------------
                hmmmmmmmmmm jump.
                perhaps a map from the visualisable to all we know about it?
                e.g.

                "The [1] and the [2], Molly, went down the lane":{
                    [1]:{
                            "visualisable":tractor
                            "variant":null / base version
                            "action":null / unknown / base action
                            "location"?: null/ base / unknown / presumably farmland.
                    },
                    [2]:{
                            ...
                    }
                },
                "[3] passed a [4]":{
                    [1]:{
                            "visualisable": [1], [2]
                            "variant":null / base version
                            "action":null / unknown / base action
                            "location"?: the lane
                     },
                     [2]:{
                            "visualisable": "bee"
                            "variant":null / base version
                            "action":null / unknown / base action
                            "location"?:  null/ base / unknown / presumably farmland.
                     }
                }
                            
                --------------------------------------------------------------

                importance?
                    - which are the most important visualisables in the sentence?
                        - based on prevoius context?
                        - based on what has recently changed (obviolulsy most important)

        }


        They passed a bee.
        It revved really loud. The cat didn't like it.
        So she poured yellow paint onto the tractor. It swished and swerved after having the paint splatter its windscreen.
        It then crashed into the lampost by accident.
        The yellow and black guy flew away.
        Its windscreen broke.
        


    """
    # 0) Split all sentences
    #    ^ NOT this file's job any more: the sentence splitter already cut
    #      them, and get_visualisable_data() handed us the pieces.
    #      e.g. ["In Egypt,", "there's a valley filled with", "It was once covered"]

    # 1)i) Resolve all abstract terms -- ONCE, for the WHOLE script.
    #      This is step 2) below, moved in front of the loop, because:
    #        - the four coreference models cost ~40 s per RUN, not per pronoun,
    #          so running them per line segment costs ~5 s a segment
    #        - they read the whole script, so they see more than a segment can
    #      What comes back is a MAP, keyed by where each pronoun IS. Never the
    #      rewritten text: the words on screen have to stay the words the
    #      viewer hears ("It was once covered", not "the valley was once...").
    script_text = join_segments(line_segments)     # "In Egypt, there's a valley filled with It was once covered"
    abstract_terms = resolve_all_abstract_terms(script_text,
                                                doc=parse_script(script_text))
    # {(45, 47): {"surface": "It", "resolved": "valley", "confidence": 0.26,
    #             "source": "models", "possessive": False, "number": "Sing"}}

    # A LIST, one entry per line segment, in script order -- NOT one big
    # {template: slots} map. Two different segments really do land on the same
    # template: "Whale skeletons." and "about $2 million." are both "[1].", so
    # merging them into one dict silently loses a segment (measured: 23 in,
    # 19 out, on script-whales.txt). Each entry is still the {template: slots}
    # map for its own segment, exactly as the pipeline handed it over.
    visualisable_data = []
    for i, line_segment in enumerate(line_segments):
        previous_line_segments = line_segments[:i]                 # ["In Egypt,", ...]
        next_line_segment = (line_segments[i + 1]                  # "by the Tethys Sea."
                             if i + 1 < len(line_segments) else None)

        # 1)ii) 
        #      i.e. Build the cumulative list of visualisables (TODO: only concrete ones?)
        #      e.g. The tractor and the cat, Molly, went down the lane. --> ["tractor", "cat", "lane"] 
        #           They passed a bee                                   --> ["tractor", "cat", "lane", "bee"] 
        line_to_found_visualisables = get_line_segment_visualisables(  line_segment, previous_line_segments, next_line_segment, abstract_terms=abstract_terms)
        # {"[1] was once covered": {"1": {"visualisable": "valley",
        #                             "variant": None,
        #                             "action": "covered", "location": "Egypt",
        #                             "kind": "reference", "identity": "valley",
        #                             "is_setting": False, "amount": 1,
        #                             "confidence": 0.26}}}

        # 2) Resolve all abstract terms
        # for each unknown, determine which visualisable it points to
        # e.g. " *it* plouged the field"  --> " [the tractor] ploughed the field
        # resolve_all_abstract_terms()
        #    ^ DONE ONCE, ABOVE, in 1)i) -- and handed in as abstract_terms, so
        #      the line above is a dict lookup and not another 40 s of models.

        # ??) 
        #    - match verbs to visualisables?

        # x) [BONUS] Find all wishy washy terms.
        # e.g. "The yellow and black guy flew away." 

        # x) [BONUS] Resolve all wishy washy terms.
        # e.g. "The yellow and black guy flew away." -> "The [bee] flew away"

        visualisable_data.append(line_to_found_visualisables)

    return visualisable_data

# ==============================================================================

_CACHED_VISUALISABLE_DATA = {}

def get_visualisable_data_for_line_segments(line_segments):
    """
    The same thing as get_visualisable_data(), for callers that are already
    holding the line segments as plain strings.

    @input line_segments = the split sentences, as text.
    e.g.
     ["In Egypt,", "there's a valley filled with whale skeletons.",
      "It was once covered by", "the Tethys Sea."]

    @output exactly what get_visualisable_data() returns -- one entry per
    line segment, in script order.

    Two callers want it this way round:
      - 3-manual-tagging, which is showing the split lines on a page and
        never had Chunks in the first place
      - anything re-reading a saved split out of a json

    Cached on the segments themselves, because the manual tagger recomputes
    on every save and 1)i)'s coreference models cost ~40 s a run. Same
    segments, same answer, no models.
    """
    key = tuple(line_segments)
    if key not in _CACHED_VISUALISABLE_DATA:
        _CACHED_VISUALISABLE_DATA[key] = _build_visualisable_data(list(line_segments))
    return _CACHED_VISUALISABLE_DATA[key]

# ==============================================================================

def _DEBUG_output_resolved_visualisables(visualisable_data):
    """Print what we worked out, one line segment at a time, with the NAMES in
    the brackets instead of the slot numbers.

        [valley] was once [covered]
          [1]  valley          reference  variant=None  action=covered  in=the Tethys Sea
          [2]  covered         action     variant=None  action=None     in=the Tethys Sea

    A "*" after the slot number means it is the SETTING — the thing that goes
    in the background with everything else on top of it.
    """
    for line in visualisable_data:
        for template, slots in line.items():
            named = template
            for slot, v in slots.items():
                named = named.replace(f"[{slot}]", f"[{v['visualisable']}]")
            print(f"\n{named}")
            for slot, v in slots.items():
                setting = "*" if v["is_setting"] else " "
                print(f"  [{slot}]{setting} {v['visualisable']:<20} {v['kind']:<10}"
                      f" variant={str(v['variant']):<14}"
                      f" action={str(v['action']):<10} in={v['location']}")


if __name__ == "__main__":
    # The whole thing, end to end, on a real script:
    #     uv run myownstuff.py                  (script-whales.txt)
    #     uv run myownstuff.py ../script-rome.txt
    from sentence_splitter import split_text_into_sections

    script = Path(sys.argv[1] if len(sys.argv) > 1
                  else Path(__file__).resolve().parent.parent / "script-whales.txt")

    print(f"=== {script.name} ===")
    sentence_splitter_output = split_text_into_sections(script.read_text())
    _DEBUG_output_resolved_visualisables(
        get_visualisable_data(sentence_splitter_output))
