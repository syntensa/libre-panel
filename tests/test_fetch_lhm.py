"""packaging/windows/fetch_lhm.py: only the pinned packages and libraries get through.

The packages are made here, as small archives; nothing is downloaded.
"""

import dataclasses
import hashlib
import importlib.util
import io
import re
import sys
import zipfile
from pathlib import Path

import pytest

from libre_panel.sensors import lhm_inprocess

SCRIPT = Path(__file__).resolve().parent.parent / "packaging" / "windows" / "fetch_lhm.py"
spec = importlib.util.spec_from_file_location("fetch_lhm", SCRIPT)
fetch_lhm = importlib.util.module_from_spec(spec)
sys.modules["fetch_lhm"] = fetch_lhm  # dataclasses look their module up
spec.loader.exec_module(fetch_lhm)

SHA256 = re.compile(r"^[0-9a-f]{64}$")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def make_package(member, content, *extra):
    """A .nupkg: the library at ``member``, other files next to it (name, content)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(member, content)
        for name, data in extra:
            archive.writestr(name, data)
    return buffer.getvalue()


def lab(tmp_path):
    """Two libraries in packages we made, pinned as the real ones are: the one with a
    licence text inside, the other with a library per architecture."""
    packages = {
        "First": make_package(
            "lib/net472/First.dll",
            b"first",
            ("ref/net472/First.dll", b"a reference assembly: not this"),
            ("../evil.dll", b"a name that goes up a folder"),
            ("LICENSE.txt", b"Apache-ish text"),
        ),
        "Second": make_package(
            "runtimes/win-x64/lib/net472/Second.dll",
            b"second x64",
            ("runtimes/win-x86/lib/net472/Second.dll", b"second x86"),
            ("runtimes/win-arm64/lib/net472/Second.dll", b"second arm64"),
        ),
    }
    libraries = (
        fetch_lhm.Library(
            "First", "1.0", sha(packages["First"]), "lib/net472/First.dll", "First.dll",
            sha(b"first"), "Apache-2.0", "https://example.org/first", text="LICENSE.txt",
        ),
        fetch_lhm.Library(
            "Second", "2.0", sha(packages["Second"]),
            "runtimes/{arch}/lib/net472/Second.dll", "Second.dll",
            {"x64": sha(b"second x64"), "x86": sha(b"second x86"), "arm64": sha(b"second arm64")},
            "MPL-2.0", "https://example.org/second",
        ),
    )  # fmt: skip
    texts = tmp_path / "texts"
    texts.mkdir()
    (texts / "MPL-2.0.txt").write_text("the MPL")
    (texts / "notes.md").write_text("not a licence text")
    return packages, libraries, texts


def run(tmp_path, folder, packages, libraries, texts, **options):
    return fetch_lhm.fetch(
        folder,
        fetch_package=lambda library: packages[library.package],
        libraries=libraries,
        licence_texts=texts,
        **options,
    )


def test_the_pinned_packages_are_what_the_program_loads():
    names = [library.dll.removesuffix(".dll") for library in fetch_lhm.LIBRARIES]
    assert names == list(lhm_inprocess.LIBRARIES)  # the same libraries, in the same order
    for library in fetch_lhm.LIBRARIES:
        assert SHA256.match(library.nupkg), library.package
        hashes = library.sha256 if isinstance(library.sha256, dict) else {"x64": library.sha256}
        assert all(SHA256.match(h) for h in hashes.values()), library.package
        assert library.url == (
            f"https://api.nuget.org/v3-flatcontainer/{library.package.lower()}/"
            f"{library.version}/{library.package.lower()}.{library.version}.nupkg"
        )
        assert library.licence and library.source.startswith("https://")
        assert "ref/" not in library.member  # the reference assembly has no code in it
    main = fetch_lhm.LIBRARIES[-1]
    assert main.package == "LibreHardwareMonitorLib" and set(main.sha256) == {"x64", "x86", "arm64"}
    assert "runtimes/{arch}/lib/net472/" in main.member  # the .NET Framework build
    assert len({library.package for library in fetch_lhm.LIBRARIES}) == len(fetch_lhm.LIBRARIES)


def test_only_the_pinned_libraries_come_out_of_the_packages(tmp_path):
    packages, libraries, texts = lab(tmp_path)
    folder = tmp_path / "libs"
    lines = run(tmp_path, folder, packages, libraries, texts)
    assert sorted(p.name for p in folder.iterdir()) == [
        "First.dll", "SOURCES.txt", "Second.dll", "licenses",
    ]  # fmt: skip
    assert (folder / "First.dll").read_bytes() == b"first"
    assert (folder / "Second.dll").read_bytes() == b"second x64"
    assert sorted(p.name for p in (folder / "licenses").iterdir()) == [
        "First-LICENSE.txt", "MPL-2.0.txt",
    ]  # fmt: skip
    assert (folder / "licenses" / "First-LICENSE.txt").read_bytes() == b"Apache-ish text"
    assert not (tmp_path / "evil.dll").exists()  # nothing was taken by the name an archive gives
    assert [line.split()[0] for line in lines] == ["First.dll", "Second.dll"]
    assert f"sha256 {sha(b'first')}" in lines[0] and "Apache-2.0" in lines[0]
    sources = (folder / "SOURCES.txt").read_text("utf-8")
    assert "First.dll: First 1.0, https://example.org/first" in sources
    assert "Second.dll: Second 2.0, https://example.org/second" in sources
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".libs")]  # no scraps


@pytest.mark.parametrize("arch", ["x64", "x86", "arm64"])
def test_the_architecture_picks_the_library_and_its_hash(tmp_path, arch):
    packages, libraries, texts = lab(tmp_path)
    run(tmp_path, tmp_path / "libs", packages, libraries, texts, arch=arch)
    assert (tmp_path / "libs" / "Second.dll").read_bytes() == f"second {arch}".encode()


def test_an_architecture_nobody_knows_is_refused(tmp_path):
    packages, libraries, texts = lab(tmp_path)
    with pytest.raises(fetch_lhm.FetchError, match="unknown architecture"):
        run(tmp_path, tmp_path / "libs", packages, libraries, texts, arch="mips")


def test_a_package_that_is_not_the_checked_one_is_refused(tmp_path):
    packages, libraries, texts = lab(tmp_path)
    packages["Second"] = make_package(
        "runtimes/win-x64/lib/net472/Second.dll", b"second x64", ("extra", b"changed package")
    )
    folder = tmp_path / "libs"
    with pytest.raises(fetch_lhm.FetchError, match=r"Second 2.0 is not what was checked"):
        run(tmp_path, folder, packages, libraries, texts)
    assert not folder.exists()  # not even the first one stays
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".libs")]


def with_package(libraries, package):
    """The first library, pinned to this package as it is."""
    return (dataclasses.replace(libraries[0], nupkg=sha(package)), libraries[1])


def test_a_library_that_is_not_the_checked_one_is_refused(tmp_path):
    """A package that holds another library at the right place, pinned as it is."""
    packages, libraries, texts = lab(tmp_path)
    packages["First"] = make_package("lib/net472/First.dll", b"swapped", ("LICENSE.txt", b"x"))
    libraries = with_package(libraries, packages["First"])
    with pytest.raises(fetch_lhm.FetchError, match=r"First.dll of First 1.0 is not what was"):
        run(tmp_path, tmp_path / "libs", packages, libraries, texts)
    assert not (tmp_path / "libs").exists()


@pytest.mark.parametrize("package", [make_package("lib/other/First.dll", b"first"), b"not a zip"])
def test_a_package_without_the_library_is_refused(tmp_path, package):
    packages, libraries, texts = lab(tmp_path)
    packages["First"] = package
    with pytest.raises(fetch_lhm.FetchError, match="has no lib/net472/First.dll"):
        run(tmp_path, tmp_path / "libs", packages, with_package(libraries, package), texts)


def test_a_failure_leaves_the_folder_that_was_there(tmp_path):
    packages, libraries, texts = lab(tmp_path)
    folder = tmp_path / "libs"
    run(tmp_path, folder, packages, libraries, texts)
    packages["Second"] = b"damaged"
    with pytest.raises(fetch_lhm.FetchError):
        run(tmp_path, folder, packages, libraries, texts)
    assert (folder / "Second.dll").read_bytes() == b"second x64"  # as it was


def test_a_new_run_replaces_the_old_folder_whole(tmp_path):
    packages, libraries, texts = lab(tmp_path)
    folder = tmp_path / "libs"
    folder.mkdir()
    (folder / "Old.dll").write_bytes(b"left over from another version")
    run(tmp_path, folder, packages, libraries, texts)
    assert not (folder / "Old.dll").exists() and (folder / "First.dll").exists()


def test_the_licence_texts_are_optional(tmp_path):
    packages, libraries, _ = lab(tmp_path)
    run(tmp_path, tmp_path / "libs", packages, libraries, None)  # a copy of the script alone
    assert [p.name for p in (tmp_path / "libs" / "licenses").iterdir()] == ["First-LICENSE.txt"]


def test_the_repository_carries_the_licence_texts_that_ship_with_the_libraries():
    names = sorted(p.name for p in fetch_lhm.LICENSES.glob("*.txt"))
    assert names == ["LGPL-2.1.txt", "MIT-dotnet-foundation.txt", "MPL-2.0.txt"]
    assert "Mozilla Public License Version 2.0" in (fetch_lhm.LICENSES / "MPL-2.0.txt").read_text()
    assert "Version 2.1, February 1999" in (fetch_lhm.LICENSES / "LGPL-2.1.txt").read_text()
    assert "Copyright (c) .NET Foundation" in (fetch_lhm.LICENSES / names[1]).read_text()


def test_what_was_fetched_stays_out_of_the_repository():
    root = SCRIPT.parent.parent.parent
    assert "packaging/windows/lhm-libs/" in (root / ".gitignore").read_text().splitlines()
    assert fetch_lhm.DEFAULT_FOLDER == root / "packaging" / "windows" / "lhm-libs"


# -- downloading -------------------------------------------------------------------------


class Answer:
    def __init__(self, data, url):
        self.data, self.url = data, url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.data

    def geturl(self):
        return self.url


def test_downloads_are_https_only(monkeypatch):
    asked = []

    def urlopen(request, timeout=None):
        asked.append((request.full_url, timeout))
        return Answer(b"bytes", request.full_url)

    monkeypatch.setattr(fetch_lhm.urllib.request, "urlopen", urlopen)
    assert fetch_lhm.download("https://api.nuget.org/x.nupkg") == b"bytes"
    assert asked == [("https://api.nuget.org/x.nupkg", 120)]
    with pytest.raises(fetch_lhm.FetchError, match="not an https address"):
        fetch_lhm.download("http://api.nuget.org/x.nupkg")
    assert len(asked) == 1  # not even asked

    monkeypatch.setattr(
        fetch_lhm.urllib.request, "urlopen", lambda request, timeout=None: Answer(b"", "http://x/")
    )
    with pytest.raises(fetch_lhm.FetchError, match="redirected to http://x/"):
        fetch_lhm.download("https://api.nuget.org/x.nupkg")


# -- the command ----------------------------------------------------------------------------


def test_the_command_reports_and_fails_with_a_message(tmp_path, capsys, monkeypatch):
    packages, libraries, texts = lab(tmp_path)
    monkeypatch.setattr(fetch_lhm, "LIBRARIES", libraries)
    monkeypatch.setattr(fetch_lhm, "LICENSES", texts)
    nupkgs = tmp_path / "nupkgs"
    nupkgs.mkdir()
    for library in libraries:
        (nupkgs / library.filename).write_bytes(packages[library.package])
    folder = tmp_path / "libs"
    assert fetch_lhm.main([str(folder), "--nupkg-dir", str(nupkgs)]) == 0
    out = capsys.readouterr().out
    assert "2 libraries in" in out and "First.dll" in out and (folder / "First.dll").exists()

    (nupkgs / libraries[1].filename).write_bytes(b"damaged")
    assert fetch_lhm.main([str(folder), "--nupkg-dir", str(nupkgs)]) == 1
    assert "Second 2.0 is not what was checked" in capsys.readouterr().err
    (nupkgs / libraries[1].filename).unlink()
    assert fetch_lhm.main([str(folder), "--nupkg-dir", str(nupkgs)]) == 1
    assert "second.2.0.nupkg cannot be read" in capsys.readouterr().err
    assert (folder / "Second.dll").exists()  # the folder from before is as it was
