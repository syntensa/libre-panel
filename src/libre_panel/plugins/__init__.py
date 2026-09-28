"""The plugin API. Plugins import everything they need from here.

See docs/PLUGINS.md. Kinds so far: services (``libre_panel.services``);
sensor sources and display drivers have their own entry point groups
(``libre_panel.sensors``, ``libre_panel.devices``).
"""

from libre_panel.plugins.host import (
    EVENTS,
    PluginHost,
    Service,
    ServiceHost,
    ServiceManager,
    Toast,
)
from libre_panel.plugins.loader import API_VERSION, PluginError, discover, registry

__all__ = [
    "API_VERSION",
    "EVENTS",
    "PluginError",
    "PluginHost",
    "Service",
    "ServiceHost",
    "ServiceManager",
    "Toast",
    "discover",
    "registry",
]
