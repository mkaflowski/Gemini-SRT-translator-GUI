#!/usr/bin/env python3
"""Merge two SRT files into one, keeping the first file as the base and
attaching the second file's lines as reference below the matching cues.

Output layout:

    12
    00:03:41,180 --> 00:03:43,097
    Did they say how long
    you'll be gone for?
    {fr: Ils ont dit combien de temps tu partais ?}

When a cue from the second file has no counterpart in the first file, it is
emitted as a standalone cue:

    13
    00:01:12,197 --> 00:01:14,449
    [en: BRAK NAPISOW - PRZETLUMACZ Z POZOSTALYCH]
    {fr: PRESENTE}

Usable as a library (``merge_srt``) or from the command line.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Iterable, Sequence

MISSING_TEMPLATE = "[{lang}: BRAK NAPISÓW - PRZETŁUMACZ Z POZOSTAŁYCH]"

# Tekst do pola "description" w gemini-srt-translator / GUI.
GST_DESCRIPTION = (
    "This subtitle file is bilingual. Every line wrapped as {{{other}: ...}} is a "
    "reference translation of the same moment from another subtitle track. "
    "Use those reference lines ONLY as context - to resolve grammatical gender, "
    "formality (T-V distinction), number, idioms and who is speaking to whom. "
    "A line reading '{missing}' means the main subtitle track has no text for that "
    "moment: translate the reference line instead and use it as the subtitle. "
    "IMPORTANT: the translated text must contain ONLY the final subtitle in the "
    "target language. Remove the {{{other}: ...}} reference lines and the "
    "'{missing}' marker from your output - never copy or translate them as "
    "visible subtitle text."
)


def gst_description(lang_base: str = "en", lang_other: str = "fr") -> str:
    """Opis do wklejenia w polu description tłumacza (kontekst dla modelu)."""
    return GST_DESCRIPTION.format(
        other=lang_other, missing=MISSING_TEMPLATE.format(lang=lang_base)
    )

_TIME_RE = re.compile(
    r"(?P<h>\d{1,3}):(?P<m>\d{1,2}):(?P<s>\d{1,2})[,.](?P<ms>\d{1,3})"
)
_TIMELINE_RE = re.compile(
    r"^\s*(?P<start>[\d:,.]+)\s*-->\s*(?P<end>[\d:,.]+)(?P<rest>.*)$"
)


@dataclass
class Cue:
    start: int  # milliseconds
    end: int  # milliseconds
    lines: list[str]
    coords: str = ""  # optional trailing position data on the timecode line
    extras: list[str] = field(default_factory=list)  # attached reference lines
    order: int = 0  # original position, used as a stable sort key
    injected: bool = False  # cue that came from the second file only

    @property
    def duration(self) -> int:
        return max(0, self.end - self.start)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


# --------------------------------------------------------------------------- #
# pomocnicze dla GUI: zgadywanie języka i szukanie pliku pomocniczego
# --------------------------------------------------------------------------- #
LANG_CODES = {
    "en": "en", "eng": "en", "english": "en", "ang": "en",
    "pl": "pl", "pol": "pl", "polish": "pl", "polski": "pl",
    "fr": "fr", "fre": "fr", "fra": "fr", "french": "fr", "francais": "fr",
    "de": "de", "ger": "de", "deu": "de", "german": "de",
    "es": "es", "spa": "es", "spanish": "es",
    "it": "it", "ita": "it", "italian": "it",
    "pt": "pt", "por": "pt", "portuguese": "pt",
    "nl": "nl", "dut": "nl", "nld": "nl", "dutch": "nl",
    "cs": "cs", "cze": "cs", "ces": "cs", "czech": "cs",
    "sk": "sk", "slo": "sk", "slk": "sk",
    "ru": "ru", "rus": "ru", "russian": "ru",
    "uk": "uk", "ukr": "uk",
    "sv": "sv", "swe": "sv", "da": "da", "dan": "da", "no": "no", "nor": "no",
    "fi": "fi", "fin": "fi", "hu": "hu", "hun": "hu", "ro": "ro", "ron": "ro",
    "tr": "tr", "tur": "tr", "ar": "ar", "ara": "ar", "he": "he", "heb": "he",
    "ja": "ja", "jpn": "ja", "ko": "ko", "kor": "ko",
    "zh": "zh", "chi": "zh", "zho": "zh", "hr": "hr", "hrv": "hr",
    "sr": "sr", "srp": "sr", "bg": "bg", "bul": "bg", "el": "el", "ell": "el",
}


def guess_lang_tag(path: str, default: str = "xx") -> str:
    """Zgadnij kod języka z nazwy pliku (``Film.eng.srt`` -> ``en``).

    Bierze pod uwagę ostatni pasujący człon, bo kod języka zwykle stoi tuż
    przed rozszerzeniem (``Film.eng.fr.srt`` -> ``fr``).
    """
    stem = os.path.basename(path)
    if stem.lower().endswith(".srt"):
        stem = stem[:-4]
    parts = re.split(r"[.\-_\[\]() ]+", stem.lower())
    for part in reversed(parts):
        if part in LANG_CODES:
            return LANG_CODES[part]
    return default


_MERGED_NAME_RE = re.compile(r"\.([A-Za-z]{2,3})_([A-Za-z]{2,3})\.srt$", re.IGNORECASE)


def parse_merged_name(path: str) -> tuple[str, str] | None:
    """Rozpoznaj plik scalony po nazwie: ``Film.en_fr.srt`` -> ``("en", "fr")``.

    Zwraca None, jeśli nazwa nie ma końcówki ``<lang1>_<lang2>.srt``.
    """
    m = _MERGED_NAME_RE.search(os.path.basename(path or ""))
    if not m:
        return None
    return m.group(1).lower(), m.group(2).lower()


def find_reference_candidates(base_path: str) -> list[str]:
    """Znajdź w tym samym katalogu inne pliki SRT nadające się na pomocnicze.

    Pomija sam plik bazowy i wcześniejsze wyniki scalania
    (``<nazwa>.<lang1>_<lang2>.srt``); najbliższe nazwą są pierwsze.
    """
    folder = os.path.dirname(os.path.abspath(base_path)) or "."
    base_name = os.path.basename(base_path)
    base_stem = base_name[:-4] if base_name.lower().endswith(".srt") else base_name
    candidates = []
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    for name in names:
        if not name.lower().endswith(".srt") or name == base_name:
            continue
        stem = name[:-4]
        if parse_merged_name(name):
            continue
        shared = len(os.path.commonprefix([stem.lower(), base_stem.lower()]))
        candidates.append((-shared, name))
    return [os.path.join(folder, name) for _, name in sorted(candidates)]


# --------------------------------------------------------------------------- #
# parsing / formatting
# --------------------------------------------------------------------------- #
def parse_timestamp(value: str) -> int:
    m = _TIME_RE.match(value.strip())
    if not m:
        raise ValueError(f"Nieprawidłowy znacznik czasu: {value!r}")
    return (
        int(m.group("h")) * 3_600_000
        + int(m.group("m")) * 60_000
        + int(m.group("s")) * 1000
        + int(m.group("ms").ljust(3, "0"))
    )


def format_timestamp(ms: int) -> str:
    ms = max(0, int(ms))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def read_text(path: str) -> str:
    with open(path, "rb") as fh:
        raw = fh.read()
    for encoding in ("utf-8-sig", "utf-8", "cp1250", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_srt(text: str) -> list[Cue]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", text):
        lines = [ln for ln in block.split("\n") if ln.strip() != ""]
        if not lines:
            continue
        idx = 0
        if "-->" not in lines[0] and lines[0].strip().isdigit():
            idx = 1
        if idx >= len(lines):
            continue
        m = _TIMELINE_RE.match(lines[idx])
        if not m:
            continue
        try:
            start = parse_timestamp(m.group("start"))
            end = parse_timestamp(m.group("end"))
        except ValueError:
            continue
        body = [ln.rstrip() for ln in lines[idx + 1 :]]
        if not body:
            continue
        cues.append(
            Cue(
                start=start,
                end=end,
                lines=body,
                coords=m.group("rest").rstrip(),
                order=len(cues),
            )
        )
    return cues


def format_srt(cues: Sequence[Cue], newline: str = "\n") -> str:
    out: list[str] = []
    for i, cue in enumerate(cues, start=1):
        out.append(str(i))
        out.append(
            f"{format_timestamp(cue.start)} --> {format_timestamp(cue.end)}{cue.coords}"
        )
        out.extend(cue.lines)
        out.extend(cue.extras)
        out.append("")
    return newline.join(out)


# --------------------------------------------------------------------------- #
# czyszczenie: napisy muzyczne + opisy dla niesłyszących (SDH)
# --------------------------------------------------------------------------- #
MUSIC_CHARS = "\u266a\u266b\u266c\u2669\U0001f3b5\U0001f3b6"
_MUSIC_SPAN_RE = re.compile("[%s][^%s]*[%s]" % (MUSIC_CHARS, MUSIC_CHARS, MUSIC_CHARS))
_MUSIC_ANY_RE = re.compile("[%s]" % MUSIC_CHARS)

# "MAN:", "JIM (V.O.):", "-NARRATOR:" — etykieta mówiącego z napisów SDH
_SPEAKER_RE = re.compile(
    r"^(?P<lead>\s*(?:[-–—]\s*)?(?:<[^>]+>\s*)*)"
    r"(?P<label>[A-ZÀ-ÖØ-Þ0-9][A-ZÀ-ÖØ-Þ0-9 '’.\-]{0,24}?)\s*:\s+"
    r"(?=\S)"
)
_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")


def _remove_spans(text: str, opener: str, closer: str) -> str:
    """Wytnij fragmenty opener..closer, zachowując znaki nowej linii.

    Niedomknięty fragment (np. ``[drzwi trzaskają`` bez ``]``) jest usuwany
    do końca napisu — opisy dźwięków bywają łamane na kilka linii.
    """
    out: list[str] = []
    i = 0
    while i < len(text):
        j = text.find(opener, i)
        if j == -1:
            out.append(text[i:])
            break
        out.append(text[i:j])
        k = text.find(closer, j + len(opener))
        if k == -1:
            out.append("\n" * text.count("\n", j))
            break
        out.append("\n" * text.count("\n", j, k))
        i = k + len(closer)
    return "".join(out)


def _is_blank(line: str) -> bool:
    """Czy po wycięciu zostały same śmieci (myślnik, kropki, tagi)?"""
    return _TAG_RE.sub("", line).strip(" \t-–—.…\"'«»¿?!,:;*•") == ""


def _strip_speaker(line: str) -> str:
    m = _SPEAKER_RE.match(line)
    if not m:
        return line
    rest = line[m.end() :]
    label = m.group("label")
    if not any(ch.isalpha() for ch in label) or len(label.split()) > 3:
        return line
    # "OPERATION: OVERLORD" — plansza/tytuł, nie etykieta mówiącego
    letters = [ch for ch in rest if ch.isalpha()]
    if letters and all(ch.isupper() for ch in letters):
        return line
    return m.group("lead") + rest


def clean_lines(lines: Sequence[str], music: bool = True, sdh: bool = True) -> list[str]:
    """Wyczyść linie jednego napisu; zwraca tylko te, które coś wnoszą."""
    original = list(lines)
    text = "\n".join(original)

    if sdh:
        text = _remove_spans(text, "[", "]")
        text = _remove_spans(text, "(", ")")

    result: list[str] = []
    for old, new in zip(original, text.split("\n")):
        if _is_blank(new):
            continue
        if music:
            new = _MUSIC_SPAN_RE.sub("", new)
            if _MUSIC_ANY_RE.search(new):
                # niesparowana nutka — cała linia to tekst piosenki
                new = ""
        if sdh:
            new = _strip_speaker(new)
        if new == old:
            result.append(old)
            continue
        new = re.sub(r"[ \t]{2,}", " ", new).strip()
        if new and not _is_blank(new):
            result.append(new)
    return result


def clean_cues(
    cues: list[Cue], music: bool = True, sdh: bool = True
) -> tuple[list[Cue], dict]:
    """Usuń z napisów teksty piosenek i opisy dźwięków dla niesłyszących.

    Puste po czyszczeniu napisy są usuwane w całości.
    """
    stats = {"removed_cues": 0, "cleaned_cues": 0}
    kept: list[Cue] = []
    for cue in cues:
        new_lines = clean_lines(cue.lines, music=music, sdh=sdh)
        if not new_lines:
            stats["removed_cues"] += 1
            continue
        if new_lines != cue.lines:
            stats["cleaned_cues"] += 1
            cue.lines = new_lines
        kept.append(cue)
    return kept, stats


# --------------------------------------------------------------------------- #
# synchronizacja i kolizje czasowe
# --------------------------------------------------------------------------- #
def shift_cues(cues: list[Cue], ms: int) -> list[Cue]:
    """Przesuń wszystkie napisy o ``ms`` milisekund (czas nie schodzi poniżej 0)."""
    for cue in cues:
        cue.start = max(0, cue.start + ms)
        cue.end = max(cue.start, cue.end + ms)
    return cues


def _popcount(value: int) -> int:
    try:
        return value.bit_count()  # Python 3.10+
    except AttributeError:
        return bin(value).count("1")


def _timeline(cues: Sequence[Cue], res: int) -> int:
    """Oś czasu jako maska bitowa: bit = ``res`` ms, w których widać napis."""
    mask = 0
    for cue in cues:
        lo, hi = cue.start // res, max(cue.start // res + 1, cue.end // res)
        mask |= ((1 << (hi - lo)) - 1) << lo
    return mask


def estimate_offset(
    base: Sequence[Cue],
    other: Sequence[Cue],
    max_shift: int = 60_000,
    res: int = 100,
) -> tuple[int, float, float]:
    """Oszacuj stałe przesunięcie czasu pliku pomocniczego wobec bazowego.

    Korelacja wzajemna dwóch osi czasu „tu widać napis" — odporna na to, że oba
    pliki inaczej dzielą kwestie na napisy.  Zwraca krotkę:

    * przesunięcie w ms (dodatnie = plik pomocniczy trzeba przesunąć w przód),
    * pewność 0-1: udział wspólnego czasu wyświetlania w szczycie korelacji,
    * zysk: ile razy szczyt jest lepszy od zerowego przesunięcia (1.0 = pliki
      już są zsynchronizowane i nie ma czego poprawiać).
    """
    if not base or not other:
        return 0, 0.0, 1.0

    a, b = _timeline(base, res), _timeline(other, res)
    denom = min(_popcount(a), _popcount(b))
    if not denom:
        return 0, 0.0, 1.0

    lags = max_shift // res
    scores: dict[int, int] = {}
    for lag in range(-lags, lags + 1):
        shifted = b << lag if lag >= 0 else b >> -lag
        scores[lag] = _popcount(a & shifted)

    best_lag = max(scores, key=lambda lag: (scores[lag], -abs(lag)))
    zero = scores[0] or 1
    gain = scores[best_lag] / zero
    return best_lag * res, min(1.0, scores[best_lag] / denom), gain


def should_shift(
    offset: int,
    confidence: float,
    gain: float,
    min_offset: int = 500,
    min_confidence: float = 0.4,
    min_gain: float = 1.10,
) -> bool:
    """Czy warto przesunąć plik pomocniczy — wąskie kryteria, bo pomyłka boli.

    Przesuwamy tylko wtedy, gdy przesunięcie jest zauważalne, pliki wyraźnie
    do siebie pasują, a synchronizacja realnie na tym zyskuje.
    """
    return (
        abs(offset) >= min_offset
        and confidence >= min_confidence
        and gain >= min_gain
    )


def fix_overlaps(cues: list[Cue], min_duration: int = 400, gap: int = 1) -> int:
    """Rozsuń w czasie napisy wstrzyknięte, które nachodzą na sąsiadów.

    Ruszamy wyłącznie napisy z drugiego pliku — czasy napisów bazowych zostają
    nietknięte.  Jeśli wolne okno jest krótsze niż ``min_duration``, napis
    zostaje bez zmian (lepszy nachodzący napis niż migający).
    """
    fixed = 0
    for i, cue in enumerate(cues):
        if not cue.injected:
            continue
        prev_end = cues[i - 1].end if i else None
        next_start = cues[i + 1].start if i + 1 < len(cues) else None
        start, end = cue.start, cue.end
        if prev_end is not None and start < prev_end:
            start = prev_end + gap
        if next_start is not None and end > next_start:
            end = next_start - gap
        if (start, end) == (cue.start, cue.end):
            continue
        if end - start < min_duration:
            continue
        cue.start, cue.end = start, end
        fixed += 1
    return fixed


# --------------------------------------------------------------------------- #
# merging
# --------------------------------------------------------------------------- #
def _overlap(a: Cue, b: Cue) -> int:
    return min(a.end, b.end) - max(a.start, b.start)


def _ref_line(cue: Cue, lang: str, join_lines: bool) -> str:
    if join_lines:
        return "{%s: %s}" % (lang, " ".join(ln.strip() for ln in cue.lines))
    return "{%s: %s}" % (lang, cue.text)


def merge_cues(
    base: list[Cue],
    other: list[Cue],
    lang_base: str = "en",
    lang_other: str = "fr",
    min_overlap: float = 0.2,
    all_overlaps: bool = True,
    join_lines: bool = True,
) -> tuple[list[Cue], dict]:
    """Attach ``other`` cues under the overlapping ``base`` cues.

    ``min_overlap`` is the fraction of the shorter of the two cues that has to
    overlap for the pair to count as the same moment.  Cues from ``other`` that
    match nothing become standalone cues marked with ``MISSING_TEMPLATE``.
    """
    base = sorted(base, key=lambda c: (c.start, c.end, c.order))
    other = sorted(other, key=lambda c: (c.start, c.end, c.order))
    starts = [c.start for c in base]

    import bisect

    stats = {"attached": 0, "missing": 0, "base": len(base), "other": len(other)}
    orphans: list[Cue] = []

    for cue in other:
        # candidates: base cues that can possibly overlap this one
        lo = bisect.bisect_left(starts, cue.start) - 1
        while lo > 0 and base[lo - 1].end > cue.start:
            lo -= 1
        lo = max(0, lo)
        matches: list[tuple[float, Cue]] = []
        for cand in base[lo:]:
            if cand.start >= cue.end:
                break
            ov = _overlap(cand, cue)
            if ov <= 0:
                continue
            shorter = min(cand.duration, cue.duration) or 1
            ratio = ov / shorter
            if ratio >= min_overlap:
                matches.append((ratio, cand))

        if not matches:
            orphans.append(
                Cue(
                    start=cue.start,
                    end=cue.end,
                    lines=[MISSING_TEMPLATE.format(lang=lang_base)],
                    extras=[_ref_line(cue, lang_other, join_lines)],
                    order=cue.order,
                    injected=True,
                )
            )
            stats["missing"] += 1
            continue

        targets = [c for _, c in matches] if all_overlaps else [
            max(matches, key=lambda pair: pair[0])[1]
        ]
        for target in targets:
            target.extras.append(_ref_line(cue, lang_other, join_lines))
        stats["attached"] += 1

    merged = base + orphans
    merged.sort(key=lambda c: (c.start, c.end, c.order))
    return merged, stats


def strip_lang_suffix(stem: str) -> str:
    """Utnij końcowy człon z kodem języka: ``Film.KyoGo.en`` -> ``Film.KyoGo``.

    Bez tego scalanie pliku ``Film.en.srt`` dawało ``Film.en.en_fr.srt`` —
    kod języka pojawiał się dwa razy.
    """
    head, dot, last = stem.rpartition(".")
    if dot and last.lower() in LANG_CODES:
        return head
    return stem


def default_output_path(
    path_base: str, lang_base: str = "en", lang_other: str = "fr"
) -> str:
    """``/x/Film.en.srt`` + (en, fr) -> ``/x/Film.en_fr.srt``.

    Stary kod języka w nazwie jest zastępowany parą języków pliku scalonego.
    """
    root = path_base[:-4] if path_base.lower().endswith(".srt") else path_base
    folder, sep, name = root.rpartition(os.sep)
    return f"{folder}{sep}{strip_lang_suffix(name)}.{lang_base}_{lang_other}.srt"


def merge_srt(
    path_base: str,
    path_other: str,
    out_path: str | None = None,
    lang_base: str = "en",
    lang_other: str = "fr",
    min_overlap: float = 0.2,
    all_overlaps: bool = True,
    join_lines: bool = True,
    newline: str = "\n",
    clean_music: bool = True,
    clean_sdh: bool = True,
    auto_shift: bool = True,
    shift_ms: int | None = None,
    fix_time_overlaps: bool = True,
) -> tuple[str, dict]:
    """Merge two SRT files; returns the path written to (or "-") and statistics.

    With ``out_path`` left at ``None`` the result is saved next to the base file
    as ``<nazwa1>.<lang_base>_<lang_other>.srt``.  Pass ``out_path="-"`` to skip
    writing and only get the text back.
    """
    if out_path is None:
        out_path = default_output_path(path_base, lang_base, lang_other)
    if out_path != "-" and os.path.abspath(out_path) in (
        os.path.abspath(path_base),
        os.path.abspath(path_other),
    ):
        raise ValueError(f"Plik wyjściowy nadpisałby wejściowy: {out_path}")
    base = parse_srt(read_text(path_base))
    other = parse_srt(read_text(path_other))

    clean_stats = {"removed_cues": 0, "cleaned_cues": 0}
    if clean_music or clean_sdh:
        base, s1 = clean_cues(base, music=clean_music, sdh=clean_sdh)
        other, s2 = clean_cues(other, music=clean_music, sdh=clean_sdh)
        for key in clean_stats:
            clean_stats[key] = s1[key] + s2[key]

    offset, conf, gain = estimate_offset(base, other)
    sync_stats = {
        "offset_ms": offset,
        "offset_confidence": round(conf, 3),
        "offset_gain": round(gain, 3),
        "shift_applied": 0,
    }
    if shift_ms is not None:
        shift_cues(other, shift_ms)
        sync_stats["shift_applied"] = shift_ms
    elif auto_shift and should_shift(offset, conf, gain):
        shift_cues(other, offset)
        sync_stats["shift_applied"] = offset

    merged, stats = merge_cues(
        base,
        other,
        lang_base=lang_base,
        lang_other=lang_other,
        min_overlap=min_overlap,
        all_overlaps=all_overlaps,
        join_lines=join_lines,
    )
    stats.update(sync_stats)
    stats["overlaps_fixed"] = fix_overlaps(merged) if fix_time_overlaps else 0

    text = format_srt(merged, newline=newline)
    if out_path != "-":
        with open(out_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
    stats.update(clean_stats)
    stats["output"] = out_path
    stats["text"] = text
    return out_path, stats


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Łączy dwa pliki SRT: bazowy + napisy pomocnicze pod spodem."
    )
    ap.add_argument("base", nargs="?", help="plik główny (np. angielski)")
    ap.add_argument("other", nargs="?", help="plik pomocniczy (np. francuski)")
    ap.add_argument(
        "-o",
        "--output",
        help="plik wyjściowy; domyślnie obok pliku głównego jako "
        "<nazwa1>.<lang1>_<lang2>.srt, '-' wypisuje na stdout",
    )
    ap.add_argument("--lang-base", default="en", help="tag języka bazowego (domyślnie en)")
    ap.add_argument("--lang-other", default="fr", help="tag języka pomocniczego (domyślnie fr)")
    ap.add_argument(
        "--min-overlap",
        type=float,
        default=0.2,
        help="minimalna część wspólna czasu (0-1) uznawana za ten sam moment",
    )
    ap.add_argument(
        "--best-match-only",
        action="store_true",
        help="napis pomocniczy trafia tylko pod najlepiej pasujący napis "
        "(domyślnie pod każdy, z którym się pokrywa)",
    )
    ap.add_argument(
        "--keep-overlaps",
        action="store_true",
        help="nie poprawiaj czasów wstrzykniętych napisów nachodzących na sąsiadów",
    )
    ap.add_argument(
        "--no-shift",
        action="store_true",
        help="nie synchronizuj automatycznie pliku pomocniczego",
    )
    ap.add_argument(
        "--shift",
        type=int,
        metavar="MS",
        help="przesuń plik pomocniczy o podaną liczbę ms (wymusza, pomija autodetekcję)",
    )
    ap.add_argument(
        "--print-description",
        action="store_true",
        help="wypisz gotowy opis (kontekst) do wklejenia w polu description tłumacza",
    )
    ap.add_argument(
        "--keep-linebreaks",
        action="store_true",
        help="zachowaj łamanie linii w napisach pomocniczych",
    )
    ap.add_argument(
        "--keep-music",
        action="store_true",
        help="nie usuwaj tekstów piosenek (♪ ... ♪)",
    )
    ap.add_argument(
        "--keep-sdh",
        action="store_true",
        help="nie usuwaj opisów dla niesłyszących ([ODGŁOSY], (śmiech), ETYKIETA:)",
    )
    ap.add_argument("--crlf", action="store_true", help="zapisz z końcami linii CRLF")
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.print_description:
        print(gst_description(args.lang_base, args.lang_other))
        return 0
    if not args.base or not args.other:
        ap.error("podaj dwa pliki SRT (base i other)")

    out_path, stats = merge_srt(
        args.base,
        args.other,
        out_path=args.output,
        lang_base=args.lang_base,
        lang_other=args.lang_other,
        min_overlap=args.min_overlap,
        all_overlaps=not args.best_match_only,
        join_lines=not args.keep_linebreaks,
        newline="\r\n" if args.crlf else "\n",
        clean_music=not args.keep_music,
        clean_sdh=not args.keep_sdh,
        auto_shift=not args.no_shift,
        shift_ms=args.shift,
        fix_time_overlaps=not args.keep_overlaps,
    )
    if out_path == "-":
        try:
            sys.stdout.write(stats["text"])
            sys.stdout.flush()
        except BrokenPipeError:  # np. `... -o - | head`
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    else:
        print(f"[merge] zapisano: {out_path}", file=sys.stderr)
    print(
        f"[merge] wyczyszczono: {stats['cleaned_cues']} napisów, "
        f"usunięto w całości: {stats['removed_cues']}",
        file=sys.stderr,
    )
    sync = (
        f"przesunięto o {stats['shift_applied']:+d} ms"
        if stats["shift_applied"]
        else "bez przesuwania"
    )
    print(
        f"[merge] synchronizacja: wykryty offset {stats['offset_ms']:+d} ms "
        f"(pewność {stats['offset_confidence']:.2f}, zysk {stats['offset_gain']:.2f}) — {sync}",
        file=sys.stderr,
    )
    print(
        f"[merge] poprawione kolizje czasowe: {stats['overlaps_fixed']}",
        file=sys.stderr,
    )
    print(
        f"[merge] baza: {stats['base']} napisów, pomocnicze: {stats['other']} — "
        f"dopasowane: {stats['attached']}, brakujące: {stats['missing']}",
        file=sys.stderr,
    )
    if out_path != "-":
        print(
            "[merge] pamiętaj o kontekście dla modelu: "
            "srt_merge.py --print-description",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
