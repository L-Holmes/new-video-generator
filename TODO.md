

TODO:
ask claude to fix the most current issue after 10:10pm...


TODO:
next update: for WORDS_ON_SCREEN.py
- have a flag on the words thing, that, instead of doing the words as a sentence, it just shows one word at once.
    - lets have this turned on as 'true' by default.


TODO:
- try again with stickman explainer scenes...
- then do todos on either phone or below..  
    - do i consider doing more functional tihngs rather than the stock footage processing?!?!??!
    - do i organsie things like move the below definition??!??!
    - do i create a true read me???


TODO:
- do i get some 8 bit music in the same style as mario etc, in order to fit the theme???

-------------------------------------


lets also now integrate the 3_row thing, but for stickmen/ai...
--> so if its stickmen_3_row, it will fetch the ai image(s) as usual... 
then it will treat them like a regular 3 row and add them side by side...
(using basically the same logic if that's possible...)


--------

other other:
- update the ai_edit.py.
    - we don't currently pass in the reference images...
    -> and then maybe mixing in some just basic edits as well, like to spruce up a scene midw way through (like raising the sword of alaric the goth...)

plus:
- defo mix in some real stock footage at some point... with my professor dude... or on like a cinema screen...
    - have a few things that are reused between videos for consistency.
    - i.e.:
        - wikipedia for named things (especially obscure named things...)
            - e.g. 'Tutankamun'
            - e.g. 'Alaric the goth'
            - e.g. 'accona desert'
        - maps from [google or open street map or something?], with a dot on, for when a place is named...
        - stock footage from pexels for things that work well with stock footage;   
            - e.g. specific places / mountains..
                - e.g. 'the banda islands' or 'manhatten' or 'the great pyramid'
                - e.g. sahara desert


also stop ai detection:
- make all similar colours the same
- overlay noise (i.e. like 'image noise' as they call it to confuse their thing)
- whatever else would be useful...


later:
- for stock footage, apply same edits to all-- like a subtle film grain or whatever...

-------
then integrate with joint...
e.g. 'stickman-joint-3'??? will i need stickman-joint-3??? 
and then handle in the same way we do regular joint things with the positions etc...

--------

hmmmm...
- maybe zoom in, reveal the cuboard, and then in the next scene the same cupboard but with a jar of nutmeg on the side...
    - or maybe it spins around and reveals that it is nutmeg...
- then for two dollars...
    - one dollar coin comes on screen, and then another one, and then the equals sign to the jar of nutmeg...


-------------------------

TODO:
- think how to optimise the start of the video..
    - then add those changes... Think what I would want, then code it! 
- work out how to process the audio 
    - getting that effect of constant speaking that that other youtuber does
- work out how to automate the process of generating the code that says how to generate scenes... (that input json...)
    -> I guess this will be AI choosing from a few preset options?


- get stock stuff   
    - will need sound effects, and when to use them.
        --> look for a youtube starter package with the beeps and bops and what not...
        --> (could maybe get music later on)
    - may want to pay for like the stock footage things once just to get all of the explainer backgrounds like crinkled paper, slow black static, grid background, etc. that i want...
--> will then need to make a list of scenarios and when i want them..
    - e.g. always wikipedia for named things...
    - e.g. always use 'bops' on the rule of three thing...

- start generating actual videos...
    - use the actual sentence splitter... 
        - if there are issues... fix one by one...
    - inspect the output video... if there's something wrong, fix it...


- look at channels like 'adam something'... think what they do, and how i want to achieve that...


-------


consider:
- just simple things like:
    - overlaying text on the stock footage to keep things easy
    - adding blank background and then my text coming on the screen as i say it..
    - adding sound effects...?
    - things like just presets - like adding question mark over existing picture for question... etc.

Then:
- maybe just for like lists (e.g. with like 'and'), add the thing to show three things one by one on a shared background...
--> perhaps for now just manually tag the lists? not sure how i'd do this!
    -> type=stock (default)
    -> type=row(3)::1 (first element of a row three) -> validatoin ensures that there are three of these in a row...

-----------------

TODO MEGA MASTER PLAN:
- todo: go through the below notes...
- ... 
- Create a youtube formula!
    - add to list of rules:
        - go through the big best youtubers. (will of course need a list first! I think i have on already on the website...)
        - analyse what they do.. how they structure, and add to my list of rules
        - + perhaps also have a seperate document for general patterns underneath each.. (I could make a youtube video on this!)
    - .......
    - what lines should have what...
    - ... have like 5 options to pick from for certain things...
    - ... much like the script.. have a sort of formula or a few different variations for the script...
    - Add line categroisation stage
        - Go through the text.
            - each line will need the context of the other lines/scenes
            - identify what type of line it is. 
            - if its linked to other scenes, need to identify those (e.g. via index or something)
- also just research 'youtube formula' both on youtube and online...
    - i assume it'll be mostly script guides but just write them down....

----------

TODO:
- Add method for determining joint images...
    - AI the original text?
        - e.g. short, like 1 or 2 word sentences should be part of joint pics...
        - things like landscapes of places etc. should be stock footage (just anything that works well as standalone stock footage...)
        - subjects / people should ideally be cut out and put on a background...
    - AI the new text with output of what pictures are used? (probs easier for determing people etc...) (may also identify people images for wikipedia fetching?)
- Add better sentence splitter...
    - perhaps with loads of tests...
    - ask opus to add a few more clauses to the script generator thing.
- Link up wikipedia etc. if possible...
- For longer things... like:  'Manhattan. Yep... New York City was traded for nutmeg.'
    - e.g. show manhatten and then manhatten with text overlayed at slight angle: "traded for pepper"
- do i intersplice text coming on the screen as I say it, for dull moments? 
    - is that even possible? Like the one by group of 5 words flash on screen in successive order, synched to the script???
    - words on screen.
    --> probs have a seperate python file that does this, and interacts with the audio script synchronizer thing and how it uses that thing to determine when words go...
- hmmm
    - do i add a thing like cgp grey where you zoom out and then you show the same picture but on like a whiteboard and then a stickman, representing the teacher, sitting there???
    - what else could i use this method in? 
        - (like a buid a library of reactions and add them at appropriate times in the video????)
- maybe....
    - can i add a system to identify sort of stock footage applicable scenes?
    - because only clearly visible landscapes or whatever is good for stock footage... or maybe like black pepper type things... 
    - but the rest will need to be more specific????
- YEAH!
    - I just need to build a system..
    - so like 'for this [group of lines]... i do this...
    - ... if longer than x seconds/words and its [this type of line].. i do this..
    - ... and then I add this at a random point..
    - SO:
        - need to somehow categorize lines and groups of lines so that i can identify what to do!!!
        - then make a document that details all of the rules
        - !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        - !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        - !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
        - (need to watch some youtubers to get some ideas! ideally the good ones...)
- hmmmm!!
    - maybe just the explainer thing with teacher stickman each time?
    - analyse the intros of the best faceless youtubers...
    - get a plan for an intro.. or a few variations of intro which will always just work real good straight away...
- hm
    - do i overlay like question marks that are kind of transparent on screen if we have questions?!?!?!?
    - what other repeatable things are easily recognisable with NLP???
- hmm
    - would it be possible to integrate my stickman idea?
    --> like match the sentiment of the sentence to a stick man?  like always have a stick person for comments people... (everytime I say "now, you may be thinking..."
    --> or have the stick man at the desk or in the science lab...
    
------------

TODO:
- Add things to make the intro better
    - e.g. ??? whatever like highlighting or whatever to make intro more interesting...
        - highlighting (feels manual...)
        - darkening rest of area apart from the thing you're talking about (again, feels manual...)
- Add the sound effects thing.
    - Download loads of sound effects
    - Have a system for adding the sound effects...

    ------------

TODO LATER ISH
- start planning the finance videos
- find out how I can do that audio thing done by https://youtu.be/AGkRkNuhO_o?si=Jf38aI0woBjtVYAd ... 

------------

TODO LATER:
- maybe link up the other image providers??!?!?!?!? (or actually... just stick with pexels for now...)



- load the background.
- Get picture of the person
- pipe into remove background
- compose that on top of the animated background




# Review against plan:

1) Add things one by one
- Add one by one, build on previous image..
2) Paint..
- Cut out pictures of subjects, places on blank background
- ==> shadows, colour grading etc...
3) custom animated
- e.g. custom animated graph (e.g. bar chart or line going up.. . numbers ticking up...)
4) visual hierarchy
- blur unimportant
- circle, highlight, arrow important..
- bigger, brighter, moving...

5) ==> reuse library of icons...


"how can I design this visually step by step" 




----

debian 13
- manual method of locking the screen? (e.g. for if I'm out and about??? (but when im just at home it doesn't lock?)
        - also annoying with that error box that has started popping up saying that the screen didn't lock
