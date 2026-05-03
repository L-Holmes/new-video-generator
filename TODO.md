
The manual timing thing doesn't seem to go back fast enoough for short clips... 
    - seems to go out of sync with longer videos as well..
    - (which makes sense since it just stitches together the timings etc.)
it doesn't seem to rewind right
Consider adding the thing for switching out the images
Add the thing that creates images by joining three together onto a background...
    - maybe have a preset list of animated backgrounds? like scrumpled paper or static or water or whatever????





Loading stock footage...
🔍 Cache miss. Fetching from Pexels...
Traceback (most recent call last):
  File "/home/main/code/easyStockGenerators/MY-VIDEO-GENERATOR/main.py", line 1021, in <module>
    main()
    ~~~~^^
  File "/home/main/code/easyStockGenerators/MY-VIDEO-GENERATOR/main.py", line 977, in main
    script_text_to_media_url_and_runtime = load_stock_footage(scriptTextToPexelSearch)
  File "/home/main/code/easyStockGenerators/MY-VIDEO-GENERATOR/main.py", line 816, in load_stock_footage
    num_images, max_runtime_per_clip_seconds = _get_num_stock_images(script_text)
                                               ~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^
  File "/home/main/code/easyStockGenerators/MY-VIDEO-GENERATOR/main.py", line 554, in _get_num_stock_images
    raise KeyError(
    ...<3 lines>...
    )
KeyError: "[_get_num_stock_images] Could not find timing for:\n  'If you open your kitchen cupboard right now, you probably have a jar'\n\nAvailable keys:\n"


-===========================================================-
--- (A) ---

I now want to add an additional stage:
- for any media that aren't right (i.e. have 'False') - we'll want to check the output of the new code... 
- attempt to:
    - fetch 2 more (different) stock videos
        - (save these to our local file system as we do when we do the initial media fetch)
    - ask the user to review them, using the same system...
        -> actually, show both on screen using a tkinter window. 
        -> with the text on how to accept. 
        -> e.g. user picks either (a) or (b) or presses space or 'n' or 'f' to accept none.
    - if they do yes / accept, end immediately, and replace the reference to that media in the 
    - if they say no to first two (accept none), 
    - then instead search for images.
    - fetch a maximum of three images.
    - show those on screen as well, and ask for choose.
    - if they say no to all of those, set the media for that thing as [MANUAL_INTERVENTION]

- then when that's all done:
    - as long as there is [MANUAL INTERVENTION], just loop and wait.
    - print to the cli that there is manual intervention required for [all lines that require manual intervention].
    - print instruction on how to update...
        - i.e. adding to the images, updating the cache files manually, and then changing [MANUAL INTERVENTION] to the image reference...
        TODO- make the above stage more clear....


-===========================================================-



TODO:
- add the code for what to do if the images aren't right
- link up the new code into the main function
    - Do a dry run
- need to add cache fetching for all of the new code...
    - if the output file already exists and not empty...
- [A] add the code for what to do if the images aren't right
    - I guess fetch 2 more searches... (or 2 more stock footage, and then 2 more images? at max?)
    ---> in fact we don't even fetch stock footage as is! perhaps I add htat functionality
    - . Anyway, then if still not good, they are set to manual review.
        - then code will end if there is anything left in the manual review. (assumes user adds an image / picture, then manually updates whatever map file).
        - ...Presumably the code will give user instructions on this every time it detects anything left with 'manual resolve'
- Add functionality to stitch together the final video and the script.wav into one mp4.
- review below todods

-------


TODO LATER:
- test with fetching actual video footage- not just images...
- look through my old archive code to see if i've missed anything obvious..
- maybe link up the other image providers??!?!?!?!? (or actually... just stick with pexels for now...)

============================
