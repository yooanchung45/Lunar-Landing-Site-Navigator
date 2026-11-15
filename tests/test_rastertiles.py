import numpy as np

from backend.rastertiles import hillshade, ramp_rgba, tile_bounds_m, SUN_RAMP


def test_tile_bounds_cover_the_map_square():
    # z=1: four tiles; row 0 is the top (lon 0 side), col 0 the left.
    left, bottom, right, top = tile_bounds_m(1, 0, 0)
    assert (left, top) == (-320_000, 320_000) and (right, bottom) == (0, 0)
    left, bottom, right, top = tile_bounds_m(1, 1, 1)
    assert (left, top, right, bottom) == (0, 0, 320_000, -320_000)


def _slope(direction, steepness=0.5, n=12):
    """Elevation grid rising toward ``direction`` ('up', 'right', 'down', 'left')."""
    y, x = np.mgrid[0:n, 0:n].astype(float)       # y grows downward (rows)
    return {"right": x, "left": -x, "up": -y, "down": y}[direction] * steepness * 80.0  # 80 m cells


def test_flat_ground_is_mid_grey():
    shade = hillshade(np.zeros((10, 10)), 80.0, 315.0, 12.0)
    assert np.allclose(shade, 0.5)


def test_slope_facing_the_light_is_brighter_than_one_facing_away():
    # Light from the right (az=90): ground rising toward the left faces it ... no -- a slope
    # facing the light is one whose uphill side is AWAY from the light, i.e. rising toward the left.
    facing = hillshade(_slope("left"), 80.0, 90.0, 20.0).mean()
    away = hillshade(_slope("right"), 80.0, 90.0, 20.0).mean()
    assert facing > 0.5 > away


def test_light_azimuth_is_clockwise_from_top_of_map():
    # Light from the top (az=0): ground rising toward the bottom faces the light.
    facing = hillshade(_slope("down"), 80.0, 0.0, 20.0).mean()
    away = hillshade(_slope("up"), 80.0, 0.0, 20.0).mean()
    assert facing > 0.5 > away


def test_ramp_masks_invalid_pixels_and_clips():
    values = np.array([[0.0, 1.0], [2.0, 0.5]])
    valid = np.array([[True, True], [False, True]])
    out = ramp_rgba(values, valid, SUN_RAMP, alpha=200)
    assert out[0, 0, 3] == 200 and out[1, 0, 3] == 0
    assert tuple(out[0, 1, :3]) == SUN_RAMP[-1][1]


def test_tiles_line_up_with_the_raster_grid(tmp_path):
    """A raster whose values are its own x / y coordinates must read back as the tile's coordinates."""
    import rasterio
    from rasterio.transform import from_origin
    from backend.rastertiles import read_window

    res, n = 1000.0, 800                      # 1 km pixels covering +-400 km
    xs = -400_000 + (np.arange(n) + 0.5) * res
    ys = 400_000 - (np.arange(n) + 0.5) * res
    for name, grid in (("x", np.tile(xs, (n, 1))), ("y", np.tile(ys[:, None], (1, n)))):
        path = tmp_path / f"{name}.tif"
        with rasterio.open(path, "w", driver="GTiff", width=n, height=n, count=1, dtype="float32",
                           transform=from_origin(-400_000, 400_000, res, res)) as dst:
            dst.write(grid.astype("float32"), 1)
        left, bottom, right, top = tile_bounds_m(1, 1, 0)          # top-right quarter of the map
        with rasterio.open(path) as ds:
            tile = read_window(ds, (left, bottom, right, top), (256, 256))
        px = (right - left) / 256
        if name == "x":      # increases left -> right, constant down a column
            assert np.allclose(tile[100], left + (np.arange(256) + 0.5) * px, atol=res / 2)
        else:                # decreases top -> bottom
            assert np.allclose(tile[:, 100], top - (np.arange(256) + 0.5) * px, atol=res / 2)
