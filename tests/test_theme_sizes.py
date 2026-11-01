"""Design → the theme's menu → Every panel size: the theme as each panel shows it, made for the
size, laid out from the version for the nearest shape, or scaled into the panel."""

import threading

from PIL import Image
from test_editor_server import request

from libre_panel.editor.server import make_server, panel_sizes


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
