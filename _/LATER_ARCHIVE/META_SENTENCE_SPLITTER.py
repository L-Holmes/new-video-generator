import spacy

nlp = spacy.load("en_core_web_sm")

def split_text_into_sections(text: str) -> list[str]:
    # 0) strip markdown headings first
    text = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    doc = nlp(text)
    splits = {0, len(doc)}

    # 1) hard ends + hyphen
    for i, t in enumerate(doc):
        if t.text in {".", "!", "?", ";", ":", "-"}:
            splits.add(i + 1)

    # 2) clause starters – only if left side >3 tokens
    for i, t in enumerate(doc):
        if t.lower_ in {"where", "that", "who", "as", "while", "when", "but"}:
            prev = max(s for s in splits if s < i)
            if i - prev > 3:
                splits.add(i)

    # 3) verbs – keep short S-V-O and phrasal verbs together
    for t in doc:
        if t.pos_!= "VERB" or t.dep_ in {"amod", "acl"}:
            continue
        # keep "sped past", "laid down"
        if t.i + 1 < len(doc) and doc[t.i + 1].dep_ == "prt":
            continue
        dobj = next((c for c in t.children if c.dep_ in {"dobj", "obj"}), None)
        if dobj and len(list(dobj.subtree)) <= 3 and (dobj.i - t.i) < 4:
            continue
        if t.dep_ == "ROOT":
            prev = max(s for s in splits if s < t.i)
            if t.i - prev > 12:
                splits.add(t.i)
                continue
        splits.add(t.i + 1)

    # 4) prepositions – never cut short in/on/at
    for t in doc:
        if t.pos_!= "ADP":
            continue
        if t.lower_ in {"in", "on", "at"}:
            continue
        if t.lower_ in {"of", "with", "for", "to"}:
            j = t.i + 1
            while j < len(doc) and doc[j].text not in ".!?;:":
                j += 1
            if j - t.i > 4:
                splits.add(t.i + 1)

    # 5) noun lists
    ordinals = {"first","second","third","fourth","fifth",
                "sixth","seventh","eighth","ninth","tenth"}
    chunks = list(doc.noun_chunks)
    for a, b in zip(chunks[:-1], chunks[1:]):
        between = doc[a.end:b.start]
        if any(x.pos_ == "VERB" for x in between):
            continue
        if b.root.dep_ == "appos":
            continue
        if a.root.head == b.root.head and a.root.dep_ == "dobj":
            continue
        # ---- FIX 1: keep "John Ford the second" together ----
        if b.text.lower().startswith("the ") and b.root.lower_ in ordinals:
            continue
        # ---- FIX 2: don't split "lift in the skyscraper" ----
        if len(between) == 1 and between[0].lower_ in {"in","on","at"}:
            continue
        # keep "what if... and..." together
        if b.start >= 2 and doc[b.start-2].lower_ == "what" and doc[b.start-1].lower_ == "if":
            continue
        splits.add(b.start)

    # 6) bare "NOUN the NOUN" lists – skip ordinals
    for i in range(1, len(doc)-1):
        if doc[i].lower_ == "the" and doc[i-1].pos_ in {"NOUN","PROPN"}:
            if i+1 < len(doc) and doc[i+1].lower_ in ordinals: # << FIX
                continue
            prev = max(s for s in splits if s < i)
            if not any(t.pos_ == "VERB" for t in doc[prev:i]):
                splits.add(i)

    # 7) split before "all" after a list
    for i, t in enumerate(doc):
        if t.lower_ == "all" and i > 0 and doc[i-1].pos_ in {"NOUN","PROPN"}:
            splits.add(i)

    # ---- build sections (your original merge logic) ----
    idx = sorted(splits)
    raw = [doc[idx[i]:idx[i+1]].text.strip() for i in range(len(idx)-1)]

    final_sections, buf = [], ""
    must_merge = {"and","but","so","where","that","who","as","while","which"}
    for sec in raw:
        if not sec:
            continue
        has_noun = any(t.pos_ in {"NOUN","PROPN","NUM"} for t in nlp(sec))
        if (sec.lower() in must_merge or not has_noun) and len(sec.split()) < 3 and sec[-1] not in ",;:.!?-":
            buf += sec + " "
        else:
            final_sections.append((buf + sec).strip())
            buf = ""
    if buf:
        final_sections[-1] += " " + buf.strip()

    return [s.strip() for s in final_sections if s.strip()]



def run_test(text):
    print(f"\nBEFORE:\n{text}")
    print("\nAFTER:")
    sections = split_text_into_sections(text)
    print("\n".join(sections))
    print("-" * 30)

if __name__ == "__main__":
    original = "#first heading\nThe old lighthouse keeper the wandering sailor the curious child and the patient dog all walked along the endless shoreline where the crashing waves the drifting clouds the distant mountains and the whispering wind created a tapestry of motion and sound that inspired the painter the poet the musician and the dreamer who gathered their brushes their notebooks their instruments and their hopes as the gentle sun the rising tide the circling gulls and the rustling dunes surrounded them with a quiet reminder that the world the sea the sky and the land are always alive with stories waiting to be found"
    cases = [original, "The fast cat sat on the comfortable mat", "The baker kneaded the bread while the fire crackled", "The red car the blue truck the green bike sped past the house", "anyway, here is a sentence that has punctuatoin", "and here is another- does it handle everything all fine? but what if the other person and the dragon laid down together at the edge of the brook?", "The empire state building is really big. Built in Manhattan in the 19th century. Back in 1946, the technician John Ford the second created a new OpenAI carburettor for the lift in the skyscraper where they drunk chanoyu tea, which would go on to revolutionize the entire world."]
    
    for t in cases:
        run_test(t)
