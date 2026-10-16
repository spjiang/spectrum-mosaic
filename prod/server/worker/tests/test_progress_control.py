from ms_mosaic.progress import STAGES, global_percent_for, stage_index


def test_stage_order():
    assert stage_index("S0_io") == 0
    assert stage_index("S6_report") == len(STAGES) - 1


def test_global_percent_monotonic():
    a = global_percent_for("S2_at", 0)
    b = global_percent_for("S2_at", 100)
    c = global_percent_for("S3_dense", 0)
    assert a < b <= c
