# Reviewing the translation

Read this before the review. It is also the whole brief for a reviewer subagent: together with the paths (`$S` scripts, `$W` work folder, the glossary, `<output.srt>`, the video) and the batch numbers, it is all the subagent needs.

Review like a senior subtitle editor, **reading dialogue sequences in context**, never as a regex or CPS pass. Read the glossary first (`<folder>/.translate_work/glossary.md`): the film, the characters and their gender, `ty`/`pan`, the settled terms and recurring lines.

## Input: `review_NNN.json`

Each item has:
- `index`: the cue number in `<output.srt>`, used by the fixes,
- `original`: all overlapping source cues, with `{xx: ...}` reference lines and `{sdh: SPEAKER (NOTE)}` taken from the English SDH track or file: use it to verify who speaks (`FATIHA:` vs `LATIF:`),
- `translation`,
- `cps` (only when over 17 characters per second),
- `repeated_at` (only when the same source line recurs elsewhere in the film, with the translation used there).

For a visual doubt left after that, look at the video: `python "$S/subs_tool.py" frames "<output.srt>" "<video>" --index 214 --out-dir "$W/frames"`.

## What is a defect

Report only **real** defects:

- `translation`: wrong meaning, lost information, wrong name/number/unit
- `coherence`: an answer that doesn't fit the question, a lost echo or callback between speakers
- `hallucination`: content not in the original
- `spelling`: spelling, grammar, punctuation, typos
- `unnatural`: correct but not what a Pole would say (calque, word order, register)
- `gender`: wrong gender, number or formality of the speaker or the listener
- `condense`: too long to read (`cps`) where a shorter wording keeps the meaning, or a word-for-word rendering of something a subtitler would say in a few words

Condensing and dropping filler are **not** defects; dropping information the story needs **is**. An acceptable alternative is not a defect. Don't invent defects.

**Read across cues.** The defects left most often are visible only there:
- an answer that doesn't fit the question (*"Nie kucharzem. Pisarzem."* after *"Młody mówi, że gotuje"* → the question must say *"jest kucharzem"*),
- a running joke or mispronounced word that must stay the same each time (*"building's Roman"*; don't "fix" it the first time),
- callbacks and bookend quotes between distant scenes (`repeated_at`: e.g. a parable or proverb read at the beginning and at the end of the film must use the same Polish wording),
- consistent family terms, nicknames and proper names from the first batch to the last (e.g. if *"Baba"* is translated as *"tato"*, never switch to raw *"Baba"* / *"babę"* / *"baba"* in later batches; keep one spelling and Polish declension for names like *Khadija* → *Khadiji*, *z Khadiją*; never confuse a character's name like *Fatiha* with a religious term like *Al-Fatiha*): the glossary says which form was chosen,
- tense and person (*"I think I got it"* → *"chyba je dostałem"*, not *"dostanę"*),
- agreement across a line break (*"na kałamarnice, od którego"* → *"na gulasz z kałamarnicy, od którego"*),
- `pan`/`ty` changing inside one conversation for no reason,
- signs and letters written the Polish way (*"To whom it may concern"* → *"Szanowni Państwo,"*).

**Polish naturalness**, typical calques: *"Przysięgam"* for a casual *"I swear"* (→ *"Serio"*, *"Mówię ci"*), *"Ta, ta, ta"* (→ *"Tak, tak"*), stiff words in casual speech (*"wadzi"*), *"Start o 11:00"* (→ *"Zaczynamy o 11:00"*), a swear word without commas (→ *"Kurwa, ideał."*), an object English left implied (*"Rzucam to"* → *"Rzucam studia"*), food names word for word (*"basy pasiaste"* → *"bassy"*), and bare 1–2 word follow-up questions (*"I need a photo." - "Of what?"* → *"Zdjęcie czego?"*, not *"Czego?"*; *"Like what?"* → *"Na przykład?"*; *"For what?"* → *"Po co?"*).

## Output

**Fixes.** A JSON list, one item per corrected cue, with the **whole** corrected subtitle:

```json
[{"index": "12", "text": "the whole corrected subtitle (with \n)", "category": "gender", "problem": "..."},
 {"index": "116", "delete": true, "category": "spelling", "problem": "only a hesitation sound"}]
```

Follow the format rules of the translation: at most 2 lines of ≤ 42 characters, `- ` for each of two speakers, plain `-` (never `—`/`–`), `„...”` quotes, tags kept, no `{xx: ...}` lines.

The main reviewer writes `$W/fixes.json`. A subagent reviewing batch NNN writes `$W/fixes_NNN.json` (an empty list `[]` when it found nothing), so parallel reviewers never write the same file.

**Verdict, for every batch you read to the end:** `$W/reviewed_NNN.json`

```json
{"first": "901", "last": "1050", "defects": 4}
```

`first` and `last` are the `index` of the first and the last item of `review_NNN.json`, copied from it; `defects` is how many you reported, `0` included. `review-apply` refuses to run while a batch has no verdict, so a batch that was never read can't pass as "no defects". Write it only after reading the whole batch.

**A subagent also returns** (as its answer, in a few lines): the number of defects per category, the 3–5 most serious ones (before → after, why), and anything that concerns other batches: a name, a term or a recurring line whose wording differs from the glossary or from `repeated_at`. Don't edit `<output.srt>` and don't run `review-apply`.
