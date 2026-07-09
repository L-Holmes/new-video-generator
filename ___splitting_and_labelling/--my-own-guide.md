


# Estimating the previous words


# ???

Media type to approx how often it should appear:

Stock: 0.7
Wikipedia: 0.3



# ???

- Start of a sentence
    - Mostly new stock


- Middle / End of a sentence
    - Mostly edits of previous

- Comma list of visualisable things (probably nouns (with optional adjectives etc.) or perhaps verbs (again, with optional adverbs)
    - group
    (so first one is new stock, with modifier 'group'.
    (rest are then edit previous, with modifier 'group'.
    i.e. is_noun_list,

- is_location
    - Map

- is_quote_or_speech,
    - typography

- is_big_number_or_statistic,
    - ??? 
    - a graph or something...

- is_year_or_date,
    - decorate previous, with text saing the year/date...

- is_famous_person_or_thing,
    - place on one of our backgrounds
    - cut out the subject (decorate, on object mode)
        -> TODO do we have a shortcut where it just asks the user to 'click' the subject(s) (and then the rest of background will be removed)



    ---------------


----
# When determining what we should suggest as the search term;

    Determine the theme
    ==> [what use theme for] (e.g. Roman)
    Determine the features / 'what is' identified key words are.
    ==> e.g. 'Rome' -> City, Italian
    etc.

determine the time period (ignore if its recent and not historic) -> get the year / era (e.g. victorian or ancient egyptian or 1600s or 17th century etc)

For each, continuously calculate the score to determine if we show it..
Scoring based off of;
- Whether its a captialised noun (much more key, very high) (e.g. Leonard Nemoy)
- How often the word/concept has been mentioned previously (high)
- Whether it is a noun (high)
- Whether it is a verb (low)
- How close it occurred to the current sentence (i.e. the previous sentence would score higher... a sentence that was 15 entries back would score lower, but not zero as previous context is still important)
