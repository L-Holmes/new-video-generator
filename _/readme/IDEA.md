
For all:
- If a cache file exists, it won't be overwritten unless the user explicitly asks to or deleted that particular file.
- (this will then waterfall down future steps and clear them out accordingly.. apart from cached images..) 


# 1)
# - Manually break into scenes
# - save to "scenes.txt"
# - Either.. manually type in like $ to show scene ends
# - Or ask AI to identify, before manual resolution
# 
# 2)
# For each scene,
# break down into "clips".
# Each should be ideal for watch time-- e.g. less than 8 seconds
# --> generates a file showing the scenes and the clips, and the length of each clip
# e.g.
# #### scene 1
# $ the white stork landed upon ~ 3
# $ the red farm house ~ 4 
# 
# 2.5)
# Manually tag each 'scene'. 
# - Show user each scene
# - user tags as "image" or "stock vid" or "custom step by step diagram"... etc. (or even AI generated...)
# 
# --> generates a file showing the scenes, the clips, and the tags... 
# 
# 
# 2.6)
# Determine the keywords for each of those scenes (used for searching for clips. 
# - could maybe pipe keywords and original scene into AI to get a search term?
# - --> again, generate new file for clips -> Search term... (again, manually editable) 
# 
# 3) fetch images
# - First get the URLs (e.g. 2 or 3 for user to pick from???)
# - Then if any URLs are already cached, don't fetch them. Fetch the others.. 
# 
# 4)
# Custom images
# e.g. like you have a default background like that moving slightly crunched paper affects
# - then layer over the elements (e.g. three wise men, or map with arrow / lines...)
# - try and reuse lines / have set textures and appearance in a  config file... So same style for each vid... 
# 
# 
# 4) 
# Stitch together into initial video
# - maybe option to add the voice track? no probs not. 
# 
# x)
# Manually select scenes to have Ken Burns effect
# Only after all done and good 



questions:
- ways to add animated circles around key text?
- or common thing like highlight a word on e.g. a stock image of a newspaper in screen????
- would be nice to have this tool... or maybe a seperate tool with a  panel of sound affects.. so you can easily watch the video through, like on VLC, and add sound affects from the list... although probs some video editting software already does that...
- maybe transitions.. e.g. cross-fades between major scenes
- cache based on image source
- melt cli tool???
- Editly cli tool??


- highlighted text
-  circle
- The "Spotlight" Effect: Instead of a circle, dim the entire screen by 50% except for one focused rectangular or circular area to force the viewer's eye.
- Motion Arrows: Simple 2-frame animated arrows. They provide much more "energy" than a static arrow and are easy to overlay as small transparent loops.
- Subtle Camera Shake/Pulse: Adding a tiny, random pixel offset (1-2px) or a rhythmic "pulse" zoom (100\% to 102\%) to static images to make them feel like "video."
- Texture Overlays (Luma Mattes): Applying a semi-transparent "film grain" or "dust" loop over stock footage. It unifies different sources (AI vs. Stock) into a single cohesive aesthetic.
- Zoom punch — quick 10-15% zoom snap on a cut for emphasis
- Lower thirds — name/label strip at bottom of frame
- Vignette — subtle dark edge, makes any image feel cinematic
- Arrow/pointer — static or animated, pointing at something in frame
- Shake — 1-2 frame camera shake on impact moments


