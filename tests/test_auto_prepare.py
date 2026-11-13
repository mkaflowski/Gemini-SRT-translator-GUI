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


def test_choose_swaps_the_reference_and_the_defaults_stay_selectable():
    subs = ["Film.en.srt", "Film.es.srt", "Film.ru.srt"]
    plan = ap.plan_video("Film.mkv", subs, [track(0, "ita")], 1)
    assert plan["reference_track"]["language"] == "ita"  # Italian beats the Spanish file
    labels = [ap.option_label(e) for e in plan["options"]["reference"]]
    assert labels[:2] == ["Film.es.srt", "Film.ru.srt"] and "ita" in labels[2] and labels[2].startswith("track #1")
    spanish = plan["options"]["reference"][0]
    ap.choose(plan, "reference", spanish)
    assert plan["reference"] == "Film.es.srt" and plan["reference_track"] is None
    assert plan["reference_lang"] == "es" and ap.current_entry(plan, "reference") is spanish
    assert "en+es" in ap.outcome(plan)
    ap.choose(plan, "reference", None)
    assert not plan["reference"] and "base alone" in ap.outcome(plan)
    ap.choose(plan, "base", None)
    assert not ap.is_ready(plan) and "skipped" in ap.outcome(plan)


def test_choose_clears_a_ready_source_and_never_uses_the_base_as_sdh():
    plan = ap.plan_video("Film.mkv", ["Film.en.srt", "Film.fr.srt"], [], 1)
    assert plan["source"] is None
    only_base = ap.plan_video("Film.mkv", ["Film.en.srt"], [], 1)
    assert only_base["source"] == "Film.en.srt"
    ap.choose(only_base, "base", only_base["options"]["base"][0])
    assert only_base["source"] is None  # extract/merge again, do not shortcut to the old file
    plan = ap.plan_video("Film.mkv", ["Film.en.srt", "Film.en.sdh.srt", "Film.fr.srt"], [], 1)
    assert plan["base"] == "Film.en.srt" and plan["sdh"] == "Film.en.sdh.srt"
    ap.choose(plan, "base", plan["options"]["base"][1])  # the SDH file becomes the base
    assert plan["sdh"] is None


def test_execute_uses_the_chosen_files(tmp_path):
    (tmp_path / "Film.mkv").write_bytes(b"")
    (tmp_path / "Film.en.srt").write_text(srt("Who's there?"), encoding="utf-8")
    (tmp_path / "Film.fr.srt").write_text(srt("Qui est là ?"), encoding="utf-8")
    (tmp_path / "Film.es.srt").write_text(srt("¿Quién es?"), encoding="utf-8")
    plan = ap.plan_folder(tmp_path, probe=lambda path: [])[0]
    assert plan["reference"] == "Film.fr.srt"
    ap.choose(plan, "reference", plan["options"]["reference"][1])
    out = ap.execute_plan(plan, log=lambda *a: None)
    assert out.endswith("Film.en_es.srt")
    assert "{es: ¿Quién es?}" in (tmp_path / "Film.en_es.srt").read_text(encoding="utf-8")
