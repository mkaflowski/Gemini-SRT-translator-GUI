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

**Asking the user.** One question at a time, always with the default. In Claude Code use AskUserQuestion with the default first; in other tools (OpenCode, Antigravity) ask in chat and wait. Talk to the user in their language.

**Defaults / full auto** ("użyj domyślnych ustawień", "defaults", "full auto"): take every default without asking, and always finish the whole pipeline: `check`, `apply`, `stamp`, Fix subs, `verify`, the review with scores, the rename to `.ar.srt` (unless the user said to keep `.pl.srt`), and delete `$W` when the episode is done.

**Keep timings** ("nie ruszaj czasów", "zachowaj czasy", manually synced subtitles): pass `--no-timing` to `srt_fix.py` and to `review-apply`.

**Series:** episode by episode, one glossary for the whole season. Steps 3–6 are asked once per season; step 10 is one question listing all the files. Subagents can translate batches to save your context.

## Step 1: Movie folder

Ask for the movie folder (or a video file: take its folder) and run:

```
python "$S/subs_tool.py" scan "<folder>"
```

Show the videos and the `.srt` files with their `language` (`en+fr` = an already merged file). Files with the code `ar` are the user's earlier **AI translations**, not Arabic: never translate from them and don't count them as a language.

## Step 2: No subtitles → the video file

