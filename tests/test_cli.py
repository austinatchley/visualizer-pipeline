from pathlib import Path

import pytest

from vp.cli import _hook_flags, main

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
SHORT = FIXTURES / "d_short"
LONG = FIXTURES / "a_landscape"


def _require(path: Path) -> None:
    if not path.is_dir():
        pytest.skip("fixtures missing; run scripts/make_fixtures.py")


def test_hook_flags_parse():
    assert _hook_flags(None) == {}
    assert _hook_flags("auto") == {"mode": "auto"}
    assert _hook_flags("full") == {"mode": "full"}
    assert _hook_flags("10-50") == {"start": 10.0, "end": 50.0, "mode": "manual"}


def test_hook_flags_reject_invalid():
    with pytest.raises(ValueError, match="invalid --hook"):
        _hook_flags("10")
    with pytest.raises(ValueError, match="START < END"):
        _hook_flags("50-10")


def test_inspect_prints_plan(capsys):
    _require(LONG)
    assert main(["inspect", str(LONG)]) == 0
    out = capsys.readouterr().out
    assert "16x9 (full)" in out
    assert "9x16 (hook)" in out
    assert "1x1 (hook)" in out


def test_render_is_idempotent(tmp_path, capsys):
    _require(SHORT)
    args = ["render", str(SHORT), "--aspect", "vertical", "--out", str(tmp_path)]

    assert main(args) == 0
    first = capsys.readouterr().out
    assert "skip" not in first

    assert main(args) == 0
    second = capsys.readouterr().out
    assert "skip" in second


def test_render_rerenders_when_style_changes(tmp_path, capsys):
    """Issue #40: a different preset/flag must not be reported as up to date."""
    _require(SHORT)
    args = ["render", str(SHORT), "--aspect", "vertical", "--out", str(tmp_path)]
    assert main(args) == 0
    capsys.readouterr()

    assert main([*args, "--preset", "punchy"]) == 0
    out = capsys.readouterr().out
    assert "skip" not in out and "up to date" not in out

    assert main([*args, "--preset", "punchy"]) == 0
    assert "skip" in capsys.readouterr().out

    sidecars = list(tmp_path.rglob("*.mp4.vp.json"))
    assert len(sidecars) == 1


def test_render_variant_jobs_renders_all(tmp_path):
    _require(SHORT)
    code = main(
        [
            "render", str(SHORT), "--aspect", "both", "--out", str(tmp_path),
            "--variant-jobs", "2",
        ]
    )
    assert code == 0
    outputs = sorted((tmp_path / "interlude").glob("*.mp4"))
    assert len(outputs) == 2


def _link_track(root: Path, name: str, source: Path, with_images: bool) -> Path:
    folder = root / name
    folder.mkdir()
    (folder / "audio.wav").symlink_to(source / "audio.wav")
    if with_images:
        (folder / "images").symlink_to(source / "images")
    (folder / "title.txt").write_text(name)
    return folder


def test_batch_isolates_failures(tmp_path, capsys):
    _require(SHORT)
    root = tmp_path / "tracks"
    root.mkdir()
    _link_track(root, "good", SHORT, with_images=True)
    _link_track(root, "broken", SHORT, with_images=False)

    code = main(
        ["batch", str(root), "--aspect", "vertical", "--out", str(tmp_path / "out")]
    )

    captured = capsys.readouterr()
    assert code == 1
    assert "FAILED broken" in captured.err
    assert "1/2" in captured.out