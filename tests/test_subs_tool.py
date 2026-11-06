import json
import os
import subprocess
import sys

import pytest

import subs_tool

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_SCRIPTS = os.path.join(ROOT, "skills", "translate-subtitles", "scripts")
TOOL = os.path.join(SKILL_SCRIPTS, "subs_tool.py")

SRT = "".join(
    f"{i}\n00:00:{i * 3:02d},000 --> 00:00:{i * 3 + 2:02d},000\nLine number {i}.\n\n" for i in range(1, 16)
)


def run(*args):
    env = dict(os.environ, PYTHONUTF8="1")
    res = subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True,
                         encoding="utf-8", env=env)
    return res.returncode, json.loads(res.stdout)


@pytest.fixture
def work(tmp_path):
    src = tmp_path / "Film.en.srt"
    src.write_text(SRT, encoding="utf-8")
    return src, tmp_path / "work"


def _translate_batches(work_dir, edit=None):
    for name in sorted(os.listdir(work_dir)):
        if name.startswith("batch_"):
            items = json.loads((work_dir / name).read_text(encoding="utf-8"))["translate"]
            out = [{"index": i["index"], "text": "PL " + i["text"]} for i in items]
            if edit:
                edit(name, out)
            (work_dir / name.replace("batch_", "out_")).write_text(json.dumps(out), encoding="utf-8")


def test_split_status_apply_stamp(work):
    src, w = work
    rc, manifest = run("split", str(src), "--out-dir", str(w), "--batch-size", "5", "--context", "2")
    assert rc == 0 and manifest["cues"] == 15
    second = json.loads((w / "batch_002.json").read_text(encoding="utf-8"))
    assert len(second["context_before"]) == 2 and second["context_after"]

    rc, status = run("status", "--out-dir", str(w))
    assert status["done"] == 0 and status["next"] == "batch_001.json"

    _translate_batches(w)
    rc, status = run("status", "--out-dir", str(w))
    assert status["done"] == status["batches"]

    # split must not wipe started work without --force
    rc, res = run("split", str(src), "--out-dir", str(w))
    assert rc == 2 and res["error"] == "work in progress"

    out = src.with_name("Film.pl.srt")
    rc, res = run("apply", str(src), "--out-dir", str(w), "-o", str(out))
    assert rc == 0 and res["cues"] == 15
    run("stamp", str(out), "--model", "Test Model")
    run("stamp", str(out), "--model", "Test Model")
    text = out.read_text(encoding="utf-8")
    assert text.count("# Translated by Test Model #") == 1
    assert "PL Line number 15." in text


def test_check_finds_model_slips(work):
    src, w = work
    run("split", str(src), "--out-dir", str(w), "--batch-size", "5")

    def slips(name, out):
        if name == "batch_001.json":
            out[0]["text"] = "Tekst\n{fr: Bonjour}"
            out[1]["text"] = "<i>bez zamknięcia"
            out[2]["text"] = "a\nb\nc"
            out[3]["text"] = "Line number 4."   # untranslated
            del out[4]                          # missing index

    _translate_batches(w, slips)
    rc, res = run("check", "--out-dir", str(w))
    problems = {i["index"]: i["problems"] for r in res["report"] for i in r.get("items", [])}
    assert "contains a {xx: ...} reference line" in problems["1"]
    assert "unbalanced <i> tag" in problems["2"]
    assert "3 lines (max 2)" in problems["3"]
    assert any("identical" in p for p in problems["4"])
    assert any("count mismatch" in p for r in res["report"] for p in r.get("problems", []))
    assert "missing cue in translation" in problems["5"]


def test_src_anchor_catches_shift_with_correct_count(work):
    """Merge cues 2+3, later split cue 5: the count and indexes are right, the texts are not."""
    src, w = work
    run("split", str(src), "--out-dir", str(w), "--batch-size", "100")
    items = json.loads((w / "batch_001.json").read_text(encoding="utf-8"))["translate"]
    texts = ["PL " + i["text"] for i in items]
    texts = texts[:1] + ["PL Line number 2. Line number 3."] + texts[3:5] + ["PL Line", "number 5."] + texts[5:]
    texts = texts[:len(items)]
    srcs = [subs_tool.anchor_of(i["text"]) for i in items]
    srcs = srcs[:2] + srcs[3:5] + [srcs[4]] + srcs[5:]   # anchors follow the texts, as a drifting model writes them
    srcs = srcs[:len(items)]
    out = [{"index": i["index"], "src": s, "text": t} for i, s, t in zip(items, srcs, texts)]
    (w / "out_001.json").write_text(json.dumps(out), encoding="utf-8")

    rc, res = run("check", "--out-dir", str(w))
    problem = res["report"][0]["problems"][0]
    assert rc == 2 and "src anchor mismatch" in problem and "from cue 3" in problem
    assert "text of cue 4" in problem
    rc, res = run("apply", str(src), "--out-dir", str(w), "-o", str(w / "x.srt"))
    assert rc == 2 and not (w / "x.srt").exists()


