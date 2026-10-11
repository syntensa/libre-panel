# Notices and credits

**turing-smart-screen-python** — Copyright (C) 2021 Matthieu Houdebine and
contributors, GPL-3.0-or-later. <https://github.com/mathoudebine/turing-smart-screen-python>.
The Turing/TURZX protocol knowledge (command packet format, DES key, command
ids, USB ids, panel resolutions) comes from this project. The serial drivers
follow its protocol implementations (`library/lcd/lcd_comm_rev_*.py`,
`lcd_comm_weact_*.py`): command
numbers and layouts, the bitmap formats and the commands it never needs.
Libre Panel's implementation is its own code, released under the same license.

**SPUR II** — the private project that verified the V1.x USB protocol on a 9.2"
panel, found the zero-length-packet fix, the RGBA requirement, the 480×1920
resolution and the video-mode init, and whose classic layout is the `spur-ii`
theme. Its light layers (`src/libre_panel/assets/light3d/`: fan rotors, weather
symbols and the SPUR II letters, rendered once in Blender, without colour) are
its author's work, given to Libre Panel under GPL-3.0-or-later.

**LibreHardwareMonitor** (MPL-2.0) — the sensor source for the GPU,
temperatures, power and fans on Windows. The Windows downloads carry its
library and read it inside Libre Panel (see "Windows downloads" below); a
LibreHardwareMonitor that you run yourself can still be read through its web
server. <https://github.com/LibreHardwareMonitor/LibreHardwareMonitor>.

**Barlow** (Jeremy Tribby), **JetBrains Mono** (JetBrains) and **Liberation
Sans** (Red Hat, Google) — bundled fonts under the SIL Open Font License 1.1,
see `src/libre_panel/fonts/`.

**librehardwaremonitor-api** (Sab44, MIT) — the real LibreHardwareMonitor
output in `tests/data/` comes from its test fixtures.

**Open-Meteo** — weather data from <https://open-meteo.com>, CC BY 4.0.

**lian-li-linux** (MIT) — reference for Lian Li LCD protocols (not used yet).

**Pillow**, **psutil**, **pyusb**, **pycryptodome**, **pyserial**,
**libusb-package** — runtime dependencies under their own licenses. The Windows
downloads also carry **pythonnet** (MIT, Copyright (c) 2006-2021 the contributors of
the Python.NET project), **clr-loader** (MIT, Copyright (c) 2019-2026 Benedikt
Reinartz) and **cffi** (MIT-0), which start the .NET Framework for
LibreHardwareMonitor; their licence texts are in the `*.dist-info` folders of the
download.

## Windows downloads: LibreHardwareMonitor and its libraries

The Windows downloads (setup and portable zip) carry LibreHardwareMonitor's .NET
library and the libraries it needs in the folder `_internal\lhm`, so that nothing
else has to be installed to read the GPU, temperatures, power and fans. They are
the files that NuGet publishes, unchanged, for the .NET Framework. They are not in
this repository: `packaging/windows/fetch_lhm.py` fetches them when a download is
built and checks every package against a SHA-256 written into the script. The
licence of each was read before the version was pinned (2026-10-10).

| Library | Version | Licence | Copyright | Source code |
|---|---|---|---|---|
| LibreHardwareMonitorLib | 0.9.6 | MPL-2.0 | LibreHardwareMonitor and Contributors; in part Michael Möller and Contributors (OpenHardwareMonitor) | <https://github.com/LibreHardwareMonitor/LibreHardwareMonitor>, commit `3d331e3370efb858411f19511373eff65a218701` |
| RAMSPDToolkit-NDD | 1.4.2 | MPL-2.0 | Florian K. | <https://github.com/Blacktempel/RAMSPDToolkit>, commit `3b47b960e0830fef344624ad5e389675d5f0a1ce` |
| BlackSharp.Core | 1.0.4 | MPL-2.0 | Florian K. | <https://github.com/Blacktempel/BlackSharp>, commit `1aded1badeba275f0eab429d2a1959391d04de92` |
| HidSharp | 2.6.4 | Apache-2.0 | James F. Bellinger | <https://software.seekye.com/hidsharp> |
| System.Memory, System.Runtime.CompilerServices.Unsafe | 4.6.3, 6.1.2 | MIT | .NET Foundation and Contributors | <https://github.com/dotnet/maintenance-packages>, commit `f62ca0009b038cab4725a720f386623a969d73ad` |
| System.Buffers, System.Numerics.Vectors | 4.6.1, 4.6.1 | MIT | .NET Foundation and Contributors | <https://github.com/dotnet/maintenance-packages>, commit `6b84308c9ad012f53240d72c1d716d7e42546483` |

The commits are the ones the packages name as their source. The licence texts are
in `_internal\lhm\licenses`, the same list with each file's SHA-256 in
`_internal\lhm\SOURCES.txt`.

- **MPL-2.0** (LibreHardwareMonitorLib, RAMSPDToolkit-NDD, BlackSharp.Core). The
  libraries are passed on unchanged, in executable form. Their source code is at
  the addresses above; the text of the licence is `licenses\MPL-2.0.txt`. No file
  of the three carries the "Incompatible With Secondary Licenses" notice, so
  section 3.3 of the MPL lets them be combined with a program under the GPL, as
  Libre Panel is.
- **Apache-2.0** (HidSharp). Its licence text, with the copyright line, is
  `licenses\HidSharp-LICENSE.txt`, as the package has it.
- **MIT** (the four System libraries): `licenses\MIT-dotnet-foundation.txt`.
- **PawnIO modules inside LibreHardwareMonitorLib (LGPL-2.1-or-later).**
  LibreHardwareMonitorLib.dll embeds twelve modules of
  [PawnIO.Modules](https://github.com/namazso/PawnIO.Modules) (Copyright (C)
  namazso and contributors; signed builds of release 0.1.6): AMDFamily0F,
  AMDFamily10, AMDFamily17, IntelMSR, IsaBridgeEC, LpcACPIEC, LpcCrOSEC, LpcIO,
  RyzenSMU, SmbusI801, SmbusNCT6793 and SmbusPIIX4. LibreHardwareMonitor names
  them in its own list of third-party notices. Their licence is the GNU Lesser
  General Public License, version 2.1 or (at your option) any later version
  (`licenses\LGPL-2.1.txt`); their source code is in the repository named above
  (`IsaBridgeEC.p` came into it after release 0.1.6, with release 0.2.0). The
  modules are data that LibreHardwareMonitor hands to the PawnIO driver; Libre
  Panel does not change or use them otherwise.
- **The PawnIO driver is not part of the downloads.** It is a separate program
  (GPL-2.0-or-later, with an exception for modules; <https://pawnio.eu>) that
  LibreHardwareMonitor uses to read the CPU's temperature, power and clock, the
  RAM temperature and the mainboard's fans. Libre Panel does not install it; the
  user does, and starts Libre Panel as administrator.
- **Not included:** DiskInfoToolkit (MPL-2.0), which LibreHardwareMonitor needs
  only to read drives, and which Libre Panel does not read; and the
  System.Management and System.Threading.AccessControl packages that its package
  lists, which the .NET Framework build does not use (the .NET Framework has its
  own System.Management).

Product names such as TURZX, Turing Smart Screen, Lian Li and WeAct are
trademarks of their respective owners; Libre Panel is not affiliated with them.
