---
name: translate-subtitles
description: Translate a movie's or episode's subtitles (SRT) the way Gemini SRT Translator does. Asks for the movie folder, finds the subtitles or extracts them from the video file, can merge two subtitle files (e.g. EN+FR) into one bilingual source, looks up the movie, translates in batches, runs "Fix subs" and offers an AI review. Use when the user wants to translate subtitles for a film or series episode.
---

# Translating movie subtitles

This skill does what the Gemini SRT Translator GUI does, but **you translate** (not an external API).

> [!IMPORTANT]
> **Strictly do NOT use external API calls or environment variables** (e.g. `google-genai`, `openai`, `GOOGLE_API_KEY`, `GEMINI_API_KEY`).
> - External API keys have strict rate limits (e.g. 20 req/day on AI Studio Free Tier) and often fail or fall back to inferior models.
> - The user has an ample model allowance in Antigravity.
> - All translations must be generated directly by you (the assistant model) within the chat session or delegated to subagents, using file-writing tools to save `out_NNN.json`.
>
> **Preserving timings:** If the user specifies not to change or touch timings ("nie ruszaj czasów", "zachowaj czasy", manually synced subtitles), pass `--no-timing` to `srt_fix.py` (Step 8) and `subs_tool.py review-apply --no-timing` (Step 9).

Scripts are in `scripts/` next to this file (Python 3.9+, no extra packages; `ffmpeg`/`ffprobe` only for subtitles embedded in the video).

- `scripts/subs_tool.py`: scan, extract, clean, split into batches, frames from the video for unclear lines, resume (`status`), check the batches (`check`), build the SRT, add the translator info, prepare and apply the review
- `scripts/srt_merge.py`: merge two subtitle files into one bilingual file (copy from the GUI)
- `scripts/srt_fix.py`: "Fix subs", which rewraps lines and fixes display times (copy from the GUI)

Below, `$S` means the absolute path to the `scripts/` folder of this skill. Run the scripts with `python` (on Linux/macOS use `python3` if needed). All `subs_tool.py` output is JSON.

**Asking the user.** Ask one thing at a time and always show the default. In Claude Code use the question tool (AskUserQuestion) with the default as the first option. In other tools (OpenCode, Antigravity) ask in chat and wait for the answer. Talk to the user in their language.

**Using defaults / Full auto:** When the user instructs to use default/recommended options ("użyj domyślnych ustawień / opcji", "defaults", "full auto"):
- Apply all recommended defaults automatically without asking each step.
- **Always complete the full post-processing pipeline:** never stop after just writing batches. You must always run `subs_tool.py check`, assemble with `subs_tool.py apply`, add the translator stamp (`subs_tool.py stamp`), and run Fix subs (`srt_fix.py`).
- If the user specifically instructs to keep `.pl` or skip renaming to `ar` (e.g. "pomiń zmianę arr na pl", "zostaw .pl.srt"), keep `.pl.srt`.
- Otherwise, at the end (Step 10 / End), rename `.pl.srt` to `.ar.srt`.
- Automatically clean up (delete) the working directory `$W` when each episode is finished.

**Series / multi-episode seasons:** When processing multiple episodes:
- Process episode by episode sequentially.
- Keep the shared glossary in `<folder>/.translate_work/glossary.md` across episodes.
- You can invoke subagents for batch translation to preserve context window.

## Step 1: Movie folder

Ask for the path to the movie folder (or the video file, in which case take its folder). Then run:

```
python "$S/subs_tool.py" scan "<folder>"
```

Show a short summary: the video files and the `.srt` files with their language (`language`; `en+fr` means an already merged file). Files with the code `ar` are the user's earlier **AI translations** (see step 10), not Arabic subtitles: never use them as a translation source and don't count them as existing languages.

## Step 2: Missing subtitles → the video file

If there is **no** `en` **and no** `fr` among `languages_present`, ask whether to check the subtitles inside the video file.
If yes, show the tracks from `videos[].tracks` (number, language, title, `forced`/`default`, and whether `extractable`; bitmap tracks such as PGS cannot be converted to SRT) and ask **which ones to extract** (several can be chosen; the default is all `en`/`fr` text tracks). Then:

```
python "$S/subs_tool.py" extract "<video>" --tracks 0,2
```

