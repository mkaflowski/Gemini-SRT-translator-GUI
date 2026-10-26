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
    assert any("missing ['5']" in p for r in res["report"] for p in r.get("problems", []))


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
