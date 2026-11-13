#!/usr/bin/env python3
"""Kontrola jakości tłumaczenia napisów przez Gemini.

Porównuje przetłumaczony plik SRT z oryginałem (dopasowanym po czasie) i zgłasza:

* **translation**   — błąd tłumaczenia, zmieniony sens, pominięta treść,
* **hallucination** — treść, której nie ma w oryginale,
* **spelling**      — literówki, ortografia, interpunkcja, gramatyka,
* **unnatural**     — kalka, sztuczny szyk, zły rejestr,
* **gender**        — zły rodzaj gramatyczny albo forma grzecznościowa,
* **coherence**     — odpowiedź nie pasuje do pytania, zgubione echo między kwestiami,
* **condense**      — napis za długi do przeczytania, który da się skrócić bez utraty sensu.

Zasady kontroli są te same, co w skillu ``translate-subtitles``
(``references/reviewer.md``).

Jeśli plik oryginału jest naszym plikiem scalonym (``Film.en_fr.srt``), do
promptu trafia dodatkowo wyjaśnienie linii ``{fr: ...}`` i znacznika
``[en: BRAK NAPISÓW ...]``, żeby model wiedział, czym są i mógł ich użyć jako
drugiego źródła prawdy.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from typing import Iterable, Sequence

try:
    from gst_gui.utils.srt_merge import (
        Cue, gst_description, parse_merged_name, parse_srt, read_text,
    )
    from gst_gui.utils.srt_fix import CPS, char_count, format_time, visible_len
except ImportError:  # uruchamianie pliku bezpośrednio
    from srt_merge import (  # type: ignore
        Cue, gst_description, parse_merged_name, parse_srt, read_text,
    )
    from srt_fix import CPS, char_count, format_time, visible_len  # type: ignore

BATCH_MULTIPLIER = 1.5      # batch kontroli = 1,5x batcha tłumaczenia
DEFAULT_BATCH = 350
CATEGORIES = ("translation", "coherence", "hallucination", "spelling", "unnatural", "gender",
              "condense")
# stopka dopisywana przez tłumacza - nie jest częścią dialogu, nie oceniamy jej
STAMP_RE = re.compile(r"^#.*(translated by|przetłumaczone).*#$", re.IGNORECASE)
SEVERITIES = ("high", "medium", "low")
PAYLOAD_KEYS = ("index", "time", "original", "translation", "cps", "repeated_at")


# --------------------------------------------------------------------------- #
# ustawienia z konfiguracji GUI
# --------------------------------------------------------------------------- #
def gui_config() -> dict:
    """Wczytaj konfigurację GUI (klucz API, model, batch, język)."""
    import platform
    from pathlib import Path

    system = platform.system()
    if system == "Windows":
        folder = Path.home() / "AppData" / "Roaming" / "Gemini-SRT-Translator"
    elif system == "Darwin":
        folder = Path.home() / "Library" / "Application Support" / "Gemini-SRT-Translator"
    else:
        folder = Path.home() / ".config" / "gemini-srt-translator"
    path = folder / "gui_config.json"
    try:
        with open(path, encoding="utf-8") as fh:
            config = json.load(fh)
    except (OSError, ValueError):
        return {}

    # klucze API leżą na dysku zaszyfrowane - odszyfrowujemy tym samym kodem, co GUI
    try:
        from gst_gui.gui.config_manager import _SENSITIVE_KEYS, _decrypt_value
    except ImportError:
        return config
    for field in _SENSITIVE_KEYS:
        if config.get(field):
            config[field] = _decrypt_value(config[field])
    return config


def review_batch_size(translation_batch: int | None = None) -> int:
    """Rozmiar batcha kontroli — półtora raza tyle, co batch tłumaczenia."""
    if translation_batch is None or translation_batch == "":
        translation_batch = gui_config().get("batch_size")
    try:
        # w konfiguracji GUI batch bywa stringiem z pola tekstowego
        translation_batch = int(str(translation_batch).strip())
    except (TypeError, ValueError):
        translation_batch = DEFAULT_BATCH
    return max(1, round(translation_batch * BATCH_MULTIPLIER))


def find_source_candidates(translated_path: str) -> list[str]:
    """Znajdź w tym samym katalogu pliki nadające się na oryginał do porównania.

    Najpierw pliki scalone (``Film.en_fr.srt``) — mają najwięcej kontekstu —
    potem pozostałe napisy, od najbliższych nazwą.  Sam sprawdzany plik odpada.
    """
    folder = os.path.dirname(os.path.abspath(translated_path)) or "."
    name = os.path.basename(translated_path)
    stem = name[:-4] if name.lower().endswith(".srt") else name

    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []

    scored = []
    for other in names:
        if not other.lower().endswith(".srt") or other == name:
            continue
        other_stem = other[:-4]
        shared = len(os.path.commonprefix([other_stem.lower(), stem.lower()]))
        merged = 0 if parse_merged_name(other) else 1  # scalone na początek
        scored.append((merged, -shared, other))
    return [os.path.join(folder, n) for _, _, n in sorted(scored)]


# --------------------------------------------------------------------------- #
# zestawianie par oryginał / tłumaczenie
# --------------------------------------------------------------------------- #
def _overlap(a: Cue, b: Cue) -> int:
    return min(a.end, b.end) - max(a.start, b.start)


def _combine_overlapping_originals(cands: Sequence[Cue]) -> str:
    """Połącz wszystkie napisy oryginału pokrywające się ze scalonym napisem tłumaczenia."""
    if not cands:
        return ""
    if len(cands) == 1:
        return "\n".join(cands[0].lines)
    main_lines: list[str] = []
    ref_lines: list[str] = []
    for c in cands:
        for ln in c.lines:
            if ln.lstrip().startswith("{"):
                if ln not in ref_lines:
                    ref_lines.append(ln)
            else:
                main_lines.append(ln)
    return "\n".join(main_lines + ref_lines)


_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")


def _repeat_key(text: str):
    """Znormalizowane słowa głównego tekstu napisu — do szukania powtarzanych cytatów."""
    main = "\n".join(line for line in str(text).split("\n")
                     if not line.lstrip().startswith("{") and "BRAK NAPIS" not in line)
    words = _WORD_RE.findall(_TAG_RE.sub(" ", main).lower())
    return tuple(words) if len(words) >= 4 and len(" ".join(words)) >= 18 else None


def find_distant_repeats(texts: Sequence[str], min_distance: int = 15,
                         max_occurrences: int = 5) -> dict[int, list[int]]:
    """Pozycja napisu -> pozycje tej samej kwestii oddalone o co najmniej ``min_distance``."""
    by_key: dict[tuple, list[int]] = {}
    for idx, text in enumerate(texts):
        key = _repeat_key(text)
        if key:
            by_key.setdefault(key, []).append(idx)
    repeats: dict[int, list[int]] = {}
    for positions in by_key.values():
        if not 2 <= len(positions) <= max_occurrences:
            continue
        for pos in positions:
            others = [p for p in positions if abs(p - pos) >= min_distance]
            if others:
                repeats[pos] = others
    return repeats


def build_pairs(translated: Sequence[Cue], original: Sequence[Cue],
                min_overlap: float = 0.2) -> list[dict]:
    """Zestaw napisy tłumaczenia z napisami oryginału po czasie wyświetlania."""
    import bisect

    original = sorted(original, key=lambda c: (c.start, c.end))
    starts = [c.start for c in original]
    pairs = []
    for n, cue in enumerate(sorted(translated, key=lambda c: (c.start, c.end)), start=1):
        if len(cue.lines) == 1 and STAMP_RE.match(cue.lines[0].strip()):
            continue
        lo = max(0, bisect.bisect_left(starts, cue.start) - 2)
        while lo > 0 and original[lo - 1].end > cue.start:
            lo -= 1
        matched: list[Cue] = []
        for cand in original[lo:]:
            if cand.start >= cue.end:
                break
            ov = _overlap(cand, cue)
            if ov <= 0:
                continue
            ratio = ov / (min(cand.end - cand.start, cue.end - cue.start) or 1)
            if ratio >= min_overlap:
                matched.append(cand)
        pair = {
            "index": str(n),
            "time": format_time(cue.start),
            "original": _combine_overlapping_originals(matched),
            "translation": "\n".join(cue.lines),
            "cue": cue,
        }
        cps = char_count(cue.lines) / max(cue.end - cue.start, 1) * 1000
        if cps > CPS:
            pair["cps"] = round(cps, 1)  # szybciej, niż widz czyta - kandydat do skrócenia
        pairs.append(pair)

    for pos, others in find_distant_repeats([p["original"] for p in pairs]).items():
        pairs[pos]["repeated_at"] = [
            {"index": pairs[o]["index"], "translation": pairs[o]["translation"]} for o in others
        ]
    return pairs


# --------------------------------------------------------------------------- #
# prompt
# --------------------------------------------------------------------------- #
def _is_polish(language: str) -> bool:
    return (language or "").strip().lower() in ("polish", "polski", "pl", "pol")


def build_instruction(language: str, description: str = "", merged_langs=None) -> str:
    polish = _is_polish(language)

    def ex(text: str) -> str:
        return f" ({text})" if polish else ""

    parts = [
        f"# ROLE\nYou are a senior subtitle editor reviewing **{language}** subtitles. "
        "You receive the original subtitle and its translation, matched by timecode, "
        "and you report defects a professional subtitler would fix. Read the dialogue "
        "**as sequences in context**, never line by line as a mechanical check.",

        "## Categories (use exactly these ids)\n"
        "- `translation` — wrong meaning, mistranslation, essential information lost, "
        "wrong name/number/unit\n"
        "- `coherence` — an answer that doesn't fit the question, a lost echo or callback "
        "between speakers\n"
        "- `hallucination` — content in the translation that is not in the original\n"
        f"- `spelling` — spelling, grammar, punctuation or typo errors in {language}\n"
        "- `unnatural` — technically correct but not how a native speaker would say it: "
        "calques, foreign word order, wrong register\n"
        f"- `gender` — wrong grammatical gender, number or level of formality for the "
        f"speaker or the person addressed (in {language} this is often invisible in the "
        "English source and has to be inferred from context)\n"
        "- `condense` — too long to read (the item has `cps`) where a shorter wording keeps "
        "the meaning, or a word-for-word rendering of something a subtitler would say in a "
        "few words. **Meaning has priority**: never condense so far that the sentence loses "
        "its sense; a subtitle slightly over the reading speed is better than a cut one",

        "## Rules\n"
        "- Subtitles are **condensed by design**: shortening, dropping filler words and "
        "merging sentences are NOT errors. Dropping information the story needs IS.\n"
        "- Report **only real defects**. An acceptable alternative wording is not a defect.\n"
        "- `severity`: `high` = the viewer is misled or the line is wrong, "
        "`medium` = clearly noticeable, `low` = polish.\n"
        "- `problem`: one sentence, in the language of the subtitles, quoting the exact "
        "offending fragment.\n"
        "- `suggestion`: a ready-to-use replacement for the **whole** translated subtitle, "
        "or an empty string if you have none. Format: at most 2 lines of up to 42 "
        "characters (line break `\\n`), `- ` at the start of each line when two people "
        "speak, a plain hyphen `-` (never `—` or `–`), tags kept"
        + (", quotes as `„...”`" if polish else "") + ".\n"
        "- `delete`: set it to `true` (with an empty `suggestion`) only for a subtitle that "
        "is nothing but a hesitation sound (*uh, um"
        + (", eee, yyy" if polish else "") + "*). Hesitation sounds inside a longer "
        "subtitle are dropped in the `suggestion`; a `...` shows the pause.\n"
        "- Return an empty list when a batch has no defects. Never invent defects to fill "
        "the response.",

        "## Read across subtitles\n"
        "The defects left most often are visible only there:\n"
        "- an answer that doesn't fit the question as it was translated,\n"
        "- a running joke or a deliberately mispronounced word that must stay the same "
        "each time (don't \"fix\" it),\n"
        "- callbacks and recurring quotes between distant scenes: an item with "
        "`repeated_at` has the same original line elsewhere in the film — both must use "
        "the same wording,\n"
        "- family terms, nicknames and proper names kept in one form and one spelling "
        "from the first subtitle to the last,\n"
        "- tense and person" + ex('*"I think I got it"* → *"chyba je dostałem"*, not *"dostanę"*')
        + ",\n"
        "- grammatical agreement across a line break or between two subtitles of one "
        "sentence,\n"
        "- formality changing inside one conversation for no reason"
        + ex("*pan* / *ty*") + ",\n"
        "- signs and letters written the way the target language writes them"
        + ex('*"To whom it may concern"* → *"Szanowni Państwo,"*') + ",\n"
        "- bare 1–2 word follow-up questions that lost their referent"
        + ex('*"I need a photo." - "Of what?"* → *"Zdjęcie czego?"*, not *"Czego?"*; '
             '*"Like what?"* → *"Na przykład?"*; *"For what?"* → *"Po co?"*') + ".",
    ]

    if polish:
        parts.append(
            "## Polish naturalness\n"
            "Typical calques: *\"Przysięgam\"* for a casual *\"I swear\"* (→ *\"Serio\"*, "
            "*\"Mówię ci\"*), *\"Ta, ta, ta\"* (→ *\"Tak, tak\"*), stiff words in casual "
            "speech, *\"Start o 11:00\"* (→ *\"Zaczynamy o 11:00\"*), a swear word without "
            "commas (→ *\"Kurwa, ideał.\"*), an object English left implied "
            "(*\"Rzucam to\"* → *\"Rzucam studia\"*), food names word for word. "
            "Swearing keeps its strength: never sanitized, never stronger."
        )

    parts.append(
        '## Input / output\nInput items look like:\n'
        '```json\n[{"index": "12", "time": "00:03:41,180", '
        '"original": "Did they say how long\\nyou\'ll be gone for?", '
        '"translation": "Powiedzieli, na jak długo wyjeżdżasz?"}]\n```\n'
        "Optional fields: `cps` (characters per second, present only when the subtitle is "
        "faster than a viewer reads) and `repeated_at` (other subtitles with the same "
        "original line and the translation used there). A `{sdh: SPEAKER (NOTE)}` line in "
        "`original` names who speaks — use it for gender and formality, never translate it.\n"
        "Return a JSON list of defects; `index` must match the item you are reporting on. "
        "One item may have more than one defect."
    )

    if merged_langs:
        base, other = merged_langs[0], "/".join(merged_langs[1:])
        parts.append(
            f"## The original file is bilingual\n{gst_description(*merged_langs)}\n"
            f"Treat the `{{{other}: ...}}` lines as a **second reference translation of the "
            "same moment**: use them to confirm meaning, grammatical gender and formality. "
            "When the original shows the missing-subtitle marker, the translation was made "
            f"from the `{{{other}: ...}}` line — judge it against that line, and never "
            "report the marker itself as a defect."
        )

    if description:
        parts.append(f"## What the film is about\n{description}")

    return "\n\n---\n\n".join(parts)


def _response_schema():
    from google.genai import types

    return types.Schema(
        type="ARRAY",
        items=types.Schema(
            type="OBJECT",
            required=["index", "category", "severity", "problem"],
            properties={
                "index": types.Schema(type="STRING"),
                "category": types.Schema(type="STRING", enum=list(CATEGORIES)),
                "severity": types.Schema(type="STRING", enum=list(SEVERITIES)),
                "problem": types.Schema(type="STRING"),
                "suggestion": types.Schema(type="STRING"),
                "delete": types.Schema(type="BOOLEAN"),
            },
        ),
    )


# --------------------------------------------------------------------------- #
# wywołanie modelu
# --------------------------------------------------------------------------- #
def review_pairs(pairs: Sequence[dict], api_key: str, model: str, language: str,
                 description: str = "", merged_langs=None, batch_size: int | None = None,
                 log=print, retries: int = 2) -> list[dict]:
    """Prześlij pary do modelu partiami i zbierz zgłoszone usterki."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=_response_schema(),
        system_instruction=build_instruction(language, description, merged_langs),
        temperature=0.0,
    )

    size = batch_size or review_batch_size()
    batches = [pairs[i:i + size] for i in range(0, len(pairs), size)]
    by_index = {p["index"]: p for p in pairs}
    issues: list[dict] = []

    for n, batch in enumerate(batches, start=1):
        payload = json.dumps(
            [{k: p[k] for k in PAYLOAD_KEYS if k in p} for p in batch],
            ensure_ascii=False,
        )
        log(f"🔍 Sprawdzam partię {n}/{len(batches)} ({len(batch)} napisów)...")

        for attempt in range(retries + 1):
            try:
                response = client.models.generate_content(
                    model=model, contents=payload, config=config)
                found = json.loads(response.text or "[]")
                break
            except Exception as exc:  # noqa: BLE001 - raportujemy i lecimy dalej
                if attempt >= retries:
                    log(f"⚠️  Partia {n} nie powiodła się: {exc}")
                    found = []
                    break
                time.sleep(2 * (attempt + 1))

        for item in found:
            pair = by_index.get(str(item.get("index", "")))
            if not pair:
                continue
            log(f"   [{item.get('severity', '?')}] {item.get('category', '?')} "
                f"— napis {pair['index']} ({pair['time']}): {item.get('problem', '')}")
            if item.get("delete") and not item.get("suggestion"):
                log(f"      do usunięcia: {pair['translation']}".replace("\n", " / "))
            elif item.get("suggestion"):
                log(f"      było:      {pair['translation']}".replace("\n", " / "))
                log(f"      propozycja: {item['suggestion']}".replace("\n", " / "))
            issues.append({
                "index": pair["index"],
                "time": pair["time"],
                "category": item.get("category", "translation"),
                "severity": item.get("severity", "medium"),
                "problem": item.get("problem", ""),
                "suggestion": item.get("suggestion", ""),
                "delete": bool(item.get("delete")),
                "original": pair["original"],
                "translation": pair["translation"],
            })
        log(f"   usterek narastająco: {len(issues)}")

    order = {s: n for n, s in enumerate(SEVERITIES)}
    issues.sort(key=lambda i: (order.get(i["severity"], 9), int(i["index"])))
    return issues


