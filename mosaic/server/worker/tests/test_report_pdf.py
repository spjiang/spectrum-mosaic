"""质量报告 PDF 的测试。用 pypdf 抽文本，避免 CID 字体把数字编码进字形。"""

from pathlib import Path

from ms_mosaic.report_pdf import REPORT_SECTIONS, write_quality_pdf


def _payload():
    return {
        "project_name": "MAX_20251017",
        "created_date_cn": "2026年9月17日",
        "n_images": 38,
        "n_registered": 38,
        "area_km2": 0.052,
        "mean_agl_m": 111.8,
        "crs": "EPSG:32647",
        "crs_label": "[EPSG::32647] UTM 47N (WGS84), egm_none",
        "timings": {"at": 21.0, "dense": 41.0, "dsm": 0.4, "ortho": 12.0},
        "at": {
            "rms_reprojection_px": 0.335,
            "mean_reprojection_px": 0.22,
            "gps_rmse_m": 1.33,
            "gps_rmse_x_m": 0.4,
            "gps_rmse_y_m": 0.5,
            "gps_rmse_z_m": 1.1,
            "n_points": 12940,
            "n_observations": 29951,
            "mean_obs_per_image": 788.0,
            "n_images": 38,
            "n_gps": 38,
        },
        "cameras": {
            "Color": {
                "group": 0,
                "model": "MAX-S800",
                "width": 2048,
                "height": 1536,
                "f": 2381.3,
                "cx": 1024.0,
                "cy": 768.0,
                "k1": -0.12,
                "k2": 0.05,
                "k3": 0.01,
                "p1": 0.0,
                "p2": 0.0,
                "b1": 0.0,
                "b2": 0.0,
                "n_images": 38,
                "n_registered": 38,
                "rms_px": 0.33,
            }
        },
        "dsm": {"gsd": 0.107747293, "z_min": 1678.0, "z_median": 1757.0, "z_max": 1801.0},
        "ortho": {"gsd": 0.053873647, "mode": "基于DSM逐像素拼接", "blend": "中", "color_correction": "禁用"},
        "overlap": {
            "forward": 0.78,
            "side": 0.64,
            "poor_forward": [
                {"a": 10, "b": 11, "name_a": "MAX_0011_Color_D.jpg", "name_b": "MAX_0012_Color_D.jpg", "overlap": 0.53}
            ],
            "poor_side": [
                {"a": 2, "b": 20, "name_a": "MAX_0003_Color_D.jpg", "name_b": "MAX_0021_Color_D.jpg", "overlap": 0.12}
            ],
        },
        "primary_band": "组0-",
        "features": {
            "n_keypoints": 12000,
            "max_keypoints": 8192,
            "mean_keypoints": 7309,
            "most": "8192 (序号 = 0, 组号 = 0, MAX_0001_Color_D.jpg)",
            "least": "400 (序号 = 7, 组号 = 0, MAX_0008_Color_D.jpg)",
            "poor": ["400 (序号 = 7, 组号 = 0, MAX_0008_Color_D.jpg)"],
            "n_poor": 1,
        },
        "matching": {
            "n_tracks": 12940,
            "pair_mode": "一般",
            "max_two_view": 1024,
            "mean_tracks": 2007,
            "most": "3818 (序号 = 5, 组号 = 0, MAX_0006_Color_D.jpg)",
            "least": "12 (序号 = 7, 组号 = 0, MAX_0008_Color_D.jpg)",
            "sequences": [{"id": 0, "n": 38, "indices": list(range(38))}],
        },
        "strips": [["MAX_0001_Color_D.jpg", "MAX_0002_Color_D.jpg", "MAX_0003_Color_D.jpg"]],
        "gps_rows": [
            {
                "index": 0,
                "name": "MAX_0001_Color_D.jpg",
                "gps0": [192972.5808, 2493945.235, 140.1638],
                "gps1": [192972.6679, 2493946.289, 140.4566],
                "error": 1.096,
                "dx": 0.087,
                "dy": 1.054,
                "dz": 0.293,
            }
        ],
        "gps_histogram": [
            {"label": "[0 ~ 2)", "error": 100.0, "dx": 100.0, "dy": 100.0, "dz": 100.0},
        ],
        "gps_extrema": {
            "min_x": {
                "index": 0,
                "name": "MAX_0001_Color_D.jpg",
                "gps0": [192972.5808, 2493945.235, 140.1638],
                "gps1": [192972.6679, 2493946.289, 140.4566],
                "error": 1.096,
                "dx": 0.087,
                "dy": 1.054,
                "dz": 0.293,
            }
        },
        "unregistered": [36, 37],
    }


def test_report_sections_cover_limapper_chapters():
    titles = [s for s in REPORT_SECTIONS]
    for must in ("概述", "2D关键点检测", "2D关键点匹配", "相机标定信息",
                 "空中三角测量", "密集点云", "数字表面模型", "正射影像", "GPS配准",
                 "相机位置视图", "空三视图", "重叠度视图", "航线信息"):
        assert any(must in t for t in titles), must


def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def test_write_quality_pdf_is_valid_and_contains_metrics(tmp_path: Path):
    path = write_quality_pdf(_payload(), tmp_path / "quality.pdf")
    assert path.exists() and path.stat().st_size > 2000
    assert path.read_bytes().startswith(b"%PDF")
    text = _pdf_text(path)
    assert "0.335" in text
    assert "32647" in text
    assert "质量报告" in text
    assert "ms_mosaic" not in text
    assert "创建于" in text
    assert "LiMapper" not in text
    assert "基于DSM逐像素拼接" in text or "DSM" in text
    assert "椭球高" in text and "不是海拔" in text
    assert "基于GPS配准" in text
    assert "GPS配准列表" in text
    assert "相机位置视图" in text
    assert "初始POS" in text and "优化POS" in text
    assert "重叠度视图" in text
    assert "航线信息" in text
    assert "较差航向重叠率信息" in text
    assert "MAX-S800" in text
    assert "未注册影像" in text


def test_write_quality_pdf_lists_all_camera_groups(tmp_path: Path):
    payload = _payload()
    payload["cameras"]["550nm"] = dict(payload["cameras"]["Color"])
    payload["cameras"]["550nm"]["f"] = 2500.0
    payload["cameras"]["550nm"]["group"] = 1
    text = _pdf_text(write_quality_pdf(payload, tmp_path / "q.pdf"))
    assert "2381" in text and "2500" in text
    assert "组 0" in text and "组 1" in text


def test_write_quality_pdf_embeds_limapper_figures(tmp_path: Path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    png = tmp_path / "fig.png"
    fig, ax = plt.subplots(figsize=(2, 2))
    ax.plot([0, 1], [0, 1])
    fig.savefig(png)
    plt.close(fig)
    payload = _payload()
    payload["figures"] = {k: str(png) for k in ("ortho", "dsm", "camera_pos", "at_view", "overlap")}
    path = write_quality_pdf(payload, tmp_path / "q.pdf")
    assert path.stat().st_size > 20_000
    text = _pdf_text(path)
    assert "Orthomosaic_pix_surf_group0" in text
    assert "空三视图" in text

