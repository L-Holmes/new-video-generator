
-------------


x) perhpas add the other maths things whilst claude ready?
at:

TODO:
I'm creating a video editting app, and using manim to generate the graphics. I want maybe just a selection of the main graphs that would cover like 95% of circumstances. 
List the options that I should add. 
(This is a general use tool,covering a wide range of topics. For my use it will be more logical and informative, rather than decorative).

claude --resume e4bce00f-7f05-44e7-a6ae-fd7183302c6d


...


- graphs / bars / ...
    - Counter animations
    Numbers tick up, bar fills, pie chart draws on. Perfect for stats you mention.
    - hmmm - do i use manim for this? and generate code to then generate the thing? or what? 




8) When I'm doing manual intervention, where I place my own image in the stock footage folder... is there any way we could open a window which the user can just paste into, which will then paste the image which we are using for that? (as an alternative option available... so they can manually place in the folder, or they can just paste in a picture that they've copied...

Also, if we manually add to the folder... and we've entered the number we are resolving.. can it not auto detect the most recent file that was added to the folder, and ask you to confirm it? (and ideally open a window which displays the image, so the user can confirm that they want to use that image/clip for the given entry...?)


10) On the manual tagger, need a way for me to quickly log the change I made and why.

so everytime I either: join a line, split a line, Or change a media type [and search text], it should ask for the reason why.



It will then update an ongoing log file we have, which will do before and after, and then give the user's reason (if they gave one) 



12) need an actual reliable thing for the words on screen... and perhaps have a flag that we change in config that determines whether it shows like the whole sentence on screen, but one word at once... or just each word one by one as it does now..


===========================================


Ideas:
- extend previous
    - take the previous, put it side by side with a second image. 
    e.g. Rome was not built in a day || but in burned in six
        -> rome construction -> rome construction + fire

------------------------------------------------------------------------------------------------------------------
------------------------------------------------------------------------------------------------------------------
------------------------------------------------------------------------------------------------------------------
------------------------------------------------------------------------------------------------------------------

----------

find stickman-CACHE -type f -name "*.json" -delete && rm -rf stickman-OUTPUT/output.mp4


rm -rf TESTER-OUTPUT/output.mp4 TESTER-CACHE/output_video_final.mp4 TESTER-CACHE/{joint,blank,map,collage,decorate}_scenes

----

tell me where to add stuff in an idiot proof way. before and after....

------------------------------------------------------------------------------------------------------------------
------------------------------------------------------------------------------------------------------------------
------------------------------------------------------------------------------------------------------------------
------------------------------------------------------------------------------------------------------------------


TODO THEN THEN:
- for when a map is shown...
    Add ability to show a map when:
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



---------------


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


OVERAHALL THE AUTO WORD-FINDING SYSTEM... MANUALLY!!!!! NO AI!!!

Theme, context, time, place. 

(These should be carried through all entries...) 

Problem is, the theme may change... And same with the time and place... 


=====
## translation engine [open source]

OPTIONS:
- Harper
- [Open source small AI option]



## Abstract identification engine (give full preceding text, ask it to identify what a particular "it"/"he"/"they" etc. refer to 


## theme / context resolution 
(Theme / time / context of overall thing, and then at a particular point, and perhaps "theme up to this point" (current line and proceeding text)) 

## details about a thing 
Take a reference to a "car" at a point in text, then based on previous words I get an outfit of all the details describing that thing. 
E.g. "red Toyota Aygo car"

And / or keep track of that thing as we go through the text. 
E.g. so theoretically could wire into code that will colour code repeated things, and then I can hover over the word, then at the side it shows the full description (of the Aygo...)


## auto complete engine? 

------

def get_search_term(line:str, preceeding_lines:str[], full_text:str):
    """
    Get a line, which shall be shown as stock footage / wikipedia / maybe even AI...
    Generate a search term for it.

    """

    # 1) Identify all visualisables

    # 2) Replace abstract words with concrete words

    # 2.5) The thing about identifying all details about a noun
    # (don't know if we'll want to pre-generate the data set of nouns, and then identifying where they repeat in the text and their full descriptions...
    # obviously it'll be dependent on the context up to that point... like if in the script we haven't revealed who a person is... then we don't want to show the final person...
    # if we've just said 'car' so far, we don't want to do the full red toyota etc... (I don't think at least...)
    


    # n) Correct grammer for final search term
    search_text = _generate_search_term(z)


def _generate_search_term(search_text):
    """
    Adds correct grammer

    e.g. Jar Nutmeg -> Jar of Nutmeg
    e.g. Nero Emporar -> Emporer Nero
    """
    # (1) === Grammer engine ===


    # (2) === translation engine to translate from english to english ===

    return search_text

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