def test_src_anchor_tolerates_labels_and_punctuation():
    source = [{"index": "1", "text": "JOHN: Wow, really?"}, {"index": "2", "text": "<i>I don't know.</i>\n{fr: Je sais pas.}"}]
    good = [{"index": "1", "src": "Wow, really", "text": "Serio?"}, {"index": "2", "src": "I don't know", "text": "Nie wiem."}]
    assert subs_tool.anchor_mismatches(source, good) == []
    bad = [{"index": "1", "src": "I don't", "text": "Nie wiem."}, {"index": "2", "src": "", "text": "x"}]
    found = subs_tool.anchor_mismatches(source, bad)
    assert [b["index"] for b in found] == ["1", "2"] and found[0]["looks_like"] == "2"


def test_shift_heuristic_without_anchors():
    import random
    random.seed(3)
    words = "alpha beta gamma delta epsilon zeta eta theta iota kappa".split()
    src, pl = [], []
    for k in range(60):
        n = random.randint(1, 9)
        end = random.choice(["?", "!", "."])
        line = " ".join(random.choice(words) for _ in range(n)) + end
        src.append({"index": str(k + 1), "text": line})
        pl.append({"index": str(k + 1), "text": "pl " + line})
    assert subs_tool.shift_suspects(src, pl) == []
    shifted = [dict(p) for p in pl]
    for k in range(30, 59):
        shifted[k]["text"] = pl[k + 1]["text"]
    runs = subs_tool.shift_suspects(src, shifted)
    assert runs and runs[0]["offset"] == 1 and 29 <= int(runs[0]["from"]) <= 35


def test_apply_keeps_count_check_for_blank_source_cues(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nHi.\n\n2\n00:00:03,000 --> 00:00:04,000\n{fr: Salut.}\n\n"
                   "3\n00:00:05,000 --> 00:00:06,000\nBye.\n\n", encoding="utf-8")
    run("split", str(srt), "--out-dir", str(w))
    (w / "out_001.json").write_text(json.dumps([{"index": "1", "text": "Cześć."}, {"index": "2", "text": ""},
                                                {"index": "3", "text": "Pa."}]), encoding="utf-8")
    rc, res = run("apply", str(srt), "--out-dir", str(w), "-o", str(tmp_path / "o.srt"))
    assert rc == 0 and res["cues"] == 2


def test_skill_copies_match_app_modules():
    """skills/.../srt_merge.py and srt_fix.py must stay identical to the app's modules."""
    for name in ("srt_merge.py", "srt_fix.py"):
        with open(os.path.join(ROOT, "gst_gui", "utils", name), encoding="utf-8") as a, \
                open(os.path.join(SKILL_SCRIPTS, name), encoding="utf-8") as b:
            assert a.read() == b.read(), f"{name} differs - copy gst_gui/utils/{name} to the skill"


def test_clean_removes_songs_and_sdh(tmp_path):
    src = tmp_path / "Film.en.srt"
    src.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\n♪ I get lost in your eyes ♪\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\n[door slams]\nJOHN: Who's there?\n\n"
        "3\n00:00:05,000 --> 00:00:06,000\nNobody.\n\n", encoding="utf-8")
    out = tmp_path / "source.srt"
    rc, res = run("clean", str(src), "-o", str(out))
    assert rc == 0 and res["removed_cues"] == 1 and res["cues_after"] == 2
    text = out.read_text(encoding="utf-8")
    assert "♪" not in text and "[door slams]" not in text and "JOHN:" not in text
    assert "Who's there?" in text and "Nobody." in text


def test_review_apply_fixes_timing_again(work):
    src, w = work
    fixes = w.parent / "fixes.json"
    fixes.write_text(json.dumps([{"index": "1", "text": "A much longer corrected subtitle that needs more time"}]),
                     encoding="utf-8")
    rc, res = run("review-apply", str(src), str(fixes))
    assert rc == 0 and res["applied"] == 1 and "fix" in res


