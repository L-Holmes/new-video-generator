
I want to update the media review stage:
- currently you can choose 1,2,3,4,5.
    -> I want another option... if I press 'e', that will bring it into edit mode.
    (something will change on screen to indicate that... e.g. like blue outlines or something)
so then.. I choose a number as usual (or press 'e' again to toggle off edit mode)

If edit mode is selected after I choose something:
- It will pause the editting.
- either it opens a new window, or changes the actual edit window itself (preferred but I suspect that isn't possible! -- i would really like the kolourpaint to be put in the window! or some other paint clone!)
- E.g. so I'm on debian 13, so I'll want it to open the selected picture in kolourPaint. (have some check to ensure it is already installed... or exit and tell user to install it..)
    --> in an ideal world, there would still be some ui around the kolour paint window allowing the user to:
        - Exit (returns back to choosing from the original 5 options)
        - save & continue (saves that editted image... continues with the media review onto the next one...)


---
THEN EXTEND BY DOINOG THIS:


    Also, I'd like another option...
    'r' enters 'try again' mode...
    then for ai images or ai edits, it simply runs them again...
    for stock footage... it will fetch more previously unseen (I don't know if that's possible with pexels.. e.g. to get like results 4 through 7... (rather than 1 through 3 as it does by default...))

---

THEN DO:


and now an option to say remove the background, using the existing method of removing a background (I think at least hte joint image creator does that...)
    --> But then after that the user is shown the result.
    --> they can then either reject (and go back to the stock selection screen)
    --> or choose to edit (in which case it would bring up that editting thing again
    --> or of course just choose to accept and move on.


------------------------------------------------------------------------------------------------------------------

# SECOND (SEPERATE) TASK.
create a new option for the class MediaType(Enum):...
    (and thus for the script_to_search_term.json...)

I should be 'manual_stock_add_to_previous'


Essentially, it lets you put stock image inside of another stock image.
- It takes the previous image/stock
- then adds the current selected stock into that image.


It bring up a review window:
- at the top right, is a small preview of the current stock which will be added.
- taking 
- at the right, under the image preview, there is a control to allow you to increase the size of the image 
    - there will be a sort of dashed line box representing the size of that. Max size will be 80% of screen.
    - user can click a big plus or big minus in order to increase or decrease the size by 5%...
    - increases by increments of 5%. Or user can click text box and type in a custom amount which must be an integer between 1 and 80 inclusive. (representing the image width relative to the width of the full image its being placed inside of)
- user then uses their mouse to click somewhere on the image.
    --> It will then layer on the image at the given size, centred at the given spot.
    --> user then accepts or rejects.
    - if they accept, it of course goes onto the next one
    - To note: If you could easily show a preview of the size of the image preview that would be great! (i.e. to show the user where they are placing the image).. although I don't know if that is easily possible...
    - if they reject, it will show where the original image, ideall with a box outline of where they clicked last time, in the correct size... 
        -> they can then click again, or of course press some key to exit and return back...



e.g. the script_to_search_term.json may have entires like:
    {
    ...
    "If you open your kitchen cupboard right now,": {
    "search_term": "kitchen cupboard open",
    "search_type": "stickman",
    "position": "1",
    "sfx": "none",
    "sfx_timing": "loop_start",
    "music": "none",
    "music_trim_seconds": 0,
    "music_fade_out": 0
  },
  "you probably have a jar of nutmeg.": {
    "search_term": "jar of nutmeg",
    "search_type": "manual_stock_add_to_previous",
    "position": "1",
    "sfx": "se-pop.mp3",
    "sfx_timing": "loop_start",
    "music": "none",
    "music_trim_seconds": 0,
    "music_fade_out": 0
  },

  In this case, it would show the stock footage / image of the kitchen cupboard open... and then the user will click where the want the jar of nutmeg to be placed.


To note:
- the actual manual user input of where to place the [jar of nutmeg] will happen after all of the stock / ai edits have taken place / been selected.
    - so the choosing of the stock (this already exists), and then this layering will be seperate stages...


OOH! May be also nice to:
- if it is stock footage, 
    - have an option to 'get still, and then edit still in [the current scene] [i..e the scene after the stock footage]
    be able to press a button to stop it at the point they want a screenshot to be taken.
    then that screenshot will be used as the base 'previous image' in the image review stage...
    actually, scratch that, no. 
    Instead of the user choosing the end of the stock footage, it will instead determine how long the json will be based on the calculated scene length values (we have lots of jsons with stats already...)
        - this will then be used as the still moment automatically, for the user to then edit...

    -> to note, if they don't choose that, just use the stock footage as is, and layer the image onto the video background... (this is default!)

------------------------------------------------------------------------------------------------------------------
# THIRD THING


change the code:

try fetching like 2 wikipedia images by default... when the type is stock... (or one of the stock sub variants...)
    --> and then if I manage to get two... then show 2 pexels images, and 2 wikipedia images... 
    --> so then we will be fetching less pixels...
    --> and we will need to increase the number of images that are shown in the STOCK_FOOTAGE_REVIEW.py...

------------------------------------------------------------------------------------------------------------------

# FOURTH THING
 PIXELLATE.py
I want to apply it to all of the AI generated images


 before stitch together...
    - or actually... since.. wait do we have scenes that mix stickman and regular?
    - it would be much easier to just do it all in place before stitch together...


To be honest- its probably best to apply it right after the images are selected in by the stock footage review...

------------------------------------------------------------------------------------------------------------------
# ALL OTHER NOTES:

--> add auto zooming, but only for things that are not edit scenes? so standalone scenes? (which don't then get editted!)

Am I currently passing everything through the 256 pixelisaition??

-----

add an option:
- 'same as previous'
    
--> then could perhaps add variations... like same as previous but slight zoom cut...
    --> or same as previous but rotate the other way (if its an image slightly rotated on a background... lol...)

---

like a youtube formula...

-----

TODO:

kd 
-------

- hook up the audio processor into main
    - obviously pass in some differnet cache locations thta ill have to define...
    - make the output like stickman-CACHE/AUDIO/script-stickman-processed.wav... 
    - then update main to use that instead of script-stickman.wav 
        (obvoiusly it won't be 'stickman' for all.. that's just the prefix for this example... as you know there may not even be a prefix...)

- add the same affects to all stock footage to make it all look aligned????
    - (and maybe try out a sort of retro appearance to it?)

-------

TODO:
- add an option to the review ai generations???
    - To sort of request new ai generations?!?!?!? !?!? (not sure how we will know if its ai or just stock footage!)

TODO:
- apply same affects to all footage...
    - film grain... crafted by cm... etc.
- add reusable rules... 
    - e.g. things part of the same sentence / same concept... should be ai edit, not new pics...


TODO:
- do i get some 8 bit music in the same style as mario etc, in order to fit the theme???

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
