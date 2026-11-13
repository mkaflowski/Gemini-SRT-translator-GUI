---
name: translate-subtitles
description: Translate a movie's or episode's subtitles (SRT) the way Gemini SRT Translator does. Asks for the movie folder, finds the subtitles or extracts them from the video file, can merge two subtitle files (e.g. EN+FR) into one bilingual source, looks up the movie, translates in batches, runs "Fix subs", verifies the fixed file and does an AI review with scores before and after. Use when the user wants to translate subtitles for a film or series episode.
---

# Translating movie subtitles

This skill does what the Gemini SRT Translator GUI does, but **you translate** (not an external API).

> [!IMPORTANT]
> **Do NOT use external APIs or API keys** (`google-genai`, `openai`, `GOOGLE_API_KEY`, `GEMINI_API_KEY`...): they are rate-limited and fall back to weaker models. You write every translation yourself (or through subagents) and save it with your file-writing tool.

Scripts are in `scripts/` next to this file (Python 3.9+, no extra packages; `ffmpeg`/`ffprobe` only for subtitles embedded in the video). `$S` below is the absolute path to that folder. Run them with `python` (`python3` on Linux/macOS if needed). All `subs_tool.py` output is JSON.

- `subs_tool.py`: scan, extract, clean, split, status, check, apply, stamp, frames, verify, review-split, review-apply
- `srt_merge.py`: merge two subtitle files into one bilingual source (same module as the GUI)
- `srt_fix.py`: "Fix subs": line breaks, display times, dialogue dashes, joining sentence pieces, Polish typography (same module as the GUI)

`references/` next to this file is read when its step comes, and is the brief handed to subagents:

- `references/translator.md`: how to translate a batch (output contract, `check`, style, format rules, frames). **Read before step 7.3.**
- `references/reviewer.md`: how to review (defects, cross-cue checks, fixes and verdict files). **Read before step 9.**
- `references/glossary-template.md`: the structure of `glossary.md` (step 6).

**Asking the user.** One question at a time, always with the default. In Claude Code use AskUserQuestion with the default first; in other tools (OpenCode, Antigravity) ask in chat and wait. Talk to the user in their language.

**Defaults / full auto** ("użyj domyślnych ustawień", "defaults", "full auto"): take every default without asking, and always finish the whole pipeline: `check`, `apply`, `stamp`, Fix subs, `verify`, the review with scores, the rename to `.ar.srt` (unless the user said to keep `.pl.srt`), and delete `$W` when the episode is done.

**Keep timings** ("nie ruszaj czasów", "zachowaj czasy", manually synced subtitles): pass `--no-timing` to `srt_fix.py` and to `review-apply`.

**Series:** episode by episode, one glossary for the whole season. Steps 3–6 are asked once per season; step 10 is one question listing all the files. Subagents can translate batches (step 7.3) and review them (step 9) to save your context.

## Step 1: Movie folder

Ask for the movie folder (or a video file: take its folder) and run:

```
python "$S/subs_tool.py" scan "<folder>"
```

Show the videos and the `.srt` files with their `language` (`en+fr` = an already merged file). Files with the code `ar` are the user's earlier **AI translations**, not Arabic: never translate from them and don't count them as a language.

`plan` (one per video) holds the defaults for steps 2–3: `base` (English), `reference` / `reference_lang` (best reference in order: **French, Italian, Spanish, Russian**, then others; never Polish or `ar`), `extra_references`, `sdh` / `sdh_track` (English SDH for speaker context) and `extract_tracks` / `extract_langs`. Files and video tracks compete together (a French track beats an `.ita.srt` file); plain files come before SDH, forced tracks are ignored.

## Step 2: Missing subtitles → the video file

