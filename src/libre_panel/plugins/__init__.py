"""The plugin API. Plugins import everything they need from here.

See docs/PLUGINS.md. Kinds: services (``libre_panel.services``), screens
(``libre_panel.screens``), widget types (``libre_panel.widgets``) and
transitions (``libre_panel.transitions``), toast styles
(``libre_panel.toasts``) and editor pages (``libre_panel.editor_pages``); sensor
sources and display drivers have their own entry point groups
(``libre_panel.sensors``, ``libre_panel.devices``).
"""

from libre_panel.plugins.host import (
    ANY_MODE,
    EVENTS,
    PluginHost,
    Service,
    ServiceHost,
    ServiceManager,
    Toast,
)
from libre_panel.plugins.loader import API_VERSION, PluginError, discover, registry
from libre_panel.plugins.pages import EditorPage, PageContext
from libre_panel.plugins.render import (
    RenderContext,
    Screen,
    ToastStyle,
    Transition,
    WidgetType,
)

__all__ = [
    "ANY_MODE",
    "API_VERSION",
    "EVENTS",
    "EditorPage",
    "PageContext",
    "PluginError",
    "PluginHost",
    "RenderContext",
    "Screen",
    "Service",
    "ServiceHost",
    "ServiceManager",
    "Toast",
    "ToastStyle",
    "Transition",
    "WidgetType",
    "discover",
    "registry",
]