Files are saved as `<video name>.<lang>.srt`. If the result has `"status": "exists"`, ask whether to **overwrite** or **add a number** (`numbered_alternative`), then rerun for those tracks with `--on-exists overwrite` or `--on-exists number`. Finally run `scan` again.

If there are several episodes in the folder, do steps 2 to 10 for each video (ask once and apply the same choices to all of them; for step 10 one question covers all the episodes, listing the files that will be renamed). Steps 3 to 6 only need to be asked once per season.

## Step 3: Merging two subtitle files

Ask whether to merge two subtitle files into one (a bilingual source gives better gender, formality and context). If yes, ask which ones. **Default: the base is the `en` file and the reference is the `fr` file.**

```
python "$S/srt_merge.py" "<base.srt>" "<reference.srt>" --lang-base en --lang-other fr
```

The result is `<name>.en_fr.srt` next to the base file. Each base line can have a reference line `{fr: ...}` below it, and moments missing from the base have the marker `[en: BRAK NAPISÓW - PRZETŁUMACZ Z POZOSTAŁYCH]` with only the `{fr: ...}` line. The script also syncs the reference file automatically (the time offset is printed in the report).

## Step 4: Which file to translate from

Ask which file to translate from. **Default: the merged file** (if one was made or already exists), otherwise the `en` file.

Then ask whether to remove **song lyrics** (lines with ♪) and **notes for the hearing impaired** (`[door slams]`, `(sighs)`, `JOHN:` labels) from the source before translating. **Default: yes**, the same as the GUI does when merging. A merged file is already cleaned, so don't ask for it. Song lyrics left in the source would end up in the translated subtitles, usually untranslated.

## Step 5: Target language

Ask for the target language. **Default: Polish (`pl`).**
Output file: `<video name>.<target code>.srt` next to the video (if there is no video, the source name without its language code + `.<code>.srt`). If it already exists, ask whether to overwrite it or add a number (`<name>.2.<code>.srt`).

## Step 6: Movie information

From the file name, work out the title, year and whether it is a series (`S01E02`, `1x02`). Search the web (TMDB, IMDb, Wikipedia) for a description: the plot, the main characters and **their gender**, how they relate to each other (who uses formal or informal address with whom), and the setting and era. Show the user what you found (title, year, 2 to 4 sentences of description) and ask **whether it is the right film**. If not, ask for a correction (another title, year or TMDB/IMDb link) and search again.
If there is a `transcription.txt` file in the folder (a description generated by the GUI), read it as additional context.

## Step 7: Batch translation

**Working folders.** Each video gets its own working folder `$W` = `<folder>/.translate_work/<video name without extension>` (with no video: the source subtitle name). The glossary is shared by everything in the folder: `<folder>/.translate_work/glossary.md`. For a series, every episode of the season uses the same glossary.

**Resuming.** A film has 1000–2000 subtitles, so the work may stop halfway (your context runs out, the session ends). Before splitting, check whether `$W/manifest.json` exists. If it does, run:

```
python "$S/subs_tool.py" status --out-dir "$W"
```

Tell the user how many batches are done and ask whether to **continue** (default) or **start over**. To continue, skip the cleaning and the split, use the `source` path from `status` as `<source.srt>`, and go on with the batch in `next`, redoing any batch reported as `incomplete` or `broken`. To start over, run the split with `--force`. Never re-split without asking: `split` refuses to overwrite started work.

1. If the user agreed to the cleaning in step 4, clean the source into the working folder and **from now on use `$W/source.srt` as `<source.srt>`** (in split, apply and review-split):

   ```
   python "$S/subs_tool.py" clean "<chosen file.srt>" -o "$W/source.srt"
   ```

   Tell the user how many subtitles were removed and show a few `removed_examples`. Add `--keep-music` or `--keep-sdh` if the user wants to keep that part.

   Then split the file into batches of about 100 subtitles:

   ```
   python "$S/subs_tool.py" split "<source.srt>" --out-dir "$W" --batch-size 100
   ```

   Batches are cut at **scene breaks**: in the 70–130 subtitle window the script picks the longest pause in the dialogue, preferably right after a finished sentence. That's why batch sizes vary (`manifest.json` shows `count`, `pause_after_ms` and `sentence_end` for each cut). Add `--fixed` to cut exactly every N subtitles instead.

