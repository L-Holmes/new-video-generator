

todo - update the map, then re-run
TODO - I assume that it will get the pop-in timing wrong? 
     - ooh do i ask the claude to make it so that for the pop transitions are added or maynbe its just easier if i add them myself... yeah

---

I have this sound effect:
_SOUND_EFFECTS/se-pop.mp3
In the future, I'll add more sound effects.

Aim:
- i want a generified system for when i add more sound effects in the future.
- but for the first implementation, I want the 'se-pop.mp3' to play ever time we switch to the video (i.e. after the transition, if there is one, or just when the loop shows).
    - would that be possible? I guess we could calculate it if we know the 
--> but I'd like this to be potentially another option that is passed in?
    - like:
        - the sound effect name (or none)
        - when it plays (default is when loop shows, for now just have that one because most if not all will use this)
        - dont know how this will work for like if we have 3 that are all being made into the same thing.. like will we be able to do that? please use your intelligence best you can.
            - maybe an option for like end of clip? so then it tries to fit on the sound effect onto th eend...



maybe a way for adding just music that starts when a particular line starts (e.g. for the stock footagae / regular ones as well)
    --> and then a setting to clip it to a certain length (e.g. like 2, 3, 5, 10 or 20 seconds trim)

--> so we perhaps need to add a new section to the script_to_search_term.json map (if you tell me, I'll update it for you), to add an optional clip (can be like none)
e.g. so I've added this one: _SOUND_EFFECTS/build-intrigue.mp3, which I'll add myself to one of the entries in the new json that you think of. Of course as just  build-intrigue.mp3 as the code will know where to look for it.
--> that clip is 24 seconds, but we want to make sure the code works regardless of the length (it may be shorter or longer or whatever)...

Again, add lots of debug print statements that I'll remove mself later when I've confirmed that everything works.



--------------------------



TODO:
- the script is missing part of what is in the actual script... like 'x' meant.. then straight onto manhatten...


---


any way we can speed up the downloading of images?
like using the usual computing tricks? I'm on debian 13... on ryzen pro 7 thinkpad... don't know if its possible really...

can we also add a sort of 'TIME REMAINING >>>>>>>>>>>>>>>>>>> x m y s' (where 'x' is minutes and 'y' is estimated seconds remaining) 
(just for the downloading of the images for now.. 
 and maybe stitching together of images.. but seperately)

if not, just say not possible. 



---------



task:
right, now i need to work out how i'm going to integrate these things into the final scene stitcher
-> first, will want to identify the scenes which are now joint scenes with multiple parts.
-> then, I'll need to know the length of the transition, and the length of the next vid so that we know how long to show each scene for...
    -> that may be quite tough.. potentially we'll need to know how long the scenes are before generating the joint scenes? so that we can adjust the transition to be at least as long as the scene length, and then if the scene is shorter than the minimum then instead of doinga transition we just do no transitions...

or potentially we need to update whatever map the sentence splitter reads from such that instead of pointing at the original footage it points at the new joint generated scenes instead?
but obviously with the joint scenes there are two vids per section of text... rather than just one how the stock footage is.. since there is the initial transition and then the bit after that...  
e.g. 
for our initial example;
/050 󰈫  stage_01_of_03.mp4
/049 󰈫  stage_01_of_03_loop.mp4
/047 󰈫  stage_02_of_03.mp4
/051 󰈫  stage_02_of_03_loop.mp4
/046 󰈫  stage_03_of_03.mp4
/048 󰈫  stage_03_of_03_loop.mp4

in this, we have:
/050 󰈫  stage_01_of_03.mp4
/049 󰈫  stage_01_of_03_loop.mp4

now again, i don't know the length of the clips but it will of course make a difference..



--------

and here is my main file code: 




-----------------



input_line:{
    "search-term":"blackbeard the pirate, wide shot"
    "type":"stock"
    "variant":"default"
    "position":"1"
}


TODO:
- then: 
    - after fetching and reviewing the stock (for now): 
        - generate the joint images. 
            --> have some defaults for the '3 row' layout etc, well as a map from the enum '3 row' to then the positions that we'd need to pass into that new function.
            --> TODO need to determine where I'm saving the images etc...
        - then:
            - update the thing that stitches the video together
            --> for each line, have an additional check of the type. Anything that is just stock, treat as usual. but if the value is say '3 row'... then it will know to use the generated images instead








try and get the multi image linked up...


--------

Go back to the claude thing- solving the word several splitting issue...


[I/someone][feels/felt some way towards][x]
e.g. 
I hate the word several
She was disgusted by the thought of the giant panda

- actually link up the multi thing
--> for like that india, china and ... just as an example.
    - think how to represent, then how to parse, etc...



then watch through the video- think what i want to add next...

-----

- get some sound effects added...
- link up wikipedia etc...

-----------------

consider:
- just simple things like:
    - overlaying text on the stock footage to keep things easy
    - adding blank background and then my text coming on the screen as i say it..
    - adding sound effects...?

Then:
- maybe just for like lists (e.g. with like 'and'), add the thing to show three things one by one on a shared background...
--> perhaps for now just manually tag the lists? not sure how i'd do this!
    -> type=stock (default)
    -> type=row(3)::1 (first element of a row three) -> validatoin ensures that there are three of these in a row...


------


- Optimise and improve my sentence splitter.
- Add as many labled sections as possible, with comments that mention exmaples of their uses
- also modify existing ones as necessary (e.g. you may want to add generic exceptions)
- don't hardcode for any specific word examples- must be generalisable to anything!

Here is the existing code:


And here are my examples of *approximate* before and after of what you should aim for. 
(You may want to create some pytests and see if you can get within say 90% accuracy of these!!!)
here they are:






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
