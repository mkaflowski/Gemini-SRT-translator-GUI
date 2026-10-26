#!/usr/bin/env python3
"""Poprawianie napisów: kontrola długości linii i łamanie ich w sensownych miejscach.

Reguły łamania (od najważniejszej):

1. po znaku interpunkcyjnym (przecinek, kropka, myślnik),
2. na granicy podmiot / orzeczenie,
3. przed spójnikiem (ale, że, i, bo),
4. przed przyimkiem (w, na, bez, do).

Czego nie wolno rozdzielać: rzeczownika od przymiotnika/zaimka, liczby od
rzeczownika lub jednostki, imienia od nazwiska, tytułu od nazwiska, czasownika
od „nie", „się" i posiłkowego, przyimka od jego frazy.

Składnia ma pierwszeństwo przed balansem linijek; przy równorzędnych opcjach
wybierana jest bardziej wyrównana, a górna linijka bywa krótsza od dolnej.

Druga część to czasy wyświetlania: minimum 0,833 s, maksimum 7 s, czas czytania
liczony jako znaki / 17 CPS, odstęp między napisami minimum 83 ms.  Napis wolno
przedłużyć o 2 s po jego końcu i cofnąć o 0,5 s przed początkiem (żeby nie
rozjechał się z mową) — i tylko tyle, ile zostawiają sąsiedzi.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from typing import Iterable, Sequence

try:
    from gst_gui.utils.srt_merge import Cue, format_srt, parse_srt, read_text
except ImportError:  # uruchamianie pliku bezpośrednio, bez pakietu
    from srt_merge import Cue, format_srt, parse_srt, read_text  # type: ignore

MAX_LEN = 42
MAX_LINES = 2

# --------------------------------------------------------------------------- #
# słowniki klas zamkniętych (tu heurystyka jest pewna)
# --------------------------------------------------------------------------- #
PREPOSITIONS = {
    "w", "we", "z", "ze", "za", "na", "do", "od", "ode", "u", "o", "po", "przy",
    "przez", "przed", "przede", "pod", "pode", "nad", "nade", "bez", "dla", "ku",
    "obok", "około", "wobec", "wśród", "między", "pomiędzy", "oprócz", "prócz",
    "poza", "ponad", "poniżej", "powyżej", "spod", "sponad", "sprzed", "spoza",
    "zza", "znad", "dzięki", "według", "wzdłuż", "podczas", "wokół", "naprzeciw",
    "względem", "spośród", "zamiast", "wedle", "mimo", "wbrew", "przeciw",
    "przeciwko", "koło", "obecnie",
}

CONJUNCTIONS = {
    "i", "a", "oraz", "ale", "lecz", "jednak", "jednakże", "że", "iż", "żeby",
    "aby", "by", "bo", "ponieważ", "gdyż", "albowiem", "więc", "dlatego",
    "zatem", "toteż", "jeśli", "jeżeli", "gdyby", "gdy", "kiedy", "choć",
    "chociaż", "albo", "lub", "bądź", "czy", "ani", "ni", "niż", "jak", "jakby",
    "aż", "dopóki", "zanim", "skoro", "czyli", "natomiast", "zaś", "który",
    "która", "które", "którego", "której", "którym", "których", "co", "kto",
    "gdzie", "dokąd", "póki",
}

# nie mogą zaczynać linii (przyklejone do poprzedniego słowa)
CLITICS = {"się", "sie", "by", "bym", "byś", "byśmy", "byście", "li", "że", "no"}

# nie mogą kończyć linii (przyklejone do następnego słowa)
PROCLITICS = {"nie", "ani", "ni", "czy", "niech", "niechaj", "niechże"}

# formy „być" — nie odrywamy ich od imiesłowu („będzie robił")
AUXILIARIES = {
    "jest", "są", "jestem", "jesteś", "jesteśmy", "jesteście", "był", "była",
    "było", "byli", "były", "byłem", "byłam", "byłeś", "byłaś", "byliśmy",
    "byłyśmy", "byliście", "będzie", "będę", "będziesz", "będziemy",
    "będziecie", "będą", "zostanie", "zostały", "został", "została", "zostali",
}

TITLES = {
    "pan", "pana", "panu", "panem", "panie", "pani", "panią", "paniom",
    "panowie", "państwo", "państwa", "dr", "doktor", "doktora", "doktorze",
    "prof", "profesor", "profesora", "profesorze", "pułkownik", "pułkownika",
    "pułkownikowi", "pułkownikiem", "kapitan", "kapitana", "major", "majora",
    "generał", "generała", "generale", "sierżant", "sierżanta", "porucznik",
    "porucznika", "admirał", "admirała", "sir", "lord", "lady", "król", "króla",
    "królowa", "królowej", "święty", "święta", "św", "ks", "ksiądz", "księdza",
    "minister", "ministra", "premier", "premiera", "prezydent", "prezydenta",
    "marszałek", "marszałka", "kpt", "płk", "gen", "mjr", "por", "mister",
    "miss", "madame", "monsieur",
}

# zaimki/określniki, które trzymają się rzeczownika
DETERMINERS = {
    "mój", "moja", "moje", "mojego", "mojej", "moim", "moich", "moimi", "mego",
    "twój", "twoja", "twoje", "twojego", "twojej", "twoim", "twoich",
    "swój", "swoja", "swoje", "swojego", "swojej", "swoim", "swoich",
    "nasz", "nasza", "nasze", "naszego", "naszej", "naszym", "naszych",
    "wasz", "wasza", "wasze", "waszego", "waszej", "waszym", "waszych",
    "jego", "jej", "ich", "ten", "ta", "to", "tego", "tej", "tym", "ci", "te",
    "tych", "tymi", "tamten", "tamta", "tamto", "taki", "taka", "takie",
    "takiego", "takiej", "takim", "każdy", "każda", "każde", "każdego",
    "wszystkie", "wszyscy", "wszystkich", "wszystkim", "żaden", "żadna",
    "każdej", "każdym", "każdych", "każdą", "każdemu", "takich", "takimi",
    "tamtego", "tamtej", "jakiego", "jakiej", "jakim", "jakich",
    "żadne", "żadnego", "jaki", "jaka", "jakie", "jakiś", "jakaś", "jakieś",
    "kilka", "kilku", "wiele", "wielu", "parę", "kilkanaście", "oba", "obie",
    "obu", "sam", "sama", "same", "samego", "cały", "cała", "całe", "całego",
    "inny", "inna", "inne", "innego", "pewien", "pewna", "pewne", "pierwszy",
    "pierwsza", "pierwsze", "drugi", "druga", "drugie", "ostatni", "ostatnia",
    "ostatnie", "dużo", "mało", "trochę", "mnóstwo", "większość",
}

# końcówki typowe dla przymiotników (heurystyka — patrz README funkcji)
ADJECTIVE_SUFFIXES = (
    "owy", "owa", "owe", "owego", "owej", "owym", "owych", "owymi",
    "ny", "na", "ne", "nego", "nej", "nym", "nych", "nymi",
    "ski", "ska", "skie", "skiego", "skiej", "skim", "skich", "skimi",
    "cki", "cka", "ckie", "ckiego", "ckiej", "ckim", "ckich",
    "czy", "cza", "cze", "czego", "czej", "czym", "czych",
    "szy", "sza", "sze", "szego", "szej", "szym", "szych",
    "ki", "ka", "kie", "kiego", "kiej", "kim", "kich", "kimi",
    "wy", "wa", "we", "wego", "wej", "wym", "wych",
    "ły", "łe", "łego", "łej", "łym", "łych",
    "ący", "ąca", "ące", "ącego", "ącej", "ącym", "ących",
    "ową", "ną", "ską", "cką", "czą", "szą", "ką", "wą", "łą", "ącą",
    "emu", "owemu", "nemu", "skiemu", "im", "ich", "imi",
)

# końcówki form osobowych czasownika (do wykrycia granicy podmiot / orzeczenie)
VERB_SUFFIXES = (
    "łem", "łam", "łeś", "łaś", "liśmy", "łyśmy", "liście", "łyście",
    "ł", "ła", "ło", "li", "ły", "sz", "ją", "ił", "ył", "ali", "eli",
    "ają", "ują", "uje", "eje",
)

# częste czasowniki, których nie łapią końcówki (-em, -am, -ę są zbyt mylące:
# tak samo kończą się rzeczowniki w narzędniku, np. „meteorologiem")
COMMON_VERBS = {
    "mam", "masz", "ma", "mamy", "macie", "mają", "wiem", "wiesz", "wie",
    "wiemy", "wiecie", "wiedzą", "chcę", "chce", "chcemy", "chcą", "mogę",
    "może", "możemy", "mogą", "muszę", "musi", "musimy", "muszą", "idę",
    "idzie", "idziemy", "idą", "robi", "robię", "robimy", "robią", "widzę",
    "widzi", "widzimy", "widzą", "mówi", "mówię", "mówimy", "mówią", "daje",
    "dam", "damy", "dają", "trzeba", "warto", "można", "należy",
}

PUNCT_END = ".,!?…:;»”\"'"
DASH_START = ("-", "–", "—")
TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
NUMBER_RE = re.compile(r"^[\d]+([.,:/-][\d]+)*[.,;:!?]?$")


# --------------------------------------------------------------------------- #
# pomocnicze na słowach
# --------------------------------------------------------------------------- #
def visible_len(text: str) -> int:
    """Długość tekstu tak, jak widzi ją widz — bez znaczników <i>, <b> itp."""
    return len(TAG_RE.sub("", text))


def bare(word: str) -> str:
    """Słowo bez znaczników i bez interpunkcji, małymi literami."""
    return TAG_RE.sub("", word).strip(PUNCT_END + "„”\"'()[]-–—").lower()


def ends_with_punct(word: str) -> str | None:
    """Znak interpunkcyjny kończący słowo (albo None)."""
    stripped = TAG_RE.sub("", word).rstrip("\"'”»)")
    return stripped[-1] if stripped and stripped[-1] in PUNCT_END else None


def is_number(word: str) -> bool:
    return bool(NUMBER_RE.match(TAG_RE.sub("", word)))


def is_capitalized(word: str) -> bool:
    clean = TAG_RE.sub("", word).lstrip("„\"'(-–—")
    return bool(clean) and clean[0].isupper()


def looks_like_verb(word: str) -> bool:
    """Czy słowo wygląda na formę osobową czasownika (heurystyka końcówek)."""
    w = bare(word)
    if w in AUXILIARIES or w in COMMON_VERBS:
        return True
    if len(w) < 4 or w in CONJUNCTIONS or w in PREPOSITIONS or w in DETERMINERS:
        return False
    return w.endswith(VERB_SUFFIXES)


def looks_like_adjective(word: str) -> bool:
    """Czy słowo wygląda na przymiotnik (heurystyka końcówek)."""
    w = bare(word)
    if w in DETERMINERS:
        return True
    if len(w) < 5 or w in CONJUNCTIONS or w in PREPOSITIONS or w in AUXILIARIES:
        return False
    if looks_like_verb(w) and w.endswith(("łem", "łam", "łeś", "łaś", "li", "ły")):
        return False
    return w.endswith(ADJECTIVE_SUFFIXES)


# --------------------------------------------------------------------------- #
# ocena miejsca podziału
# --------------------------------------------------------------------------- #
# priorytety zgodne z regułami 1-4
P_PUNCT = 400
P_CLAUSE = 300      # granica podmiot / orzeczenie
P_CONJUNCTION = 200
P_PREPOSITION = 100
P_NONE = 0


def forbidden_break(words: Sequence[str], i: int) -> str | None:
    """Twardy zakaz podziału między ``words[i-1]`` a ``words[i]`` (albo None)."""
    prev, nxt = words[i - 1], words[i]
    p, n = bare(prev), bare(nxt)

    if p in PREPOSITIONS:
        return "przyimek oderwany od frazy"
    if p in PROCLITICS:
        return "„nie" + "\"/partykuła oderwana od czasownika"
    if n in CLITICS:
        return "„się\"/„by\" nie może zaczynać linii"
    if len(p) == 1 and p.isalpha():
        return "jednoliterowe słowo na końcu linii"
    if p in AUXILIARIES and looks_like_verb(nxt):
        return "czasownik posiłkowy oderwany od imiesłowu"
    if is_number(prev) and not is_number(nxt):
        return "liczba oderwana od rzeczownika/jednostki"
    if p in TITLES and is_capitalized(nxt):
        return "tytuł oderwany od nazwiska"
    if (not ends_with_punct(prev) and is_capitalized(prev) and is_capitalized(nxt)
            and n not in CONJUNCTIONS):
        return "imię oderwane od nazwiska"
    if all(bare(w) in CONJUNCTIONS or bare(w) in PREPOSITIONS or bare(w) in CLITICS
           for w in words[:i]):
        return "w górnej linijce zostałoby samo słowo funkcyjne"
    return None


def soft_forbidden_break(words: Sequence[str], i: int) -> str | None:
    """Zakaz miękki — uchylany, gdy nie ma żadnego dozwolonego miejsca."""
    prev, nxt = words[i - 1], words[i]
    if ends_with_punct(prev):
        return None
    if bare(nxt) in PREPOSITIONS or bare(nxt) in CONJUNCTIONS:
        return None
    if bare(prev) in DETERMINERS:
        return "zaimek oderwany od rzeczownika"
    if looks_like_adjective(prev) and not looks_like_verb(nxt) and not is_capitalized(nxt):
        return "przymiotnik oderwany od rzeczownika"
    return None


def break_priority(words: Sequence[str], i: int) -> int:
    """Priorytet miejsca podziału według reguł 1-4."""
    prev, nxt = words[i - 1], words[i]
    punct = ends_with_punct(prev)
    if punct and punct in ",.!?…:;":
        return P_PUNCT
    if TAG_RE.sub("", prev).endswith(DASH_START) or TAG_RE.sub("", nxt).startswith(DASH_START):
        return P_PUNCT
    if bare(nxt) in CONJUNCTIONS:
        return P_CONJUNCTION
    if bare(nxt) in PREPOSITIONS:
        return P_PREPOSITION
    if looks_like_verb(nxt) and not looks_like_verb(prev) and not is_number(prev):
        return P_CLAUSE
    return P_NONE


# --------------------------------------------------------------------------- #
# łamanie linii
# --------------------------------------------------------------------------- #
def split_two(text: str, max_len: int = MAX_LEN) -> list[str] | None:
    """Podziel tekst na dwie linijki; None, gdy nie da się zmieścić."""
    words = text.split()
    if len(words) < 2:
        return None

    candidates = []
    for i in range(1, len(words)):
        top, bottom = " ".join(words[:i]), " ".join(words[i:])
        len_top, len_bottom = visible_len(top), visible_len(bottom)
        if len_top > max_len or len_bottom > max_len:
            continue
        hard = forbidden_break(words, i)
        if hard:
            continue
        soft = soft_forbidden_break(words, i)
        candidates.append({
            "i": i, "top": top, "bottom": bottom, "soft": soft,
            "priority": break_priority(words, i),
            "diff": abs(len_top - len_bottom),
            "top_longer": len_top > len_bottom,
        })

    if not candidates:
        return None

    # zakazy miękkie odpuszczamy dopiero, gdy nic bez nich nie zostało
    clean = [c for c in candidates if not c["soft"]]
    pool = clean or candidates
    # składnia > balans > górna linijka krótsza
    best = min(pool, key=lambda c: (-c["priority"], c["diff"], c["top_longer"]))
    return [best["top"], best["bottom"]]


def rewrap_unit(text: str, max_len: int = MAX_LEN) -> tuple[list[str], str | None]:
    """Złam pojedynczą wypowiedź na maks. 2 linijki.

    Zwraca (linijki, problem) — problem jest opisem, gdy nie udało się zmieścić
    tekstu w limicie i linijki zostają za długie.
    """
    text = " ".join(text.split())
    if visible_len(text) <= max_len:
        return [text], None

    split = split_two(text, max_len)
    if split:
        return split, None

    # nie mieści się w 2×max_len albo każdy podział jest zakazany —
    # dzielimy najbardziej wyrównanie, jak się da, i zgłaszamy problem
    words = text.split()
    best_i, best_diff = None, None
    for i in range(1, len(words)):
        if forbidden_break(words, i):
            continue
        diff = abs(visible_len(" ".join(words[:i])) - visible_len(" ".join(words[i:])))
        if best_diff is None or diff < best_diff:
            best_i, best_diff = i, diff
    if best_i is None:
        return [text], f"nie da się złamać ({visible_len(text)} znaków)"
    top, bottom = " ".join(words[:best_i]), " ".join(words[best_i:])
    longest = max(visible_len(top), visible_len(bottom))
    return [top, bottom], f"linijka nadal ma {longest} znaków"


def _italic_wrap(lines: list[str], source: str) -> list[str]:
    """Jeśli cała wypowiedź była w <i>...</i>, ubierz w tagi każdą linijkę."""
    if len(lines) < 2 or not source.startswith("<i>") or not source.endswith("</i>"):
        return lines
    out = []
    for line in lines:
        line = line.removeprefix("<i>").removesuffix("</i>")
        out.append(f"<i>{line}</i>")
    return out


def fix_cue_lines(lines: Sequence[str], max_len: int = MAX_LEN) -> tuple[list[str], list[str]]:
    """Popraw linijki jednego napisu. Zwraca (nowe linijki, lista problemów)."""
    if all(visible_len(line) <= max_len for line in lines):
        return list(lines), []

    # dialog dwóch osób: każda kwestia (od myślnika) łamana osobno
    is_dialogue = sum(1 for line in lines if TAG_RE.sub("", line).lstrip().startswith(DASH_START)) >= 2
    units = list(lines) if is_dialogue else [" ".join(lines)]

    result: list[str] = []
    problems: list[str] = []
    for unit in units:
        wrapped, problem = rewrap_unit(unit, max_len)
        result.extend(_italic_wrap(wrapped, unit.strip()))
        if problem:
            problems.append(f"{problem}: {TAG_RE.sub('', unit)[:60]}")
    return result, problems


# myślnik dialogowy na początku linii (po ewentualnych tagach), ale nie minus przed liczbą
_LEADING_DASH_RE = re.compile(r"^((?:\s*<[^>]+>)*)\s*[-–—](?!\d)\s*")


def _starts_with_dash(line: str) -> bool:
    return TAG_RE.sub("", line).lstrip().startswith(DASH_START)


def fix_lone_dash(lines: Sequence[str]) -> tuple[list[str], str | None]:
    """Myślnik dialogowy oznacza dwie osoby w jednym napisie.

    Napis z jedną linią zaczynającą się od myślnika to kwestia jednej osoby —
    myślnik jest usuwany („- Whoa.” -> „Whoa.”).  Tak samo, gdy myślnik ma tylko
    pierwsza z dwóch linii, a zdanie przechodzi do drugiej (pierwsza nie kończy
    zdania albo druga zaczyna się małą literą).  Pozostałe napisy z jednym
    myślnikiem na dwie linie są niejednoznaczne (brakujący drugi myślnik albo
    zbędny pierwszy), więc zostają bez zmian i trafiają do raportu.
    """
    lines = list(lines)
    dashed = [i for i, line in enumerate(lines) if _starts_with_dash(line)]
    if len(lines) == 1 and dashed:
        plain = TAG_RE.sub("", lines[0])
        if re.search(r"[.!?…]\s+[-–—]\s*\S", plain):
            # „- Cześć. - Pa.” — dwie kwestie w jednej linii, nie ruszamy
            return lines, f"dwie kwestie w jednej linii: {plain[:80]}"
        new = _LEADING_DASH_RE.sub(r"\1", lines[0], count=1)
        if TAG_RE.sub("", new).strip():
            return [new], None
    if len(lines) == 2 and dashed == [0]:
        first = TAG_RE.sub("", lines[0]).rstrip()
        second = TAG_RE.sub("", lines[1]).lstrip()
        # zdanie przechodzi do drugiej linii — to jedna osoba, myślnik jest zbędny
        # („- Greg wydawał się uczciwym / mechanikiem,”)
        if not first.endswith(tuple(".!?…♪\"”»")) or (second[:1].isalpha() and second[:1].islower()):
            new_first = _LEADING_DASH_RE.sub(r"\1", lines[0], count=1)
            # pusta linia w SRT kończy blok napisu — linia z samym myślnikiem znika
            return [l for l in (new_first, lines[1]) if TAG_RE.sub("", l).strip()], None
    if len(lines) == 2 and dashed == [1]:
        # „Nie? / - Nie.” — druga linia to kwestia drugiej osoby; po polsku obie
        # kwestie dialogu zaczynają się od myślnika (styl closed captions z USA
        # oznacza myślnikiem tylko zmianę mówiącego)
        m = re.match(r"^((?:\s*<[^>]+>)*)\s*", lines[0])
        return [f"{m.group(1)}- {lines[0][m.end():]}", lines[1]], None
    if len(lines) >= 2 and len(dashed) == 1:
        return lines, f"tylko jedna linia z myślnikiem dialogowym: {' / '.join(TAG_RE.sub('', l) for l in lines)[:80]}"
    return lines, None


def fix_cues(cues: list[Cue], max_len: int = MAX_LEN, max_lines: int = MAX_LINES):
    """Popraw wszystkie napisy; zwraca (napisy, statystyki)."""
    stats = {"cues": len(cues), "rewrapped": 0, "too_long_before": 0,
             "too_long_after": 0, "too_many_lines": 0, "dashes_removed": 0, "problems": []}
    for cue in cues:
        # najpierw myślnik: przy łamaniu jedna kwestia z myślnikiem byłaby dalej „dialogiem”
        undashed, dash_problem = fix_lone_dash(cue.lines)
        if undashed != cue.lines:
            stats["dashes_removed"] += 1
            cue.lines = undashed
        if dash_problem:
            stats["problems"].append(dash_problem)
        before = list(cue.lines)
        stats["too_long_before"] += sum(1 for l in before if visible_len(l) > max_len)
        new_lines, problems = fix_cue_lines(before, max_len)
        stats["too_long_after"] += sum(1 for l in new_lines if visible_len(l) > max_len)
        if len(new_lines) > max_lines:
            stats["too_many_lines"] += 1
        if new_lines != before:
            stats["rewrapped"] += 1
            cue.lines = new_lines
        for problem in problems:
            stats["problems"].append(problem)
    return cues, stats


# --------------------------------------------------------------------------- #
# czasy wyświetlania
# --------------------------------------------------------------------------- #
CPS = 17.0            # prędkość czytania dla polskiego (dorośli)
MIN_DURATION = 833    # 5/6 s — poniżej napis miga
MAX_DURATION = 7000   # standard Netflixa
MIN_GAP = 83          # 2 klatki przy 24 fps
LEAD_MAX = 500        # napis może wyprzedzić mowę o pół sekundy
TRAIL_MAX = 2000      # i zostać na ekranie 2 s po niej
MERGE_GAP = 500       # przerwa, poniżej której warto rozważyć scalenie


def char_count(lines: Sequence[str]) -> int:
    """Liczba znaków widocznych dla widza (bez tagów, spacje się liczą)."""
    return visible_len(" ".join(lines))


def needed_duration(lines: Sequence[str], cps: float = CPS,
                    min_duration: int = MIN_DURATION) -> int:
    """Ile czasu potrzeba, by zdążyć przeczytać: znaki / CPS, nie mniej niż minimum."""
    return max(min_duration, round(char_count(lines) / cps * 1000))


def fix_timing(cues: list[Cue], cps: float = CPS, min_duration: int = MIN_DURATION,
               max_duration: int = MAX_DURATION, min_gap: int = MIN_GAP,
               lead: int = LEAD_MAX, trail: int = TRAIL_MAX,
               merge_close: bool = False, max_len: int = MAX_LEN):
    """Napraw czasy wyświetlania tam, gdzie sąsiedzi zostawiają na to miejsce.

    Napis najpierw jest przedłużany w przód (maks. ``trail`` ms po oryginalnym
    końcu), a jeśli to za mało — jego początek jest przesuwany wstecz (maks.
    ``lead`` ms przed oryginalnym startem).  Między napisami zostaje zawsze
    ``min_gap`` ms.  Nic nie jest robione kosztem sąsiada.
    """
    cues.sort(key=lambda c: (c.start, c.end))
    stats = {"short_before": 0, "extended": 0, "still_short": 0, "trimmed_long": 0,
             "gaps_fixed": 0, "merged": 0, "timing_problems": []}

    if merge_close:
        cues, merged = _merge_close_cues(cues, cps, min_duration, max_duration,
                                         min_gap, max_len)
        stats["merged"] = merged

    for i, cue in enumerate(cues):
        prev_end = cues[i - 1].end if i else None
        next_start = cues[i + 1].start if i + 1 < len(cues) else None
        orig_start, orig_end = cue.start, cue.end
        need = needed_duration(cue.lines, cps, min_duration)

        if orig_end - orig_start < need:
            stats["short_before"] += 1

            # 1) wydłuż koniec — najwyżej do sąsiada i najwyżej o `trail`
            limit_end = orig_end + trail
            if next_start is not None:
                limit_end = min(limit_end, next_start - min_gap)
            cue.end = max(cue.end, min(cue.start + need, limit_end))

            # 2) jeśli wciąż za krótko, cofnij początek
            if cue.end - cue.start < need:
                limit_start = orig_start - lead
                if prev_end is not None:
                    limit_start = max(limit_start, prev_end + min_gap)
                cue.start = min(cue.start, max(cue.end - need, limit_start, 0))

            if cue.end - cue.start >= need:
                stats["extended"] += 1
            else:
                stats["still_short"] += 1
                missing = need - (cue.end - cue.start)
                stats["timing_problems"].append(
                    f"{format_time(cue.start)} brakuje {missing / 1000:.1f} s "
                    f"({char_count(cue.lines)} znaków): {TAG_RE.sub('', cue.lines[0])[:45]}")

        # napis nie powinien wisieć dłużej niż maksimum
        if cue.end - cue.start > max_duration:
            cue.end = cue.start + max_duration
            stats["trimmed_long"] += 1

        # odstęp do następnego napisu — przycinamy tylko, jeśli po przycięciu
        # napis nadal daje się przeczytać
        if next_start is not None and next_start - cue.end < min_gap:
            trimmed = next_start - min_gap
            if trimmed - cue.start >= needed_duration(cue.lines, cps, min_duration):
                cue.end = trimmed
                stats["gaps_fixed"] += 1

    return cues, stats


def _merge_close_cues(cues: list[Cue], cps: float, min_duration: int,
                      max_duration: int, min_gap: int, max_len: int):
    """Scal napis za krótki z następnym, jeśli dzieli je mniej niż pół sekundy."""
    out: list[Cue] = []
    merged = 0
    i = 0
    while i < len(cues):
        cue = cues[i]
        nxt = cues[i + 1] if i + 1 < len(cues) else None
        if nxt is not None:
            gap = nxt.start - cue.end
            too_short = cue.end - cue.start < needed_duration(cue.lines, cps, min_duration)
            if too_short and 0 <= gap < MERGE_GAP:
                lines, problems = fix_cue_lines(cue.lines + nxt.lines, max_len)
                span = nxt.end - cue.start
                if (not problems and len(lines) <= MAX_LINES
                        and span <= max_duration
                        and span >= needed_duration(lines, cps, min_duration)):
                    cue.lines = lines
                    cue.end = nxt.end
                    out.append(cue)
                    merged += 1
                    i += 2
                    continue
        out.append(cue)
        i += 1
    return out, merged


def _write_atomic(path: str, text: str) -> None:
    """Zapis przez plik tymczasowy — przerwany zapis nie zniszczy oryginału."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.replace(tmp, path)