def _jpeg_size(path):
    data = open(path, "rb").read()
    assert data[:2] == b"\xff\xd8"   # JPEG
    i = 2
    while i < len(data):
        marker, length = data[i + 1], int.from_bytes(data[i + 2:i + 4], "big")
        if marker in (0xC0, 0xC2):
            return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
        i += 2 + length


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg not installed")
def test_frames_grid_in_one_image(tmp_path):
    video = tmp_path / "Film.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=320x180:d=70",
                    "-c:v", "libx264", str(video)], check=True)
    src = tmp_path / "Film.en.srt"
    src.write_text("1\n00:00:04,000 --> 00:00:06,000\nYou're tired.\n\n"
                   "2\n00:00:15,000 --> 00:00:16,000\nGo home.\n\n", encoding="utf-8")

    # default: -5, 0, +5 s on top, +10, +20, +60 s below; 240 px frames of a 16:9 video are 426 px wide
    rc, res = run("frames", str(src), str(video), "--index", "1,9", "--out-dir", str(tmp_path / "f"))
    ok = [f for f in res["frames"] if f["status"] == "ok"]
    assert len(ok) == 1 and ok[0]["time"] == "0:00:05.000"
    assert res["frames"][1]["status"] == "no such subtitle"
    assert _jpeg_size(ok[0]["image"]) == (3 * 426, 2 * 240)
    assert ok[0]["shown"] == ["", "You're tired.", "", "Go home.", "", ""]
    assert ok[0]["subtitles_drawn"] is True

    rc, res = run("frames", str(src), str(video), "--index", "1", "--offsets=-1,0,1",
                  "--out-dir", str(tmp_path / "g"), "--no-text")
    assert res["frames"][0]["subtitles_drawn"] is False
    assert _jpeg_size(res["frames"][0]["image"]) == (3 * 426, 240)


def test_clean_drops_decoration_only_lines():
    from srt_merge import clean_lines
    assert clean_lines(["* * *"]) == []
    assert clean_lines(["•"]) == []
    assert clean_lines(["- Hi.", "-"]) == ["- Hi."]
    assert clean_lines(["Hello"]) == ["Hello"]


def test_fix_removes_lone_dialogue_dash():
    from srt_fix import fix_lone_dash
    assert fix_lone_dash(["- Whoa."]) == (["Whoa."], None)
    assert fix_lone_dash(["<i>- Hej</i>"]) == (["<i>Hej</i>"], None)
    assert fix_lone_dash(["-15 stopni."]) == (["-15 stopni."], None)      # a minus, not a dash
    assert fix_lone_dash(["- Tak.", "- Nie."]) == (["- Tak.", "- Nie."], None)
    # the sentence goes on after the comma: one speaker
    assert fix_lone_dash(["- Idę do domu,", "bo jestem zmęczony."]) == (["Idę do domu,", "bo jestem zmęczony."], None)
    lines, problem = fix_lone_dash(["- Cześć. - Pa."])
    assert lines == ["- Cześć. - Pa."] and problem                          # two speakers in one line


def test_fix_dialogue_dash_two_lines():
    from srt_fix import fix_lone_dash
    # the sentence continues on line 2: one speaker, the dash goes
    assert fix_lone_dash(["- Greg wydawał się uczciwym", "mechanikiem,"]) == \
        (["Greg wydawał się uczciwym", "mechanikiem,"], None)
    # dash only on line 2: two speakers, line 1 gets its dash too
    assert fix_lone_dash(["Nie?", "- Nie."]) == (["- Nie?", "- Nie."], None)
    assert fix_lone_dash(["<i>Nie?</i>", "- Nie."]) == (["<i>- Nie?</i>", "- Nie."], None)
    # finished sentence + capital letter: can't tell, reported only
    lines, problem = fix_lone_dash(["- Smakuje mi.", "Trochę słone."])
    assert lines == ["- Smakuje mi.", "Trochę słone."] and problem


def test_fix_dash_never_leaves_an_empty_line():
    from srt_fix import fix_lone_dash
    # an empty line would end the SRT block and lose the text
    assert fix_lone_dash(["-", "I choć wciąż się do tego przyzwyczajałem,"]) == \
        (["I choć wciąż się do tego przyzwyczajałem,"], None)


