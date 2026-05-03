
------------

instead of just fetching one video clip...
fetch 2 video clips and 3 images.
then display all on screen at same time (can do in lower resolution that original if that helps)
2 videos across the top, 3 images across the bottom.
and then some guideance on inputs on bottom.
e.g. press 1 to accept vid 1 (perhaps label it with (1), 2 for vid 2, 3 for img 1, 4 for img 2, etc...)
or then do something else... e.g. backspace or 'f' to decline all... (and then mark that image position in one of the maps as 'manual intervention'
. Then whatever cache thing is marked with manual interventoin will be checked.
If there are any entries requiring manual intervention, it will tell the user what requires manual intervention, and then where they should put the image / vid, and what maps they should manually change. 
It then ends.
user keeps running the program until there are none left at which point the code continues.


For sections that require two or more clips, as determined by def _get_num_stock_images(input_script: str),, just repeat [twice or more] for these two runs. 
since the successive runs use the same search, just present the user with the same options, minus any that have been chosen already. 

By the way, the code will cleanup the downlaoded vids and images after the user has selected an option... (i.e. remove anything that wasn't chosen...)


------------




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
