
scirpt_list= {
"deep_sea_anglerfish": """The anglerfish lives in total darkness. Four thousand meters down, pressure crushes everything. It dangles a glowing lure from its forehead, a tiny lantern made of bacteria, to trick prey into swimming straight into its jaws.""",

"sourdough_bread": """Making sourdough starts with just flour and water. You feed the starter daily. After a week of bubbling fermentation, the wild yeast is strong enough to lift a loaf, which bakes into a crusty bread with that signature tang.""",

"voyager_probe": """Voyager 1 left Earth in 1977. It flew past Jupiter and Saturn. Now it drifts in interstellar space, more than 15 billion miles away, still sending faint whispers back on a transmitter weaker than a refrigerator light bulb.""",

"library_alexandria": """The Library of Alexandria was not one building. It was a dream. Scholars from across the ancient world gathered scrolls on astronomy, medicine, and poetry, creating the first attempt to collect all human knowledge in a single place before fire took it.""",

"japanese_tea": """Tea is not just a drink in Japan. The chanoyu is slow. Every movement, wiping the bowl, whisking the matcha, bowing to the guest, is choreographed to create calm, and a single sip can stretch into five minutes of perfect silence."""
}


"""
Key principles:
- Try and make it as easy for the AI as possible- Minimal task. Simple singular task, ideally. 
- Try and do as much as possible without AI- AI isn't predictable...
"""


def get_overall_theme():
    pass

def split_into_sections():
    pass
    # split on the headings that are represented by: {heading title}

def split_into_scenes():
    pass

def split_into_sub_scenes():
    pass
    # split on punctuation

    # ask AI to split any sentences with two or more clear visual scenes into seperate scenes


def main():
    script = """
    {intro}
    The empire state building is really big. Built in Manhattan in the 19th century. Back in 1946, the technician John Ford the second created a new carburettor for the lift in the skyscraper where they drunk chanoyu tea, which would go on to revolutionize the entire world.
    {tea}
    But where exactly in the world did this tea originate? It was in the newly formed state of Okinawa. Back in the 1700s, the samurai of Japan ruled over the kingdom. They discovered Koshuta — a type of rare plant which only grows in the foothills of the Japanese Alps...
    """

    # 1) ---- Determine the overall theme ----
    # i.e. anything that would be important to what the visuals of each scene would look like.
    # - e.g. 'Japanese'. A 'tea ceremony' is different than a 'japanese tea ceremony'. So if a script is about japan, then 'japanese is key.

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



"""
Fill in this whole code, without using any AI.

(apart from maybe like the final stage?)

Use those nlp based techniques that you just mentoined:
"""


"""
 you know the things we did with AI there? like theme detection etc?
or like subsentence breaking?

Are there any existing popular well maintainced nlp based (not AI) libraries that do that already? 

And how about a library / nlp thing for just removing all common words? 
e.g. turn 'way back in 1946' -> '1946'
e.g.2. the lift in the skyscraper where they drunk chanoyu tea -> lift, skyscraper, chanoyu tea
"""



"""
No, this is clearly way off:


[The empire state building is really big.] ~ [New York City Historical Architecture big empire building really state]

[Built in Manhattan in the 19th century.] ~ [New York City Historical Architecture Manhattan 19th Built in the century century.]

[Back in 1946] ~ [New York City Historical Architecture 1946 Back]

[the technician John Ford the second created a new carburettor for the lift in the skyscraper where they drunk chanoyu tea] ~ [New York City Historical Architecture chanoyu John lift created they skyscraper where Ford tea carburettor the second new drunk]

[which would go on to revolutionize the entire world.] ~ [New York City Historical Architecture world]

[But where exactly in the world did this tea originate?] ~ [New York City Historical Architecture originate exactly did this world tea]

[It was in the newly formed state of Okinawa.] ~ [New York City Historical Architecture state Okinawa newly formed]

[Back in the 1700s, the samurai of Japan ruled over the kingdom.] ~ [New York City Historical Architecture ruled back Japan 1700s samurai kingdom]

[They discovered Koshuta — a type of rare plant which only grows in the foothills of the Japanese Alps...] ~ [New York City Historical Architecture Japanese plant discovered type Alps grows Koshuta foothills]




- the output things don't read like search terms...

- I don't want the square brackets?

- it still includes that ai text at the top... 


phrasing is all wrong. not englihs.

should be short search terms...

doesn't have the expacted key nouns... 


The empire state building is really big.~empire state building

Built in Manhattan in the 19th century. ~Manhatten 1900s

Back in 1946,~1946 text

the technician John Ford the second ~ john for the second technician

created a new carburettor for ~ carburettor for empire state building

the lift in the skyscraper ~ lift empire state building 1900s

where they drunk chanoyu tea, ~ chanoyu tea

which would go on to revolutionize the entire world. ~ earth from space

But where exactly in the world did this tea originate? ~ earth with question mark

It was in the newly formed state of Okinawa. ~ Okinawa japan map

Back in the 1700s, ~ 1700s text

the samurai of Japan ruled over the kingdom. ~ samurai warriors 

They discovered Koshuta — ~ Koshuta plant

a type of rare plant which only grows in the foothills of the Japanese Alps... ~ japanese alps plants


(as you can see the above expected is no where near the actual output)


"""


"""
expected output:


The empire state building is really big.~empire state building
Built in Manhattan in the 19th century. ~Manhatten 1900s
Back in 1946,~1946 text
the technician John Ford the second ~ john for the second technician
created a new carburettor for ~ carburettor for empire state building
the lift in the skyscraper ~ lift empire state building 1900s
where they drunk chanoyu tea, ~ chanoyu tea
which would go on to revolutionize the entire world. ~ earth from space
But where exactly in the world did this tea originate? ~ earth with question mark
It was in the newly formed state of Okinawa. ~ Okinawa japan map
Back in the 1700s, ~ 1700s text
the samurai of Japan ruled over the kingdom. ~ samurai warriors 
They discovered Koshuta — ~ Koshuta plant
a type of rare plant which only grows in the foothills of the Japanese Alps... ~ japanese alps plants
"""




script = """
{intro}
The empire state building is really big. 
Built in Manhattan in the 19th century. 
Back in 1946, 
the technician John Ford the second 
created a new carburettor for 
the lift in the skyscraper 
where they drunk chanoyu tea,
which would go on to revolutionize the entire world.
{tea}
But where exactly in the world did this tea originate? It was in the newly formed state of Okinawa.
Back in the 1700s, 
the samurai of Japan ruled over the kingdom.
They discovered Koshuta — 
a type of rare plant which only grows in the foothills of the Japanese Alps...
"""
