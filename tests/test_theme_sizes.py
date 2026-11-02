"""Design → the theme's menu → Every panel size: the theme as each panel shows it, made for the
size, laid out from the version for the nearest shape, or scaled into the panel."""

import threading

from PIL import Image
from test_editor_server import request

from libre_panel.editor.server import make_server, panel_sizes
from libre_panel.theme.adapt import laid_out
from libre_panel.theme.model import find_theme, load_theme, save_theme, theme_for


def get(port, path):
    status, body = request(port, "GET", path)
    return status, body


def test_every_panel_size_says_how_the_theme_comes_on_it():
    sizes = panel_sizes()
    assert any(s["size"] == [960, 320] and s["shape"] == "rect" for s in sizes)  # the 4.6"
    assert any(s["size"] == [480, 1920] for s in sizes)  # a bar on end too
    srv = make_server(port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        status, body = get(port, "/api/themes/lumen/sizes")
        assert status == 200
        by_size = {tuple(s["size"]): s for s in body["sizes"]}
        assert by_size[(1920, 480)]["how"] == "made"
        assert (
            by_size[(1920, 720)]["how"] == "made"
            and by_size[(1920, 720)]["version"] == "lumen-1920x720"
        )
        four = by_size[(960, 320)]
        assert four["how"] == "laid_out" and four["from"] == "lumen-1920x720"
        assert by_size[(1920, 180)]["how"] == "made"
        status, png = get(port, "/api/themes/lumen/preview?w=320&size=960x320")
        assert status == 200
        import io

        with Image.open(io.BytesIO(png)) as picture:
            assert picture.size == (320, 107)  # (the 4.6"'s shape, laid out on it)
        assert get(port, "/api/themes/no-such-theme/sizes")[0] == 404
    finally:
        srv.shutdown()
        srv.server_close()
        srv.editor_state.close()


def test_a_version_made_for_a_size_is_yours_and_that_size_shows_it(isolated_home):
    """*Make a version for this size*: the theme as the size shows it, kept as a version of its
    own (``<theme>-<w>x<h>``) that the person changes by hand and that size shows from then on."""
    srv = make_server(port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        status, body = request(port, "POST", "/api/themes/lumen/version",
                               {"width": 960, "height": 320})  # fmt: skip
        assert status == 200 and body["id"] == "lumen-960x320"
        made = load_theme(isolated_home / "themes" / "lumen-960x320")
        assert (made.width, made.height) == (960, 320) and made.name == "Lumen 960×320"
        from_12_3 = load_theme(find_theme("lumen-1920x720"))  # laid out from the 12.3"'s
        assert {w["id"] for w in made.widgets} == {w["id"] for w in from_12_3.widgets}
        assert theme_for("lumen", "landscape", (960, 320)) == "lumen-960x320"
        by_size = {tuple(s["size"]): s for s in get(port, "/api/themes/lumen/sizes")[1]["sizes"]}
        assert by_size[(960, 320)]["how"] == "made"
        assert by_size[(960, 320)]["version"] == "lumen-960x320"

        again = request(port, "POST", "/api/themes/lumen/version", {"width": 960, "height": 320})
        assert again[0] == 409  # once
        built_in = request(port, "POST", "/api/themes/lumen/version",
                           {"width": 1920, "height": 720})  # fmt: skip
        assert built_in[0] == 409  # a size it has a version for
        none = request(port, "POST", "/api/themes/no-such/version", {"width": 960, "height": 320})
        assert none[0] == 404
        assert request(port, "POST", "/api/themes/lumen/version", {"width": "x"})[0] == 400
        assert request(port, "POST", "/api/themes/lumen/version",
                       {"width": 8, "height": 8})[0] == 400  # fmt: skip

        # one's own, made for one size: its version begins scaled into the size, and from then
        # on it has versions for two sizes, and is laid out as Libre Panel's are
        save_theme("solo", load_theme(find_theme("libre-default")).to_dict(), "libre-default")
        status, body = request(port, "POST", "/api/themes/solo/version",
                               {"width": 1920, "height": 480})  # fmt: skip
        assert status == 200 and body["id"] == "solo-1920x480"
        bar = load_theme(find_theme("solo-1920x480"))
        assert (bar.width, bar.height) == (1920, 480)
        solo = load_theme(find_theme("solo"))
        assert laid_out(solo, (1920, 440)).root.name == "solo-1920x480"
    finally:
        srv.shutdown()
        srv.server_close()
        srv.editor_state.close()