2. Each `batch_NNN.json` has three parts:
   - `translate`: the subtitles to translate in this batch,
   - `context_before`: the last 10 subtitles of the previous batch, **read-only**,
   - `context_after`: the first 5 subtitles of the next batch, **read-only**.

   Use the context so that a sentence started or finished across the cut is translated with its full meaning, and so the words fit together at the boundary. For `context_before`, also read your own translation of those lines from the previous `out_NNN.json`, so the continuation matches in wording, gender and formality. **Translate only the `translate` items.** Never output context items.
3. For **each** batch in order, save `out_NNN.json` in the same folder: a list of `{"index": "...", "text": "..."}` with exactly the `index` values from `translate`. Write the file with your file-writing tool (not through the shell), UTF-8.
4. Keep a short glossary in `<folder>/.translate_work/glossary.md`: names, nicknames, who uses formal or informal address with whom, the gender of each character, recurring terms. Read it before every batch and update it afterwards, so that batches are consistent with each other.
5. After the last batch, check the batches for typical slips:

   ```
   python "$S/subs_tool.py" check --out-dir "$W"
   ```

   It reports, for each batch:
   - Copied `{xx: ...}` reference lines or the missing-subtitle marker.
   - Empty or untranslated (identical to the source) lines.
   - More than 2 lines, or lines far over the length limit.
   - Unbalanced `<i>`/`<b>` tags.
   - Missing or extra indexes.

   Fix any reported items in the `out_NNN.json` files and run `check` again until clean.
6. Build the file (the timings come from the source):

   ```
   python "$S/subs_tool.py" apply "<source.srt>" --out-dir "$W" -o "<output.srt>"
   ```

   If it reports `missing translations`, translate the listed `index` values (add them to the right `out_NNN.json`) and run it again.
7. **Always** add the translator info as the first subtitle, like the GUI does (`# Translated by <model> #`). Use the name of the model that is actually doing the translation, i.e. you (e.g. `Gemini 3.8 Flash`, `Gemini 3 Pro`, `Claude Opus 5.5`), not the name of the tool:

   ```
   python "$S/subs_tool.py" stamp "<output.srt>" --model "<your model name>"
   ```

   Running it again replaces the existing info, so the file never has two.

Report progress after each batch (e.g. "batch 3/7 done"). If your context is getting full, say so: the user can continue in a new session and the skill will resume from `status`.

### Unclear lines: look at the video

When the text alone does not tell you something the translation needs, look at that moment of the video. Typical cases:
- the gender of the speaker or of the person addressed ("You're tired" → "zmęczony" or "zmęczona"),
- who is speaking to whom, and whether it is formal or informal address,
- whether "you" means one person or several,
- text visible on screen that the subtitle refers to (an SMS, a sign, a letter).

```
python "$S/subs_tool.py" frames "<source.srt>" "<video>" --index 214,388 --out-dir "$W/frames"
```

`--index` takes the subtitle numbers (the `index` values from the batch). For each subtitle you get **one** small image: a 3×2 grid of frames, each labelled in its top-left corner with its offset from the subtitle: **-5s, 0, +5s** in the top row and **+10s, +20s, +60s** in the bottom row. The subtitle shown at each moment is drawn at the bottom of its frame, so you can see which line is said in which shot. Open the image with your image/file-reading tool. Frames far from the moment (+20s, +60s) may already be another shot or scene: use them for who is around, not for who said the line. `--offsets=-2,0,2` (with `=`, because of the leading minus) picks other moments, `--no-text` leaves the frames without subtitles.

One image costs about as much as 60 subtitle lines, so:
- use it only when you are really unsure, not for every line: at most about **20 images per film** (more only if the user agrees),
- ask for several indexes in one call,
- write what you learned into the glossary ("Cody = man, 30s; Cody uses informal address with the judge"), so the same question doesn't come up again.

Without a video file in the folder, skip this and rely on the movie description and the glossary. If your tool refuses to look at an image (some frames can be violent or explicit), skip it and decide from the text.

### Translation rules (same as Gemini SRT Translator)