def fix_srt(path: str, out_path: str | None = None, max_len: int = MAX_LEN,
            newline: str = "\n", timing: bool = True, cps: float = CPS,
            min_duration: int = MIN_DURATION, max_duration: int = MAX_DURATION,
            min_gap: int = MIN_GAP, merge_close: bool = False) -> tuple[str, dict]:
    """Popraw plik SRT — domyślnie w miejscu, nadpisując plik wejściowy."""
    cues = parse_srt(read_text(path))
    if out_path is None:
        out_path = path
    cues, stats = fix_cues(cues, max_len)
    if timing:
        # najpierw łamanie linii, potem czasy - liczba znaków się nie zmienia,
        # ale scalanie napisów korzysta z gotowego łamania
        cues, tstats = fix_timing(cues, cps=cps, min_duration=min_duration,
                                  max_duration=max_duration, min_gap=min_gap,
                                  merge_close=merge_close, max_len=max_len)
        stats.update(tstats)
    text = format_srt(cues, newline=newline)
    if out_path != "-":
        _write_atomic(out_path, text)
    stats["output"] = out_path
    stats["text"] = text
    return out_path, stats


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Poprawia łamanie linii w napisach SRT.")
    ap.add_argument("input", help="plik SRT")
    ap.add_argument("-o", "--output",
                    help="plik wyjściowy; domyślnie nadpisuje plik wejściowy, '-' na stdout")
    ap.add_argument("--max-len", type=int, default=MAX_LEN,
                    help=f"maksymalna długość linijki (domyślnie {MAX_LEN})")
    ap.add_argument("--check", action="store_true",
                    help="tylko raport, bez zapisu pliku")
    ap.add_argument("--show", action="store_true",
                    help="pokaż każdą poprawioną linijkę (przed / po)")
    ap.add_argument("--no-timing", action="store_true",
                    help="nie ruszaj czasów wyświetlania")
    ap.add_argument("--cps", type=float, default=CPS,
                    help=f"prędkość czytania w znakach na sekundę (domyślnie {CPS:g}; "
                         "dla napisów dla dzieci 13, dla angielskiego 20)")
    ap.add_argument("--min-duration", type=int, default=MIN_DURATION,
                    help=f"minimalny czas napisu w ms (domyślnie {MIN_DURATION})")
    ap.add_argument("--max-duration", type=int, default=MAX_DURATION,
                    help=f"maksymalny czas napisu w ms (domyślnie {MAX_DURATION})")
    ap.add_argument("--min-gap", type=int, default=MIN_GAP,
                    help=f"minimalny odstęp między napisami w ms (domyślnie {MIN_GAP})")
    ap.add_argument("--merge-close", action="store_true",
                    help="scalaj za krótki napis z następnym, gdy dzieli je mniej niż 0,5 s")
    ap.add_argument("--crlf", action="store_true", help="zapisz z końcami linii CRLF")
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.show or args.check:
        original = parse_srt(read_text(args.input))
        for cue in original:
            if all(visible_len(l) <= args.max_len for l in cue.lines):
                continue
            new_lines, _ = fix_cue_lines(cue.lines, args.max_len)
            print(f"\n[{format_time(cue.start)}]")
            for line in cue.lines:
                print(f"  - {line}   ({visible_len(line)})")
            for line in new_lines:
                print(f"  + {line}   ({visible_len(line)})")

    out_path, stats = fix_srt(
        args.input,
        out_path="-" if args.check else args.output,
        max_len=args.max_len,
        newline="\r\n" if args.crlf else "\n",
        timing=not args.no_timing,
        cps=args.cps,
        min_duration=args.min_duration,
        max_duration=args.max_duration,
        min_gap=args.min_gap,
        merge_close=args.merge_close,
    )
    if args.output == "-" and not args.check:
        sys.stdout.write(stats["text"])
    elif not args.check:
        print(f"\n[fix] zapisano: {out_path}", file=sys.stderr)

    print(f"[fix] napisów: {stats['cues']}, przełamanych: {stats['rewrapped']}, "
          f"za długich linii: {stats['too_long_before']} → {stats['too_long_after']}, "
          f"usuniętych pojedynczych myślników: {stats['dashes_removed']}",
          file=sys.stderr)
    if "short_before" in stats:
        print(f"[fix] czasy: za krótkich {stats['short_before']} → "
              f"wydłużonych {stats['extended']}, bez miejsca {stats['still_short']}; "
              f"skróconych zbyt długich: {stats['trimmed_long']}, "
              f"poprawionych odstępów: {stats['gaps_fixed']}, "
              f"scalonych: {stats['merged']}", file=sys.stderr)
    for problem in stats["problems"]:
        print(f"[fix] ⚠️  {problem}", file=sys.stderr)
    if args.show:
        for problem in stats.get("timing_problems", []):
            print(f"[fix] ⏱  {problem}", file=sys.stderr)
    return 0


def format_time(ms: int) -> str:
    s, ms = divmod(max(0, ms), 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


if __name__ == "__main__":
    raise SystemExit(main())
