"""Libre Panel's sensor service (Windows): LibreHardwareMonitor with the rights its sensors need.

CPU temperature, power and clock, the mainboard's temperatures and fans, the temperature of
the memory modules and of the drives can be read only by a process with administrator rights
(and through the PawnIO driver). Libre Panel itself runs without them. So a small Windows
service, ``LibrePanelSensors``, runs LibreHardwareMonitor as LocalSystem and hands the readings
to Libre Panel over a local named pipe. It only reads: it never writes to the hardware, never
sets a fan, never starts a program, and the one thing a client can ask is "the readings,
please" (and which version it is).

- ``protocol``: the messages on the pipe, as pure functions.
- ``core``: the service's reader of LibreHardwareMonitor and its answers, without Windows.
- ``winpipe``, ``winservice``, ``winapi``: the pipe, the service host and the Windows calls
  (ctypes, loaded on first use).
- ``setup``: ``libre-panel system-setup``, which installs the service (and PawnIO, and the
  group that game frame rates need) for the setup and for the portable download alike.

docs/BACKGROUND.md ("The sensor service") is the security review of this design.
"""

SERVICE_NAME = "LibrePanelSensors"
DISPLAY_NAME = "Libre Panel sensors"
DESCRIPTION = (
    "Reads temperatures, power, fan speeds, memory and drives for Libre Panel with the rights "
    "this needs. It only reads: it changes no hardware setting and runs no other program."
)
PIPE_NAME = r"\\.\pipe\LibrePanelSensors"
SERVICE_EXE = "libre-panel-sensors.exe"  # in the folder "sensors" of the Windows downloads
FOLDER = "sensors"  # that folder, next to libre-panel.exe