@pytest.mark.parametrize("before, after", [
    (["Fajnie. / - Słyszałeś... cokolwiek?"], ["- Fajnie.", "- Słyszałeś... cokolwiek?"]),
    (["- Rozumiem. / - Tak."], ["- Rozumiem.", "- Tak."]),
    (["- Rozumiem. / -", "Więc jakiekolwiek macie opcje,"], ["- Rozumiem.", "- Więc jakiekolwiek macie opcje,"]),
    (["- Dobra, trzymaj się,", "stary. / - Trzymaj się."], ["- Dobra, trzymaj się, stary.", "- Trzymaj się."]),
    (["- Kto idzie pierwszy? /", "- Mogę pójść pierwszy."], ["- Kto idzie pierwszy?", "- Mogę pójść pierwszy."]),
    (["Warto wiedzieć.", "/ - Tak."], ["- Warto wiedzieć.", "- Tak."]),
    (["Idę do domu, / bo jestem zmęczony."], ["Idę do domu, bo jestem zmęczony."]),
    (["Otwarte 24/7, 50 km/h."], ["Otwarte 24/7, 50 km/h."]),       # real slashes stay
    (["I/O i and/or"], ["I/O i and/or"]),
])
def test_fix_slash_breaks(before, after):
    from srt_fix import fix_slash_breaks
    assert fix_slash_breaks(before) == after


def test_fix_srt_handles_slashes_end_to_end(tmp_path):
    from srt_fix import fix_srt
    path = tmp_path / "Film.pl.srt"
    path.write_text("1\n00:00:01,000 --> 00:00:04,000\nFajnie. / - Słyszałeś... cokolwiek?\n\n", encoding="utf-8")
    _, stats = fix_srt(str(path))
    assert stats["slashes_fixed"] == 1
    assert "- Fajnie.\n- Słyszałeś... cokolwiek?" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("before, after", [
    (["—Tak.", "—Nie."], ["- Tak.", "- Nie."]),
    (["<i>— Tak.</i>", "— Nie."], ["<i>- Tak.</i>", "- Nie."]),
    (["Jak to mam—"], ["Jak to mam-"]),
    (["Wiesz — to nie tak."], ["Wiesz - to nie tak."]),
    (["Bez myślników."], ["Bez myślników."]),
    (["Nie wiem – czy to w porządku?"], ["Nie wiem - czy to w porządku?"]),
    (["„Zbiłeś – kupiłeś”"], ["„Zbiłeś - kupiłeś”"]),
    (["– Tak.", "– Nie."], ["- Tak.", "- Nie."]),
])
def test_replace_em_dashes(before, after):
    from srt_fix import replace_em_dashes
    assert replace_em_dashes(before) == after


def test_check_flags_slashes_and_em_dashes():
    from subs_tool import check_items
    src = [{"index": "1", "text": "Cool. / - Heard anything?"}, {"index": "2", "text": "Wait—"}]
    out = [{"index": "1", "text": "Fajnie. / - Słyszałeś?"}, {"index": "2", "text": "Czekaj—"}]
    problems = {p["index"]: " ".join(p["problems"]) for p in check_items(src, out)}
    assert '" / "' in problems["1"] and "long dash" in problems["2"]


def test_fix_srt_resolves_doubtful_dashes_with_source(tmp_path):
    from srt_fix import fix_srt
    pl = tmp_path / "Film.pl.srt"
    en = tmp_path / "Film.en.srt"
    pl.write_text("1\n00:00:01,000 --> 00:00:04,000\n- Smakuje mi.\nTrochę słone.\n\n"
                  "2\n00:00:05,000 --> 00:00:08,000\n- Dobra.\nTo będzie 87 dolarów.\n\n", encoding="utf-8")
    en.write_text("1\n00:00:01,000 --> 00:00:04,000\n- I LIKE IT.\nYEAH, A LITTLE SALTY.\n\n"
                  "2\n00:00:05,000 --> 00:00:08,000\n- OKAY.\n- THAT'LL BE $87.\n\n", encoding="utf-8")
    _, stats = fix_srt(str(pl), timing=False)
    assert stats["dashes_from_source"] == 0                     # without a source: only reported
    _, stats = fix_srt(str(pl), timing=False, source_path=str(en))
    assert stats["dashes_from_source"] == 1
    text = pl.read_text(encoding="utf-8")
    assert "Smakuje mi.\nTrochę słone." in text and "- Smakuje" not in text   # one speaker: dash gone
    assert "- Dobra.\nTo będzie 87 dolarów." in text                           # speaker change: left as is


