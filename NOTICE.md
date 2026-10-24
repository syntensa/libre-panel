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
resolution and the video-mode init, and whose screens are the `spur-ii`,
`spur-classic`, `spur-horizon`, `spur-header`, `spur-focus`, `spur-session`,
`spur-cockpit`, `spur-studio` and `round-report` themes. Its light layers
(`src/libre_panel/assets/light3d/`: fan rotors, weather symbols and the SPUR II
letters, rendered once in Blender, without colour) are its author's work, given
to Libre Panel under GPL-3.0-or-later.

**jonsbo-zc360-linux-driver** — Copyright (c) 2026 Lucille Hon and the earlier
work it builds on, MIT. <https://github.com/RimuruNava/jonsbo-zc360-linux-driver>.
The JONSBO ZC-360 protocol (the command and frame packets, their DES key, the
commands, the first frame's commit, the order the screens are written in and why)
comes from its notes, made from USB captures and the vendor's Windows
application. Libre Panel's driver (`devices/jonsbo_zc360.py`) is its own code.

**jonsbo-tf3-linux-driver** — purplebrain23, MIT.
<https://github.com/purplebrain23/jonsbo-tf3-linux-driver>. That the pump's screen of the
JONSBO TF3-360SC is a TURZX USB panel (`1cbe:0035`), and that its fans carry the ZC-360's
screens, comes from its notes.

**InfoPanel** — habibrehmansg/infopanel and its contributors, GPL-3.0.
<https://github.com/habibrehmansg/infopanel>. The protocols of the JONSBO DS916 (its
handshake and the JPEG pictures) and DS339 (the MS9132 chip's start, its EDID, the mode
numbers, the frame header and when its output is turned on) come from its JONSBO drivers,
made from the vendor's application and USB captures; so do those of the Hongtai / Jungle
Leopard screens (JL-PROTOCOL.md: the command frame, the picture's envelope, the keep-alive) and
of Lian Li's TURZX-like screens (from the MIT-licensed lian-li-linux driver: the JPEG layer,
the cleared PNG layer, the picture queue), and of Thermalright's Trofeo Vision as far as its
notes from the vendor's application go (the bars' 462 rows, the 4:2:0 JPEG and its size, the
packet count), and of BeadaPanel's (the Status-Link and Panel-Link messages, the start and
end tags by the panel's version, the reset before a stream). Libre Panel's drivers
(`devices/jonsbo_ds916.py`, `devices/ms9132.py`, `devices/hongtai.py`, `devices/lianli_usb.py`,
`devices/thermalright.py`, `devices/beadapanel.py`) are its own code.

**turing-smart-screen** (.NET) — Copyright (c) 2021 machi_pon, MIT.
<https://github.com/usausa/turing-smart-screen>. The protocol of Thermalright's Trofeo Vision
screens (the question and its answer, the picture's header and packets, the turn for each
variant, how often a picture goes) comes from its `LcdDriver.TrofeoVision` and
`LcdDriver.TrofeoVisionLy`. Libre Panel's driver (`devices/thermalright.py`) is its own code.

