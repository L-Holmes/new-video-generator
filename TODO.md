

(0)
According to the logic in sentence_splitter.py (which does a lot of 'splitting on visualisables'
create a bullet pointed list stating extensively and completely the definition of what a 'visaulisable' is, in NLP terms
My personal defintion is something we can represent in our video. Something we can easily picture.
something we can draw. etc. etc.
keep it straightforward and simple.
for each, give examples.
e.g. 
- All capital named nouns. 
    - e.g. "New York city", "Albert Einstein", etc...
- ...
literally that simple.

put results in VISUALISABLES_NLP_DEFINITION.txt


(0.5)
Following the guide at VISUALISABLES_NLP_DEFINITION.txt
implement the logic to identify 
keep logic straightfoward and step by step. 
ideally one main orchestration function were we can clearly see each step like a bash function.
if using algorithms / logic, prefer to use 
 pre-existing things with scientific proof, backing and reigourous benchmarking. (in the same way as how abstract_term_resolver.py, did not implement its own logic, but rather reuse that of existing tools.
         prioritise using existing reliable tools!)

To note:
- Our sentence splitter (source of the input to our main myownstuff.py file puts additional data 
  alongside each split line, mentioning why it was split.
  You may or may not want to use this, depending on if it is useful or not to you. 




(3) 
regarding:
abstract_term_resolver.py
and related:
     myownstuff.py


- my planned method isolated each sentence to only the current line, next line, and
  preceeding lines (since we don't want to spoil anything that is coming up later)...
    is that worth doing?
    would it increase accuracy etc?




-----
(2)
so our aim is to identify all visualisables and put them in a map.
- make it so we get it in the correct output method:
    so it takes as input the target line, all lines before, and rest of that sentence + the sentence after if applicable.


    e.g. input:
      visualisables = create_visualisables_entry(input_text:str, rest_of_line_plus_next_sentence:str, all_preceeding_text:str)
    visualisables = create_visualisables_entry("The tractor and the cat, Molly, went down the lane.", 
                    "They passed a bee", null)
        (again, you may want to also pass in the tags from the sentence splitter if they're useful)
    
    e.g. output:

    "The [1] and the [2], Molly, went down the lane":{
        [1]:{
                "visualisable":tractor
                "variant":null / base version
                "action":null / unknown / base action
                "location"?: null/ base / unknown / presumably farmland.   (or well, in this case we know its 'the lane')
        },
        [2]:{
                ...
        }
        (maybe we'd have [3] as 'the lane'.. but lets just see what the code produces!)
    },
        
    maybe we'll want to create a struct to represent the map.
    to note: for now we just populate the 'visualisable' value. rest are left as null/unknown etc.


(3)
- Integrate the abstract value into main code.
    visualisables = add_abstract_values_to_visualisables_map(input_text:str, rest_of_line_plus_next_sentence:str, all_preceeding_text:str)
    












----------------------------

AUTO SEARCH TERM DETECTION:
(do i write this myself?)

- for the logic that auto detects the search term...
    - if we have a prevoius line like: "there is a phone box standing in a field in japan", then...
    - and next/future line: 
        "it is not connected to anything"
    - then we know 'it' is the phone box as:
        - it was the first (and thus main) thing that was introduced.
        --> the other details of '*in* a field' and *in* japan, are just details about the phone...
        --> we also know that 'connected' is related to 'phone box' as they commonly need connections...
        - so basically, think about the logic i just mentioned (identifying the key noun subject (if there is one), and differentiating other nouns / visualisables which are secondary to that / adding detail...
        --> so then we can more accurately give better predictoins for the most likely visaulsiable for when we encounter words like 'in' later on...
    - also, look at the context around 'in' (or words like that relating to other nouns...)
        --> if there are other words like 'connected', then think of the probability of that word being related to each of the candidate nouns/visualisables (e.g. 'phone box' / 'field'), and get scores of all that..
    --> add those as two extra stages for us to get more accurate noun associations
    e.g.2.- same with later on... it says 'locals call it the wind phone'... 'wind phone' is obviously relatded to the phone box... and then because we already know htat phone box is a 'higher up' / bigger 'importance score' (a new metric!) visualisable, we also know its more likely to be referring to that anyway!
    - so yeah, add lots of logic for this


-------------


- I've prepared the tests for testing the auto tagger.  
    - see the readme for how to proceed.
- once the Auto_add_mediatypes.py has been sorted:
    - go through an implement hthe logic myself for determining how to split, and what mediaypes / search terms to use.
- then:
    - start going through lots of example scripts.
    - use the manual tagger to adjust.
    - (basically start building up the list of changes that we have made...)


-------------

TODO;
- bang out each of the below
- then try an optimise the auto tagging...

12) need an actual reliable thing for the words on screen... and perhaps have a flag that we change in config that determines whether it shows like the whole sentence on screen, but one word at once... or just each word one by one as it does now..

===========================================


Ideas:
- extend previous
    - take the previous, put it side by side with a second image. 
    e.g. Rome was not built in a day || but in burned in six
        -> rome construction -> rome construction + fire

or almost like 'rearrange and add more elements to a joint image!?!?'

------------------------------------------------------------------------------------------------------------------
------------------------------------------------------------------------------------------------------------------
------------------------------------------------------------------------------------------------------------------
------------------------------------------------------------------------------------------------------------------

find stickman-CACHE -type f -name "*.json" -delete && rm -rf stickman-OUTPUT/output.mp4

rm -rf TESTER-OUTPUT/output.mp4 TESTER-CACHE/output_video_final.mp4 TESTER-CACHE/{joint,blank,map,collage,decorate}_scenes

------------------------------------------------------------------------------------------------------------------
------------------------------------------------------------------------------------------------------------------
------------------------------------------------------------------------------------------------------------------
------------------------------------------------------------------------------------------------------------------


TODO THEN THEN:
- for when a map is shown...
    Add ability to show a map when e.g. two or more place names are mentioned:
    - place names mentioned..
    - show both places on the same map
        - but create two pictures...
            - so actually, this will be a new media type... 'addanothermappoint'... (?)
    - ==> Ideally I want to mark where is is on the map... If it's a country, show world map, if it's a county/town/city, show national map... Etc. and have a marker of the place I'm after...
    - (if it can't be done automatically, ask user to click where it should go, then add a pin and then text of what the place is called...) 
    - ........
    - well.. just make it work for like 'n' name places... 
         up to a reasonable limit like 10 places...
- Add a transtiion for things like the collage???


========================================================================
========================================================================
========================================================================
========================================================================
========================================================================
========================================================================
===============================================================


THEN:
------------------------------------------------------------------------------------------------------------------

Manual TODO;
- Grid background (2 or 3 variations)
    ---> then two, up to five pictures layered on top, like a collage, each slightly rotated
    (Perhaps also with slight box shadow etc. to look like it's been placed there...) 
    --> I THINK THE PAPER MAY BE A BIT.. TACKY? NOT 'TIMELESS'...?


Other ideas:
- 2 to 3 brand colours? (for like labels.. text .. arrows.. stock.. etc.)
- slight rotation for layered things...
    - hmmm . yeah so either layer on prexisting background
    - would this be a variation of the 3 row???
    - i.e. better collage...
- maybe transitions etc. for when I place something in a scene manually?? 
    - (even if i place multiple items in a scene? - e.g. adding 5 coins... could have default it adds them one by one, cutting one after the other... (unless the length of the clip isn't long enough?)

    i.e. custom animations (manim?)
    - e.g. custom animated graph (e.g. bar chart or line going up.. . numbers ticking up...)
- look at powerpoint etc. for ideas on auto generation / arranging
- subjects / people should ideally be cut out and put on a background...????
    - Get picture of the person
    - pipe into remove background
    - compose that on top of the animated background

    --> would i make that a joint scene where i have the background as the first, then the second, user can choose to add 'wikipedia + decorate', where ...
        - would they need a seperate tab/window for them to remove background for second picture? or to click on subject using our extract subject, then make background white, then keyout the background??
        - then use that as the 'stamp' to stamp onto the existing background???


TODO LATER:
- maybe link up the other image providers??!?!?!?!? (or actually... just stick with pexels for now...)


For prod:
- ADD WAY FOR USERS TO EASILY GET AI TO POPULATE THE TAGS;
    - for the manual tagging;
        - add a button 'let AI populate this list for me'
        - at the top left is instructions.
        - once clicked, at the left side, it shows the json with a copy button (and which confirms when you copy)
        - at the right, there is a prompt that the user is expected to copy and paste into an AI agent chat.
        - it also has a clear button.
        - user then pastes the new json into the text box. code confirms whether its different (and perhaps other verification, like checking the keys are all the same...)


----------------------------------------------------------------------------------------------------------------
----------------------------------------------------------------------------------------------------------------
----------------------------------------------------------------------------------------------------------------

Other from better stock notes:
- motion tracking labels?
    - so the user sort of manually tracks a moving thing in a video, and we attach a label to it
- update the object edit, such that we can add text behind a selected subject?

????
- synced music
    - have a theme, switch to different footage on the beats of a song??
- speed ramps:
    - 100% -> 150% for boring middle section, snap back to 100% on beat. Great for walking, clouds, city timelapses.
- character reactions library
- Reuse a small library of stickman poses: thinking, pointing, shocked, shrug. Trigger by sentiment of line.
