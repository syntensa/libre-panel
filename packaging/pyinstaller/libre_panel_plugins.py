"""Entry point of libre-panel-plugins, the plugin host of the Rust app's downloads: the Python
plugins run in it (src/libre_panel/plugin_host.py, docs/PLUGINS.md)."""

import sys

from libre_panel.plugin_host import main

if __name__ == "__main__":
    sys.exit(main())
