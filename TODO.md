
find stickman-CACHE -type f -name "*.json" -delete && rm -rf stickman-OUTPUT/output.mp4

----

tell me where to add stuff in an idiot proof way. before and after....

------------------------------------------------------------------------------------------------------------------


3:40pn;
- rmeove the 'text ready to be placed' thing..  
    - Should just automatically be ready to place as soon as there are > 0 letters (if < 1, obviously not)
    - and so the text size should also show if there are > 0 letters... (exculding whitespace of course!)

- Highlighter?

repeated things for common things.
i.e.
what automated things can I detect just from the script though? Like;
- questoins (question mark comes on screen)
        - sentence with a question mark in it, gets a question mark on screen.
- date on screen in like the top right or something, when a date is mentioned... 
    - sentences that have dates, get that date on screen.
    - (or just a year)
    _- (do i do a timeline???)
- Times
    - analogue clock on screen!?!??!?
- Percentages
    - animated bar / bar chart etc
- Measurements / distances
    - In Km/[american units]
- Monetary amounts
    - In £ [and euros / USD / yen...]
- 

------------------------------------------------------------------------------------------------------------------


THEN:

------------------------------------------------------------------------------------------------------------------
THEN:
- actually create the rules... for when to add certain scenes / sound effects etc.
    - don't shy away from it compadre!

------------------------------------------------------------------------------------------------------------------

