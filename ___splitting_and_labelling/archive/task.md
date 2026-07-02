

Task:

Goal:
- Inputs:   
    - input script name (e.g. script-spices.txt)
    -  
- Outputs:
    - map of split sentences to config.. script_to_search_term.json (e.g. spices-script_to_search_term.json)
- Run generate_script_to_search_term() 
    - Takes the input script
    - Passes it through the sentence splitter 
    - It then takes that, and looks at all of the numbers representing which things it split on
    - It then uses those values to build a map (which we can save as <prefix>-CACHE/split-and-lable/WEIGHTS-spices.json);
        - NOTE: we get the prefix from the input script name. e.g. script-spices.txt (prefix would be 'spices'
        - mapping each split entry to all of the possible 'effects'.. e.g.;
            [text]:{
                {
            enum:probability,
                     ....
                }
            }
            ```
            {
              "If you open your kitchen cupboard right now,": {
                "stock": 0.12,
                "wikipedia": 0.03,
                "joint_3_row": 0.07,
                "read_out": 0.09,
                "map": 0.01,
                "stickman": 0.11,
                "ai_edit": 0.06,
                "stickman_explain_stock": 0.08,
                "stickman_explain_wikipedia": 0.04,
                "stickman_text_overlay": 0.05,
                "stickman_joint_3_row": 0.07,
                "manual_stock_add_to_previous": 0.04,
                "zoom_prev_img": 0.08,
                "static_of_previous": 0.06,
                "decorate_previous": 0.05,
                "object_generate": 0.14
              }
            }

            ```
        I'm not sure if we'll want the probabilities to add to one or if each one just has a seperate self-contained probability? (don't let my example fool you!)
        (in a similar vein to 80% chance its a dog... 50% chance its a muffin, type jazz... You're the expert- I'll leave that up to you...)
    - and lastly of course... then I builds the final map based onto he probabilities it chooses the type... Frome enums. 
        heres an excerpt example from one i manually made;
        ```
{
  "If you open your kitchen cupboard right now,": {
    "search_term": "kitchen cupboard open",
    "search_type": "stock",
    "position": "1",
    "sfx": "none",
    "sfx_timing": "loop_start",
    "music": "none",
    "music_trim_seconds": 0,
    "music_fade_out": 0
  },
  "you probably have a jar of nutmeg.": {
    "search_term": "open the door and add jar of nutmeg into the cupboard",
    "search_type": "manual_stock_add_to_previous",
    "position": "1",
    "sfx": "se-pop.mp3",
    "sfx_timing": "loop_start",
    "music": "none",
    "music_trim_seconds": 0,
    "music_fade_out": 0
  },
  "It costs about": {
    "search_term": "one dollar coin",
    "search_type": "stickman",
    "position": "1",
    "sfx": "none",
    "sfx_timing": "loop_start",
    "music": "none",
    "music_trim_seconds": 0,
    "music_fade_out": 0
  },
  "two dollars": {
    "search_term": "add a second one dollar coin",
    "search_type": "ai_edit",
    "position": "1",
    "sfx": "none",
    "sfx_timing": "loop_start",
    "music": "none",
    "music_trim_seconds": 0,
    "music_fade_out": 0
  },
  "But in the 1600s, this little wrinkled seed was the": {
    "search_term": "whole nutmeg seeds wrinkled",
    "search_type": "stickman",
    "position": "1",
    "sfx": "none",
    "sfx_timing": "loop_start",
    "music": "build-intrigue.mp3",
    "music_trim_seconds": 0,
    "music_fade_out": 15
  },
  "single most contested resource on the planet.": {
    "search_term": "whole nutmeg seeds wrinkled",
    "search_type": "zoom_prev_img",
    "position": "1",
    "sfx": "none",
    "sfx_timing": "loop_start",
    "music": "none",
    "music_trim_seconds": 0,
    "music_fade_out": 0
  },
  "It was worth more than its weight in gold.": {
    "search_term": "gold bar",
    "search_type": "stickman_explain_wikipedia",
    "position": "1",
    "sfx": "none",
    "sfx_timing": "loop_start",
    "music": "none",
    "music_trim_seconds": 0,
    "music_fade_out": 0
  },
  "Nutmeg only grew in one place on Earth:": {
    "search_term": "antique world map",
    "search_type": "stock",
    "position": "1",
    "sfx": "none",
    "sfx_timing": "loop_start",
    "music": "none",
    "music_trim_seconds": 0,
    "music_fade_out": 0
  },
  "The Banda Islands. A tiny, incredibly remote volcanic archipelago": {
    "search_term": "Banda Islands Indonesia aerial volcanic",
    "search_type": "stickman_explain_stock",
    "position": "1",
    "sfx": "none",
    "sfx_timing": "loop_start",
    "music": "none",
    "music_trim_seconds": 0,
    "music_fade_out": 0
  },

        ```
        to note: 
        - don't worry about the music / sound affects... just keep them all the same/blank.
        - (position only increments for joint images... like 3 row...)

        ensure you document everything, decisions, guideance, how things work.. in a super logical, arch linux type way in a readme....
- We get an output:
    - script_to_search_term.json


so create a new file;
SPLIT_AND_LABEL.py...


IMPORTANT:
- please use your best intelligence to determine the baseline conversion / weights / mappings etc. (i can adjust later manually if needed!)

TO NOTE:
- add a flag 'TESTING_SCRIPT_SEARCH_TERM_GENERATION=True'
    - if true, it prepends 'TESTING_' to the output file name...   (and any cache file names...)

ALSO TO NOTE:
- mention the toggling of the AI stuff... which we'll leave as 'False' for now...
    - (i.e. just ignore that they are even in the choosable optoins...)
    - thats any with 'ai' or any with 'stickman'
    



make it so when its ran directly (e.g. uv run SPLIT_AND_LABEL.py) that it tests using script-spices.wav...
    - BUT WHEN RAN LOCALLY ALWAYS RUN IN TESTING MODE! 


If any of the cache files already exist... just skip the step and fetch the cache!






Here is my sentence splitter:
