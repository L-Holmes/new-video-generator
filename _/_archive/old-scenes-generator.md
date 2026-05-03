

# old main generator

# a) ---- Determine the overall theme ----
    # i.e. anything that would be important to what the visuals of each scene would look like.
    # - e.g. 'Japanese'. A 'tea ceremony' is different than a 'japanese tea ceremony'. So if a script is about japan, then 'japanese is key.

....


# 2)
# For each scene,
# break down into "clips".
# Each should be ideal for watch time-- e.g. less than 8 seconds
# --> generates a file showing the scenes and the clips, and the length of each clip
# e.g.
# #### scene 1
# $ the white stork landed upon ~ 3
# $ the red farm house ~ 4 


# 2.5)
# Manually tag each 'scene'. 
# - Show user each scene
# - user tags as "image" or "stock vid" or "custom step by step diagram"... etc. (or even AI generated...)
# 
# --> generates a file showing the scenes, the clips, and the tags... 


# 2.6)
# Determine the keywords for each of those scenes (used for searching for clips. 
# - could maybe pipe keywords and original scene into AI to get a search term?
# - --> again, generate new file for clips -> Search term... (again, manually editable) 

--------------------------------------------------------------
--------------------------------------------------------------
--------------------------------------------------------------
--------------------------------------------------------------
--------------------------------------------------------------



# old scenes generator

# 2 ---- Split into sections. ----
# e.g. if a section is all about 'how buildings are built'- this is generally quite a large section
# e.g. may be multiple paragraphs. 
# Think: if you were adding headings to the script... where would they go...
# e.g. "The empire state building is really big. Built in Manhattan in the 19th century. Back in 1946, the technician John Ford the second created a new carburettor for the lift in the skyscraper where they drunk chanoyu tea, which would go on to revolutionize the entire world."
# IN FACT - assume we have done this already

# 3) ---- Section themes ----
# for each section, determine the themes explored.
# (can be none)

# 4) ---- Splitting ----
# 4)a) Split into natural phrases. 
# (a visual 'scene')
# This is generally between 1 - 5 sentences in length. Most will be around 1-3 sentences... with 2 probably the most common.
# e.g. "a new carburettor for the lift in the skyscraper where they drunk chanoyu tea"

# 4)b) Split each phrase into sub phrases. 
# --> sub phrases shouldn't be more than 20 words long (8 second rule)
# --> e.g. phrases my have a noun associated with them. Or something you can picture...
# e.g. may want to start by breaking on punctuation. Then doing some additional checks on each and splitting up sub-sentences that happen to not have punctuation.
#   "the dog jumped in the back of the car before the man who was fixing the light could turn around" -> "the dog jumped in the back of the car " and "the man who was fixing the light could turn around" 
# e.g. 
#   --> "a new carburettor" 
#   --> "for the lift in the openAI skyscraper" 
#   --> "where they drunk chanoyu tea"


# 5) ---- extract imaigery / nouns ----
# 5)a) For each sub phrase, extract the imagery / nouns (use nlp)
# e.g. 
#   --> "a new carburettor" [carburettor]
#   --> "for the lift in the openAI skyscraper" [lift, skyscraper]
#   --> "where they drunk chanoyu tea" [tea]

# 5)b) For each sub phrase, extract the imagery / nouns (use AI, to get any that may have been missed)
# --> Must be an actual word from the text itself!
# --> Think of things that may not be in the nlp libraries... 
# --> Only adds to the list
# e.g. 
#   --> "a new carburettor" [carburettor]
#   --> "for the lift in the openAI skyscraper" [lift, skyscraper, openAI]
 #   --> "where they drunk chanoyu tea" [tea, chanoyu]

# x) ---- apply the themes ----
# - both the overall theme and the section theme



=====================================

# Old imigary fetching:


# 5) ---- extract imaigery / nouns ----
    # 5)a) For each sub phrase, extract the imagery / nouns (use nlp)
    # e.g. 
    #   --> "a new carburettor" [carburettor]
    #   --> "for the lift in the openAI skyscraper" [lift, skyscraper]
    #   --> "where they drunk chanoyu tea" [tea]

    # 5)b) For each sub phrase, extract the imagery / nouns (use AI, to get any that may have been missed)
    # --> Must be an actual word from the text itself!
    # --> Think of things that may not be in the nlp libraries... 
    # --> Only adds to the list! (just do a join of the results or whatever- remove overlaps...)
    # e.g. 
    #   --> "a new carburettor" [carburettor]
    #   --> "for the lift in the openAI skyscraper" [lift, skyscraper, openAI]
     #   --> "where they drunk chanoyu tea" [tea, chanoyu]

    # x) ---- apply the themes ----
    # - both the overall theme and the section theme