**BeadaPanel's own sources** — NXElec's beadapanelDaemon, panelLink and BeadaTools (GPL-3.0,
<https://github.com/NXElec>) and JT365's beada Linux driver (GPL-2.0-or-later,
<https://github.com/JT365/beada>). The BeadaPanel protocol (the Status-Link and Panel-Link
messages and their checksum, the panel's information, the model codes, the stream's format,
the backlight's range) comes from them. Libre Panel's driver (`devices/beadapanel.py`) is its
own code.

**LibreHardwareMonitor** (MPL-2.0) — the sensor source for the GPU,
temperatures, power and fans on Windows. The Windows downloads carry its
library and read it inside Libre Panel (see "Windows downloads" below); a
LibreHardwareMonitor that you run yourself can still be read through its web
server. <https://github.com/LibreHardwareMonitor/LibreHardwareMonitor>.

**Barlow** (Jeremy Tribby), **JetBrains Mono** (JetBrains) and **Liberation
Sans** (Red Hat, Google) — bundled fonts under the SIL Open Font License 1.1,
see `src/libre_panel/fonts/`. The app shows its own text in them too.

**Preact** (MIT, Copyright (c) 2015-present Jason Miller) and **htm** (Apache-2.0,
Copyright 2018 Google Inc.) — the app's front-end is built with them. The files
are those npm publishes, unchanged but for one import in Preact's hooks; their
versions, addresses and SHA-256 are in `src/libre_panel/editor/app/vendor/SOURCES.txt`,
their licence texts beside them.

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
library and the libraries it needs in the folder `_internal\lhm` (and once more in
`sensors\_internal\lhm`, for the sensor service), so that nothing else has to be
installed to read the GPU, temperatures, power and fans. They are
the files that NuGet publishes, unchanged, for the .NET Framework. They are not in
this repository: `packaging/windows/fetch_lhm.py` fetches them when a download is
built and checks every package against a SHA-256 written into the script. The
licence of each was read before the version was pinned (2026-10-10).

| Library | Version | Licence | Copyright | Source code |
|---|---|---|---|---|
| LibreHardwareMonitorLib | 0.9.7-pre769 (a preview build of 0.9.7) | MPL-2.0 | LibreHardwareMonitor and Contributors; in part Michael Möller and Contributors (OpenHardwareMonitor) | <https://github.com/LibreHardwareMonitor/LibreHardwareMonitor>, commit `a8381734697f901ed3a99706bab62c867c4bb62e` |
| RAMSPDToolkit-NDD | 1.6.3 | MPL-2.0 | Florian K. | <https://github.com/Blacktempel/RAMSPDToolkit>, commit `69aab150784385dc2733a31f3332f646ab3c2e16` |
| DiskInfoToolkit | 2.2.1 | MPL-2.0 | Florian K. | <https://github.com/Blacktempel/DiskInfoToolkit>, commit `87ffc498301c92ef61d2b8c3066928bf4cf51409` |
| BlackSharp.Core | 1.2.2 | MPL-2.0 | Florian K. | <https://github.com/Blacktempel/BlackSharp>, commit `ee9cdfeefcfee0f5c4c9fb8919dcce5f89bce7cc` |
| HidSharp | 2.6.4 | Apache-2.0 | James F. Bellinger | <https://software.seekye.com/hidsharp> |
| System.Memory, System.Runtime.CompilerServices.Unsafe | 4.6.3, 6.1.2 | MIT | .NET Foundation and Contributors | <https://github.com/dotnet/maintenance-packages>, commit `f62ca0009b038cab4725a720f386623a969d73ad` |
| System.Buffers, System.Numerics.Vectors | 4.6.1, 4.6.1 | MIT | .NET Foundation and Contributors | <https://github.com/dotnet/maintenance-packages>, commit `6b84308c9ad012f53240d72c1d716d7e42546483` |

The commits are the ones the packages name as their source. The licence texts are
in `_internal\lhm\licenses`, the same list with each file's SHA-256 in
`_internal\lhm\SOURCES.txt`.

- **MPL-2.0** (LibreHardwareMonitorLib, RAMSPDToolkit-NDD, DiskInfoToolkit,
  BlackSharp.Core). The libraries are passed on unchanged, in executable form. Their
  source code is at the addresses above; the text of the licence is
  `licenses\MPL-2.0.txt`. No file of the four carries the "Incompatible With Secondary
  Licenses" notice, so section 3.3 of the MPL lets them be combined with a program
  under the GPL, as Libre Panel is.
- **Apache-2.0** (HidSharp). Its licence text, with the copyright line, is
  `licenses\HidSharp-LICENSE.txt`, as the package has it.
- **MIT** (the four System libraries): `licenses\MIT-dotnet-foundation.txt`.
- **PawnIO modules inside LibreHardwareMonitorLib (LGPL-2.1-or-later).**
  LibreHardwareMonitorLib.dll embeds fourteen modules of
  [PawnIO.Modules](https://github.com/namazso/PawnIO.Modules) (Copyright (C)
  namazso and contributors; signed builds): AMDFamily0F, AMDFamily10,
  AMDFamily17, IntelMSR, IsaBridgeEC, LpcACPIEC, LpcCrOSEC, LpcIO, Nvidia,
  RyzenSMU, SmbusI801, SmbusIntelSkylakeIMC, SmbusNCT6793 and SmbusPIIX4.
  LibreHardwareMonitor names them in its own list of third-party notices. Their
  licence is the GNU Lesser General Public License, version 2.1 or (at your option)
  any later version (`licenses\LGPL-2.1.txt`); their source code is in the
  repository named above. The
  modules are data that LibreHardwareMonitor hands to the PawnIO driver; Libre
  Panel does not change or use them otherwise.
- **The PawnIO driver is a program of its own** (GPL-2.0-or-later, with an
  exception for modules; <https://pawnio.eu>) that LibreHardwareMonitor uses to
  read the CPU's temperature, power and clock, the RAM temperature and the
  mainboard's fans. The Windows downloads carry its installer, not the driver
  (next section); the setup, or `libre-panel system-setup` from the portable zip,
  runs it. Uninstalling Libre Panel leaves the driver on the PC, as other programs
  may use it.
- **Not included:** the System.Management and System.Threading.AccessControl
  packages that LibreHardwareMonitorLib's package lists, which the .NET Framework
  build does not use (the .NET Framework has its own System.Management).

## Downloads: ffmpeg, PresentMon and PawnIO's installer

The downloads carry the programs that Libre Panel's features run, in the folder
`tools` next to the program, so that nothing else has to be installed: **ffmpeg**
(Windows and Linux) for the video mode, **PresentMon** (Windows) for the frame
rates of games and the installer of the **PawnIO** driver (Windows) for the CPU's
sensors. They are the files their projects publish, unchanged; of ffmpeg's release
only the program itself. They are not in this repository:
`packaging/fetch_tools.py` fetches them when a download is built and checks every
file against a SHA-256 written into the script. It also checks that ffmpeg is the
GPL build with libx264 and without the non-free parts, and that the licence text
came with it. `tools\SOURCES.txt` in the download lists the files with their
SHA-256 and says where each one's source code is; the licence texts are in
`tools\licenses`. The licence of each was read before the version was pinned
(2026-10-11).

| Program | Version | Licence | Copyright | Source code |
|---|---|---|---|---|
| ffmpeg | n8.1.3-9-g29e619e767: FFmpeg 8.1.3 and nine more commits of its release branch, as built by FFmpeg-Builds (win64-gpl and linux64-gpl) on 2026-09-30 | GPL-3.0-or-later | the FFmpeg developers; x264: the x264 project (VideoLAN) | <https://github.com/FFmpeg/FFmpeg>, commit `29e619e767cde9045a75c29bc9a8278ae7b3a98b`; more below |
| PresentMon (console program) | 2.6.0 | MIT | Intel Corporation | <https://github.com/GameTechDev/PresentMon>, commit `e13fce6acdb55a808fd8318175a56863e532d95f` (tag `v2.6.0`) |
| PawnIO installer (`PawnIO_setup.exe`) | 2.2.0 | the installer may be redistributed unmodified, as it says itself; the driver it installs: GPL-2.0-or-later with a special exception | namazso | the driver: <https://github.com/namazso/PawnIO>, commit `5cdf470831fdfff3f7f1d06363ca6b230f3bf35a` (tag `2.2.0`) |

- **ffmpeg (GPL-3.0-or-later).** The build is configured `--enable-gpl
  --enable-version3`: libx264, which does the encoding, and the other libraries
  the recipe names are part of the program, and the whole is passed on under the
  GNU General Public License, version 3 or later. The licence text that came with
  the build is `tools\licenses\FFmpeg-LICENSE.txt`. Libre Panel starts ffmpeg as a
  program of its own and talks to it through pipes; it is not linked with it, and
  it is under the same licence. Nothing of the build was changed. Another ffmpeg
  takes its place with `video.ffmpeg` in `config.toml`. The source of what is in
  the program:
  - FFmpeg: <https://github.com/FFmpeg/FFmpeg>, commit
    `29e619e767cde9045a75c29bc9a8278ae7b3a98b` (branch release/8.1).
  - The build recipe, which names every library in the build with its version or
    commit (folder `scripts.d`): <https://github.com/BtbN/FFmpeg-Builds>, commit
    `6c9aec5fc9a72ec3abedd1fa84db141fa18cf52b` (tag `autobuild-2026-09-30-13-08`).
  - x264, which does the encoding: <https://code.videolan.org/videolan/x264>,
    commit `0480cb05fa188d37ae87e8f4fd8f1aea3711f7ee`.

  If one of these addresses stops working, ask for the source in an issue of this
  project.
- **PresentMon (MIT).** Its licence text, with the copyright line, is
  `tools\licenses\PresentMon-LICENSE.txt`; the notices of the third-party parts in
  its source (AMD, NVIDIA, Google, Microsoft) are `PresentMon-THIRD_PARTY.txt`.
  Libre Panel starts it as a program of its own.
- **PawnIO's installer.** The official `PawnIO_setup.exe` of the PawnIO project, as
  published on its release page, unmodified (it is signed by its author, and the
  signature stays valid). It says of itself: "This installer can be redistributed
  unmodified." The build checks that the file still says so. The driver it installs
  is free software under the GNU General Public License, version 2 or later, with a
  special exception for programs and modules that talk to it through its device
  interface. Its licence texts are `tools\licenses\PawnIO-COPYING.txt` and
  `PawnIO-NOTICE.txt`; its source code is at the address above. Libre Panel does not
  run the installer unless the user asks for it, and does not remove the driver when
  it is uninstalled itself.

## Windows downloads: the app's window

The Windows downloads show the app in a window of its own with **pywebview**
(BSD-3-Clause, Copyright (c) 2014-2017, Roman Sirokov) and what it needs: **bottle**
(MIT), **proxy_tools** (MIT) and **typing_extensions** (PSF-2.0). Their licence texts
are in the `*.dist-info` folders of the download. pywebview brings the libraries of the
**Microsoft Edge WebView2 SDK** (`Microsoft.Web.WebView2.Core.dll`,
`Microsoft.Web.WebView2.WinForms.dll`, `WebView2Loader.dll`), which come with this
licence:

> Copyright (C) Microsoft Corporation. All rights reserved.
>
> Redistribution and use in source and binary forms, with or without modification, are
> permitted provided that the following conditions are met:
>
> - Redistributions of source code must retain the above copyright notice, this list of
>   conditions and the following disclaimer.
> - Redistributions in binary form must reproduce the above copyright notice, this list
>   of conditions and the following disclaimer in the documentation and/or other
>   materials provided with the distribution.
> - The name of Microsoft Corporation, or the names of its contributors may not be used
>   to endorse or promote products derived from this software without specific prior
>   written permission.
>
> THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY
> EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF
> MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL
> THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
> SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
> PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
> INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,
> STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF
> THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

The WebView2 runtime that draws the window is not in the downloads: Windows 11 has it,
and Windows 10 gets it with Microsoft Edge.

Product names such as TURZX, Turing Smart Screen, Lian Li, Thermalright, BeadaPanel, WeAct and
Noctua (and its fans' model names) are trademarks of their respective owners; Libre Panel is not
affiliated with them. The fan looks named after Noctua's fans are drawings of Libre Panel's own, made
from the blades' shape, without any of Noctua's pictures or logos.
