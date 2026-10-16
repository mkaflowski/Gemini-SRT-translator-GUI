from pathlib import Path

import pytest

from gst_gui.utils.flags import flag_image
from gst_gui.utils.languages import (
    country_code,
    detect_language_from_filename,
    language_label,
    with_language_code,
)


@pytest.mark.parametrize("name, code", [
    ("Movie.2020.fr.srt", "fr"),
    ("Movie.2.fr.srt", "fr"),
    ("Movie_extracted_eng.srt", "en"),
    ("Movie.en.forced.srt", "en"),
    ("It.Follows.2014.srt", ""),   # 'It' in the title is not Italian
    ("Movie.srt", ""),
])
def test_detect_language_from_filename(name, code):
    assert detect_language_from_filename(name) == code


def test_language_label_and_flag():
    assert language_label("fre") == "FR"
    assert language_label("fre", with_emoji=True) == "FR \U0001F1EB\U0001F1F7"
    assert country_code("en") == "GB"
    assert flag_image(country_code("pl")).size == (21, 14)
    assert flag_image("XX") is None


@pytest.mark.parametrize("src, number, expected", [
    ("Film.pl.srt", None, "Film.ar.srt"),
    ("Film.2020.pl.srt", None, "Film.2020.ar.srt"),
    ("Film.2.pl.srt", None, "Film.2.ar.srt"),
    ("Film.srt", None, "Film.ar.srt"),
    ("Film.2020.pl.srt", 2, "Film.2020.2.ar.srt"),   # a year is not a counter
    ("Film.2.pl.srt", 3, "Film.3.ar.srt"),
])
def test_with_language_code(src, number, expected):
    assert with_language_code(Path(src), "ar", number=number).name == expected
