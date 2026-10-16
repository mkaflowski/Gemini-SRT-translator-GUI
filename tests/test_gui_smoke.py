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


def test_lang_column_labels(gui):
    gui.ai_mark_code.set("ar")
    assert gui._lang_label_for("Film.fr.srt") == "FR"
    assert gui._lang_label_for("Film.ar.srt") == "AI"
    assert gui._lang_label_for("Film.en_fr.srt") == "EN+FR"
