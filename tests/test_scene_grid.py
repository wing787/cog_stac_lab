"""scene_grid の窓計算テスト。"""

from __future__ import annotations

import pytest

from cogstac.scene_grid import Window, split_windows


def test_even_split_2x2() -> None:
    windows = split_windows(6000, 6000, 2, 2)
    assert windows == [
        Window(0, 0, 3000, 3000),
        Window(3000, 0, 3000, 3000),
        Window(0, 3000, 3000, 3000),
        Window(3000, 3000, 3000, 3000),
    ]


def test_windows_cover_image_without_overlap_or_gap() -> None:
    """割り切れない分割でも、面積の合計が元画像と一致する（重複も欠落も無い）。"""
    width, height = 1001, 777
    windows = split_windows(width, height, 3, 4)
    assert sum(w.width * w.height for w in windows) == width * height


def test_last_window_does_not_exceed_image() -> None:
    for w in split_windows(1001, 777, 3, 4):
        assert w.col + w.width <= 1001
        assert w.row + w.height <= 777


def test_as_srcwin_is_string_quadruple() -> None:
    assert Window(3000, 0, 3000, 3000).as_srcwin() == ("3000", "0", "3000", "3000")


@pytest.mark.parametrize(
    ("width", "height", "n_cols", "n_rows"),
    [(0, 10, 1, 1), (10, 10, 0, 1), (10, 10, 11, 1), (10, 10, 1, 11)],
)
def test_invalid_arguments_raise(width: int, height: int, n_cols: int, n_rows: int) -> None:
    with pytest.raises(ValueError):
        split_windows(width, height, n_cols, n_rows)
