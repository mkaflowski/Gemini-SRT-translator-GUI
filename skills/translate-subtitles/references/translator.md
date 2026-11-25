# Translating a batch

Read this before translating, in every session (also after resuming). It is also the whole brief for a subagent that translates batches: together with the paths (`$S` scripts, `$W` work folder, the glossary, the source file, the video) and the batch numbers, it is all the subagent needs.

**You translate.** Never call an external API or use an API key; write every translation yourself and save it with your file-writing tool.

## Before each batch

1. Read the glossary (`<folder>/.translate_work/glossary.md`): the film, the characters and their gender, who says `ty`/`pan` to whom, the terms and recurring lines already settled.
2. Read `$W/batch_NNN.json`:
   - `translate`: the subtitles to translate. Each has `max_chars` (its reading budget) and sometimes `earlier_cue` (the first occurrence of a recurring quote in the film).
   - `context_before` (last 10 of the previous batch) and `context_after` (first 5 of the next): **read-only**, so a sentence cut by the batch boundary keeps its meaning.
3. Read your translation of `context_before` in the previous `out_NNN.json`, so wording, gender and formality carry on.
4. For an item with `earlier_cue`, look up how that cue was translated earlier: recurring quotes and bookend scenes use the exact same Polish wording.

Batches are translated **in order, one at a time**: each builds on the previous one.

## Output: `out_NNN.json`

A UTF-8 list of `{"index", "src", "text"}`, one item per `translate` item, same `index`, same order. **`src`** is the first 3 words of that item's source text, copied as they are (skip `{xx: ...}` lines; a shorter cue: all of it). Write `index` and `src` first, looking at the source, then the translation:

```json
{"index": "280", "src": "John: Wow.", "text": "Wow."}
```

> [!CRITICAL]
> **One source cue = one translated item. Never merge, split, skip or reorder cues.** Each cue has its own time on screen. Merging cues 243+244 shifts every later translation one cue early, and splitting another cue later only hides it (the count is right, everything in between is shifted). If a sentence runs over two cues, its first part stays in the first and the rest in the second. Never leave `text` empty when the source has text. The `src` anchor is what catches a shift.

## Check the batch right after writing it

Before the next batch builds on it:

```
python "$S/subs_tool.py" check --out-dir "$W" --batch 3
```

- `src anchor mismatch` (error): the translations are shifted from the reported cue (`it is the text of cue 281`). Don't patch single cues: from the first wrong cue to the end of the batch, compare with the source one by one and retranslate or move the texts back, with a correct `src` for each.
- `count mismatch`, `index mismatch`, `empty translation` (errors): merged or missing cues, same fix.
- `shift_suspects` (warning): a run of cues whose texts fit the neighbouring source cues better than their own (`offset: 1` = each cue has the next cue's text). Look at it; if every text matches its own cue, go on.
- `too long to read`: over `max_chars`; condense it, unless the shorter wording would lose the sense of the sentence (then leave it slightly over).
- copied `{xx: ...}` lines or the marker, ` / ` instead of a line break, `—`/`–`, unbalanced tags.

Fix and rerun until clean. Then **update the glossary** with what this batch settled (see its sections: new characters and their gender, `ty`/`pan` pairs, terms, recurring lines, decisions) and report progress ("batch 3/7 done"). If your context is getting full, say so: a new session resumes from `status`.

## Unclear lines: look at the video

When the text can't tell you the speaker's or listener's gender ("You're tired" → "zmęczony"/"zmęczona"), who talks to whom and how formally, whether "you" is one person or several, or what an on-screen text says:

```
python "$S/subs_tool.py" frames "<source.srt>" "<video>" --index 214,388 --out-dir "$W/frames"
```

One image per subtitle: a 3×2 grid at **-5s, 0, +5s / +10s, +20s, +60s**, each labelled top-left, with the subtitle shown at that moment drawn at the bottom. Far frames may be another shot: use them for who is around, not for who spoke. `--offsets=-2,0,2` picks other moments (note the `=`), `--no-text` leaves the subtitles out. An image costs about 60 subtitle lines: use it only when really unsure, at most ~**20 per film** (more if the user agrees), several indexes per call, and write what you learned into the glossary ("Decisions"). No video, or your tool refuses an image: decide from the text.

## Translation style: film subtitles, not a transcript

A subtitle gives **the meaning, intent and tone** of a line, not every word: the viewer reads it while watching. A good Polish subtitle is usually clearly shorter than a word-for-word translation; dropping a quarter or a third of the words is normal.

**Budget.** `max_chars` is what a viewer reads in the subtitle's time (17 characters per second, spaces count, tags don't), for both lines together. Over it: condense. Well under it: keep the full meaning, don't cut for the sake of it. **The sense of the sentence has priority over the budget:** drop fillers, repetitions and names first, but when the only shorter wording loses or distorts what is said, keep the meaning and go slightly over the limit.

