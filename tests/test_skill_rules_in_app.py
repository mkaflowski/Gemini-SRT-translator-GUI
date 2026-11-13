"""The rules worked out in the translate-subtitles skill, as the app applies them."""
from datetime import timedelta

import pytest

pytest.importorskip("google.genai")
pytest.importorskip("srt")

from srt import Subtitle  # noqa: E402

from gemini_srt_translator.helpers import get_translate_instruction  # noqa: E402
from gemini_srt_translator.main import BUDGET_MAX, BUDGET_MIN, char_budget  # noqa: E402
from gst_gui.utils import srt_review  # noqa: E402
from gst_gui.utils.srt_merge import Cue  # noqa: E402


def sub(i, start_ms, end_ms, text="x"):
    return Subtitle(index=i, start=timedelta(milliseconds=start_ms),
                    end=timedelta(milliseconds=end_ms), content=text)


def test_char_budget_follows_display_time():
    subs = [sub(1, 0, 2000), sub(2, 2100, 2200), sub(3, 2400, 30000)]
    # 2 s on screen, the next cue starts right after: no trailing time to borrow
    assert char_budget(subs, 0) == int((2100 - 83) / 1000 * 17)
    assert char_budget(subs, 1) == BUDGET_MIN  # a flash still gets a minimum
    assert char_budget(subs, 2) == BUDGET_MAX  # never more than two full lines


def test_translate_instruction_has_style_rules():
    polish = get_translate_instruction("Polish", thinking=False, thinking_compatible=False)
    assert "max_chars" in polish and "Meaning has priority over the budget" in polish
    assert "Zdjęcie czego?" in polish  # Polish examples only for Polish
    german = get_translate_instruction("German", thinking=False, thinking_compatible=False)
    assert "max_chars" in german and "Zdjęcie czego?" not in german


def test_review_instruction_categories_and_priority():
    text = srt_review.build_instruction("Polish")
    for category in srt_review.CATEGORIES:
        assert f"`{category}`" in text
    assert "Meaning has priority" in text and "repeated_at" in text and "Polish naturalness" in text
    assert "Polish naturalness" not in srt_review.build_instruction("German")


def cue(start, end, *lines):
    return Cue(start=start, end=end, lines=list(lines))


def test_review_pairs_carry_cps_and_repeats():
    quote = "The river remembers every stone it carried."
    original, translated = [], []
    for n in range(20):
        start = n * 3000
        text = quote if n in (0, 19) else f"Line number {n} of the film."
        original.append(cue(start, start + 2000, text))
        translated.append(cue(start, start + 2000, f"Napis {n}"))
    translated[5] = cue(15000, 15500, "Bardzo długi napis, którego nikt nie zdąży przeczytać")
    pairs = srt_review.build_pairs(translated, original)
    assert "cps" in pairs[5] and "cps" not in pairs[4]
    assert pairs[0]["repeated_at"] == [{"index": "20", "translation": "Napis 19"}]
    assert "repeated_at" not in pairs[3]


def test_review_apply_deletes_hesitation_cue():
    translated = [cue(0, 1000, "Yyy..."), cue(2000, 4000, "Dzień dobry.")]
    pairs = srt_review.build_pairs(translated, [cue(0, 1000, "Uh..."), cue(2000, 4000, "Hello.")])
    issues = [{"index": "1", "category": "spelling", "severity": "low", "suggestion": "",
               "delete": True},
              {"index": "2", "category": "unnatural", "severity": "low", "suggestion": "Cześć."}]
    assert srt_review.apply_suggestions(pairs, issues, log=lambda m: None) == 2
    assert translated[0].lines == [] and translated[1].lines == ["Cześć."]
