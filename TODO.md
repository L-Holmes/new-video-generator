<--->
BEFORE: This is a story about cathedrals — not the architecture, not the stained glass, but the sound trapped inside them.
AFTER: ['This is a story about cathedrals —', 'not the architecture, not the stained glass, but', 'the sound trapped inside', 'them.']
EXPECTED: ['This is a story about cathedrals —', 'not the architecture,','not the stained glass, but', 'the sound trapped inside them.']
- should have split on the list of noun things...  either at 'the' for each, or after the common, just before 'not'...
- 'them' should surely have been joined back since its not visualisable.
<--->
BEFORE: Because if you walk into a medieval cathedral and clap your hands, the echo you hear isn’t just long — it’s wrong.
AFTER: ['Because if you walk', 'into a medieval cathedral and', 'clap your hands,', 'the echo you hear isn’t just long —', 'it’s wrong.']
<--->
BEFORE: It lingers for nearly ten seconds, smearing every sound into a kind of sonic fog that makes speech almost impossible to understand.
AFTER: ['It lingers', 'for nearly ten seconds,', 'smearing every sound into', 'a kind of', 'sonic fog', 'that makes speech almost impossible to understand.']
EXPECTED: ['It lingers for nearly', ten seconds,', 'smearing every sound into', 'a kind of', 'sonic fog', 'that makes speech almost impossible to understand.']
should split just before the reveal of ten seconds..
<--->
BEFORE: And the strange part is that none of this was planned.
AFTER: ['And the strange part is that', 'none of this was planned.']
<--->
BEFORE: Medieval builders didn’t have acoustic modelling software; they barely had consistent units of measurement.
AFTER: ['Medieval builders didn’t have', 'acoustic modelling software; they barely had', 'consistent units of measurement.']
<--->
BEFORE: They built cathedrals the way you might stack stones on a beach: slowly, carefully, and with a lot of guesswork.
AFTER: ['They built cathedrals the way you might stack stones on', 'a beach: slowly, carefully, and', 'with a lot of guesswork.']
<--->
BEFORE: But somehow, through trial, error, and a few lucky accidents, they created some of the most acoustically extreme spaces on Earth.
AFTER: ['But somehow, through trial,', 'error, and', 'a few lucky accidents, they created', 'some of the most acoustically extreme spaces on', 'Earth.']
EXPECTED: ['But somehow, through','trial,', 'error, and', 'a few lucky accidents, they created', 'some of the most acoustically extreme spaces on Earth.']
yet again, we have a list of noun type things and the first doesn't get split on?
also maybe just hardcode an exceptoin where 'on Earth' specifically doesn't get split...
<--->
BEFORE: The problem starts with scale: cathedrals are enormous, and sound behaves badly in enormous rooms.
AFTER: ['The problem starts with scale:', 'cathedrals are enormous, and', 'sound behaves badly in', 'enormous rooms.']
<--->
BEFORE: Every surface — the pillars, the vaults, the carved stone saints — reflects sound in a slightly different direction.
AFTER: ['Every surface —', 'the pillars,', 'the vaults,', 'the carved stone saints —', 'reflects sound in', 'a slightly different direction.']
<--->
BEFORE: So instead of one clean echo, you get thousands of tiny reflections arriving at your ears at slightly different times.
AFTER: ['So instead of one clean echo, you get', 'thousands of tiny reflections arriving at', 'your ears at', 'slightly different times.']
<--->
BEFORE: The result is a kind of auditory soup where consonants dissolve and vowels smear into each other.
AFTER: ['The result is', 'a kind of auditory soup where', 'consonants dissolve and', 'vowels smear into each other.']
<--->
BEFORE: If you tried to give a TED talk in a cathedral, no one would understand a word of it.
AFTER: ['If you tried to give a TED talk in a cathedral,', 'no one would understand', 'a word of it.']
<--->
BEFORE: But medieval worship wasn’t about understanding every word.
AFTER: ['But medieval worship wasn’t about understanding', 'every word.']
<--->
BEFORE: It was about awe.
AFTER: ['It was about awe.']
<--->
BEFORE: And a ten‑second echo turns even a single note into something that feels supernatural.
AFTER: ['And a ten‑second echo turns', 'even a single note into something', 'that feels supernatural.']
<--->
BEFORE: Gregorian chant wasn’t designed for cathedrals — cathedrals shaped Gregorian chant.
AFTER: ['Gregorian chant wasn’t designed for', 'cathedrals —', 'cathedrals shaped', 'Gregorian chant.']
<--->
BEFORE: Long, slow notes survive the echo; fast syllables don’t.
AFTER: ['Long, slow notes survive the echo;', 'fast syllables don’t.']
<--->
BEFORE: So the music adapted to the building, and the building adapted to the music, in a feedback loop that lasted centuries.
AFTER: ['So the music adapted', 'to the building, and', 'the building adapted', 'to the music, in', 'a feedback loop', 'that lasted centuries.']
<--->
BEFORE: But here’s the twist: the echo wasn’t just a side effect.
AFTER: ['But here’s the twist:', 'the echo wasn’t', 'just a side effect.']
<--->
BEFORE: It was a tool.
AFTER: ['It was a tool.']
<--->
BEFORE: Priests realised that if they spoke slowly enough, the echo made their voices sound bigger, deeper, more authoritative.
AFTER: ['Priests realised that if', 'they spoke slowly enough,', 'the echo made their voices sound', 'bigger, deeper,', 'more authoritative.']
<--->
BEFORE: A single voice could fill a space the size of a football pitch without amplification.
AFTER: ['A single voice could fill a space', 'the size of', 'a football pitch without', 'amplification.']
<--->
BEFORE: And in a world without microphones, that was power.
AFTER: ['And in a world without microphones,', 'that was power.']
<--->
BEFORE: But the echo also caused problems.
AFTER: ['But the echo also caused problems.']
<--->
BEFORE: During the Reformation, Protestant reformers complained that cathedrals were acoustically hostile to preaching.
AFTER: ['During the Reformation,', 'Protestant reformers complained that', 'cathedrals were', 'acoustically hostile to preaching.']
<--->
BEFORE: They wanted sermons — long, complicated, theological arguments — and cathedrals simply swallowed them.
AFTER: ['They wanted sermons —', 'long,', 'complicated, theological arguments —', 'and cathedrals simply', 'swallowed them.']
<--->
BEFORE: So new churches were built smaller, with wooden interiors, designed for clarity rather than grandeur.
AFTER: ['So new churches were built smaller,', 'with wooden interiors,', 'designed for', 'clarity rather than grandeur.']
<--->
BEFORE: Meanwhile, the old cathedrals stayed as they were: giant stone echo chambers that refused to modernise.
AFTER: ['Meanwhile, the old cathedrals stayed as they were:', 'giant stone echo chambers', 'that refused to modernise.']
<--->
BEFORE: Today, sound engineers study these buildings because they break all the rules.
AFTER: ['Today,', 'sound engineers study these buildings because', 'they break all the rules.']
<--->
BEFORE: They’re too big, too reflective, too chaotic — and yet they work.
AFTER: ['They’re too big,', 'too reflective, too chaotic —', 'and yet they work.']
EXPECTED: ['They’re too big,', 'too reflective, too chaotic —', 'and yet they work.']
- to be honest, is this our new rule that is keeping these two 'toos' together? if so lets loosen that. I'd rather the list things get split up than 'big, giant birds' not be split up... defo just split.
<--->
BEFORE: Not for everything, but for the things they were accidentally optimised for.
AFTER: ['Not for everything, but for', 'the things they were accidentally optimised for.']
<--->
BEFORE: Stand in the centre of a cathedral and sing a single note, and the building sings back.
AFTER: ['Stand in the centre of', 'a cathedral and', 'sing a single note, and', 'the building sings back.']
<--->
BEFORE: It’s like the walls remember every voice that’s ever passed through them.
AFTER: ['It’s like the walls remember', 'every voice', 'that’s ever passed through', 'them.']
expected: ['It’s like the walls remember', 'every voice', 'that’s ever passed through them.']
- why is the non visualisable 'them' by itself?
<--->
BEFORE: And maybe that’s the real reason these places still feel sacred, even if you’re not religious.
AFTER: ['And maybe that’s', 'the real reason', 'these places still feel', 'sacred, even if', 'you’re not religious.']
<--->
BEFORE: They don’t just hold history in their stones.
AFTER: ['They don’t just hold history in their stones.']
<--->
BEFORE: They hold it in their sound.
AFTER: ['They hold it in their sound.']


-----


continue just tackling one sentence splitter issue at once...
then processing with this:
uv run test_ss_against_real_before_after.py -v > log_test_9.txt
sed -nE '/^test #(175|164|141|117|53|79|59|1|142|123|56|69|28|37|49|61|82|87|4|7|8|113|9|25|41|145|18|23|116|19|63|64|68|6|62|67|191|81|112|178|42|43)\)/,+3p' log_test_8.txt

then get to a decent level...
then in future again, just tackle issues one by one...

-------

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
