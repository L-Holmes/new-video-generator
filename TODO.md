
-------------


3) new media type option for 'timeline'... uses manim to generate a timeline affect where it shows a timeline and a moving thing that goes back to the given date. It then holds on that after doing the initial. 
e.g. so If I pass data '1600', then it'll start at 2026, then move back to 1600s then 1600 pops up on the timeline once it gets there...

 movement. 

 in ___splitting_and_labelling ... (specifically the manual tagger..), have this under a new heading; new - maths 
(e.g. as opposed to under 'NEW — brand-new material' ...)
put it seperate the NEW — brand-new material, in fact, have dots reprenting the tabs at the top, and have a second tab where we'll put these maths options. 
so for now, we'll have the existing, but just with 2 small dots at the top, with the current one highlighted to show we're on that tab. there will then be arrows to move between the tabs. on the second tab, under the new heading there will just be this one new option. 
(we will need some way to pass the data that will be be required for this manim thing to be made in the script to search term json some how... In a way that won't massively complicate the json, and will allow for lots of different types of data to be passed for other types of things...)
    - perhaps just an additional field like 'data' or something? 
    (which then, in the manual tagger, after selecting the 'timeline' option, the user will be asked to enter the data... for timeline this is straightforward as it'll just be years (we'll auto detect the current year in the code itself...).

(bear in mind- we'll add lots more like this in the future, like creating a pie chart animation etc...)



hmmmm...
how will we determine the length of the animation?
perhaps if the clip is 

... hmmm so i guess the way this'll work for us is that we have a static of the final finished timeline. and then a seperate mp4 of the transition. 
then when we have determined the scene length (which occurs at some point in the ___visuals stage...), we can then check: is the scene shorter than the transition? because if it is, we just show the finished one. If its longer, we show the transition, then hold the static finished position for the rest of the scene. 

(in fact, add a note somewhere for AI future reference that we'll always use this method when 
 just literally a standalone file like 'AI_READ_THIS.txt' or something...


8) When I'm doing manual intervention, where I place my own image in the stock footage folder... is there any way we could open a window which the user can just paste into, which will then paste the image which we are using for that? (as an alternative option available... so they can manually place in the folder, or they can just paste in a picture that they've copied...

Also, if we manually add to the folder... and we've entered the number we are resolving.. can it not auto detect the most recent file that was added to the folder, and ask you to confirm it? (and ideally open a window which displays the image, so the user can confirm that they want to use that image/clip for the given entry...?)


10) On the manual tagger, need a way for me to quickly log the change I made and why.

so everytime I either: join a line, split a line, Or change a media type [and search text], it should ask for the reason why.



It will then update an ongoing log file we have, which will do before and after, and then give the user's reason (if they gave one) 



12) need an actual reliable thing for the words on screen... and perhaps have a flag that we change in config that determines whether it shows like the whole sentence on screen, but one word at once... or just each word one by one as it does now..
























===========================================


Ideas:
- timeline for dates (maybe make this animated?)
    - e.g. In 64 AD...
- extend previous
    - take the previous, put it side by side with a second image. 
    e.g. Rome was not built in a day || but in burned in six
        -> rome construction -> rome construction + fire



TODO:
- Add the stamp thing back - for like 'traded for nutmeg!' etc?
    - or do we sort of auto-populate the 'text' portion of the decorator to use that text, and then apply a 'stamp' affect to the text where it does the same styling with the red background?





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
- stock fallback
    - (if all the stock is just rubbish... do i allow backup of generate ai image?)
    - or could i do open . on the image folder where they should add the file to... or the file path..
    - then auto detect when a new file has been added and ask them to confirm; 'is image_1.jpg the image for "Big Tower collapses"'? Y/N: [answer]
- 2 to 3 brand colours? (for like labels.. text .. arrows.. stock.. etc.)
- slight rotation for layered things...
    - hmmm . yeah so either layer on prexisting background
    - would this be a variation of the 3 row???
    - i.e. better collage...
- maybe transitions etc. for when I place something in a scene manually?? 
    - (even if i place multiple items in a scene? - e.g. adding 5 coins... could have default it adds them one by one, cutting one after the other... (unless the length of the clip isn't long enough?)
- graphs / bars / ...
    - Counter animations
    Numbers tick up, bar fills, pie chart draws on. Perfect for stats you mention.
    - hmmm - do i use manim for this? and generate code to then generate the thing? or what? 

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



## Abstract resumption engine (give full preceding text, ask it to identify what a particular "it"/"he"/"they" etc. refer to 


## theme / context resolution 
(Theme / time / context of overall thing, and then at a particular point, and perhaps "theme up to this point" (current line and proceeding text)) 

## details about a thing 
Take a reference to a "car" at a point in text, then based on previous words I get an outfit of all the details describing that thing. 
E.g. "red Toyota Aygo car"

And / or keep track of that thing as we go through the text. 
E.g. so theoretically could wire into code that will colour code repeated things, and then I can hover over the word, then at the side it shows the full description (of the Aygo...)


## auto complete engine? 


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



- synced music
    - have a theme, switch to different footage on the beats of a song??



- speed ramps:
    - 100% -> 150% for boring middle section, snap back to 100% on beat. Great for walking, clouds, city timelapses.


- character reactions library
- Reuse a small library of stickman poses: thinking, pointing, shocked, shrug. Trigger by sentiment of line.