If `plan` has `extract_tracks` (no English file, or the video has a better reference language than the files), ask whether to take them from the video. Show `videos[].tracks` (number, language, title, `forced`/`default`/`sdh`, `extractable`; bitmap tracks like PGS can't become SRT). **Default: `extract_tracks`** (plus `plan.sdh_track` when present, saved as `<video>.en.sdh.srt`):

```
python "$S/subs_tool.py" extract "<video>" --tracks 1,3
```

Files are saved as `<video name>.<lang>.srt` (or `.sdh.srt`). On `"status": "exists"` ask whether to overwrite or number (`--on-exists overwrite` / `--on-exists number`), then `scan` again.

## Step 3: Merge subtitle files

Ask whether to merge two files (a bilingual source helps with gender, formality and context). **Default: yes, base `plan.base` (English), reference `plan.reference`**:

```
python "$S/srt_merge.py" "<base.srt>" "<reference.srt>" --lang-base en --lang-other <reference_lang>
```

The result `<name>.en_<lang>.srt` has `{<lang>: ...}` reference lines under each base line and `[en: BRAK NAPISÓW - PRZETŁUMACZ Z POZOSTAŁYCH]` where only the reference has a cue.
- If the first reference has noticeably fewer cues than `base` and `plan.extra_references` has another top language (`fr`/`it`/`es`/`ru`), chain a second merge (`--lang-other it`).
- If an English SDH file (`plan.sdh`) is available alongside plain `base`, attach its speaker labels (`--lang-other sdh` → `{sdh: SPEAKER (NOTE)}`).

## Step 4: Source file

Ask which file to translate from. **Default: the merged file**, otherwise `en`. Then ask whether to remove **song lyrics** (♪) and **notes for the hearing impaired** (`[door slams]`, `(sighs)`, `JOHN:`). **Default: yes.** A merged file is already cleaned: don't ask.

## Step 5: Target language

**Default: Polish (`pl`).** Output: `<video name>.<code>.srt` next to the video (no video: the source name without its language code). If it exists, ask: overwrite or `<name>.2.<code>.srt`.

## Step 6: Movie information

**The glossary** `<folder>/.translate_work/glossary.md` (one per folder / season) holds everything known about the film: later sessions and subagents read it instead of searching again. If it already has a filled "Film" section (resuming, the next episode of a season), read it and skip the search; for a new episode only add its line.

Otherwise, from the file name get the title, year and whether it is a series (`S01E02`, `1x02`). Search TMDB / IMDb / Wikipedia for the plot, the main characters and **their gender**, who uses formal or informal address with whom, the setting and era. Show title, year and 2–4 sentences and ask **whether it is the right film**; if not, ask for a correction and search again. Read `transcription.txt` in the folder too, if there is one.

Then create the glossary from `references/glossary-template.md` (next to this file) and fill in "Film", "Characters" and "Address" with what you found. Keep all its sections; the translation fills the rest.

## Step 7: Batch translation

**Folders.** `$W` = `<folder>/.translate_work/<video name without extension>`. Glossary: `<folder>/.translate_work/glossary.md`.

**Resuming.** If `$W/manifest.json` exists, run `python "$S/subs_tool.py" status --out-dir "$W"`, tell the user how many batches are done and ask: **continue** (default) or start over. To continue, skip cleaning and splitting, read the glossary, use `source` from `status` as `<source.srt>`, go on from `next` and redo every `incomplete` / `broken` batch. To start over, split with `--force`. Never re-split without asking.

1. **Clean** (if agreed in step 4) and from then on use `$W/source.srt` as `<source.srt>` (pass `--sdh-as-ref` so speaker labels like `FATIHA:` or `(SOBBING)` in an unmerged SDH source become `{sdh: ...}` context lines instead of being lost):

   ```
   python "$S/subs_tool.py" clean "<chosen file.srt>" --sdh-as-ref -o "$W/source.srt"
   ```

   Report how many subtitles were removed with a few `removed_examples` (`--keep-music` / `--keep-sdh` keep that part in the visible text).

2. **Split** into batches of about 100, cut at scene breaks (the longest pause in a 70–130 window, preferably after a finished sentence; `--fixed` cuts every N):

   ```
   python "$S/subs_tool.py" split "<source.srt>" --out-dir "$W" --batch-size 100
   ```

   `split` refuses batches over 150: in long batches a model loses track of the cues and shifts the translations. Don't work around it.

3. **Translate.** First read **`references/translator.md`** (next to this file) in full: the batch format, the `out_NNN.json` contract, the translation style, the format rules and how to look at the video for unclear lines. Then, for each `batch_NNN.json` **in order**:

   1. read the glossary,
   2. translate and save `out_NNN.json` (one item per source cue, each with its `src` anchor),
   3. run `python "$S/subs_tool.py" check --out-dir "$W" --batch N` and fix until clean,
   4. update the glossary and report progress ("batch 3/7 done").

   **Subagents** (to save your context on a long film or a season): give a subagent the absolute path of `references/translator.md`, `$S`, `$W`, the glossary, `<source.srt>`, the video, the batch numbers (2–3 consecutive batches each) and any wish of the user about style ("faithful"). **One subagent at a time, in order**: every batch builds on the previous one and on the glossary, so parallel translators drift apart in names, gender and `ty`/`pan`. When it returns, run `status` yourself and read what it added to the glossary before starting the next one.

4. **After the last batch**, check everything once more, with whole-film coverage:

   ```
   python "$S/subs_tool.py" check --out-dir "$W" --srt "<source.srt>"
   ```

5. **Build** the file (timings come from the source). `apply` refuses on any count, index, empty or anchor error:

   ```
   python "$S/subs_tool.py" apply "<source.srt>" --out-dir "$W" -o "<output.srt>"
   ```

6. **Always** add the translator info as the first subtitle, with the model actually translating, i.e. you (`Gemini 3 Pro`, `Claude Opus 5.5`...), not the tool. Running it again replaces it:

   ```
   python "$S/subs_tool.py" stamp "<output.srt>" --model "<your model name>"
   ```

## Step 8: Fix subs

```
python "$S/srt_fix.py" "<output.srt>" --source "<source.srt>"
```

It rewraps lines, fixes display times and dialogue dashes (using `--source` to settle doubtful dashes), joins sentence fragments split across adjacent cues (`--no-join-fragments` turns it off), applies Polish typography (`„...”`, `?..`, `!..`), and borrows display time from neighbours when a cue is too short (`--no-timing` leaves times untouched). `--clean-music` / `--clean-sdh` remove any leftover lyrics or SDH notes.

Show the user the short report. `⏱` lines list subtitles still too short with no room around them; condense long ones in the review.

## Step 8b: Verify (always)

```
python "$S/subs_tool.py" verify "<output.srt>" --json-out "$W/verify.json"
```

Each item has the `index` used by `review-apply`:
- `timing`, `empty`, `too_many_lines`, `line_too_long`, `tags`, `leftovers`: a broken file (`"ok": false`); fix before going on.
- `midsentence_capital`: a capital right after a comma, usually a joined cue whose second part started a sentence. Rewrite it as one sentence.
- `hanging_word`: a line ending in a one-letter word or short preposition (`w`, `z`, `i`, `na`, `do`...); move it to the next line.
- `too_fast`: condense where no meaning is lost; where condensing would lose the sense of the sentence, leave it slightly over (meaning has priority). `spacing`, `quotes`: small typography fixes (`„”` for Polish).
- `filler`: hesitation sounds (`eee`, `yyy`, `hmm`, `um`, `uh`). Subtitles leave them out: the viewer hears them, and `...` shows the pause (*"Nie, to raczej... eee..."* → *"Nie, to raczej..."*). A cue that is only a hesitation (*"Yyy..."*) is deleted with `{"index": "116", "delete": true}` in `fixes.json`. Keep one only when the hesitation itself matters (a joke about someone fumbling, another character reacting to it), written once as `yyy` or `eee`.

Read every joined cue in context as well: it must read as one natural sentence, without a doubled word, a dangling conjunction or punctuation left from the old break. Put corrections into `$W/fixes.json` and apply them with `review-apply` (step 9).

## Step 9: AI review (correction)

Ask whether to review. **Default: yes** (always in full auto).

```
python "$S/subs_tool.py" review-split "<output.srt>" "<source.srt>" --out-dir "$W" --batch-size 150
```

First read **`references/reviewer.md`** (next to this file) in full: the `review_NNN.json` items, what counts as a defect, what to look for across cues, and the output files. Then read **every** batch and write for each:

- the corrections, added to `$W/fixes.json` (the same file as the step 8b corrections),
- its verdict `$W/reviewed_NNN.json` = `{"first": "<first index>", "last": "<last index>", "defects": N}`, also when N is 0.

**Subagents** (to save your context): reviewer subagents may run **in parallel**, 1–2 batches each. Give each the absolute path of `references/reviewer.md`, `$S`, `$W`, the glossary, `<output.srt>`, the video and its batch numbers. Each writes `$W/fixes_NNN.json` and `$W/reviewed_NNN.json` and reports its defects per category and anything concerning other batches. A subagent sees only its own stretch, so the **whole-film** check stays with you: from their reports and the glossary, make sure names, terms, `ty`/`pan` and recurring lines (`repeated_at`) are the same from the first batch to the last, and add those corrections to `$W/fixes.json`.

**Scores.** Before applying, score **fidelity**, **Polish naturalness** and **overall** from 1 to 10; after applying, score again. Show both in a short table, then the main meaning errors (before → after, why) and the main unnatural phrasings.

Show the user a short list of the corrections (category, before → after), then apply all the fixes files in one call:

```
python "$S/subs_tool.py" review-apply "<output.srt>" "$W/fixes.json" "$W/fixes_001.json" "$W/fixes_002.json"
```

`review-apply` reruns Fix subs (text lengths changed) and `verify`, and reports `review` (batches reviewed, defects reported). Don't edit the file by hand afterwards. It refuses when:

- **a review batch has no valid verdict** (`not_reviewed`): read that batch and write its `reviewed_NNN.json`. `--partial` only when the review was really not done (the user declined or stopped it after `review-split`) and you are applying the other fixes; say so in the summary.
- **`<output.srt>` changed after `review-split`** (another session, an editor, Fix subs again), because the indexes may point at other cues: run `review-split` again and match your fixes by text. `--force` only if you are sure no cue moved.

If more than a few `too_fast` / `quotes` are still reported, fix them (except `too_fast` cues left over on purpose to keep the meaning) in a **new** file (`$W/fixes_round2.json`, indexes from the fresh `verify`) and apply only that one. Never apply the same fixes file twice: after a `delete` the indexes have moved.

## Step 10: Mark the file as an AI translation (always ask)

**Mandatory** unless the user said otherwise ("pomiń zmianę na ar", "zostaw .pl.srt"): ask every time a translated file was written, also when the review was declined and after resuming. Skip it only when the name already ends with `.ar.srt` (or the user's code).

The user marks AI translations with `ar` instead of the language code. Ask whether to rename `Film.pl.srt` → `Film.ar.srt`. **Default: yes** (automatic in full auto); another code or keeping the name are possible. Only the last code before `.srt` changes (`<name>.<code>.srt`, `<name>.<n>.<code>.srt`). If the new name exists, ask: overwrite or `<name>.2.ar.srt`.

## End

Check that the step 10 question was asked for **every** translated file. Give the file path and a short summary: subtitles, batches, Fix subs result, `verify` result, review corrections (and review batches read, from `review-apply`), scores before and after. Ask whether to delete `$W` (automatic in full auto). For a series keep `glossary.md` until the last episode.