def test_split_gives_reading_budget_and_check_flags_long_lines(work):
    src, w = work
    run("split", str(src), "--out-dir", str(w), "--batch-size", "100")
    items = json.loads((w / "batch_001.json").read_text(encoding="utf-8"))["translate"]
    # 2 s on screen + 0.7 s after it (the next cue is 1 s later) at 17 cps
    assert items[0]["max_chars"] == int(2.7 * 17)
    out = [{"index": i["index"], "src": subs_tool.anchor_of(i["text"]), "text": "Linia " + i["index"]} for i in items]
    out[2]["text"] = "Bardzo długie zdanie, którego nikt nie zdąży\nprzeczytać w dwie sekundy na ekranie."
    (w / "out_001.json").write_text(json.dumps(out), encoding="utf-8")
    rc, res = run("check", "--out-dir", str(w))
    problems = {i["index"]: i["problems"] for r in res["report"] for i in r.get("items", [])}
    assert list(problems) == ["3"] and "too long to read" in problems["3"][0]


def test_char_budget_limits():
    from srt_merge import Cue
    cues = [Cue(0, 400, ["Hi."]), Cue(500, 20000, ["Long speech."])]
    assert subs_tool.char_budget(cues, 0) == subs_tool.BUDGET_MIN      # never below the minimum
    assert subs_tool.char_budget(cues, 1) == 84                       # never more than two full lines


def test_join_sentence_fragments():
    from srt_fix import join_sentence_fragments
    from srt_merge import Cue
    cues = [
        Cue(157653, 158521, ["Powiem starym,"]),
        Cue(158522, 160358, ["że to stypendium w Nowym Jorku,"]),
        Cue(160359, 161919, ["i spadam stąd."]),
        Cue(162000, 163000, ["- Serio?", "- Tak."]),            # a dialogue is never joined
        Cue(163100, 164000, ["<i>Kiedy byłem</i>"]),
        Cue(164010, 165900, ["<i>mały, wierzyłem w to.</i>"]),
        Cue(166000, 166900, ["A oni"]),                          # together over 2 x 42 chars
        Cue(166910, 169800, ["uwierzą w to wszystko bez żadnego dowodu,", "bez papierów, listów, zdjęć i pieczątek?"]),
        Cue(170000, 171000, ["Wiesz,"]),                        # pause too long
        Cue(171500, 172500, ["że cię kocham."]),
        Cue(2196888, 2197756, ["Musisz tak..."]),               # a pause inside one sentence
        Cue(2197757, 2198840, ["...siorbać i stękać?"]),
        Cue(2199000, 2200000, ["Ja chyba..."]),                 # trails off, a new sentence follows
        Cue(2200100, 2201000, ["Nieważne."]),
    ]
    out, joined = join_sentence_fragments(cues)
    texts = [c.lines for c in out]
    assert joined == 4 and len(out) == 10
    assert texts[0] == ["Powiem starym, że to stypendium", "w Nowym Jorku, i spadam stąd."]
    assert (out[0].start, out[0].end) == (157653, 161919)
    assert texts[2] == ["<i>Kiedy byłem mały, wierzyłem w to.</i>"]
    assert texts[3] == ["A oni"] and texts[5] == ["Wiesz,"]
    assert texts[7] == ["Musisz tak... siorbać i stękać?"]
    assert texts[8] == ["Ja chyba..."] and texts[9] == ["Nieważne."]


@pytest.mark.parametrize("flags, cues", [([], 1), (["--join-fragments"], 1), (["--no-join-fragments"], 2),
                                         (["--no-timing"], 2)])
def test_fix_joins_fragments_by_default(tmp_path, flags, cues):
    from srt_merge import parse_srt, read_text
    path = tmp_path / "a.srt"
    path.write_text("1\n00:36:36,888 --> 00:36:37,756\nMusisz tak...\n\n"
                    "2\n00:36:37,757 --> 00:36:38,840\nsiorbać i stękać?\n", encoding="utf-8")
    res = subprocess.run([sys.executable, os.path.join(SKILL_SCRIPTS, "srt_fix.py"), str(path), *flags],
                         capture_output=True, text=True, encoding="utf-8", env=dict(os.environ, PYTHONUTF8="1"))
    assert res.returncode == 0, res.stderr
    assert len(parse_srt(read_text(str(path)))) == cues