- Translate only the `text` of the `translate` items into the target language. The number of output items must be **identical** to the number of `translate` items. Do not add, remove, reorder or **merge text between items**: the original and the translation must match item by item.
- An empty or whitespace-only `text` stays unchanged.
- Keep the formatting: HTML tags (`<i>`, `<b>`) and line breaks (`\n`).
- Lines of 40 to 50 characters at most (ideally ≤ 42), at most 2 lines, broken at natural phrase boundaries or after punctuation.
- Dialogue between two speakers: each speaker's line on its own line, starting with a dash (`- `).
- Correct spaces between words and after punctuation. When a sentence continues on the next line, keep the spacing between the lines.
- Natural, spoken target language, not a word-for-word calque. Grammatical gender and formality (formal or informal address) follow from the characters and context (glossary, movie description).
- **Merged file**: the `{xx: ...}` lines are only a reference translation of the same moment from another track. Use them as context (gender, formality, number, idioms, who is speaking to whom). When the base line is the `[en: BRAK NAPISÓW - PRZETŁUMACZ Z POZOSTAŁYCH]` marker, translate the reference line instead. **The output contains ONLY the final subtitle in the target language**: never copy the `{xx: ...}` lines or the marker into `text`.
- If the user wants a "concise translation": short, clear sentences that keep the full meaning; drop filler words and repetitions ("uhm", "well", "you know").

## Step 8: Fix subs

Run it on the translated file (it rewraps lines that are too long and fixes display times):

```
python "$S/srt_fix.py" "<output.srt>"
```

> [!NOTE]
> If the user requested to keep/preserve timings ("nie ruszaj czasów", "zachowaj czasy"), pass `--no-timing`:
> ```
> python "$S/srt_fix.py" "<output.srt>" --no-timing
> ```

Show the user the short report the script printed.

## Step 9: AI review (correction)

Ask whether to review the translation. If yes, compare it with the file you translated from (with a merged source you also get the second reference):

```
python "$S/subs_tool.py" review-split "<output.srt>" "<source.srt>" --out-dir "$W" --batch-size 150
```

For each `review_NNN.json` (items `{index, original, translation}`), check the translation like a senior subtitle reviewer. For `gender` doubts you can use `frames` (see "Unclear lines"), but the `index` here is the number in the **translated** file, so pass `<output.srt>` instead of `<source.srt>`. Report only **real** defects:

- `translation`: wrong meaning, lost information, wrong name/number/unit
- `hallucination`: content that is not in the original
- `spelling`: spelling, grammar, punctuation, typos
- `unnatural`: correct but not how a native speaker would say it (calque, word order, register)
- `gender`: wrong grammatical gender, number or formality of the speaker or the person addressed

Subtitles are condensed on purpose: shortening and dropping filler words are **not** defects. An acceptable alternative is not a defect. Do not invent defects.

Collect the corrections in `$W/fixes.json` as `[{"index": "12", "text": "the whole corrected subtitle (with \n)", "category": "gender", "problem": "..."}]`, and show the user a short list (category, fragment, before → after). Then apply them:

```
python "$S/subs_tool.py" review-apply "<output.srt>" "$W/fixes.json"
```

If timings must be preserved, pass `--no-timing`:
```
python "$S/subs_tool.py" review-apply "<output.srt>" "$W/fixes.json" --no-timing
```

`review-apply` runs Fix subs again by itself (corrections change the text length, so display times must be fixed again). Do not skip it, and don't edit the file by hand after it.

## Step 10: Mark the file as an AI translation (always ask)

**This question is mandatory unless the user explicitly requested otherwise (e.g. "pomiń zmianę na ar", "zostaw .pl.srt").**
Ask it every time a translated file has been written, whatever the answers to the earlier steps were: also when the review in step 9 was declined, and also after resuming interrupted work. Skip it only when the output name already ends with `.ar.srt` (or the code the user gave for AI translations).

The user marks AI translations with the code `ar` in the file name instead of the language code. Ask whether to change the language code in the output file name, e.g. `Film.pl.srt` → `Film.ar.srt`. **Default: yes, to `ar`**; the user can also give a different code or keep the name (`.pl.srt`). If the user requested default settings, apply the rename to `.ar.srt` automatically unless they stated to omit this step.
Only the last language code before `.srt` is replaced (`<name>.<code>.srt` or `<name>.<n>.<code>.srt`). If the new name already exists, ask whether to overwrite it or add a number (`<name>.2.ar.srt`). Rename the file with your file tools or the shell (`mv` / `Move-Item`).

## End

Before finishing, check that the step 10 question was asked for **every** translated file (or skipped per user instruction).

Give the path of the finished file and a short summary (number of subtitles, batches, Fix subs result, number of review corrections). Ask whether to delete the video's working folder `$W` (if the user requested default settings or full auto, delete it automatically; otherwise do not delete it without consent). For a series, keep `glossary.md` until the last episode is done.
