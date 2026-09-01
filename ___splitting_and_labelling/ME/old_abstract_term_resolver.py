

def resolve_all_abstract_terms():
    pass
    # 0) Find abtract terms

    # 1) Here is the text up to the [abstract point], and then next sentence
    # e.g. 
    #      "The tractor and the cat, Molly, went down the lane. They passed a bee. The cat didn't like it."


    # 2) Call _Large Language Model_, ask it to resolve what the target abstract word is
    # e.g. 
    #     "What does the [It] refer to?"


    # 3)
    # try and resolve manually:
        # - what words describe that thing / what its doing? and what makes the most sense for each?
            # (e.g. e.g. "It had a long furry tail" is much more likely to be a cat than a house.)
        # for all visualisables:
            # - How close that word is to the current one
            # - num. references in the text of that word
            # - num. references in the text of that word, weighted for how close they are to the current point (closer = higher weighted)
