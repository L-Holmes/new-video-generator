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

BUILT_VISUALISABLE_DATA=False

def get_visualisable_data(sentece_splitter_output):
    """
    Main function o

    """
    BUILT_VISUALISABLE_DATA=False
    if not BUILT_VISUALISABLE_DATA:
        visualisable_data = _build_visualisable_data(sentece_splitter_output)
    return visualisable_data


def _build_visualisable_data():
    """
    @input The text.
    e.g.
    "
    The tractor and the cat, Molly, went down the lane. 
    They passed a bee.
    It revved really loud. The cat didn't like it.
    So she poured yellow paint onto the tractor. It swished and swerved after having the paint splatter its windscreen.
    It then crashed into the lampost by accident.
    The yellow and black guy flew away.
    Its windscreen broke.
    "


    @generates:
    (as a pseudo intermediary step)

    [tractor][v2-with-yellow-paint-splat-and-broken-window][ploughing]

    [tractor]
    --> [v2-with-yellow-paint-splat-and-broken-window]
        --> [ploughing]


    @output


    """
    # 0) Split all sentences
    sentences = split(input_text, <end-of-sentence-regex>)

    for sentence, next_sentence in sentences:
        # 1)i)  Find all abstract terms

        # 1)ii) 
        #      i.e. Build the cumulative list of visualisables (TODO: only concrete ones?)
        #      e.g. The tractor and the cat, Molly, went down the lane. --> ["tractor", "cat", "lane"] 
        #           They passed a bee                                   --> ["tractor", "cat", "lane", "bee"] 
        line_to_found_visualisables = add_found_visalisables(line, line_to_found_visualisables)


        # 2) Resolve all abstract terms
        # for each unknown, determine which visualisable it points to
        # e.g. " *it* plouged the field"  --> " [the tractor] ploughed the field

        # ??) 
        #    - match verbs to visualisables?

        # x) [BONUS] Find all wishy washy terms.
        # e.g. "The yellow and black guy flew away." 

        # x) [BONUS] Resolve all wishy washy terms.
        # e.g. "The yellow and black guy flew away." -> "The [bee] flew away"

    BUILT_VISUALISABLE_DATA=True
    return 



# ==============================================================================

def _resolve_all_abstract_terms():
    pass

    # 1) Here is the text up to the [abstract point], and then next sentence
    # e.g. 
    #      "The tractor and the cat, Molly, went down the lane. They passed a bee. The cat didn't like it."


    # 2) Call _Large Language Model_, ask it to resolve what the target abstract word is
    # e.g. 
    #     "What does the [It] refer to?"

# ==============================================================================

def _DEBUG_output_resolved_visualisables():
    pass
