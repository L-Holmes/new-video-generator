script = """
The empire state building is really big. 
Built in Manhattan in the 19th century. 
Back in 1946, 
the technician John Ford the second 
created a new carburettor for 
the lift in the skyscraper 
where they drunk chanoyu tea,
which would go on to revolutionize the entire world.
But where exactly in the world did this tea originate? It was in the newly formed state of Okinawa.
Back in the 1700s, 
the samurai of Japan ruled over the kingdom.
They discovered Koshuta — 
a type of rare plant which only grows in the foothills of the Japanese Alps...
"""

import ollama


# === IMIGARY FOR THAT SCENE ===
# ai_request = f"For each newline in this script, think what imagery would be shown for that scene in my YouTube video. Be really specific with your noun choices. Here is my script:\n{script}"
# 
# response = ollama.chat(
    # model="qwen2.5:7b",
    # messages=[
        # {"role": "user", "content": ai_request}
    # ]
# )
# 
# stage_1 = response["message"]["content"]
# print(stage_1)
# 
# === CLEANUP: IMIGARY --> PEXELS SEARCH ===
#
# ai_request = f"Now, no extra text, just plain script response, no ai fluff. For each of the scenes, turn it into a search term I would type in to pexels. Just do [actual sentence from original input]~[video request]. Here is the document you generated me earlier: \n{stage_1}"
# 
# response2 = ollama.chat(
    # model="qwen2.5:7b",
    # messages=[
        # {"role": "user", "content": ai_request}
    # ]
# )
# 
# reply2 = response2["message"]["content"]
# print(reply2)



# ===========================================================================================================

# === SPLIT SCENE INTO MULTIPLE DIFERENT IMAGES ===
# twotest="But where exactly in the world did this tea originate"
# 
# ai_request = f"Split this sentence into the different images that would make up this slide on my powerpoint. Identify the key nouns and visual elements. Just simple bullet point: \n{twotest}"
# 
# response2 = ollama.chat(
    # model="qwen2.5:7b",
    # messages=[
        # {"role": "user", "content": ai_request}
    # ]
# )
# 
# reply2 = response2["message"]["content"]
# print(reply2)
# 
# # --------------
# === (CLEANUP) SPLIT SCENE INTO MULTIPLE DIFERENT IMAGES ===
# 
# ai_request2 = f"Strip out any ai fulff, explanations, headings or follow up questions: give me just a csv of identified key terms. nothing else: \n{reply2}"
# 
# response3 = ollama.chat(
    # model="qwen2.5:7b",
    # messages=[
        # {"role": "user", "content": ai_request2}
    # ]
# )
# reply3 = response3["message"]["content"]
# print(reply3)

# -------------
# === DETERMINE IF STOCK FOOTAGE ===

scenes_text = """
The empire state building is really big.
Built in Manhattan in the 19th century.
Back in 1946,
the technician John Ford the second
created a new carburettor for
the lift in the skyscraper
where they drunk chanoyu tea,
which would go on to revolutionize the entire world.
But where exactly in the world did this tea originate?
It was in the newly formed state of Okinawa.
Back in the 1700s,
the samurai of Japan ruled over the kingdom.
They discovered Koshuta —
a type of rare plant which only grows in the foothills of the Japanese Alps...
"""

# turn each non-empty line into a scene
scenes = [line.strip() for line in scenes_text.split("\n") if line.strip()]

for scene in scenes:
    ai_request = f"""
Would this scene be likely to have nice stock footage available on sites like Pexels, Pixabay or Storyblocks?

Things like: dates, anything that is abstract without key nouns.. Obscure specific things like a specific type of something... or a named person who isn't that famous.. will not have stock footage (video) available.
Only popular things, with obvious nouns will. 
Or maybe like popular celebrities etc.

Scene: {scene}

Just output:
yes
or
no

Nothing else.
"""

    response4 = ollama.chat(
        model="qwen2.5:7b",
        messages=[
            {"role": "user", "content": ai_request}
        ]
    )

    reply4 = response4["message"]["content"].strip()

    print(scene)
    print(reply4)
    print("-----")


# === ADD IN THE KEYWORDS ===
# "what are the keywords in this sentence? i.e. the nouns that you can see / imagine? Say 'none' if none... Just give me the keywords in a csv format. Nothing else, no intro, or outro or other AI slop..."
