from datetime import timedelta

import srt

from gemini_srt_translator.main import GeminiSRTTranslator
from srt_merge import Cue
import subs_tool


def _cues(n, long_pause_after=(), sentence_end=lambda i: True):
    cues, t = [], 1000
    for i in range(n):
        text = f"Line {i + 1}" + ("." if sentence_end(i) else ",")
        cues.append(Cue(start=t, end=t + 1500, lines=[text]))
        t += 1500 + (5000 if i + 1 in long_pause_after else 300)
    return cues


def test_skill_cuts_at_scene_break():
    ranges = subs_tool.scene_cuts(_cues(250, long_pause_after={80, 180}), 100)
    assert ranges[0] == (0, 80)
    assert ranges[1] == (80, 180)
    assert ranges[-1][1] == 250


def test_skill_avoids_cutting_mid_sentence():
    # only line 95 ends a sentence inside the 70..130 window
    ranges = subs_tool.scene_cuts(_cues(200, sentence_end=lambda i: i + 1 == 95), 100)
    assert ranges[0] == (0, 95)


def test_engine_scene_batch_end_never_exceeds_batch_size():
    subs = [srt.Subtitle(i + 1, timedelta(milliseconds=c.start), timedelta(milliseconds=c.end), c.text)
            for i, c in enumerate(_cues(300, long_pause_after={85, 400}))]
    g = GeminiSRTTranslator.__new__(GeminiSRTTranslator)
    g.batch_size, g.scene_split = 100, True
    assert g._scene_batch_end(subs, 0) == 85
    end = g._scene_batch_end(subs, 85)
    assert 85 + 70 <= end <= 85 + 100
    assert g._scene_batch_end(subs, 250) == 300   # tail fits in one batch
    g.scene_split = False
    assert g._scene_batch_end(subs, 0) == 100
