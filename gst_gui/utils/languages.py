"""
Language code helpers: normalize ISO 639-1/639-2 codes and names,
detect the language of a subtitle file from its name, and format
a display label with a flag emoji (e.g. 'FR 🇫🇷').
"""
import re
from pathlib import Path

# ISO 639-1 code -> (ISO 639-2 codes / names that map to it, flag country code)
_LANGUAGES = {
    'en': ({'eng', 'english'}, 'GB'),
    'pl': ({'pol', 'polish', 'polski'}, 'PL'),
    'fr': ({'fre', 'fra', 'french', 'francais'}, 'FR'),
    'de': ({'ger', 'deu', 'german', 'deutsch'}, 'DE'),
    'es': ({'spa', 'esp', 'spanish', 'espanol'}, 'ES'),
    'it': ({'ita', 'italian', 'italiano'}, 'IT'),
    'pt': ({'por', 'portuguese', 'portugues'}, 'PT'),
    'ru': ({'rus', 'russian'}, 'RU'),
    'uk': ({'ukr', 'ukrainian'}, 'UA'),
    'ja': ({'jpn', 'japanese'}, 'JP'),
    'ko': ({'kor', 'korean'}, 'KR'),
    'zh': ({'chi', 'zho', 'chinese'}, 'CN'),
    'ar': ({'ara', 'arabic'}, 'SA'),
    'hi': ({'hin', 'hindi'}, 'IN'),
    'nl': ({'dut', 'nld', 'dutch'}, 'NL'),
    'sv': ({'swe', 'swedish'}, 'SE'),
    'no': ({'nor', 'nob', 'norwegian'}, 'NO'),
    'da': ({'dan', 'danish'}, 'DK'),
    'fi': ({'fin', 'finnish'}, 'FI'),
    'tr': ({'tur', 'turkish'}, 'TR'),
    'he': ({'heb', 'hebrew'}, 'IL'),
    'el': ({'gre', 'ell', 'greek'}, 'GR'),
    'cs': ({'cze', 'ces', 'czech'}, 'CZ'),
    'sk': ({'slo', 'slk', 'slovak'}, 'SK'),
    'hu': ({'hun', 'hungarian'}, 'HU'),
    'ro': ({'rum', 'ron', 'romanian'}, 'RO'),
    'bg': ({'bul', 'bulgarian'}, 'BG'),
    'hr': ({'hrv', 'croatian'}, 'HR'),
    'sr': ({'srp', 'serbian'}, 'RS'),
    'sl': ({'slv', 'slovenian'}, 'SI'),
    'et': ({'est', 'estonian'}, 'EE'),
    'lv': ({'lav', 'latvian'}, 'LV'),
    'lt': ({'lit', 'lithuanian'}, 'LT'),
    'vi': ({'vie', 'vietnamese'}, 'VN'),
    'th': ({'tha', 'thai'}, 'TH'),
    'id': ({'ind', 'indonesian'}, 'ID'),
}

_ALIASES = {}
for _code, (_names, _flag) in _LANGUAGES.items():
    _ALIASES[_code] = _code
    for _name in _names:
        _ALIASES[_name] = _code


def normalize_language(value):
    """Map 'eng' / 'English' / 'en' / 'en-US' to 'en'. Returns '' if unknown."""
    if not value:
        return ''
    value = value.strip().lower()
    if value in _ALIASES:
        return _ALIASES[value]
    base = re.split(r'[-_]', value)[0]
    return _ALIASES.get(base, '')


def flag_emoji(code):
    """Flag emoji for a language code ('fr' -> '🇫🇷'), '' if unknown."""
    code = normalize_language(code)
    if not code:
        return ''
    country = _LANGUAGES[code][1]
    return ''.join(chr(0x1F1E6 + ord(c) - ord('A')) for c in country)


def country_code(code):
    """Flag country for a language code ('en' -> 'GB'), '' if unknown."""
    code = normalize_language(code)
    return _LANGUAGES[code][1] if code else ''


def language_label(code, with_emoji=False):
    """Display label for the file list, e.g. 'FR' (or 'FR 🇫🇷'). '' if unknown."""
    code = normalize_language(code)
    if not code:
        return ''
    return f"{code.upper()} {flag_emoji(code)}" if with_emoji else code.upper()


def detect_language_from_filename(filename):
    """
    Detect the subtitle language from tokens at the end of the file name,
    e.g. 'Movie.2020.fr.srt', 'Movie.2.fr.srt', 'Movie_extracted_eng.srt' -> 'fr'/'fr'/'en'.
    Only the last meaningful token is checked (numbers and flags like 'forced'
    are skipped) to avoid false positives from titles such as 'It.Follows'.
    """
    if not filename:
        return ''
    stem = Path(str(filename)).stem
    tokens = [t for t in re.split(r'[.\-_\s]+', stem) if t]
    for token in reversed(tokens):
        token = token.lower()
        if token.isdigit() or token in ('forced', 'sdh', 'cc', 'hi'):
            continue
        return normalize_language(token)
    return ''
