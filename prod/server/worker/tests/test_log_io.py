from ms_mosaic.log_io import stamp_print_line


def test_stamp_print_line_adds_time():
    out = stamp_print_line("正射 Color 56/783 块\n", now="2026-09-20 12:45:03")
    assert out == "2026-09-20 12:45:03 正射 Color 56/783 块\n"


def test_stamp_print_line_keeps_existing_timestamp():
    line = "2026-09-20 04:11:31,980 INFO job start\n"
    assert stamp_print_line(line, now="2026-09-20 12:45:03") == line


def test_stamp_print_line_blank():
    assert stamp_print_line("\n") == "\n"
    assert stamp_print_line("") == ""
