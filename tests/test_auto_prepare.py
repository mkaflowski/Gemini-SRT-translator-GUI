"""Auto: the sources proposed for a folder follow the skill's defaults."""
from gst_gui.utils import auto_prepare as ap


def track(i, lang, title="", default=False, forced=False, text=True, sdh=False):
    return {"type_index": i, "codec": "subrip" if text else "hdmv_pgs_subtitle", "language": lang,
            "title": title, "default": default, "forced": forced, "sdh": sdh, "text_based": text}


def srt(*lines):
    return "".join(f"{n}\n00:00:{n * 3:02d},000 --> 00:00:{n * 3 + 2:02d},000\n{text}\n\n"
                   for n, text in enumerate(lines, 1))


def test_tracks_only_extracts_english_french_and_sdh():
    tracks = [track(0, "eng", default=True), track(1, "eng", "SDH"), track(2, "ita"),
              track(3, "fre"), track(4, "spa", forced=True), track(5, "ger", text=False)]
    plan = ap.plan_video("Film.mkv", [], tracks, 1)
    assert plan["base_track"]["type_index"] == 0
    assert plan["sdh_track"]["type_index"] == 1
    assert plan["reference_track"]["type_index"] == 3 and plan["reference_lang"] == "fr"
    assert plan["source"] is None and ap.is_ready(plan)


def test_better_track_beats_a_file_and_ai_or_target_files_are_never_used():
    subs = ["Film.en.srt", "Film.ita.srt", "Film.pl.srt", "Film.ar.srt"]
    plan = ap.plan_video("Film.mkv", subs, [track(0, "fre")], 1)
    assert plan["base"] == "Film.en.srt" and plan["reference"] is None
    assert plan["reference_track"]["language"] == "fre"
    plan = ap.plan_video("Film.mkv", subs, [], 1)
    assert plan["reference"] == "Film.ita.srt" and plan["reference_lang"] == "it"
    plan = ap.plan_video("Film.mkv", ["Film.en.srt", "Film.pl.srt", "Film.ar.srt"], [], 1)
    assert plan["source"] == "Film.en.srt"  # nothing to merge with


def test_existing_merged_file_is_used_and_episodes_keep_their_own_files():
    subs = ["S01E01.en.srt", "S01E01.en_fr.srt", "S01E02.en.srt", "S01E02.es.srt"]
    first = ap.plan_video("S01E01.mkv", subs, [], 2)
    second = ap.plan_video("S01E02.mkv", subs, [], 2)
    assert first["source"] == "S01E01.en_fr.srt"
    assert second["base"] == "S01E02.en.srt" and second["reference"] == "S01E02.es.srt"


def test_nothing_usable():
    plan = ap.plan_video("Film.mkv", [], [track(0, "eng", text=False)], 1)
    assert not ap.is_ready(plan)
    assert any("no usable subtitles" in line for line in ap.describe_plan(plan))


def test_execute_merges_base_reference_and_sdh(tmp_path):
    (tmp_path / "Film.mkv").write_bytes(b"")
    (tmp_path / "Film.en.srt").write_text(srt("Who's there?", "Nobody."), encoding="utf-8")
    (tmp_path / "Film.fr.srt").write_text(srt("Qui est là ?", "{\\an8}(crachat)"), encoding="utf-8")
    (tmp_path / "Film.en.sdh.srt").write_text(srt("JOHN: Who's there?", "[SPITS]"), encoding="utf-8")
    plans = ap.plan_folder(tmp_path, probe=lambda path: [])
    assert len(plans) == 1 and plans[0]["sdh"] == "Film.en.sdh.srt"
    logs = []
    out = ap.execute_plan(plans[0], log=logs.append)
    assert out.endswith("Film.en_fr.srt")
    text = (tmp_path / "Film.en_fr.srt").read_text(encoding="utf-8")
    assert "{fr: Qui est là ?}" in text and "{sdh: JOHN}" in text
    assert "{fr: {\\an8}}" not in text  # an SDH-only reference cue leaves no empty line
    # the second run finds the merged file and has nothing to prepare
    again = ap.plan_folder(tmp_path, probe=lambda path: [])[0]
    assert again["source"] == "Film.en_fr.srt"