# --------------------------------------------------------------------------- #
# nakładanie poprawek
# --------------------------------------------------------------------------- #
def apply_suggestions(pairs: Sequence[dict], issues: Sequence[dict], rewrap: bool = True,
                      log=print) -> int:
    """Wstaw propozycje modelu do napisów (obiekty Cue w ``pairs``).

    Na jeden napis nakładana jest jedna poprawka — ta o najwyższej wadze, bo
    propozycja zastępuje całą jego treść.  Zmieniony napis jest od razu
    przełamywany według zasad łamania linii.  Usterka z ``delete`` opróżnia
    napis (``cue.lines == []``) — taki napis trzeba pominąć przy zapisie.
    """
    by_index = {p["index"]: p for p in pairs}
    applied, done = 0, set()

    for issue in issues:  # posortowane od najcięższych
        suggestion = (issue.get("suggestion") or "").strip()
        index = issue["index"]
        if index in done:
            continue
        pair = by_index.get(index)
        if not pair:
            continue
        cue = pair["cue"]
        if issue.get("delete") and not suggestion:
            # sam odgłos wahania - napis znika (pusty napis odpada przy zapisie)
            log(f"🗑️  {pair['time']} [{issue['category']}] {' / '.join(cue.lines)}")
            cue.lines = []
            issue["applied"] = True
            done.add(index)
            applied += 1
            continue
        if not suggestion:
            continue
        new_lines = [line for line in suggestion.split("\n") if line.strip()]
        if not new_lines or new_lines == cue.lines:
            continue

        if rewrap:
            try:
                from gst_gui.utils.srt_fix import fix_cue_lines
            except ImportError:
                from srt_fix import fix_cue_lines  # type: ignore
            new_lines, _ = fix_cue_lines(new_lines)

        log(f"✏️  {pair['time']} [{issue['category']}] "
            f"{' / '.join(cue.lines)} → {' / '.join(new_lines)}")
        cue.lines = new_lines
        issue["applied"] = True
        done.add(index)
        applied += 1

    return applied


