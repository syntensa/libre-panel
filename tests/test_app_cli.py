from PIL import Image

from libre_panel.app import fit_frame, run, target_size
from libre_panel.cli import main
from libre_panel.config import Config, DeviceConfig, SensorsConfig
from libre_panel.theme.model import find_theme, load_theme


def test_run_once_with_virtual_display(tmp_path):
    out = tmp_path / "frame.png"
    config = Config(
        device=DeviceConfig(driver="virtual", output=str(out)),
        sensors=SensorsConfig(providers=["demo"]),
    )
    run(config, once=True)
    with Image.open(out) as frame:
        assert frame.size == (480, 320)


def test_theme_is_fitted_to_a_different_panel():
    theme = load_theme(find_theme("libre-default"))
    config = Config(device=DeviceConfig(model="turing-9.2-usb"))
    size = target_size(config, theme)
    assert size == (1920, 480)
    frame = fit_frame(Image.new("RGB", (480, 320)), size, "#000000")
    assert frame.size == (1920, 480)


def test_cli_preview_and_models(tmp_path, capsys):
    out = tmp_path / "p.png"
    assert main(["preview", "spur-ii", "-o", str(out)]) == 0
    assert out.exists()
    assert main(["models"]) == 0
    assert "turing-9.2-usb" in capsys.readouterr().out


def test_cli_reports_errors(capsys):
    assert main(["preview", "does-not-exist"]) == 2
    assert "not found" in capsys.readouterr().err


def test_config_init(isolated_home):
    assert main(["config", "init"]) == 0
    assert (isolated_home / "config.toml").exists()
    assert main(["config", "init"]) == 1


def test_set_active_theme_keeps_comments(isolated_home):
    from libre_panel.config import load_config, set_active_theme, write_default_config

    path = write_default_config()
    set_active_theme("spur-ii")
    text = path.read_text()
    assert 'theme = "spur-ii"' in text and "# Libre Panel configuration" in text
    assert load_config(path).theme == "spur-ii"
    assert text.count("theme =") == 1


def test_running_panel_picks_up_theme_changes(tmp_path, isolated_home):
    import threading
    import time

    from libre_panel.config import load_config, set_active_theme, write_default_config

    path = write_default_config()
    out = tmp_path / "frame.png"
    text = path.read_text().replace('providers = ["psutil"]', 'providers = ["demo"]')
    path.write_text(
        text.replace('output = "libre-panel-frame.png"', f'output = "{out.as_posix()}"')
    )
    config = load_config(path)
    config.refresh_ms = 100
    stop = threading.Event()
    thread = threading.Thread(target=run, args=(config,), kwargs={"stop": stop}, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 10
        while not out.exists() and time.time() < deadline:
            time.sleep(0.05)
        with Image.open(out) as first:
            assert first.size == (480, 320)
        time.sleep(0.05)  # make sure the next write gets a new mtime
        set_active_theme("spur-ii")
        while time.time() < deadline:
            with Image.open(out) as frame:
                if frame.size == (1920, 480):
                    break
            time.sleep(0.05)
        with Image.open(out) as frame:
            assert frame.size == (1920, 480)
    finally:
        stop.set()
        thread.join(5)


def test_auto_driver_falls_back_to_png(tmp_path):
    from libre_panel.devices.base import create_display

    out = tmp_path / "auto.png"
    display = create_display(DeviceConfig(driver="auto", output=str(out)))
    display.open()
    display.show(Image.new("RGB", (32, 16)))
    display.close()
    assert out.exists()
