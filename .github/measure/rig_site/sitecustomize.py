"""MEASUREMENT (feature branch only): in the app's process of the Windows rig
(.github/measure/rig_windows.py), Libre Panel's engine talks to the simulated panels the rig
serves (LP_RIG_LINKS: "0123=tcp:HOST:PORT,0092=..."), not to USB; and once, LP_RIG_CENSUS_AT
seconds after the start, the process's memory is broken down (winmap.census) as it is, after
the heaps gave back, and 30 s and 150 s after its working set was emptied; into LP_RIG_CENSUS."""

import os
import sys

_links = os.environ.get("LP_RIG_LINKS")
if _links:
    import subprocess

    def _patch():
        from libre_panel.devices import turzx_usb
        from libre_panel.devices.engine import EngineLink, EngineTransport

        links = dict(part.split("=", 1) for part in _links.split(","))

        def open_by_engine(path, pid=None, beside=(), skip=0, popen=subprocess.Popen):
            key = f"{pid or 0x92:04x}"
            link = EngineLink([path, "panel", "--pid", key, "--link", links[key]])
            return EngineTransport(link, int(key, 16))

        EngineTransport.open = staticmethod(open_by_engine)
        turzx_usb.UsbTransport.present = staticmethod(lambda pid=None: True)

    _patch()

_census = os.environ.get("LP_RIG_CENSUS")
if _links and _census and "tray" in sys.argv:

    def _run():
        import json
        import time
        import traceback

        time.sleep(float(os.environ.get("LP_RIG_CENSUS_AT", "300")))
        out = {}
        try:
            sys.path.insert(0, os.environ["LP_RIG_MEASURE"])
            import winmap

            from libre_panel import light, load_report

            out["as_it_is"] = winmap.census()
            try:
                held = load_report.inside()
                out["inside"] = {k: v for k, v in held.items() if isinstance(v, (int, float, str))}
            except Exception as exc:  # noqa: BLE001
                out["inside_error"] = repr(exc)
            started = time.perf_counter()
            light.windows_heaps()
            out["heaps_gave_back_ms"] = round((time.perf_counter() - started) * 1000, 1)
            out["after_heaps"] = winmap.census()
            winmap.trim()
            out["right_after_trim"] = winmap.counters()
            time.sleep(30)
            out["trim_30s"] = winmap.census()
            time.sleep(120)
            out["trim_150s"] = winmap.census()
        except Exception:  # noqa: BLE001
            out["error"] = traceback.format_exc()
        with open(_census, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)

    import threading

    threading.Thread(target=_run, name="rig-census", daemon=True).start()