# --------------------------------------------------------------------------- #
# raport
# --------------------------------------------------------------------------- #
def format_report(issues: Sequence[dict], translated_path: str, original_path: str,
                  checked: int, model: str) -> str:
    counts = {c: sum(1 for i in issues if i["category"] == c) for c in CATEGORIES}
    sev = {s: sum(1 for i in issues if i["severity"] == s) for s in SEVERITIES}

    lines = [
        f"# Kontrola napisów: {os.path.basename(translated_path)}",
        "",
        f"- oryginał: `{os.path.basename(original_path)}`",
        f"- model: `{model}`",
        f"- sprawdzonych napisów: {checked}",
        f"- znalezionych usterek: **{len(issues)}** "
        f"(high: {sev['high']}, medium: {sev['medium']}, low: {sev['low']})",
        "",
        "| kategoria | ile |",
        "|---|---|",
    ]
    lines += [f"| {c} | {counts[c]} |" for c in CATEGORIES]
    lines.append("")

    if not issues:
        lines.append("Nie znaleziono usterek.")
        return "\n".join(lines)

    for issue in issues:
        lines += [
            f"## [{issue['severity']}] {issue['category']} — napis {issue['index']} "
            f"({issue['time']})",
            "",
            f"- **oryginał:** {issue['original'] or '—'}".replace("\n", " / "),
            f"- **tłumaczenie:** {issue['translation']}".replace("\n", " / "),
            f"- **problem:** {issue['problem']}",
        ]
        if issue.get("delete") and not issue.get("suggestion"):
            lines.append("- **propozycja:** usunąć napis"
                         + (" ✅ (usunięty)" if issue.get("applied") else ""))
        elif issue.get("suggestion"):
            mark = " ✅ (wprowadzona)" if issue.get("applied") else ""
            lines.append(
                f"- **propozycja:**{mark} {issue['suggestion']}".replace("\n", " / "))
        lines.append("")
    return "\n".join(lines)


