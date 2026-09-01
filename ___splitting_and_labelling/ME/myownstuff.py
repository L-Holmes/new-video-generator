"""
TODO
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
from abstract_term_resolver import resolve_all_abstract_terms

BUILT_VISUALISABLE_DATA=False
VISUALISABLE_DATA = None

def get_visualisable_data(sentece_splitter_output):
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

    """
    BUILT_VISUALISABLE_DATA=False
    if not BUILT_VISUALISABLE_DATA:
        input_list = [chunk.text for chunk in sentence_splitter_output] # ["In Egypt", "there's a valley filled with whale skeletons.", "It was once covered by", "the Tethys Sea."]
        VISUALISABLE_DATA = _build_visualisable_data(input_list)
        BUILT_VISUALISABLE_DATA=True
    return VISUALISABLE_DATA

def _build_visualisable_data():
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

    [tractor][v2-with-yellow-paint-splat-and-broken-window][ploughing]

    [tractor]
    --> [v2-with-yellow-paint-splat-and-broken-window]
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
    sentences = split(input_text, <end-of-sentence-regex>)

    for sentence, next_sentence in sentences:
        # 1)ii) 
        #      i.e. Build the cumulative list of visualisables (TODO: only concrete ones?)
        #      e.g. The tractor and the cat, Molly, went down the lane. --> ["tractor", "cat", "lane"] 
        #           They passed a bee                                   --> ["tractor", "cat", "lane", "bee"] 
        line_to_found_visualisables = add_found_visalisables(line, line_to_found_visualisables)


        # 2) Resolve all abstract terms
        # for each unknown, determine which visualisable it points to
        # e.g. " *it* plouged the field"  --> " [the tractor] ploughed the field
        resolve_all_abstract_terms()

        # ??) 
        #    - match verbs to visualisables?

        # x) [BONUS] Find all wishy washy terms.
        # e.g. "The yellow and black guy flew away." 

        # x) [BONUS] Resolve all wishy washy terms.
        # e.g. "The yellow and black guy flew away." -> "The [bee] flew away"

    return 

# ==============================================================================

def _DEBUG_output_resolved_visualisables():
    pass
