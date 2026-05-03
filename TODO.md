instead of just fetching one video clip...
fetch 2 video clips and 3 images.
then display all on screen at same time (can do in lower resolution that original if that helps)
2 videos across the top, 3 images across the bottom.
and then some guideance on inputs on bottom.
e.g. press 1 to accept vid 1 (perhaps label it with (1), 2 for vid 2, 3 for img 1, 4 for img 2, etc...)
or then do something else... e.g. backspace or 'f' to decline all... (and then mark that image position in one of the maps as [MANUAL_INTERVENTION]
. Then whatever cache thing is marked with manual interventoin will be checked.
If there are any entries requiring manual intervention, it will 
maybe we make it easier for the user by having a sort of thing pop (or just CLI input)pop up where they can enter a number to select the number that they have added the image/footage for, and then enter the name of that file...? and then it will auto update the maps, re check and then ask again (user presses ctrl-c to cancel)



It then ends.
user keeps running the program until there are none left at which point the code continues.
For sections that require two or more clips, as determined by def _get_num_stock_images(input_script: str),, just repeat [twice or more] for these two runs. 
since the successive runs use the same search, just present the user with the same options, minus any that have been chosen already. 
By the way, the code will cleanup the downlaoded vids and images after the user has selected an option... (i.e. remove anything that wasn't chosen...)

Ensure the file picker doesn't freeze

Launching media review GUI...
[review] 49 item(s) need review (0 already decided). Launching GUI…
[cleanup] removed 178 unchosen file(s).

           }

  [2] SCRIPT: "But the Europeans believed it, and happily paid the monster-bird premium."



  ---------
Also i don't really know what happens for when a single line requires multiple footages... feel free to update the jsons and expected structures to make that work better if need be... 





TODO:
- ask AI to do the thing were it fetches multiple clips and images to choose from...
===> integrate that...     (A)
- Add things to make the intro better
    - e.g. the custom 'built on' images that add a new image to an arrangement as it goes on...
    - e.g. ??? whatever like highlighting or whatever to make intro more interesting...
- Add the sound effects thing.
    - Download loads of sound effects
    - Have a system for adding the sound effects...


------------
(A)

instead of just fetching one video clip...
fetch 2 video clips and 3 images.
then display all on screen at same time (can do in lower resolution that original if that helps)
2 videos across the top, 3 images across the bottom.
and then some guideance on inputs on bottom.
e.g. press 1 to accept vid 1 (perhaps label it with (1), 2 for vid 2, 3 for img 1, 4 for img 2, etc...)
or then do something else... e.g. backspace or 'f' to decline all... (and then mark that image position in one of the maps as [MANUAL_INTERVENTION]
. Then whatever cache thing is marked with manual interventoin will be checked.
If there are any entries requiring manual intervention, it will tell the user what requires manual intervention, and then where they should put the image / vid, and what maps they should manually change. 
It then ends.
user keeps running the program until there are none left at which point the code continues.


For sections that require two or more clips, as determined by def _get_num_stock_images(input_script: str),, just repeat [twice or more] for these two runs. 
since the successive runs use the same search, just present the user with the same options, minus any that have been chosen already. 

By the way, the code will cleanup the downlaoded vids and images after the user has selected an option... (i.e. remove anything that wasn't chosen...)

------------


TODO LATER:
- maybe link up the other image providers??!?!?!?!? (or actually... just stick with pexels for now...)
