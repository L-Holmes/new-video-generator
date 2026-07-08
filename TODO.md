

tasks:


-------------


1) - option for just 'blank' / 'random background' in the mediatype chooser 
- blank is just white
- 'random background' picks a random background from our backgrounds folder.

(you'll have to update to allow for these two new options... + impelement the fnucitonlaity for that..)




3) new media type option for 'timeline'... uses manim to generate a timeline affect where it shows a timeline and a moving thing that goes back to the given date. It then holds on that after doing the initial. 

 movement. 




7) If no options are good, from the stock that I searched (this is in the media review stage)...

I should have an option... 

to;

perhaps try again, with a different search term, which this time searches both stock and wikipedia? and then gives you 5 more options to choose from - 3 from stock, and 2 from wikipedia? 



7.5) Same for if we choose wikipedia as the option, but non of the options are good... but in that scenario, just get 5 stock, and no wikipedia...



7.7) In fact, lets have a second additional option, which is just try again, but with a different search term.



8) When I'm doing manual intervention, where I place my own image in the stock footage folder... is there any way we could open a window which the user can just paste into, which will then paste the image which we are using for that? (as an alternative option available... so they can manually place in the folder, or they can just paste in a picture that they've copied...

Also, if we manually add to the folder... and we've entered the number we are resolving.. can it not auto detect the most recent file that was added to the folder, and ask you to confirm it? (and ideally open a window which displays the image, so the user can confirm that they want to use that image/clip for the given entry...?)






10) On the manual tagger, need a way for me to quickly log the change I made and why.

so everytime I either: join a line, split a line, Or change a media type [and search text], it should ask for the reason why.



It will then update an ongoing log file we have, which will do before and after, and then give the user's reason (if they gave one) 



11) We shouldn't allow brand new clips (i.e. wikipedia or stock) (but edit previous or things like that are fine) to be shorter than a threshold which we define in the config (e.g. like 0.5 seconds)... If they are shorter than that... we should make the user instead either manually edit the tag file... or use an 'edit prevoius' instead...
(instead of setting 1 second, we just use a hardcoded value for narrator words per minute (slightly faster than average)...

 Then in the manual tagger we give the user options (it would be fantastic if the user could click on an option to apply an option, then undo if they didn't like it..) (it would be even better if when they hover over an option, if it shows what will happen (e.g. if the option infers joining to the previous, then we use our existing code as if the user had hovered over the 'join to previous')...

    Give these options to the user, in very close to my wording;


That sentence is too short to have new footage.
It will just flash on the screen for th user 
You need *[x]* more words to make the scene long enough to stand by itself 
Your options;
(1) Edit and add to the previous scene [recommended] 
(2) Join this scene to the previous scene, thus making the previous scene be on the screen for longer
(3) Split the previous scene, join it to the start of this scene
(4) Make the scene after this scene be an edit of this scene 
(5) Join [at least part of] the scene after this scene to this scene 
(X) Manual override and use quick stock anyway 


We'll also want the auto tagger to generally follow these rules as well...
make it have some intelliggence when deciding whether to just edit previous, adding decorated stock (do this most of the time), or just joining to previous scene or next scene (do this very rarely...)


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

