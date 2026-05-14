
todo ask claude:


nice.
next one, why don't we split after 'is'.. because we are revealing what it is (really big);

uv run slentence_tester.py
======================================================================
SENTENCE SPLITTER — STAGE-BY-STAGE DEBUG
======================================================================

Original:
    ["The empire state building is really big."]

==> rule_hard_punct (FALSE)
    ["The empire state building is really big."]
==> rule_dashes (FALSE)
    ["The empire state building is really big."]
==> rule_ellipsis (FALSE)
    ["The empire state building is really big."]
==> rule_pre_ellipsis_reveal (FALSE)
    ["The empire state building is really big."]
==> rule_quotes (FALSE)
    ["The empire state building is really big."]
==> rule_brackets (FALSE)
    ["The empire state building is really big."]
==> rule_initial_adverbial_comma (FALSE)
    ["The empire state building is really big."]
==> rule_comma_split (FALSE)
    ["The empire state building is really big."]
==> rule_comma_list_extension (FALSE)
    ["The empire state building is really big."]
==> rule_long_subord_comma (FALSE)
    ["The empire state building is really big."]
==> rule_long_clause_comma (FALSE)
    ["The empire state building is really big."]
==> rule_appositive_comma (FALSE)
    ["The empire state building is really big."]
==> rule_clause_starters (FALSE)
    ["The empire state building is really big."]
==> rule_but_or_coord (FALSE)
    ["The empire state building is really big."]
==> rule_verb_clause (FALSE)
    ["The empire state building is really big."]
==> rule_long_lead_in (FALSE)
    ["The empire state building is really big."]
==> rule_long_preps (FALSE)
    ["The empire state building is really big."]
==> rule_pp_intro_reveal (FALSE)
    ["The empire state building is really big."]
==> rule_terminal_of_reveal (FALSE)
    ["The empire state building is really big."]
==> rule_noun_lists (FALSE)
    ["The empire state building is really big."]
==> rule_bare_noun_lists (FALSE)
    ["The empire state building is really big."]
==> rule_list_quantifiers (FALSE)
    ["The empire state building is really big."]
==> rule_entity_reveal (FALSE)
    ["The empire state building is really big."]
1
1
1
1
1
1
1
1
==> rule_numeric_intro_reveal (FALSE)
    ["The empire state building is really big."]
==> rule_post_entity_split (FALSE)
    ["The empire state building is really big."]
==> rule_currency_reveal (FALSE)
    ["The empire state building is really big."]
==> rule_imperative_start (FALSE)
    ["The empire state building is really big."]
==> rule_and_or_clause (FALSE)
    ["The empire state building is really big."]
==> rule_terminal_descriptor (FALSE)
    ["The empire state building is really big."]
==> rule_terminal_adj_coord (FALSE)
    ["The empire state building is really big."]
==> rule_adjective_reveal (FALSE)
    ["The empire state building is really big."]
==> rule_numeric_phrase_reveal (FALSE)
    ["The empire state building is really big."]
==> rule_participle_split (FALSE)
    ["The empire state building is really big."]
==> rule_progressive_split (FALSE)
    ["The empire state building is really big."]
==> rule_copula_attr_reveal (FALSE)
    ["The empire state building is really big."]
==> rule_pron_participle_pp_reveal (FALSE)
    ["The empire state building is really big."]
==> rule_terminal_pp_after_copula (FALSE)
    ["The empire state building is really big."]
==> rule_phrasal_object_reveal (FALSE)
    ["The empire state building is really big."]
==> rule_infinitive_split (FALSE)
    ["The empire state building is really big."]
==> rule_prep_object_reveal (FALSE)
    ["The empire state building is really big."]
==> rule_transition_adverb (FALSE)
    ["The empire state building is really big."]
==> rule_sconj_hang (FALSE)
    ["The empire state building is really big."]

==> anti_rule_compound_ne (FALSE)
    ["The empire state building is really big."]
==> anti_rule_aux_main_verb (FALSE)
    ["The empire state building is really big."]
==> anti_rule_hyphen_compound (FALSE)
    ["The empire state building is really big."]
==> anti_rule_possessive (FALSE)
    ["The empire state building is really big."]
==> anti_rule_phrasal_particle (FALSE)
    ["The empire state building is really big."]
==> anti_rule_numeric_unit (FALSE)
    ["The empire state building is really big."]
==> anti_rule_det_head (FALSE)
    ["The empire state building is really big."]
==> anti_rule_inside_quote (FALSE)
    ["The empire state building is really big."]
==> anti_rule_inside_bracket (FALSE)
    ["The empire state building is really big."]
==> anti_rule_frozen_bigram (FALSE)
    ["The empire state building is really big."]
==> anti_rule_adj_noun (FALSE)
    ["The empire state building is really big."]
==> anti_rule_to_infinitive (FALSE)
    ["The empire state building is really big."]
==> anti_rule_numeric_range (FALSE)
    ["The empire state building is really big."]
==> anti_rule_no_split_before_comma (FALSE)
    ["The empire state building is really big."]
==> anti_rule_currency_glued (FALSE)
    ["The empire state building is really big."]
==> anti_rule_num_unit (FALSE)
    ["The empire state building is really big."]
==> anti_rule_neg_modifier (FALSE)
    ["The empire state building is really big."]
==> anti_rule_compound_noun (FALSE)
    ["The empire state building is really big."]
==> anti_rule_markdown_emphasis (FALSE)
    ["The empire state building is really big."]
==> anti_rule_short_sentence (FALSE)
    ["The empire state building is really big."]
==> anti_rule_verb_to_verb (FALSE)
    ["The empire state building is really big."]
==> anti_rule_verb_to_dem_pron (FALSE)
    ["The empire state building is really big."]
==> anti_rule_orphan_measure_tail (FALSE)
    ["The empire state building is really big."]
==> anti_rule_content_starved (FALSE)
    ["The empire state building is really big."]
==> anti_rule_split_before_sconj (FALSE)
    ["The empire state building is really big."]

  [protected split indices: [8]]

=== POST-PROCESSING ===

==> merge_throwaways (FALSE)
    ["The empire state building is really big."]
==> fuse_orphans (FALSE)
    ["The empire state building is really big."]
==> post_merge_unvisualisable (FALSE)
    ["The empire state building is really big."]

======================================================================
FINAL RESULT:
======================================================================
  1. The empire state building is really big.


should be:

1. The empire state building is
2. really big

now, with this one we need to be very careful.. 
some examples:

["The problem is getting worse every day."]  --> don't split this one! hmm actuall maybe we do
["Her favourite part is ", "the quiet just before sunrise."] --> we do split as we reveal
["The strange thing is ", "how calm everyone seems."] --> again split
["My only concern is ", "the timing of the launch."] --> again revealing
["The truth is ", "we never had a real plan."] --> again, we are revealing what the truth is.. 
["His biggest strength is ", "remaining focused under pressure."]
["The remarkable part is ", "how naturally it all fits together."]
["The rule is simple enough to follow."] --> DON'T split this one! again, im not sure of the specific spacy tagging etc.. but like the right isn't really a reveal..  sure we are 'revealing' that 'the rule' is 'simple enough to follow'.. 
["The real challenge is ", "balancing speed with accuracy."]
["The final result is better than anyone expected."]

right.. im realising that most of hte time we should split but in some scenarios we dont... please try and find a pattern here but have the code split most of the time..
 



Here is my entire codebase:






--------


TODO NEXT:
- copy across the #5 test results so i can start removing thing sthat ive fixed...
- do the lists next... ensure they are processed correctly...



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
