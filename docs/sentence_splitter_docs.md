


# Sentence tagging rules

fantastic!
Maybe clarify with examples for each... also a few of the last ones.. like 53 54 etc.. they are too jargonny.. I should be able to know what each one means as a non-jargon knower / simpleton!

Then next task:
Just for the first rule to begin with... add a feature were after a split it will update the integer list (the 'value' of the map / the 'ids' section of the Chunk... to record what operation happened. 


E.g.
The dog jumped. The man barked
[
==> Chunk(The dog jumped., [1])
==> Chunk(The man barked, [])
]


Again, just implement for rule 1 for now, as a proof of concept. 
in fact yeah;
- If we split, then we add the number of the splitting rule to the left part.
- If we merge, then combine the rules of the two halves.. left half first, then right half.. and of course insert the merging rules at the start as is standard. 
- If we split, and the current sentence already has rules... give all the existing rules to the right half, and then of course only add the newest split rule to the left half.. as is standard.. 

Always:
most recently ran rules get inserted at the front of the array...

