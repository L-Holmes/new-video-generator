"""
___visuals/ai — everything that asks a model to make a picture.

    generation.py                the pipeline stages: ai_stock stills,
                                 ai_stock groups, ai_edit_previous
    generate_stickman_images.py  the fal.ai call itself (flux edit, style-
                                 grounded on .resources/ai-reference-images/)
    edit.py                      edit an existing frame with a prompt
    postprocess.py               de-AI a returned image (white balance the
                                 background, drop the artefacts) before it
                                 goes anywhere near a scene

Keep this file EMPTY of imports: `from ___visuals.ai.generation import ...`
should not drag fal_client (and a network client) into a run with no AI
scenes in it.
"""
