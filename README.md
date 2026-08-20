# 🎬 Gemini SRT Translator GUI

A desktop application for translating SRT subtitles with Google Gemini — with drag & drop, TMDB context, bilingual reference tracks, and automatic clean-up of the finished subtitles.

> **Origin of the project.** The command-line tool [Gemini SRT Translator](https://github.com/MaKTaiL/gemini-srt-translator) by [MaKTaiL](https://github.com/MaKTaiL) served as the **base** for this project. It is no longer used as an external dependency: a copy of it lives in `gemini_srt_translator/` inside this repository and has been **heavily modified** — API key switching on overload, non-blocking resume, progress output the GUI can parse, compatibility fixes for newer/older `google-genai` releases. On top of that sits a GUI and a set of subtitle tools that do not exist upstream. All credit for the original translation engine goes to MaKTaiL; all bugs in the modifications are ours.

<img src="https://github.com/user-attachments/assets/0298d098-f201-4d9b-b50d-8acccfd6c525" width="75%" />

## ✨ Features

### 🚀 Core functionality
- **Drag & drop interface** — drop subtitle files, video files or whole folders, several at a time
- **Automatic subtitle/video pairing** with a checkbox list of everything found
- **Batch processing** — translate many files in one run, with per-file status
- **Progress bar driven by the translator's own output**, plus a working Cancel button
- **Language code support** — old language codes are stripped from the filename, the new one is added (`Movie.ita.srt` → `Movie.pl.srt`)
- **Translator info** — optional `# Translated by <model> #` stamp at the top of the file

### 🔀 Bilingual merge (second-language reference)
Translating from English alone forces the model to guess grammatical gender, formality and idioms. Merging a second-language subtitle track into the source file gives it a second opinion for every line.

- **`🔀 Merge subs`** — merges the selected subtitles with a reference file; the reference is auto-detected in the same folder or taken from a second selected row
- Reference lines are attached below the main ones as `{fr: ...}`; moments **missing** from the main track are inserted as new subtitles marked `[en: BRAK NAPISÓW - PRZETŁUMACZ Z POZOSTAŁYCH]`, so nothing is lost
- **Song lyrics (`♪ ... ♪`) and hearing-impaired text** (`[DOOR SLAMS]`, `(sighs)`, `MAN:`) are stripped from both files first
- **Automatic time-offset detection** by cross-correlating the two subtitle timelines; the reference is shifted only when it clearly helps
- **Timing collisions** created by injected lines are resolved without touching the original timings
- A **bilingual context note is added to Overview automatically** whenever a merged file (`…en_fr.srt`) is selected — and removed when it is not — so the model knows what the reference lines mean. It coexists with the TMDB description instead of replacing it.

### ✨ Subtitle fixer (line breaks & timings)
- **`✨ Fix subs`** — fixes the selected files in place, and **"Fix subtitles after translation"** (on by default) does the same automatically for every translated file
- **Line breaking** follows Polish subtitling practice: break at the highest syntactic node (punctuation → subject/predicate boundary → before a conjunction → before a preposition), never separating a number from its noun, a first name from a surname, a title from a name, a verb from `nie`/`się`/its auxiliary, or a preposition from its phrase. Max 42 characters, max 2 lines, syntax before balance.
- **Display times** follow the usual guidelines: minimum 0.833 s, maximum 7 s, reading speed 17 CPS, minimum 83 ms gap between subtitles. A subtitle is extended only into space its neighbours leave free (at most 2 s past its end, 0.5 s before its start), never at a neighbour's expense.
- Subtitles that still cannot be fixed are **reported instead of being silently mangled**; optional `--merge-close` joins a too-short subtitle with the next one when they are less than 0.5 s apart.

### 🔍 AI review of a finished translation
- **`🔍 AI review`** — compares the translated file with its source (matched by timecode) and reports **translation errors, hallucinations, spelling mistakes, unnatural phrasing and wrong grammatical gender/formality**, each with a severity and a ready-to-use replacement line
- **The fixes can be written straight back into the subtitles** (on by default in the dialog): one fix per subtitle, the most severe one, and the changed line is re-wrapped immediately so it still fits 42 characters
- Every defect and every applied fix is printed in the console as it is found
- The film description (Overview) is passed to the model as context; when the source is one of our **merged bilingual files**, the reference lines are explained to the model and used as a second source of truth
- Review batches are **1.5× the translation batch size**
- Optional Markdown report next to the subtitles (`<name>.review.md`), with applied fixes marked; it opens straight from the summary dialog
- **"AI review after translation"** does the same automatically for every translated file, applying the fixes and reporting into the console only (no report file)
- `Check only first N subtitles` (`--limit N`) runs a cheap trial before spending tokens on the whole film

### 🧰 Tools that follow the selection
The buttons above the file list work on whatever is checked, and grey out when the selection cannot feed them: `🎞 Embedded subs` and `📝 Describe video` need a row with a real video file, `🔍 AI review` needs a subtitle file.

### 🎞 Subtitles from video files
- **`🎞 Embedded subs`** — detects subtitle tracks embedded in MKV/MP4 files and extracts the chosen one to SRT (matched by language across all selected videos)
- Videos without an external subtitle file are probed automatically and the row is annotated with the number of embedded tracks

### 📝 Video description / transcription
- **`📝 Describe video`** — analyses the video with Gemini and loads the result into Overview as translation context
- **`gst_transcribe`** — standalone transcription of long videos with automatic splitting; a `transcription.txt` next to the file is loaded into Overview on the next run

### 🎭 Movie & TV integration
- **TMDB integration** — automatic ID lookup and plot fetching, or manual TMDB ID
- **Context-aware translation** — the plot, title and content type are passed to the model
- **Smart TV series detection** from multiple episode files in one folder

### ⚙️ Advanced
- **Two Gemini API keys** — the second one takes over on quota/overload instead of waiting out the backoff
- **Fallback models** — a comma-separated list of models to try when the primary one fails; a partially translated file resumes instead of restarting
- **Model list fetched from the API** on demand
- **Batch size** — 350 lines by default, automatically reduced to 100 for Gemini 2.0 models
- **Translation type** — `Default` or `Concise translation` (shorter lines, easier to read on screen)
- **Include timestamps** and **audio extraction** options
- **Persistent configuration** — every setting survives a restart

## 🛠️ Installation

```bash
pip install git+https://github.com/mkaflowski/Gemini-SRT-translator-GUI.git
gst_gui
```
For update use:
```bash
pip install --upgrade git+https://github.com/mkaflowski/Gemini-SRT-translator-GUI.git
```

### Prerequisites
- Python 3.8 or higher
- Google Gemini API key (https://aistudio.google.com/apikey)
- TMDB API key (optional, for movie context - https://www.themoviedb.org/settings/api)

## 🚀 Usage

- **`gst_gui`** — Translator GUI
- **`gst_transcribe --api_key KEY --lang Polish`** — Video transcription and overview *(experimental)*

gst_transcribe saves transcribe.txt next to the selected file. If the file is in the same folder as the selected file, it will be loaded in the GUI as the description.

### Basic workflow
1. **Launch** the application
2. **Enter API keys** in the settings
3. **Drag & drop** subtitle files or folders
4. **Auto-detection** identifies movies/TV series
5. *(optional)* **`🔀 Merge subs`** to add a second-language reference track
6. **Review** extracted information
7. **Click Translate** — the finished file is fixed automatically, and reviewed by the model too if you turned that on

### Output examples
- **Input:** `Movie.ita.srt` → **Output:** `Movie.pl.srt` (Italian removed, Polish added)
- **Merge:** `Movie.eng.srt` + `Movie.fr.srt` → `Movie.eng.en_fr.srt`

## 🧰 Standalone scripts

Both subtitle tools also work from the command line, without the GUI:

```bash
# merge two subtitle files into one bilingual file
python srt_merge.py Movie.eng.srt Movie.fr.srt
python srt_merge.py --print-description          # context note for the model

# fix line breaks and display times (overwrites the file)
python srt_fix.py Movie.pl.srt
python srt_fix.py Movie.pl.srt --check --show    # report only, nothing is written

# have Gemini review a translation against its source
python srt_review.py Movie.pl.srt Movie.eng.en_fr.srt --description "..."
python srt_review.py Movie.pl.srt Movie.eng.srt --limit 60   # cheap trial run
python srt_review.py Movie.pl.srt Movie.eng.srt --apply      # write the fixes back
```

Useful flags — merge: `--lang-base/--lang-other`, `--min-overlap`, `--best-match-only`, `--keep-music`, `--keep-sdh`, `--shift MS`, `--no-shift`, `--keep-overlaps`.
Fix: `--max-len`, `--cps` (17 Polish, 20 English, 13 for children), `--min-duration`, `--max-duration`, `--min-gap`, `--merge-close`, `--no-timing`, `-o FILE`.
Review: `--apply` (write the suggested fixes into the checked file), `--api-key`, `--model`, `--language`, `--description/--description-file`, `--batch-size`, `--limit`, `-o FILE`.

## ⚙️ Configuration

### Settings panel
- **Language** and **language code** for the output filename
- **Model selection** and **fallback models**
- **Batch size**
- **Fix subtitles after translation** (on by default) and **AI review after translation** (off by default - it costs extra API calls)
- **Translation type**, **include timestamps**, **audio extraction**
- **Auto-fetch TMDB ID**, **TV series** mode, **translator info**

## 🧩 To Do

-  Getting description for each episode from TMDB
-  Scene-cut aware timing (needs the video file)
-  Optional POS tagger for even better line breaking

## 🔗 Related projects

- **[Gemini SRT Translator](https://github.com/MaKTaiL/gemini-srt-translator)** — the original command-line tool this project is based on. A modified copy is bundled here; if you only need the CLI, use the original.

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- **[MaKTaiL](https://github.com/MaKTaiL)** — for the Gemini SRT Translator, which this project is built on and which is bundled here in modified form
- **Google Gemini AI** - For powerful translation capabilities
- **TMDB** - For movie and TV series metadata
- **[tkinterdnd2](https://github.com/TomSchimansky/CustomTkinter)** - For drag and drop functionality
- **PyInstaller** - For executable creation

---

**⭐ Star this repo if you find it helpful!**
