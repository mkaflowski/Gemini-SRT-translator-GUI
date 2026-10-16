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
