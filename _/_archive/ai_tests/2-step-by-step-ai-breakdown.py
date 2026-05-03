import spacy
import ollama
from collections import Counter

# Load spaCy for the heavy lifting
nlp = spacy.load("en_core_web_sm")

script = """The empire state building is really big. Built in Manhattan in the 19th century. Back in 1946, the technician John Ford the second created a new carburettor for the lift in the skyscraper where they drunk chanoyu tea, which would go on to revolutionize the entire world."""

doc = nlp(script)

# 1) ---- Determine the overall theme (NLP) ----
# We find the most frequent "Proper Nouns" or "Noun Chunks" to identify the subject
nouns = [chunk.text.lower() for chunk in doc.noun_chunks if not chunk.root.is_stop]
overall_theme = Counter(nouns).most_common(1)[0][0] if nouns else ""

# 2 ---- Split into sections (NLP/Logic) ----
# Without AI, we split by paragraphs (double newlines) or a specific sentence count
sections_text = [s.strip() for s in script.split('\n\n') if s.strip()]

# 3) ---- Section themes (NLP) ----
section_data = []
for sec in sections_text:
    sec_doc = nlp(sec)
    # Get the most prominent noun in this specific section
    sec_nouns = [t.text for t in sec_doc if t.pos_ in ["PROPN", "NOUN"] and not t.is_stop]
    sec_theme = Counter(sec_nouns).most_common(1)[0][0] if sec_nouns else overall_theme
    section_data.append({"text": sec, "theme": sec_theme})

# 4) ---- Splitting ----
final_phrases = []

for section in section_data:
    sec_doc = nlp(section["text"])
    
    # 4)a) Split into natural phrases (Sentences)
    for sent in sec_doc.sents:
        
        # 4)b) Split phrase into sub-phrases (Grammar-based breaking)
        # We break if the segment > 8 words AND we hit a "logical" split point
        tokens = list(sent)
        current_sub = []
        
        for i, token in enumerate(tokens):
            current_sub.append(token.text)
            
            # Logic: Split on Punctuation OR Conjunctions/Relative Pronouns (where, which, before, because)
            is_long_enough = len(current_sub) > 6
            is_split_point = token.pos_ in ["PUNCT", "CCONJ", "SCONJ"] or token.dep_ == "relcl"
            
            if is_long_enough and is_split_point:
                final_phrases.append({
                    "text": " ".join(current_sub).strip(" ,."),
                    "sec_theme": section["theme"]
                })
                current_sub = []
        
        if current_sub:
            final_phrases.append({
                "text": " ".join(current_sub).strip(" ,."),
                "sec_theme": section["theme"]
            })

# 5) ---- extract imagery / nouns ----
for item in final_phrases:
    phrase_doc = nlp(item["text"])
    
    # 5)a) NLP extraction (Nouns, ignoring "fluff" words)
    # We filter out stop words here to get '1946' from 'way back in 1946'
    nlp_imagery = [t.text for t in phrase_doc if t.pos_ in ["NOUN", "PROPN", "NUM"] and not t.is_stop]
    
    # 5)b) AI extraction (Refinement only)
    # This only catches items the dictionary might miss (like "chanoyu")
    existing = ", ".join(nlp_imagery)
    res = ollama.chat(model="qwen2.5:7b", messages=[
        {"role": "user", "content": f"List only the visual nouns in this text missing from this list: [{existing}]. Output ONLY the missing words, comma separated. Text: {item['text']}"}
    ])
    ai_extra = [n.strip() for n in res["message"]["content"].split(",") if n.strip() in item["text"]]
    
    item["imagery"] = list(set(nlp_imagery + ai_extra))

# x) ---- apply the themes & generate search term ----
# This is the "Final Stage" where AI combines context into a readable search term.
for item in final_phrases:
    imagery_str = ", ".join(item["imagery"])
    
    # Single simple task: Turn these nouns + context into a Pexels search query
    ai_final = ollama.chat(model="qwen2.5:7b", messages=[
        {"role": "system", "content": "You are a stock footage bot. Convert imagery into a 3-word Pexels search term. No extra text."},
        {"role": "user", "content": f"Context: {overall_theme}, {item['sec_theme']}. Imagery: {imagery_str}. Text: {item['text']}"}
    ])
    
    search_term = ai_final["message"]["content"].strip().replace('"', '')
    print(f"{item['text']} ~ {search_term}")
