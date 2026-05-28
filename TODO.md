
I want to update it so that for all static images (just stock, that isn't video, but static),
it adds an editting affect to the image. 
Lets have a data structure, that has the possible edit types, and then the probability of it being chosen.
then when encountering an image, it will apply that affect (I assume turning it into an mp4 or whatever?)-- of course ensure that any of the cached tihngs are updated as needed... 

i.e. lets use these values and effects;
(these are the effect and the probability of it being chosen)

| Technique Name | Probability | Why it is safe and effective for automation |
| --- | --- | --- |
| **1. Centered Slow Zoom In (The Subtle Push-In)** | **0.28** | The absolute king of automated editing. It mimics a slow camera dolly move. Because it scales evenly toward the true center, it will never accidentally crop out a vital subject on the margins of an unknown image. |
| **2. Centered Slow Zoom Out (The Subtle Pull-Out)** | **0.22** | The inverse of the push-in. It starts slightly tighter and slowly opens up the frame. It is entirely risk-free as long as your minimum scale value doesn't drop below the container's boundaries. |
| **3. Horizontal Pan: Left to Right** | **0.14** | Mimics Western reading order, making it feel incredibly natural to the viewer. Ideal for landscape or wide group shots. Your program just slides the X-axis smoothly across the pre-padded canvas. |
| **4. Horizontal Pan: Right to Left** | **0.12** | Slightly lower probability than left-to-right because it fights natural reading order, which makes it feel more deliberate, moody, or investigative. Mechanically identical and 100% safe. |
| **5. Vertical Tilt: Bottom to Top (Tilt Up)** | **0.06** | Mimics a camera crane rising. It works beautifully to establish a sense of height, scale, or grandeur. Lower probability overall because vertical movement is less universally optimal on standard landscape video frames. |
| **6. Vertical Tilt: Top to Bottom (Tilt Down)** | **0.05** | A gentle downward scan of the Y-axis. It mimics a human eye looking down from a sky or ceiling. Perfectly safe, but reserved mostly for images where the top of the frame holds less vital context than the bottom. |
| **7. Compound: Zoom In + Pan Left-to-Right** | **0.04** | A highly cinematic combined move (scaling up while shifting the X-axis right). Because it moves along a slight arc, it looks deeply sophisticated, but requires a strict, tiny limit on the X-axis shift to prevent clipping. |
| **8. Compound: Zoom In + Pan Right-to-Left** | **0.04** | The same dual-axis movement as above, just shifting the camera left as it scales inward. It creates a beautiful parallax illusion natively without requiring any actual layer separation. |
| **9. Compound: Zoom Out + Pan Left-to-Right** | **0.03** | Shifting the X-axis right while slowly scaling down to reveal a wider view. It feels highly narrative, like a camera backing away from a scene while tracking past it. |
| **10. Compound: Zoom Out + Pan Right-to-Left** | **0.02** | The final variation of the compound moves. It is sophisticated and completely safe for your automation script, but holds the lowest probability simply because it is the most specialized mood of the group. |

To ensure these look professional, hardcode these three mathematical constraints into your rendering logic:
1. **Linear Interpolation (or Smooth Ease-In/Out):** Never let the camera "snap" to a stop. Use a very gentle ease curve at the first and last 10% of the clip duration.
2. **The 0.5% Rule:** Keep the actual offset movement microscopic. If a clip is 5 seconds long, the X or Y coordinates should not shift by more than 3% to 5% of the total image width/height.
3. **No Over-Rotation:** Avoid any Z-axis rotation (rolling the camera). While humans do this slightly, automated rotation looks instantly synthetic and tacky. Keep your axes perfectly locked.


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