**Drop or shorten** when the scene doesn't need it: filler (*well, you know, I mean, like, okay, look*) and hesitation sounds (*uh, um, eee, yyy*: a `...` shows the pause), stutters and false starts; repetitions (*"No, no, no!"* → *"Nie!"*); subject pronouns (*"Nie wiem"*, not *"Ja nie wiem"*); names and forms of address once it's clear who talks to whom; tag questions (*right?, okay?*) that add nothing; what the picture shows; long-winded wording (*"Do you think you could possibly help me?"* → *"Pomożesz mi?"*).

**Never drop:** information the story needs (facts, names on first mention, numbers, times, places), the subject or key noun that the next line reacts to (e.g. *"physician's assistants"*, *"a pimp"*), negations and conditions (*"If I had missed the burial, then so be it"* must keep the condition, not just *"Trudno"*), the point of a joke, irony, threats and promises, the character's register and emotion.

**Sentences split across two cues:** make sure both halves form one grammatical Polish sentence when read together—never drop the verb from both (*"Don't you ever put your hands..."* / *"- on my daughter again!"* → *"Nie waż się..."* / *"- dotknąć córki!"*, not *"Nigdy więcej... do mojej córki!"*).

**Rebuild the sentence, not each fragment.** Before translating a cue that ends mid-sentence, read the whole sentence to its end (it may run over 2–4 cues) and decide how it goes in Polish; then share it out over the same cues. The cue count and each cue's time stay as they are, but the Polish wording of every part may change, and a cue may end on a different word than the English one does. Never carry over an English construction just because the English cue breaks there:
- *"- Okay."* / *"- What I was doing wrong is"* | *"he doesn't think that I'm mixing them"* | *"- vigorously enough."* → *"- Okej."* / *"- Mój błąd polegał na tym, że"* | *"według niego mieszam je"* | *"- za słabo."*, not *"- Robiłam źle to, że"* (a literal *"what… is"*).
- *"So part of me maybe"* | *"is a little insecure that"* → *"Więc może"* | *"trochę się obawiam, że"*, not *"Więc po części"* | *"czuję się trochę niepewnie, że"*.

**Spontaneous speech** (documentaries, interviews, reality TV, home footage): people start a sentence, change course and stack fillers (*"The world believes a reality about me that isn't true"*, *"What I was doing wrong is he doesn't think…"*). Translate what the speaker means in correct, spoken Polish: tidy the broken syntax as a Polish speaker would say it, don't reproduce it word by word (*"Świat wierzy w obraz mnie, który nie jest prawdziwy"*, not *"Świat wierzy w pewną rzeczywistość o mnie"*). Keep false starts only when they matter (hesitation before a confession, someone interrupted). Watch for English words with no direct Polish twin: *insecure / an insecurity* → *"mam kompleks"*, *"obawiam się"*, not *"niepewność"*; *"Pick your piece wisely"* → *"Dobrze wybierz kawałek"*; *"There's not just plans, there's plans in the works"* → *"To nie tylko plany, już nad nimi pracujemy"*.

**Translating from a reference line** (`[en: BRAK NAPISÓW ...]` marker): there is no English to lean on, so be extra careful with words whose meaning depends on context (Spanish *"Así sabemos…"* = *"Dzięki temu wiemy…"*, not *"Tak wiemy…"*). Read the cues around it in both languages first.

