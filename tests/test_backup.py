"""Settings → System → Backup: the settings and own themes in one zip, and back from one."""

import io
import json
import zipfile

import pytest

from libre_panel import backup
from libre_panel.config import load_config


@pytest.fixture
def home(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('theme = "lumen"\n\n[device]\nbrightness = 40\n', encoding="utf-8")
    mine = tmp_path / "themes" / "mine"
    (mine / "assets").mkdir(parents=True)
    (mine / "theme.json").write_text('{"name": "Mine"}', encoding="utf-8")
    (mine / "assets" / "back.png").write_bytes(b"\x89PNG picture")
    (mine / "layers.npz").write_bytes(b"made again by itself")
    return config, tmp_path / "themes"


def zipped(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_a_backup_holds_the_settings_and_the_own_themes_and_brings_them_back(home, tmp_path):
    config, themes = home
    data = backup.make_backup(config, themes)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read(backup.MANIFEST))
    assert names == {backup.MANIFEST, "config.toml", "themes/mine/theme.json",
                     "themes/mine/assets/back.png"}  # fmt: skip
    assert manifest["kind"] == backup.KIND and manifest["themes"] == ["mine"]

    other = tmp_path / "other"  # another computer, with a config and a theme of its own
    (other / "themes" / "theirs").mkdir(parents=True)
    (other / "themes" / "theirs" / "theme.json").write_text("{}", encoding="utf-8")
    (other / "config.toml").write_text('theme = "slate"\n', encoding="utf-8")
    done = backup.restore_backup(data, other / "config.toml", other / "themes")
    assert done == {"config": True, "themes": ["mine"]}
    assert load_config(other / "config.toml").device.brightness == 40
    assert (other / backup.KEPT).read_text(encoding="utf-8") == 'theme = "slate"\n'  # kept
    assert (other / "themes" / "mine" / "assets" / "back.png").read_bytes() == b"\x89PNG picture"
    assert (other / "themes" / "theirs" / "theme.json").exists()  # its own stays


@pytest.mark.parametrize(
    "files, said",
    [
        ({"config.toml": "x"}, "not a backup"),  # no manifest
        ({backup.MANIFEST: '{"kind": "else"}'}, "not a backup"),
        ({backup.MANIFEST: json.dumps({"kind": backup.KIND}), "../evil.txt": "x"}, "not a file"),
        (
            {backup.MANIFEST: json.dumps({"kind": backup.KIND}), "themes/../../x/y": "x"},
            "not a file",
        ),
        (
            {backup.MANIFEST: json.dumps({"kind": backup.KIND}), "themes/a b/theme.json": "x"},
            "not a file",
        ),
        ({backup.MANIFEST: json.dumps({"kind": backup.KIND}), "other.txt": "x"}, "not a file"),
        (
            {
                backup.MANIFEST: json.dumps({"kind": backup.KIND}),
                "config.toml": "[device]\nbrightness = 400\n",
            },
            "config.toml cannot be read",
        ),
    ],
)
def test_nothing_is_written_from_a_file_that_is_not_a_sound_backup(home, tmp_path, files, said):
    config, themes = home
    before = config.read_text(encoding="utf-8")
    with pytest.raises(backup.BackupError, match=said):
        backup.restore_backup(zipped(files), config, themes)
    assert config.read_text(encoding="utf-8") == before
    assert not (tmp_path / "evil.txt").exists() and not (tmp_path / "x").exists()
    with pytest.raises(backup.BackupError, match="not a backup"):
        backup.restore_backup(b"no zip at all", config, themes)