If there is no `en` and no `fr` in `languages_present`, ask whether to look inside the video. Show `videos[].tracks` (number, language, title, `forced`/`default`, `extractable`; bitmap tracks like PGS can't become SRT) and ask which to extract (default: all `en`/`fr` text tracks):

```
python "$S/subs_tool.py" extract "<video>" --tracks 0,2
```

Files are saved as `<video name>.<lang>.srt`. On `"status": "exists"` ask whether to overwrite or number the file (`--on-exists overwrite` / `--on-exists number`), then `scan` again.

## Step 3: Merge two subtitle files

Ask whether to merge two files (a bilingual source helps with gender, formality and context). **Default: base `en`, reference `fr`.**

```
python "$S/srt_merge.py" "<base.srt>" "<reference.srt>" --lang-base en --lang-other fr
```

The result `<name>.en_fr.srt` has a `{fr: ...}` reference line under each base line, and the marker `[en: BRAK NAPISÓW - PRZETŁUMACZ Z POZOSTAŁYCH]` where only the reference has the moment. The reference is synced automatically.

## Step 4: Source file

Ask which file to translate from. **Default: the merged file**, otherwise `en`. Then ask whether to remove **song lyrics** (♪) and **notes for the hearing impaired** (`[door slams]`, `(sighs)`, `JOHN:`). **Default: yes.** A merged file is already cleaned: don't ask.

## Step 5: Target language

**Default: Polish (`pl`).** Output: `<video name>.<code>.srt` next to the video (no video: the source name without its language code). If it exists, ask: overwrite or `<name>.2.<code>.srt`.

## Step 6: Movie information

From the file name get the title, year and whether it is a series (`S01E02`, `1x02`). Search TMDB / IMDb / Wikipedia for the plot, the main characters and **their gender**, who uses formal or informal address with whom, the setting and era. Show title, year and 2–4 sentences and ask **whether it is the right film**; if not, ask for a correction and search again. Read `transcription.txt` in the folder too, if there is one.

## Step 7: Batch translation

**Folders.** `$W` = `<folder>/.translate_work/<video name without extension>`. Glossary (shared by the folder / season): `<folder>/.translate_work/glossary.md`.

**Resuming.** If `$W/manifest.json` exists, run `python "$S/subs_tool.py" status --out-dir "$W"`, tell the user how many batches are done and ask: **continue** (default) or start over. To continue, skip cleaning and splitting, use `source` from `status` as `<source.srt>`, go on from `next` and redo every `incomplete` / `broken` batch. To start over, split with `--force`. Never re-split without asking.

1. **Clean** (if agreed in step 4) and from then on use `$W/source.srt` as `<source.srt>`:

   ```
   python "$S/subs_tool.py" clean "<chosen file.srt>" -o "$W/source.srt"
   ```

   Report how many subtitles were removed with a few `removed_examples` (`--keep-music` / `--keep-sdh` keep that part).

2. **Split** into batches of about 100, cut at scene breaks (the longest pause in a 70–130 window, preferably after a finished sentence; `--fixed` cuts every N):

   ```
   python "$S/subs_tool.py" split "<source.srt>" --out-dir "$W" --batch-size 100
   ```

   `split` refuses batches over 150: in long batches a model loses track of the cues and shifts the translations. Don't work around it.

3. **Translate** each `batch_NNN.json` in order. It has `translate` (the subtitles, each with `max_chars`, its reading budget), `context_before` (last 10 of the previous batch) and `context_after` (first 5 of the next). The context is **read-only**: use it so a sentence cut by the batch boundary keeps its meaning, and read your own translation of `context_before` in the previous `out_NNN.json` so wording, gender and formality carry on. Translate only the `translate` items.

   Save `out_NNN.json` (UTF-8, with your file-writing tool) as a list of `{"index", "src", "text"}`, one item per `translate` item, same `index`, same order. **`src`** is the first 3 words of that item's source text, copied as they are (skip `{xx: ...}` lines; a shorter cue: all of it). Write `index` and `src` first, looking at the source, then the translation:

   ```json
   {"index": "280", "src": "John: Wow.", "text": "Wow."}
   ```

   > [!CRITICAL]
   > **One source cue = one translated item. Never merge, split, skip or reorder cues.** Each cue has its own time on screen. Merging cues 243+244 shifts every later translation one cue early, and splitting another cue later only hides it (the count is right, everything in between is shifted). If a sentence runs over two cues, its first part stays in the first and the rest in the second. Never leave `text` empty when the source has text. The `src` anchor is what catches a shift.

4. **Check the batch right after writing it**, before the next one builds on it:

   ```
   python "$S/subs_tool.py" check --out-dir "$W" --batch 3
   ```

   - `src anchor mismatch` (error): the translations are shifted from the reported cue (`it is the text of cue 281`). Don't patch single cues: from the first wrong cue to the end of the batch, compare with the source one by one and retranslate or move the texts back, with a correct `src` for each.
   - `count mismatch`, `index mismatch`, `empty translation` (errors): merged or missing cues, same fix.
   - `shift_suspects` (warning): a run of cues whose texts fit the neighbouring source cues better than their own (`offset: 1` = each cue has the next cue's text). Look at it; if every text matches its own cue, go on.
   - `too long to read`: over `max_chars`; condense it.
   - copied `{xx: ...}` lines or the marker, ` / ` instead of a line break, `—`/`–`, unbalanced tags.

   Fix and rerun until clean. Then update the glossary (names, nicknames, gender, who says `ty`/`pan` to whom, recurring terms) and report progress ("batch 3/7 done"). Read the glossary before every batch. If your context is getting full, say so: a new session resumes from `status`.

5. **After the last batch**, check everything once more, with whole-film coverage:

   ```
   python "$S/subs_tool.py" check --out-dir "$W" --srt "<source.srt>"
   ```

6. **Build** the file (timings come from the source). `apply` refuses on any count, index, empty or anchor error:

   ```
   python "$S/subs_tool.py" apply "<source.srt>" --out-dir "$W" -o "<output.srt>"
   ```

7. **Always** add the translator info as the first subtitle, with the model actually translating, i.e. you (`Gemini 3 Pro`, `Claude Opus 5.5`...), not the tool. Running it again replaces it:

   ```
   python "$S/subs_tool.py" stamp "<output.srt>" --model "<your model name>"
   ```

### Unclear lines: look at the video

When the text can't tell you the speaker's or listener's gender ("You're tired" → "zmęczony"/"zmęczona"), who talks to whom and how formally, whether "you" is one person or several, or what an on-screen text says:

```
python "$S/subs_tool.py" frames "<source.srt>" "<video>" --index 214,388 --out-dir "$W/frames"
```

One image per subtitle: a 3×2 grid at **-5s, 0, +5s / +10s, +20s, +60s**, each labelled top-left, with the subtitle shown at that moment drawn at the bottom. Far frames may be another shot: use them for who is around, not for who spoke. `--offsets=-2,0,2` picks other moments (note the `=`), `--no-text` leaves the subtitles out. An image costs about 60 subtitle lines: use it only when really unsure, at most ~**20 per film** (more if the user agrees), several indexes per call, and write what you learned into the glossary. No video, or your tool refuses an image: decide from the text.

### Translation style: film subtitles, not a transcript

A subtitle gives **the meaning, intent and tone** of a line, not every word: the viewer reads it while watching. A good Polish subtitle is usually clearly shorter than a word-for-word translation; dropping a quarter or a third of the words is normal.

**Budget.** `max_chars` is what a viewer reads in the subtitle's time (17 characters per second, spaces count, tags don't), for both lines together. Over it: condense. Well under it: keep the full meaning, don't cut for the sake of it.

**Drop or shorten** when the scene doesn't need it: filler (*well, you know, I mean, like, okay, uh, look*), stutters and false starts; repetitions (*"No, no, no!"* → *"Nie!"*); subject pronouns (*"Nie wiem"*, not *"Ja nie wiem"*); names and forms of address once it's clear who talks to whom; tag questions (*right?, okay?*) that add nothing; what the picture shows; long-winded wording (*"Do you think you could possibly help me?"* → *"Pomożesz mi?"*).

**Never drop:** information the story needs (facts, names on first mention, numbers, times, places), negations and conditions, the point of a joke, irony, threats and promises, the character's register and emotion.

**Natural Polish, not a calque.**
- Polish idioms: *"It's raining cats and dogs"* → *"Leje jak z cebra"*, *"I'm on it"* → *"Już się robi"*.
- Wordplay that can't be kept: a Polish joke with the same function. Unknown cultural references: a general equivalent (*"a real Benedict Arnold"* → *"zdrajca"*).
- Units: metric when the exact number doesn't matter (*"a mile away"* → *"kawał drogi stąd"*); keep numbers that matter for the plot or joke. Money keeps its currency.
- Swearing: natural Polish of the same strength, varied, never stronger than the original.
- **Echoes and callbacks:** when a speaker repeats or questions a word from the previous line (*"The word for that is 'providence'."* → *"The word for what?"*), both lines must use the same Polish word (*"Właściwe słowo to «opatrzność»."* → *"Słowo na co?"*). A free paraphrase of the first line breaks the exchange.

**Faithful translation** on request ("dokładne", "wierne", "bez skracania"): translate everything, drop only pure filler, still within 2 × 42 characters.

### Format rules

- Keep tags (`<i>`, `<b>`) and line breaks; a line break is always `\n`, never ` / `.
- Plain hyphen `-` for dialogue and pauses, never `—`/`–`. Two speakers: each on their own line starting with `- `.
- At most 2 lines of ≤ 42 characters (50 at worst), broken at phrase boundaries or after punctuation; correct spacing.
- Gender and formality follow the characters (glossary, movie description).
- An empty source `text` stays empty.
- **Merged source:** `{xx: ...}` lines are only a reference (gender, formality, number, idioms, who speaks to whom). For the `[en: BRAK NAPISÓW ...]` marker, translate the reference line. The output contains **only** the Polish subtitle: never the `{xx: ...}` lines or the marker.

## Step 8: Fix subs

```
python "$S/srt_fix.py" "<output.srt>" --source "<source.srt>"
```

It rewraps lines, fixes display times and dialogue dashes, and by default also:
- **joins pieces of one sentence** that US closed captions cut into quick flashes ("Powiem starym," / "że to stypendium w Nowym Jorku," / "i spadam stąd."; "Musisz tak..." / "siorbać i stękać?"): the pause must be under 0.3 s, no dialogue, same italics, and together they fit 2 × 42 characters, 7 s and the reading speed. A next piece starting a new sentence with a capital is never joined. The joined subtitle spans both times, so nothing moves against the speech. This is the only place cues are merged (`--no-join-fragments` turns it off);
- **Polish typography:** `"..."` and `“...”` → `„...”`, `?...` / `!...` → `?..` / `!..`;
- **takes time from a neighbour** for a subtitle too short to read when the neighbour has more than it needs.

`--source` settles doubtful dialogue dashes ("- Smakuje mi. / Trochę słone.") by checking whether the speaker changes in the source. `--clean-music` / `--clean-sdh` remove lyrics / hearing-impaired notes left in the file. With `--no-timing` times are not touched (and nothing is joined).

Show the user the short report. `⏱` lines list subtitles still too short with no room around them; usually they are fine, but a long text there can be condensed in the review.

## Step 8b: Verify (always)

```
python "$S/subs_tool.py" verify "<output.srt>" --json-out "$W/verify.json"
```

Each item has the `index` used by `review-apply`:
- `timing`, `empty`, `too_many_lines`, `line_too_long`, `tags`, `leftovers`: a broken file (`"ok": false`); fix before going on.
- `midsentence_capital`: a capital right after a comma, usually a joined cue whose second part started a sentence. Rewrite it as one sentence.
- `hanging_word`: a line ending in a one-letter word or short preposition (`w`, `z`, `i`, `na`, `do`...); move it to the next line.
- `too_fast`: condense where no meaning is lost. `spacing`, `quotes`: small typography fixes (`„”` for Polish).

Read every joined cue in context as well: it must read as one natural sentence, without a doubled word, a dangling conjunction or punctuation left from the old break. Put corrections into `$W/fixes.json` and apply them with `review-apply` (step 9).

## Step 9: AI review (correction)

Ask whether to review. **Default: yes** (always in full auto).

```
python "$S/subs_tool.py" review-split "<output.srt>" "<source.srt>" --out-dir "$W" --batch-size 150
```

Each `review_NNN.json` item has `index`, `original`, `translation` and `cps` when it reads faster than 17 characters per second. Review like a senior subtitle editor, **reading dialogue sequences in context** (yourself or with reviewer subagents), never as a regex or CPS pass. For gender doubts use `frames` with `<output.srt>` (the indexes are those of the translated file). Report only **real** defects:

- `translation`: wrong meaning, lost information, wrong name/number/unit
- `coherence`: an answer that doesn't fit the question, a lost echo or callback between speakers
- `hallucination`: content not in the original
- `spelling`: spelling, grammar, punctuation, typos
- `unnatural`: correct but not what a Pole would say (calque, word order, register)
- `gender`: wrong gender, number or formality of the speaker or the listener
- `condense`: too long to read (`cps`) where a shorter wording keeps the meaning, or a word-for-word rendering of something a subtitler would say in a few words

Condensing and dropping filler are **not** defects; dropping information the story needs **is**. An acceptable alternative is not a defect. Don't invent defects.

**Read the whole film.** The defects left most often are visible only across cues:
- an answer that doesn't fit the question (*"Nie kucharzem. Pisarzem."* after *"Młody mówi, że gotuje"* → the question must say *"jest kucharzem"*),
- a running joke or mispronounced word that must stay the same each time (*"building's Roman"*; don't "fix" it the first time),
- callbacks between distant scenes (*"Czarne nie znaczy przypalone"* early and at the end),
- tense and person (*"I think I got it"* → *"chyba je dostałem"*, not *"dostanę"*),
- agreement across a line break (*"na kałamarnice, od którego"* → *"na gulasz z kałamarnicy, od którego"*),
- `pan`/`ty` changing inside one conversation for no reason,
- signs and letters written the Polish way (*"To whom it may concern"* → *"Szanowni Państwo,"*).

**Polish naturalness**, typical calques: *"Przysięgam"* for a casual *"I swear"* (→ *"Serio"*, *"Mówię ci"*), *"Ta, ta, ta"* (→ *"Tak, tak"*), stiff words in casual speech (*"wadzi"*), *"Start o 11:00"* (→ *"Zaczynamy o 11:00"*), a swear word without commas (→ *"Kurwa, ideał."*), an object English left implied (*"Rzucam to"* → *"Rzucam studia"*), food names word for word (*"basy pasiaste"* → *"bassy"*).

**Scores.** Before applying, score **fidelity**, **Polish naturalness** and **overall** from 1 to 10; after applying, score again. Show both in a short table, then the main meaning errors (before → after, why) and the main unnatural phrasings.

Corrections go into `$W/fixes.json` as `[{"index": "12", "text": "the whole corrected subtitle (with \n)", "category": "gender", "problem": "..."}]`; show the user a short list (category, before → after), then:

```
python "$S/subs_tool.py" review-apply "<output.srt>" "$W/fixes.json"
```

`review-apply` reruns Fix subs (text lengths changed) and `verify`. If more than a few `too_fast` / `quotes` are reported, fix them the same way until clean. Don't edit the file by hand afterwards. It refuses when `<output.srt>` changed after `review-split` (another session, an editor, Fix subs again), because the indexes may point at other cues: run `review-split` again and match your fixes by text. `--force` only if you are sure no cue moved.

## Step 10: Mark the file as an AI translation (always ask)

**Mandatory** unless the user said otherwise ("pomiń zmianę na ar", "zostaw .pl.srt"): ask every time a translated file was written, also when the review was declined and after resuming. Skip it only when the name already ends with `.ar.srt` (or the user's code).

The user marks AI translations with `ar` instead of the language code. Ask whether to rename `Film.pl.srt` → `Film.ar.srt`. **Default: yes** (automatic in full auto); another code or keeping the name are possible. Only the last code before `.srt` changes (`<name>.<code>.srt`, `<name>.<n>.<code>.srt`). If the new name exists, ask: overwrite or `<name>.2.ar.srt`.

## End

Check that the step 10 question was asked for **every** translated file. Give the file path and a short summary: subtitles, batches, Fix subs result, `verify` result, review corrections, scores before and after. Ask whether to delete `$W` (automatic in full auto). For a series keep `glossary.md` until the last episode.
