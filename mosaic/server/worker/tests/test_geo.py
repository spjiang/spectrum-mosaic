from ms_mosaic.catalog import Pos, Shot
from ms_mosaic.geo import epsg_utm, filter_usable, frame_affine, gsd_m, lonlat_to_utm
from rasterio.transform import xy as transform_xy


def test_utm_zone_for_this_site():
    assert epsg_utm(100.71, 23.68) == "EPSG:32647"


def test_lonlat_to_utm_near_vendor_mosaic():
    e, n = lonlat_to_utm(100.710395, 23.684523, "EPSG:32647")
    # 厂商正射约 673906–674616 E, 2619907–2620549 N
    assert 673800 < e < 674700
    assert 2619800 < n < 2620700


def test_gsd_about_5cm_at_110m():
    gsd = gsd_m(agl_m=110.0, width=2048)
    assert 0.04 < gsd < 0.07


def test_filter_drops_ground_and_white_panel():
    ground = Shot(index=1, pos=Pos(100.7, 23.6, 1755, 0, -90, 0, agl_m=0.0), role="D")
    white = Shot(index=2, pos=Pos(100.7, 23.6, 1755, 0, -90, 0, agl_m=110.0), role="W")
    air = Shot(index=3, pos=Pos(100.7, 23.6, 1870, 80, -91, 0, agl_m=110.0), role="D")
    keep = filter_usable({1: ground, 2: white, 3: air})
    assert list(keep) == [3]


def test_frame_affine_places_center_at_gps():
    pos = Pos(lon=100.710395, lat=23.684523, alt_m=1869.7, yaw_deg=0.0, pitch_deg=-90.0, roll_deg=0.0, agl_m=110.0)
    affine, crs, gsd = frame_affine(pos, width=2048, height=1536)
    e, n = lonlat_to_utm(pos.lon, pos.lat, crs)
    cx, cy = transform_xy(affine, 767.5, 1023.5)
    assert abs(cx - e) < 1.0
    assert abs(cy - n) < 1.0
    assert crs == "EPSG:32647"