**Register follows who speaks**, not only the film: a news anchor, a quoted newspaper article or a court ruling uses neutral written Polish (*"zacznie odbywać karę"*, *"został oskarżony"*, *"patologiczna kłamczyni"*); casual words (*"odsiadka"*, *"ma zarzuty"*, *"kłamczucha"*) only in casual speech. The glossary's "Registers by source" line says which voice gets which register.

**Natural Polish, not a calque.**
- Polish idioms: *"It's raining cats and dogs"* → *"Leje jak z cebra"*, *"I'm on it"* → *"Już się robi"*.
- Wordplay that can't be kept: a Polish joke with the same function across all cues of the exchange (e.g. *"but"* / *"butt"* + *"No God but God"* → *"tylko"* / *"tyłki"* + *"Nie ma boga, tylko Bóg"*). Unknown cultural references: a general equivalent (*"a real Benedict Arnold"* → *"zdrajca"*).
- Units: metric when the exact number doesn't matter (*"a mile away"* → *"kawał drogi stąd"*); keep numbers that matter for the plot or joke. Money keeps its currency, written the Polish way after the number: *"60 mld dolarów"*, *"pół miliarda dolarów"*, *"200 euro"*, never *"$60 mld"* or *"60 mld $"*.
- **Titles of films, series, books, songs and shows:** a Polish title only when the glossary ("Titles and real-world references") or a check you made says it is the official Polish title; otherwise keep the original title. Never make up a Polish title or reuse the title of a related work (*"The Dropout"* stays *"The Dropout"*, not *"Zepsuta krew"*). Inner titles inside a quotation go in `»...«`. Add each title you settle to the glossary.
- **Technical and legal terms:** translate the thing, not the words (*"patient testing"* → *"badania krwi pacjentów"*, not *"badania na pacjentach"*, which means experimenting on patients). When unsure what a term means in the film's field, check it.
- **Numbers and quantities:** never drop a number to fit the reading budget; use digits (*"45?"*) if words are too long.
- **Swearing and crude slang — never bowdlerize:** natural Polish of the same strength and register, never stronger, but **never sanitize or euphemize** sexual slang or insults (*"got his first hard on"* → *"pierwszy raz mu stanął"*, not *"poczuł krew"*; *"best fucking head"* → *"najlepszy lód"*, not *"najlepsza zabawa"*; *"side-chick"* → *"kochanka na zastępstwie"*).
- **Echoes and callbacks:** when a speaker repeats or questions a word from the previous line (*"The word for that is 'providence'."* → *"The word for what?"*), both lines must use the same Polish word (*"Jest na to słowo: «opatrzność»."* → *"Jakie słowo? Na co?"*).
- **Short follow-up questions:** English points back with a bare preposition (*"Of what?"*, *"For what?"*, *"Like what?"*); in Polish a bare *"Czego?"* loses the referent and sounds rude. Repeat the noun or use the natural Polish question: *"I need a photo." - "Of what?"* → *"Zdjęcie czego?"*; *"Like what?"* → *"Na przykład?"* / *"Co takiego?"*; *"For what?"* → *"Po co?"*. Always read a 1–2 word question together with the preceding line.

**Faithful translation** when the user asked for it ("dokładne", "wierne", "bez skracania"): translate everything, drop only pure filler, still within 2 × 42 characters.

## Format rules

- Keep tags (`<i>`, `<b>`) and line breaks; a line break is always `\n`, never ` / `.
- Plain hyphen `-` for dialogue and pauses, never `—`/`–`. Two speakers: each on their own line starting with `- `.
- At most 2 lines of ≤ 42 characters (50 at worst), broken at phrase boundaries or after punctuation; correct spacing.
- Gender and formality follow the characters (glossary).
- An empty source `text` stays empty.
- **Merged source:** `{xx: ...}` lines are only a reference (gender, formality, number, idioms, who speaks to whom; `{sdh: ...}` names the speaker). For the `[en: BRAK NAPISÓW ...]` marker, translate the reference line. The output contains **only** the Polish subtitle: never the `{xx: ...}` lines or the marker.
