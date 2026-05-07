

# prereqs
uv pip install faster-whisper num2words


# recording

arecord -f cd -t wav output.wav

# Usage:  
python main.py --name myproject
#         → reads script-myproject.txt, caches to CACHE-myproject/, outputs to myproject-OUTPUT/




# Process
1) user bullet points idea
2) user asks ai to generate a script
3) user manually splits into script scenes, by adding newlines
4) user asks ai to generate pexels search terms
5) run this script





-------------

# types
- Just stock footage
- Just diagram
- Just text???
- joint...
    - Explainer background + images
    - stock footage + images
    - stock footage + arrow / logos (like fireship...?)
    - stock footage + text????
    - step by step diagrams



hmmmm... do i make a list of top youtubers for each niche? 


e.g. 

niche |name | subscribers | views
--------------------------------
tech | fireship  | x |  y
tech | fireship  | x |  y
tech | fireship  | x |  y
animation story time| odd1stout  | x |  y
animation story time| odd1stout  | x |  y
animation story time| odd1stout  | x |  y


# running tests



uv run python -m spacy download en_core_web_sm
uv run python -m pytest test_sentence_splitter.py -v
