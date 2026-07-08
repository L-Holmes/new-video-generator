

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

- perhaps integrate everything together..
    - so i just run via main.py..
    - and it'll run the sentence splitter if no cache available..
    - and the auto tagger... (again- checking for cache)
    - and the manual reviewer... (if any of the entries in the json have no mediatype...

- For decorator, if i do 'object' tab, then do an action (e.g. select object, then remove background) then the rest of the tabs become blank and unususable... 
- For decorator:
    - Make it so you can rotate text in OUR paint thing
- For decorator:
    - For the decorate's 'zoom', reverse the plus and minus, so that plus makes hte box bigger. Also, as soon as you click, it should complete the zoom- just remove the 'complete zoom' button.
    Also, 'undo' button doesn't seem to undo the zoom. (It should!)



------------------------------------------------------------------------------------------------------------------
------------------------------------------------------------------------------------------------------------------
------------------------------------------------------------------------------------------------------------------
------------------------------------------------------------------------------------------------------------------

----------

find stickman-CACHE -type f -name "*.json" -delete && rm -rf stickman-OUTPUT/output.mp4

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

