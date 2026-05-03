
# AI)
- can' tdo each line one by one.
    - takes to long and the ai gets confused without context
- can't do all lines
    - ai will miss out key words... (e.g. choyu tea -> tea)
    - ai will get confused by context (it will ignore lines and think only about context...)
    - etc.

# x)

I don't want to manually define stop words.

Also, I'm just worried- does pexels care about word order?

e.g. 
in the sentence... 
they drove in a car and parked up outside of the empire states building.
the empire states building is built on a giant pedestool.
they where created by the romans.

If nlp parsed that it would be =:

car empire state building
empire state building giant peestool
romans

When in reality it would be:

empire states building
pedestool empire states building <<-- notice how it needs context of overall script such that it doesn't just return a random pedestool? This is key! I feel maybe ai will be needed for this?
romans making pedestools <<-- again needs context..


