"""The editor's own pages, built in like Libre Panel's own services.

*Control* shows the panel's live picture, how it runs (the numbers of the main loop), the
sensor sources, the services with a restart, a test message of each kind, the theme per
mode and the log. *Autopilot* edits the rules of the ``autopilot`` service in config.toml
and shows what it does. They are editor pages like a plugin's (docs/PLUGINS.md), found
through ``plugins.loader.BUILT_IN_PARTS``, so they work in the downloads too, and share
the scripts and styles in ``web/``. Another built-in page is a module here, a line in
``BUILT_IN_PARTS`` and a script in ``web/console.js``'s table.
"""
