"""The moon as it looks: its face, its light at each phase, the earthshine and the halo."""

import numpy as np
import pytest

from libre_panel.render import moon

LIGHT, NIGHT = (242, 239, 230, 255), (43, 50, 66, 255)


def halves(image):
    a = np.asarray(image.convert("RGBA"), np.float32)
    lum = a[..., :3].mean(-1) * a[..., 3] / 255
    n = a.shape[1]
    return lum[:, : n // 2].mean(), lum[:, n // 2 :].mean()


def test_the_face_is_the_same_moon_every_time():
    albedo, gx, gy = moon.face()
    assert albedo.shape == gx.shape == gy.shape == (moon.FACE, moon.FACE)
    # the seas are dark where they lie: Imbrium in the upper left, the highlands bright below
    n = moon.FACE
    imbrium = albedo[int(0.28 * n), int(0.35 * n)]
    highlands = albedo[int(0.80 * n), int(0.62 * n)]
    assert imbrium < 0.8 < highlands
    moon.face.cache_clear()
    assert np.array_equal(moon.face()[0], albedo)  # made again, the same


@pytest.mark.parametrize(("phase", "lit"), [(0.25, "right"), (0.75, "left")])
def test_a_half_moon_is_lit_on_its_side_and_mirrored_south_of_the_equator(phase, lit):
    left, right = halves(moon.disc(120, phase, LIGHT, NIGHT))
    assert (right > 2 * left) if lit == "right" else (left > 2 * right)
    m_left, m_right = halves(moon.disc(120, phase, LIGHT, NIGHT, mirror=True))
    assert (m_left > 2 * m_right) if lit == "right" else (m_right > 2 * m_left)


def test_the_night_side_keeps_the_earthshine():
    new = np.asarray(moon.disc(120, 0.0, LIGHT, NIGHT).convert("RGBA"), np.float32)
    middle = new[50:70, 50:70]
    assert middle[..., 3].min() == 255  # the dark disc is there, in its colour
    assert middle[..., :3].mean() > np.mean(NIGHT[:3]) * 0.6  # with a little light on it
    full = np.asarray(moon.disc(120, 0.5, LIGHT, NIGHT).convert("RGB"), np.float32)
    assert full[50:70, 50:70].mean() > 2 * middle[..., :3].mean()
    faint = np.asarray(moon.disc(120, 0.0, LIGHT, None).convert("RGBA"), np.float32)
    assert 0 < faint[50:70, 50:70, 3].mean() < 64  # without a night colour: a ghost of the disc


def test_the_halo_follows_the_light_and_breathes():
    def glow(image):
        a = np.asarray(image, np.float32)
        return a[0:4, :, 3].sum()  # the strip above the disc (from 4.8 px down): the halo

    full = moon.picture(120, 0.5, LIGHT, NIGHT)
    new = moon.picture(120, 0.0, LIGHT, NIGHT)
    assert glow(full) > 0 and glow(new) == 0
    breathing = {glow(moon.picture(120, 0.5, LIGHT, NIGHT, t=t)) for t in (0.0, 1.7, 3.5)}
    assert len(breathing) > 1
