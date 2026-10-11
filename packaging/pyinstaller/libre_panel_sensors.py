"""Entry point of libre-panel-sensors.exe, Libre Panel's sensor service (Windows release build).

Windows starts it as the service LibrePanelSensors; --check and --console are for a look by
hand (libre_panel/sensorservice/winservice.py).
"""

import sys

from libre_panel.sensorservice.winservice import main

if __name__ == "__main__":
    sys.exit(main())
