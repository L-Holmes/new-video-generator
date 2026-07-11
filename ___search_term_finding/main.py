




def get_search_term(line:str, preceeding_lines:str[], full_text:str):
    """
    !!!!! THE MAIN ORCHESTRATION FILE!!!!!
    Get a line, which shall be shown as stock footage / wikipedia / maybe even AI...
    Generate a search term for it.

    """

    # 1) Identify all visualisables
    # (e.g. Nouns mainly ... perhaps some verbs...)
    visualisables = _get_visualisables(line)

    # 2) Replace abstract words with concrete words
    visualisables_with_abstract_sorted = _replace_abstract_with_concrete(visualisables, preceeding_lines) 

    # 3) Find out & apply the theme
    theme_words = _get_theme_words()
    conrete_visualisables_with_themes_applied = apply_theme_words()

    # n) Correct grammer for final search term
    search_text = _generate_search_term(z)

    # hmmm
    # -> will we then need a way to prioritise things?  (e.g. do we search for 'jar of nutmeg' or 'bag of stones'..
    #  ---> Or will we need to even split this and search for multiple things!?!?!? 

    # also: All other things that we've done in the current search term determiner...
    # --> Like... knowing which words pair with which words... 
    #     e.g. if from previous context we have 'Roman' and 'AD' as identified themes... do we add 'AD' if the user is talking about a cup/waterfall? obviously not.. but the code wouldn't know that or what to pair.. or whats relevant at this point...

    

def _get_theme_words(fullText)
    """

    TODO 
    - we'll want seperate themes / context ...
        - (a) For the full entire thing
        - (b) For just the current section (i.e. the current paragraph / chapter... even though that's hard for us to determine!)
        - (c) For the text up to this point.
        - .... not sure how this will affect our theme tagging on the visualisables list though! 

    1) Determine what the themes / context / etc. are
    2) Turn theme words into sort of descriptions that we can use in the search term
    e.g. Rome -> Roman

    
    Also)
    - Thinking like we have perhaps a dictionary? of different types of themes..
    - like era / time / place... 
    - ==> because then when constructing a search term, we know that we insert those details at specific points in the sentence
    - e.g. "[roman] bathouse [england][bath] [500AD]"

    """

def _replace_abstract_with_concrete():
    """
    e.g. "it drove so fast" => "The red toyota aygo Mk2 drove so fast"

    hmmmm... where do i draw the line for additional details?
    ... do I eliminate anythat aren't related to appearance?
    remove non-obvious / non-key ones? (e.g. it had a microsopic black dot on the engine...)
    do I pass in like 'additional' details as well?? would we even need them??
    """

    # 2.5) Identifying all details about a noun
    # (don't know if we'll want to pre-generate the data set of nouns, and then identifying where they repeat in the text and their full descriptions...
    # obviously it'll be dependent on the context up to that point... like if in the script we haven't revealed who a person is... then we don't want to show the final person...
    # if we've just said 'car' so far, we don't want to do the full red toyota etc... (I don't think at least...)



def _generate_search_term(search_text):
    """
    Adds correct grammer

    e.g. Jar Nutmeg -> Jar of Nutmeg
    e.g. Nero Emporar -> Emporer Nero
    """
    # (1) === Grammer engine ===


    # (2) === translation engine to translate from english to english ===

    return search_text
