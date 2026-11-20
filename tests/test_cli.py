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

    assert main([*args, "--preset", "vgm"]) == 0
    out = capsys.readouterr().out
    assert "skip" not in out and "up to date" not in out

    assert main([*args, "--preset", "vgm"]) == 0
    assert "skip" in capsys.readouterr().out

    sidecars = list(tmp_path.rglob("*.mp4.vp.json"))
    assert len(sidecars) == 1


def test_render_variant_jobs_renders_all(tmp_path):
    _require(SHORT)
    code = main(
        [
            "render",
            str(SHORT),
            "--aspect",
            "both",
            "--out",
            str(tmp_path),
            "--variant-jobs",
            "2",
        ]
    )
    assert code == 0
    outputs = sorted((tmp_path / "d_short").glob("*.mp4"))
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

    code = main(["batch", str(root), "--aspect", "vertical", "--out", str(tmp_path / "out")])

    captured = capsys.readouterr()
    assert code == 1
    assert "FAILED broken" in captured.err
    assert "1/2" in captured.out


def test_duplicate_titles_render_to_distinct_dirs(tmp_path, capsys):
    """Issue #42: outputs are keyed by folder, so equal titles don't collide."""
    _require(SHORT)
    tracks = tmp_path / "tracks"
    tracks.mkdir()
    for name in ("one", "two"):
        folder = _link_track(tracks, name, SHORT, with_images=True)
        (folder / "track.json").write_text('{"title": "Same Title"}')
    out = tmp_path / "out"
    assert main(["batch", str(tracks), "--aspect", "vertical", "--out", str(out)]) == 0
    assert (out / "one" / "one_9x16.mp4").is_file()
    assert (out / "two" / "two_9x16.mp4").is_file()


# ---- style flags generated from STYLE_FIELDS (issue #48) ----

from vp.cli import _flags, build_parser  # noqa: E402
from vp.config import STYLE_FIELDS  # noqa: E402


def _parse(*argv):
    return build_parser().parse_args(["render", "tracks/x", *argv])


def test_every_style_field_has_a_cli_flag():
    for command in ("render", "inspect", "batch"):
        parser = build_parser()
        sub = next(a for a in parser._actions if a.dest == "command").choices[command]
        dests = {a.dest for a in sub._actions}
        assert set(STYLE_FIELDS) <= dests, (command, set(STYLE_FIELDS) - dests)


def test_font_is_the_choice_and_title_font_the_path():
    args = _parse("--font", "anton", "--title-font", "/fonts/x.ttf")
    flags = _flags(args)
    assert flags["font"] == "anton"
    assert flags["title_font"] == "/fonts/x.ttf"


def test_style_flags_parse_types():
    flags = _flags(
        _parse(
            "--fps",
            "60",
            "--zoom",
            "0.2",
            "--no-show-title",
            "--watermark",
            "--watermark-text",
            "VGM",
            "--text-case",
            "upper",
            "--fade-in",
            "0",
        )
    )
    assert flags["fps"] == 60
    assert flags["zoom"] == 0.2
    assert flags["show_title"] is False
    assert flags["watermark"] is True
    assert flags["watermark_text"] == "VGM"
    assert flags["text_case"] == "upper"
    assert flags["fade_in"] == 0.0


def test_unset_style_flags_are_none():
    flags = _flags(_parse())
    assert all(flags[key] is None for key in STYLE_FIELDS)


@pytest.mark.parametrize(
    "argv", [["--font", "comic-sans"], ["--grade", "sepia"], ["--fps", "25"], ["--preset", "loud"]]
)
def test_invalid_choices_are_rejected_by_argparse(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        _parse(*argv)
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_help_lists_choices():
    help_text = build_parser()._subparsers._group_actions[0].choices["render"].format_help()
    assert "--grade {" in help_text and "analog" in help_text
    assert "--title-font PATH" in help_text


def test_style_flag_reaches_the_track(tmp_path):
    _require(SHORT)
    from vp.cli import _load

    track = _load(SHORT, _parse("--font", "anton", "--grade", "night", "--fade-out", "0"))
    assert track.style["font"] == "anton"
    assert track.style["grade"] == "night"
    assert track.style["fade_out"] == 0.0
