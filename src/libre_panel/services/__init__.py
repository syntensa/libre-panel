"""Services Libre Panel brings itself (``notices``, ``game``, ``autopilot``, ``devices``,
``memory``, ``cooling``, ``downloads``).

They are written against the plugin API like any plugin's, and registered by
``plugins.loader`` as "built in", so they need no package metadata. Each is
off until ``[services] enabled`` lists it; see docs/CONFIGURATION.md.
"""
