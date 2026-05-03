



prerequs;
```
uv sync
uv run python -m spacy download en_core_web_sm
```


All steps:
1) Write note rough
2) Ask AI to generate script
3) Manually review
4) Manually add sections, and split into scenes by adding newlines (hmm maybe already do this as stage -2 --> split on punctuation?
5) run this script
6) manually review the search terms
7) run script again
8) review the fetched footage
    - shows user the line and the image / footage?? (I have vlc... is this easily possible in python???)
    - user presses enter to verify... 
    - user can request to switch out footage- either with same or different search term
9) script continues to run- stitching everything together
10) user manually adds output vid and their voice recording into shotcut
    - user trims down video to match their voice.



# Stage 1) parsing the script 

"""
Script format expected
-----------------------
Lines beginning with '#' are section headings — they provide context to
the AI but are NOT treated as scenes themselves.  Every other non-blank
line is one scene.

    # intro
    The empire state building is really big.
    Built in Manhattan in the 19th century.
    Back in 1946,
    ...
    # tea
    But where exactly in the world did this tea originate?
    ...

Output format  (returned by get_scenes / build_scene_map)
----------------------------------------------------------
A plain dict mapping each scene's narration text to its Pexels search term:

    {
        "The empire state building is really big.": "empire state building",
        "Built in Manhattan in the 19th century.":  "manhattan 1900s",
        "Back in 1946,":                            "1946 historical",
        ...
    }

"""

# Stage 2) fetching the images

"""
History file structure (JSON) =  A flat dict mapping each remote Pexels URL to the local file path where that image has been saved:
    {
        "https://images.pexels.com/photos/36042878/pexels-photo-36042878.jpeg": 
            "stock_footage/pexels-photo-36042878.jpg",
        "https://images.pexels.com/photos/11223344/pexels-photo-11223344.jpeg":
            "stock_footage/pexels-photo-11223344.jpg"
    }

Image folder structure
-----------------------
    stock_footage/
        history.json                        ← the cache index above
        pexels-photo-36042878.jpg
        pexels-photo-11223344.jpg
        ...
"""