@pytest.mark.parametrize("parts, expected", [
    ([["Kiwali głowami,"], ["jakbym im tłumaczył", "zasady panujące w burdelu."]],
     [["Kiwali głowami, jakbym im tłumaczył", "zasady panujące w burdelu."]]),   # break at the old pause
    ([["Widok na całe miasto"], ["i świadomość, że jesteś"], ["na szczycie świata."]],
     [["Widok na całe miasto i świadomość,", "że jesteś na szczycie świata."]]),  # "że" starts a line
    ([["KINO WELLFLEET"], ["TERAZ NA EKRANIE"]], [["KINO WELLFLEET"], ["TERAZ NA EKRANIE"]]),  # on-screen text
])
def test_join_wraps_at_pauses_and_skips_screen_text(parts, expected):
    from srt_fix import join_sentence_fragments
    from srt_merge import Cue
    cues = [Cue(i * 1000, i * 1000 + 999, p) for i, p in enumerate(parts)]
    assert [c.lines for c in join_sentence_fragments(cues)[0]] == expected


def test_check_single_batch_and_batch_size_limit(work):
    src, w = work
    rc, res = run("split", str(src), "--out-dir", str(w), "--batch-size", "300")
    assert rc == 2 and "over 150" in res["error"]
    run("split", str(src), "--out-dir", str(w), "--batch-size", "5")
    rc, res = run("check", "--out-dir", str(w), "--batch", "2")
    assert rc == 2 and "does not exist yet" in res["error"]
    _translate_batches(w)
    rc, res = run("check", "--out-dir", str(w), "--batch", "2")
    assert rc == 0 and res["checked_batches"] == 1


@pytest.mark.parametrize("before, after", [
    ('Morderca? "Jay zamordował', 'Morderca? „Jay zamordował'),
    ('członka zespołu i ucieka".', 'członka zespołu i ucieka”.'),
    ('<i>"Never Come Down"</i>', '<i>„Never Come Down”</i>'),
    ('“Hi”, he said', '„Hi”, he said'),
    ('Dokąd to?...', 'Dokąd to?..'),
    ('Serio!…', 'Serio!..'),
    ("Rock 'n' roll, O'Brien", "Rock 'n' roll, O'Brien"),
])
def test_polish_typography(before, after):
    from srt_fix import polish_typography
    assert polish_typography([before]) == [after]


def test_short_cue_borrows_time_from_neighbours():
    from srt_fix import fix_timing, needed_duration
    from srt_merge import Cue
    cues = [Cue(0, 4000, ["Długi napis, który ma zapas."]), Cue(4100, 4400, ["Co?"]),
            Cue(4500, 9000, ["Następny napis też ma czasu dość."])]
    out, stats = fix_timing(cues)
    co = out[1]
    assert co.end - co.start >= needed_duration(co.lines)
    assert out[0].end + 83 <= co.start and co.end + 83 <= out[2].start
    assert stats["still_short"] == 0


def test_join_skips_new_sentence_after_comma():
    from srt_fix import join_sentence_fragments
    from srt_merge import Cue
    cues = [Cue(0, 1500, ["To tylko zarys historii,"]), Cue(1550, 3000, ["Proszę śmiało pytać."]),
            Cue(5000, 6000, ["proszę bardzo."]),
            Cue(8000, 9000, ["Rozmawiałem z"]), Cue(9050, 10000, ["Johnem."])]
    out, joined = join_sentence_fragments(cues)
    assert joined == 1 and out[0].lines == ["To tylko zarys historii,"]
    assert out[-1].lines == ["Rozmawiałem z Johnem."]


def test_verify_flags_fillers_and_review_apply_deletes_cue(tmp_path):
    srt = tmp_path / "f.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:03,000\nNie, to raczej... eee...\n\n"
                   "2\n00:00:04,000 --> 00:00:05,000\nYyy...\n\n"
                   "3\n00:00:06,000 --> 00:00:08,000\nEh, szkoda. Mhm.\n\n"
                   "4\n00:00:09,000 --> 00:00:11,000\nNo to idziemy.\n", encoding="utf-8")
    rc, res = run("verify", str(srt))
    assert [d["index"] for d in res["details"]["filler"]] == ["1", "2"] and res["ok"]
    fixes = tmp_path / "fixes.json"
    fixes.write_text(json.dumps([{"index": "1", "text": "Nie, to raczej..."},
                                 {"index": "2", "delete": True},
                                 {"index": "4", "text": "No to chodźmy."}]), encoding="utf-8")
    rc, res = run("review-apply", str(srt), str(fixes))
    assert res["deleted"] == 1 and "filler" not in res["verify"]
    text = srt.read_text(encoding="utf-8")
    assert "Yyy" not in text and "Nie, to raczej..." in text and "No to chodźmy." in text
