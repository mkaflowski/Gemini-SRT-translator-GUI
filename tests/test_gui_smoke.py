"""Builds the real main window (needs a display) and checks the methods the
Translate button relies on exist and work - v0.26 shipped without one of them."""
import pytest

ctk = pytest.importorskip("customtkinter")


@pytest.fixture(scope="module")
def gui():
    try:
        root = ctk.CTk()
    except Exception as e:  # no display (CI)
        pytest.skip(f"no display: {e}")
    from gst_gui.gui.main_window import DragDropGUI
    window = DragDropGUI(root)
    yield window
    root.destroy()


def test_translate_config_and_post_steps(gui):
    config = gui._get_current_config()
    for key in ("gemini_api_key", "model", "language", "language_code", "batch_size"):
        assert key in config
    for name in ("_start_translation", "offer_post_translation_steps", "_offer_ai_rename",
                 "refresh_file_list", "_extract_tracks_for_items"):
        assert callable(getattr(gui, name))


def test_auto_dialog_and_auto_post_steps(gui, tmp_path, monkeypatch):
    from gst_gui.gui import main_window
    from gst_gui.utils import auto_prepare

    # the dialog opens for a plan with tracks to extract
    plan = auto_prepare.plan_video("Film.mkv", ["Film.en.srt"], [
        {"type_index": 0, "codec": "subrip", "language": "fre", "title": "", "default": False,
         "forced": False, "sdh": False, "text_based": True}], 1)
    plan["folder"] = str(tmp_path)
    gui._show_auto_dialog([plan], "ar")
    gui.root.update()
    dialogs = [w for w in gui.root.winfo_children() if isinstance(w, ctk.CTkToplevel)]
    assert dialogs
    for dialog in dialogs:
        dialog.destroy()

    # started from Auto: Fix subs and the rename run without a single question
    out = tmp_path / "Film.pl.srt"
    out.write_text("1\n00:00:01,000 --> 00:00:03,000\nDzień dobry.\n", encoding="utf-8")
    asked = []
    monkeypatch.setattr(main_window.messagebox, "askyesno", lambda *a, **k: asked.append(a) or False)
    gui.ai_mark_code.set("ar")
    gui._auto_post = {"fix": True, "review": False, "rename": True}
    gui.offer_post_translation_steps([{"output": str(out), "subtitle": None}], {})
    assert not asked and gui._auto_post is None
    assert (tmp_path / "Film.ar.srt").exists() and not out.exists()


def test_lang_column_labels(gui):
    gui.ai_mark_code.set("ar")
    assert gui._lang_label_for("Film.fr.srt") == "FR"
    assert gui._lang_label_for("Film.ar.srt") == "AI"
    assert gui._lang_label_for("Film.en_fr.srt") == "EN+FR"
