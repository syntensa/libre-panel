"""Fetch the .NET libraries that LibreHardwareMonitor needs, for the Windows build.

    python packaging/windows/fetch_lhm.py [FOLDER] [--arch x64|x86|arm64] [--nupkg-dir DIR]

The libraries are not part of the repository. This downloads the pinned packages from
NuGet over HTTPS, checks every package against the SHA-256 recorded below, takes out only
the library that Libre Panel loads (the .NET Framework build, not the reference assembly
or a build for another runtime) and checks that against its own SHA-256. FOLDER (default
``packaging/windows/lhm-libs``) then holds the libraries, their licence texts and
``SOURCES.txt``; the PyInstaller build bundles it as ``lhm``, and with a pip install it is
what ``dll_dir`` under ``[sensors.librehardwaremonitor]`` names. It fails, and leaves an
existing FOLDER alone, when anything does not match.

``--nupkg-dir`` reads the packages (``<id>.<version>.nupkg``) from a folder instead of
NuGet, for builds without a network; they are checked all the same.

The licences were checked before these were pinned; see NOTICE.md. To move to other
versions, check their licences again, change the table and run ``--print-pins``: it
prints the hashes of what the table now names (and checks and writes nothing).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

NUGET = "https://api.nuget.org/v3-flatcontainer"
HERE = Path(__file__).resolve().parent
DEFAULT_FOLDER = HERE / "lhm-libs"
LICENSES = HERE / "licenses"  # texts that ship with the libraries (not from the packages)
ARCHITECTURES = ("x64", "x86", "arm64")


@dataclass(frozen=True)
class Library:
    """One library to take out of one NuGet package."""

    package: str  # the NuGet id
    version: str
    nupkg: str  # SHA-256 of the .nupkg
    member: str  # the library inside it; {arch} is win-x64, win-x86 or win-arm64
    dll: str  # its file name
    sha256: str | dict[str, str]  # of the library; per architecture where they differ
    licence: str  # SPDX expression, as the package says
    source: str  # where its source code is
    text: str = ""  # a licence text inside the package to take along

    @property
    def url(self) -> str:
        name = self.package.lower()
        return f"{NUGET}/{name}/{self.version}/{name}.{self.version}.nupkg"

    @property
    def filename(self) -> str:
        return f"{self.package.lower()}.{self.version}.nupkg"

    def expected(self, arch: str) -> str:
        return self.sha256 if isinstance(self.sha256, str) else self.sha256[arch]


# In the order Libre Panel loads them (libre_panel/sensors/lhm_inprocess.py: LIBRARIES).
# The newest stable LibreHardwareMonitorLib (0.9.6) with the versions of its companions that
# its package asks for, which also are what a Libre Panel mod ran with: each file here has the
# SHA-256 of the one that worked there. HidSharp is its netstandard2.0 build, which the .NET
# Framework 4.7.2 and later run (there is a net35 one too).
LIBRARIES = (
    Library(
        "System.Memory",
        "4.6.3",
        "26078aeb758c9ae985e8bf851f973026061da6a5eb4837204d0c2d2204c72955",
        "lib/net462/System.Memory.dll",
        "System.Memory.dll",
        "d5e8e4866f9cfa66f7765660f84b210198893e55335487afe5ebda342c0e913d",
        "MIT",
        "https://github.com/dotnet/maintenance-packages/tree/f62ca0009b038cab4725a720f386623a969d73ad",
    ),
    Library(
        "System.Buffers",
        "4.6.1",
        "b00451e91d016fbec091ad1e361f3a7015e1d91d4047f7e48a74455b2a673d79",
        "lib/net462/System.Buffers.dll",
        "System.Buffers.dll",
        "2d78d770c9cb997199154ae8c018b9f1d1efbc86729f7264dde6dbad2a12cac3",
        "MIT",
        "https://github.com/dotnet/maintenance-packages/tree/6b84308c9ad012f53240d72c1d716d7e42546483",
    ),
    Library(
        "System.Numerics.Vectors",
        "4.6.1",
        "2bc500a86dcb02f2032d6d877f9e2d6e9e4a79080e57239b4198679d4031f2c7",
        "lib/net462/System.Numerics.Vectors.dll",
        "System.Numerics.Vectors.dll",
        "20c2fa81b8c70d651099d762954f285fd4f942e63b2d7217c145dab8d4b2f4c9",
        "MIT",
        "https://github.com/dotnet/maintenance-packages/tree/6b84308c9ad012f53240d72c1d716d7e42546483",
    ),
    Library(
        "System.Runtime.CompilerServices.Unsafe",
        "6.1.2",
        "5f6a7f53af3465f92beb6da873ebe0e496206c313313b98badee4355a6b25937",
        "lib/net462/System.Runtime.CompilerServices.Unsafe.dll",
        "System.Runtime.CompilerServices.Unsafe.dll",
        "08cbd7278b66f1e68425a82d4b97181a4130d93e3dd91831407aba7212ccdacf",
        "MIT",
        "https://github.com/dotnet/maintenance-packages/tree/f62ca0009b038cab4725a720f386623a969d73ad",
    ),
    Library(
        "BlackSharp.Core",
        "1.0.4",
        "6d1bb863efe7b50e18a96e99ddce18175f3de6eb8afbc791c50ffbde176c4ed7",
        "lib/net472/BlackSharp.Core.dll",
        "BlackSharp.Core.dll",
        "9a1a8409bf94104eabb3e0f1ba684a79bc5ca4e5ba9b0449dc53e4faec9f2a78",
        "MPL-2.0",
        "https://github.com/Blacktempel/BlackSharp/tree/1aded1badeba275f0eab429d2a1959391d04de92",
    ),
    Library(
        "RAMSPDToolkit-NDD",
        "1.4.2",
        "57c893824a68c71538055f242586616d4205a671c300897c21b2a6f88270ce64",
        "lib/net472/RAMSPDToolkit-NDD.dll",
        "RAMSPDToolkit-NDD.dll",
        "b6882354c7c8ec186617e421507743dbfae09c5c1fc24cef76a1d0c0c26651de",
        "MPL-2.0",
        "https://github.com/Blacktempel/RAMSPDToolkit/tree/3b47b960e0830fef344624ad5e389675d5f0a1ce",
    ),
    Library(
        "HidSharp",
        "2.6.4",
        "cf55d4b4d7b42dc1d8496b7b592ba00d1ecd48a70bc07bd8dabd3e9976d108e7",
        "lib/netstandard2.0/HidSharp.dll",
        "HidSharp.dll",
        "79f2bc8ddff102e6dbede84b2c211a3aea2ed675bf762fa0f02ecb65f61ee812",
        "Apache-2.0",
        "https://software.seekye.com/hidsharp",
        text="LICENSE.txt",
    ),
    Library(
        "LibreHardwareMonitorLib",
        "0.9.6",
        "60db5e0d9135f4658b385cae139d3b9eaeac0a8e867d322d20b5dbe4c4039197",
        "runtimes/{arch}/lib/net472/LibreHardwareMonitorLib.dll",
        "LibreHardwareMonitorLib.dll",
        {
            "x64": "6ebc194316536ba61af5be24508ad9fcbb2ecc685e716c12e787c79530f66bf0",
            "x86": "61acb0df4c05de343481ca3b56455942f121bb19e8402b4c221192d0bc9af694",
            "arm64": "155a6df244002b2f399f09edb881ec148f0e6545f1e494af657d5666093d86d2",
        },
        "MPL-2.0 (with PawnIO modules under LGPL-2.1-or-later inside)",
        "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/tree/3d331e3370efb858411f19511373eff65a218701",
    ),
)


class FetchError(Exception):
    """Something is not as pinned; the message says what."""


def download(url: str) -> bytes:
    """The bytes at an https address (a redirect to anything else is refused)."""
    if not url.startswith("https://"):
        raise FetchError(f"{url} is not an https address")
    request = urllib.request.Request(url, headers={"User-Agent": "libre-panel-build"})
    with urllib.request.urlopen(request, timeout=120) as response:
        if not response.geturl().startswith("https://"):
            raise FetchError(f"{url} was redirected to {response.geturl()}, which is not https")
        return response.read()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _check(what: str, data: bytes, expected: str) -> None:
    got = sha256(data)
    if got != expected:
        raise FetchError(
            f"{what} is not what was checked: SHA-256 {got}, expected {expected}. "
            "The download may be damaged, or the package was replaced."
        )


def _take(library: Library, package: bytes, arch: str, folder: Path, licences: Path) -> str:
    """Check one package, write its library (and licence text) into ``folder``."""
    _check(f"{library.package} {library.version}", package, library.nupkg)
    member = library.member.format(arch=f"win-{arch}")
    try:
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            dll = archive.read(member)
            text = archive.read(library.text) if library.text else b""
    except (KeyError, zipfile.BadZipFile) as exc:
        raise FetchError(f"{library.package} {library.version} has no {member}: {exc}") from exc
    expected = library.expected(arch)
    _check(f"{member} of {library.package} {library.version}", dll, expected)
    (folder / library.dll).write_bytes(dll)
    if text:
        licences.mkdir(exist_ok=True)
        (licences / f"{library.package}-{library.text}").write_bytes(text)
    return expected


def fetch(
    folder: Path = DEFAULT_FOLDER,
    arch: str = "x64",
    fetch_package: Callable[[Library], bytes] | None = None,
    libraries: tuple[Library, ...] = LIBRARIES,
    licence_texts: Path | None = LICENSES,
) -> list[str]:
    """Put the libraries into ``folder``, replacing what is there. Returns lines that say
    what was taken (library, package, licence, SHA-256). Raises FetchError."""
    if arch not in ARCHITECTURES:
        raise FetchError(f"unknown architecture {arch!r}: {', '.join(ARCHITECTURES)}")
    get = fetch_package or (lambda library: download(library.url))
    folder = Path(folder)
    folder.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".{folder.name}-", dir=folder.parent))
    work.chmod(0o755)  # mkdtemp makes it private; this is for the build, and to be read
    try:
        lines = []
        for library in libraries:
            digest = _take(library, get(library), arch, work, work / "licenses")
            lines.append(
                f"{library.dll:<44} {library.package} {library.version}, {library.licence}, "
                f"sha256 {digest}"
            )
        if licence_texts is not None and licence_texts.is_dir():
            (work / "licenses").mkdir(exist_ok=True)
            for text in sorted(licence_texts.glob("*.txt")):
                shutil.copyfile(text, work / "licenses" / text.name)
        sources = ["Where the source code of these libraries is (see also NOTICE.md):", ""]
        sources += [f"{lib.dll}: {lib.package} {lib.version}, {lib.source}" for lib in libraries]
        (work / "SOURCES.txt").write_text("\n".join(sources + ["", *lines]) + "\n", "utf-8")
        if folder.exists():
            shutil.rmtree(folder)
        work.rename(folder)
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise
    return lines


def pins(
    fetch_package: Callable[[Library], bytes] | None = None,
    libraries: tuple[Library, ...] = LIBRARIES,
) -> list[str]:
    """The SHA-256 of each package and of its library (per architecture where the package
    has one for each), to be written into the table when it moves to other versions."""
    get = fetch_package or (lambda library: download(library.url))
    lines = []
    for library in libraries:
        package = get(library)
        lines.append(f"{library.package} {library.version}: nupkg {sha256(package)}")
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            for arch in ARCHITECTURES:
                member = library.member.format(arch=f"win-{arch}")
                if member in archive.namelist():
                    lines.append(f"    {arch}: {sha256(archive.read(member))}  {member}")
                if "{arch}" not in library.member:
                    break
    return lines


def _from_folder(nupkg_dir: Path) -> Callable[[Library], bytes]:
    def read(library: Library) -> bytes:
        path = nupkg_dir / library.filename
        try:
            return path.read_bytes()
        except OSError as exc:
            raise FetchError(f"{path} cannot be read: {exc}") from exc

    return read


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folder", nargs="?", type=Path, default=DEFAULT_FOLDER)
    parser.add_argument("--arch", choices=ARCHITECTURES, default="x64", help="of the Python")
    parser.add_argument("--nupkg-dir", type=Path, help="read the packages from here, not NuGet")
    parser.add_argument(
        "--print-pins", action="store_true", help="print the hashes of the packages, nothing else"
    )
    args = parser.parse_args(argv)
    source = _from_folder(args.nupkg_dir) if args.nupkg_dir else None
    try:
        if args.print_pins:
            print("\n".join(pins(source, LIBRARIES)))
            return 0
        lines = fetch(args.folder, args.arch, source, LIBRARIES, LICENSES)
    except (FetchError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{len(lines)} libraries in {args.folder}:")
    print("\n".join(f"  {line}" for line in lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