Grid background (2 or 3 variations)
---> then two, up to five pictures layered on top, like a collage, each slightly rotated
(Perhaps also with slight box shadow etc. to look like it's been placed there...) 

------------------------------------------------------------------------------------------------------------------

THEN:
for when a map is shown...


Add ability to show a map when:
- place names mentioned..
- show both places on the same map
    - but create two pictures...
        - so actually, this will be a new media type... 'addanothermappoint'... (?)
- ==> Ideally I want to mark where is is on the map... If it's a country, show world map, if it's a county/town/city, show national map... Etc. and have a marker of the place I'm after...
- (if it can't be done automatically, ask user to click where it should go, then add a pin and then text of what the place is called...) 

------------------------------------------------------------------------------------------------------------------

Other ideas:
- stock fallback
    - (if all the stock is just rubbish... do i allow backup of generate ai image?)
- 2 to 3 brand colours? (for like labels.. text .. arrows.. stock.. etc.)
- highlighting layered things
    - like adding shaddow...
    - gaussian blur whatever that is...
    - etc.
- slight rotation for layered things...
    - hmmm . yeah so either layer on prexisting background
    - would this be a variation of the 3 row???
- maybe transitions etc. for when I place something in a scene manually?? 
    - (even if i place multiple items in a scene? - e.g. adding 5 coins... could have default it adds them one by one, cutting one after the other... (unless the length of the clip isn't long enough?)
- graphs / bars / ...
    - Counter animations
    Numbers tick up, bar fills, pie chart draws on. Perfect for stats you mention.
    - hmmm - do i use manim for this? and generate code to then generate the thing? or what? 

More questionable ideas:
- masked text title cards
    -  stock plays inside the letters. Works for titles and chapter cards.
Comic panel grid
    2x2 or 1x3 panels for rapid examples. Each panel gets its own mini caption.



------------------------------------------------------------------------------------------------------------------

------------------------------------------------------------------------------------------------------------------
------------------------------------------------------------------------------------------------------------------
hmmm




------------------------------------------------------------------------------------------------------------------

# ALL OTHER NOTES:

----
Hmmmmm..
Do i add an easy way to have specficialy styled text? e.g. at a slight rotated angle and in that pixellated style? 
    -> and then anything else I would commonly do like adding arrows that have a certain appearance? (or anything else like that???)

    WAIT - am i not just making my own paint at that point???

------
Hmmmmm....
what do i do if all of the stock images are just terrible?
--> Like there isn't just a jar of nutmeg or a cupboard...

-----

add an option:
- 'same as previous'
    
--> or same as previous but rotate the other way (if its an image slightly rotated on a background... lol...)?????

---

- add the same affects to all stock footage to make it all look aligned????
    - (and maybe try out a sort of retro appearance to it?)
    - apply same affects to all footage...
        - film grain... crafted by cm... etc.


- add reusable rules... the youtube rules
    - e.g. things part of the same sentence / same concept... should be ai edit, not new pics...


TODO:
- do i get some 8 bit music in the same style as mario etc, in order to fit the theme???

-------------------------------------
-------------------------------------

plus:
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


later:
- for stock footage, apply same edits to all-- like a subtle film grain or whatever...

-------
- then for two dollars...
    - one dollar coin comes on screen, and then another one, and then the equals sign to the jar of nutmeg...


-------------------------

TODO:

- get the stock footage things once just to get all of the explainer backgrounds like crinkled paper, slow black static, grid background, etc. that i want...
--> will then need to make a list of scenarios and when i want them..
    - e.g. always wikipedia for named things...
    - e.g. always use 'bops' on the rule of three thing...

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


--------

NEXT:
Fix the words on screen thing...
    - i wonder if we could add a manaul thing, seperately, like 'generate_manaul_timings.py'
    --> where it plays the audio at like 0.5x speed, and then every time there is a word, the user clicks (or presses enter or space), or if you press backspace, it will rewind back to the prevoius word.
    --> ideally it would also show the words on screen, and highlight the one you're up to (since we have access to the script and the input audio...)
    or is this just annoying...

-----------------

TODO MEGA MASTER PLAN:
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
- hmmm other ways to add more scenes with the things I already have...
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


---------------

FABLE TODO
- create full automated test suite
--> including for the sentence splitter...
    - For the first iteration:
        1) generate an example test with lots of variation in sentences
        2) run it through the current sentence splitter
        3) assign the outputs of the current sentence splitter as the expected results for the test (so theoretically, after the first run, all tests will pass
            --> but instead of having the test be the whole thing, make it split into sentences... so each sentence is its own test
        4) But here's the key additon:
            - make it easy to add variations to passing tests... so each test can have multiple correct answers...
            - but it must be easy for the coder to add new passing examples.. so make that very easy to understand..
                - e.g. like an array of arrays of correct answers...
                - so then hte user just adds a new array with the correct answer (i.e. because the sentence splitter outputs arrays...)
            - and make it easy to search for... e.g. so that the user can easily just search for the sentence and be at the right place




----------------------------------------------------------------------------------------------------------------
----------------------------------------------------------------------------------------------------------------
----------------------------------------------------------------------------------------------------------------


BETTER STOCK NOTES:


Zoom cuts. 

### edit
Slow zoom in/out
Subtle handheld shake
Push-ins
Parallax effect
Speed ramps
Tiny rotation/drift

### cut down
- trim.

### layer graphics on top
captions
arrows
circles
HUD graphics
maps
animated text
UI overlays
charts
motion tracking labels


### relevant sound effects

whooshes
ambient city noise
keyboard clicks
camera shutter sounds
bass hits
risers
transition impacts

### colour grade everything into one style
one LUT,
one grade,
one contrast style.
(Try equal: contrast, saturation, white balance, etc.)

### add subtle imperfections / changes to not look like every other stock footage
E.g.(?)
film grain
blur
chromatic aberration
bloom
halation
light leaks
motion blur
...

### cropping
crop tighter
reframe subjects
create closeups

### speed manipulation
slow motion for emotional moments
hyperlapse speedups
freeze frames
reverse cuts
speed ramps
E.g. 100% → 300% → freeze → text appears

### combine multiple related clips in sequence
E.g. not just computer.. 
But monitor, keyboard, typing, hard drive, etc.. 

### transittions sparingly
hard cuts
motion blur cuts
whip pans
light directional transitions

### masks and depth(?!?!)
Put text BEHIND objects
Foreground blur layers
Duplicate subject cutouts
Fake depth

### cut visual changes to music????
Cut:
zooms,
transitions,
text pops,
flashes,
clip changes


----------------------------------------------------------------------------------------------------------------
----------------------------------------------------------------------------------------------------------------
----------------------------------------------------------------------------------------------------------------



seperate character from background:
- Meta segment anything 2 ????
???
- Create seperate foreground and background, then add parallax effect?



- synced music
    - have a theme, switch to different footage on the beats of a song??



    - speed ramps:
    - 100% -> 150% for boring middle section, snap back to 100% on beat. Great for walking, clouds, city timelapses.


    - character reactions library
    - Reuse a small library of stickman poses: thinking, pointing, shocked, shrug. Trigger by sentiment of line.



-----
could we make it a bit more straight foreward for media types?
Like instead we have a sort of struct representing things like 'needs external candidates' etc.
and instead of the current media types struct, we literally define the keys which are the media types, and then a map to a struct?
e.g.
{
    STOCK:MediaType(True, False,True,False,False),
    WIKIPEDIA:MediaType(True, False,True,False,False),
    ...
}
ACTUALLY NO - thats just an object with extra steps!
but don't want database.. or csv.. or anything not hardcoded...

but don't want the over abstration that comes with objects...


