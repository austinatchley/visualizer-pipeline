from vp.render import _progress_percent


def test_progress_parses_microseconds():
    assert _progress_percent("out_time_us=500000\n", 1.0) == 50.0


def test_progress_ignores_na():
    assert _progress_percent("out_time_us=N/A\n", 4.0) is None


def test_progress_ignores_empty_and_garbage():
    assert _progress_percent("out_time_us=\n", 4.0) is None
    assert _progress_percent("out_time_us=nope\n", 4.0) is None


def test_progress_ignores_other_lines():
    assert _progress_percent("frame=10\n", 4.0) is None


def test_progress_clamps_to_range():
    assert _progress_percent("out_time_us=9999999\n", 1.0) == 100.0
    assert _progress_percent("out_time_us=-5\n", 1.0) == 0.0