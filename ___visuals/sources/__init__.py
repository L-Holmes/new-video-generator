"""
___visuals/sources — where footage comes from when it comes from outside.

    downloads.py    Pexels video/image search + every download helper +
                    load_stock_footage(), the top-level "fetch all the
                    candidates" entry point
    wikipedia.py    the MediaWiki search + page-image fetch, for scenes that
                    want the real thing rather than generic stock
    stamp_fetch.py  a handful of cut-out-able pictures for the decorate
                    editor's STAMP tab — Pexels images or Wikipedia
                    thumbnails, whichever the row's stamp_source names

Everything here talks to the network and writes into the run's
stock_footage cache; nothing here renders. Keep this file EMPTY of imports.
"""