def default_report_path(translated_path: str) -> str:
    root = translated_path[:-4] if translated_path.lower().endswith(".srt") else translated_path
    return f"{root}.review.md"


def review_srt(translated_path: str, original_path: str, api_key: str | None = None,
               model: str | None = None, language: str | None = None,
               description: str = "", batch_size: int | None = None,
               limit: int | None = None, report_path: str | None = None,
               apply: bool = False, refix: bool = False,
               log=print) -> tuple[list[dict], dict]:
    """Sprawdź plik tłumaczenia względem oryginału. Zwraca (usterki, statystyki).

    Z ``apply=True`` znalezione propozycje są wstawiane do sprawdzanego pliku
    (nadpisywanego w miejscu), a zmienione napisy od razu przełamywane.
    ``refix=True`` puszcza potem na pliku pełne "Fix subs" — poprawki zmieniają
    długość tekstu, więc czasy wyświetlania trzeba przeliczyć.
    """
    config = gui_config()
    api_key = api_key or os.environ.get("GEMINI_API_KEY") or config.get("gemini_api_key")
    if not api_key:
        raise ValueError("Brak klucza API (--api-key, GEMINI_API_KEY albo konfiguracja GUI)")
    model = model or config.get("model") or "gemini-3-flash"
    language = language or config.get("language") or "Polish"

    translated = parse_srt(read_text(translated_path))
    original = parse_srt(read_text(original_path))
    merged_langs = parse_merged_name(original_path)

    pairs = build_pairs(translated, original)
    if limit:
        pairs = pairs[:limit]

    log(f"🔍 {os.path.basename(translated_path)} ({len(translated)} napisów) "
        f"vs {os.path.basename(original_path)} ({len(original)})")
    if merged_langs:
        log(f"🔍 Oryginał jest plikiem scalonym ({merged_langs[0]} + {merged_langs[1]}) "
            "— dokładam wytyczne o liniach referencyjnych")
    without_source = sum(1 for p in pairs if not p["original"])
    if without_source:
        log(f"⚠️  {without_source} napisów nie ma odpowiednika w oryginale "
            "(sprawdzane tylko pod kątem języka)")

    size = batch_size or review_batch_size(config.get("batch_size"))
    log(f"🔍 Model: {model}, batch kontroli: {size} "
        f"({BATCH_MULTIPLIER:g}x batcha tłumaczenia)")

    issues = review_pairs(pairs, api_key=api_key, model=model, language=language,
                          description=description, merged_langs=merged_langs,
                          batch_size=size, log=log)

    applied = 0
    if apply and issues:
        applied = apply_suggestions(pairs, issues, log=log)
        if applied:
            from gst_gui.utils.srt_merge import format_srt
            text = format_srt(sorted((c for c in translated if c.lines),
                                     key=lambda c: (c.start, c.end)))
            tmp = f"{translated_path}.tmp"
            with open(tmp, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
            os.replace(tmp, translated_path)
            log(f"✏️  Wprowadzono {applied} poprawek do {os.path.basename(translated_path)}")
            if refix:
                try:
                    from gst_gui.utils.srt_fix import fix_srt
                except ImportError:
                    from srt_fix import fix_srt  # type: ignore
                _, fix_stats = fix_srt(translated_path, source_path=original_path)
                log(f"✨ Fix subs po poprawkach: {fix_stats.get('extended', 0)} czasów "
                    f"wydłużonych, {fix_stats.get('rewrapped', 0)} linii przełamanych")
        else:
            log("✏️  Żadna z usterek nie miała gotowej propozycji do wstawienia")

    report_path = report_path or default_report_path(translated_path)
    report = format_report(issues, translated_path, original_path, len(pairs), model)
    if report_path != "-":
        with open(report_path, "w", encoding="utf-8") as fh:
            fh.write(report)

    stats = {
        "checked": len(pairs),
        "issues": len(issues),
        "applied": applied,
        "without_source": without_source,
        "report": report_path,
        "report_text": report,
        "by_category": {c: sum(1 for i in issues if i["category"] == c) for c in CATEGORIES},
        "by_severity": {s: sum(1 for i in issues if i["severity"] == s) for s in SEVERITIES},
        "merged_langs": merged_langs,
        "batch_size": size,
        "model": model,
    }
    return issues, stats


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Sprawdza tłumaczenie napisów przez Gemini (błędy, halucynacje, "
                    "ortografia, nienaturalność, zły rodzaj gramatyczny).")
    ap.add_argument("translated", help="napisy do sprawdzenia")
    ap.add_argument("original", help="napisy oryginalne (do porównania)")
    ap.add_argument("-o", "--output", help="plik raportu (domyślnie <nazwa>.review.md, '-' na stdout)")
    ap.add_argument("--api-key", help="klucz Gemini (domyślnie z GEMINI_API_KEY lub konfiguracji GUI)")
    ap.add_argument("--model", help="model (domyślnie z konfiguracji GUI)")
    ap.add_argument("--language", help="język napisów sprawdzanych (domyślnie z konfiguracji GUI)")
    ap.add_argument("--description", default="", help="opis filmu przekazywany modelowi")
    ap.add_argument("--description-file", help="plik z opisem filmu")
    ap.add_argument("--batch-size", type=int,
                    help="rozmiar partii (domyślnie 1,5x batch tłumaczenia z GUI)")
    ap.add_argument("--limit", type=int, help="sprawdź tylko pierwsze N napisów (test)")
    ap.add_argument("--apply", action="store_true",
                    help="wprowadź znalezione poprawki do sprawdzanego pliku (nadpisuje go)")
    ap.add_argument("--refix", action="store_true",
                    help="po wprowadzeniu poprawek puść na pliku Fix subs (czasy, łamanie)")
    args = ap.parse_args(list(argv) if argv is not None else None)

    description = args.description
    if args.description_file:
        with open(args.description_file, encoding="utf-8") as fh:
            description = fh.read().strip()

    try:
        issues, stats = review_srt(
            args.translated, args.original, api_key=args.api_key, model=args.model,
            language=args.language, description=description,
            batch_size=args.batch_size, limit=args.limit,
            report_path=args.output, apply=args.apply, refix=args.refix,
            log=lambda m: print(m, file=sys.stderr),
        )
    except ValueError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1

    if args.output == "-":
        print(stats["report_text"])
    else:
        print(f"🔍 Raport: {stats['report']}", file=sys.stderr)
    counts = ", ".join(f"{c}: {n}" for c, n in stats["by_category"].items() if n)
    print(f"🔍 Sprawdzono {stats['checked']} napisów, usterek: {stats['issues']}"
          + (f" ({counts})" if counts else "")
          + (f", wprowadzonych poprawek: {stats['applied']}" if args.apply else ""),
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
