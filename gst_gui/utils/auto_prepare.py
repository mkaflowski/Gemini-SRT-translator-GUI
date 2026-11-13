"""Automatic choice and preparation of the subtitles to translate from.

The same defaults the ``translate-subtitles`` skill uses (its ``scan`` plan):

* the base is English - a file next to the video, else a track inside it,
* the reference is the best second language available, in the order French,
  Italian, Spanish, Russian, then others (never the target language and never
  the user's own AI translations); a track in a better language than every
  file next to the video wins,
* an English SDH file or track adds ``{sdh: SPEAKER (NOTE)}`` context lines,
* base + reference (+ SDH) are merged into one bilingual source, with song
  lyrics and hearing-impaired notes removed.

``plan_folder`` only looks; ``execute_plan`` extracts and merges.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from gst_gui.utils import srt_merge
from gst_gui.utils.languages import detect_language_from_filename, normalize_language
from gst_gui.utils.subtitle_tracks import (
    extract_subtitle_track, format_track_label, probe_subtitle_tracks,
)

VIDEO_EXT = {'.mkv', '.mp4', '.avi', '.mov', '.m4v', '.webm', '.wmv', '.ts'}
BASE_LANG = 'en'
# Second language for the bilingual source, best first.
REFERENCE_ORDER = ['fr', 'it', 'es', 'ru', 'pt', 'de', 'nl', 'sv', 'no', 'da', 'fi', 'cs', 'sk',
                   'hu', 'ro', 'uk', 'bg', 'hr', 'sr', 'el', 'tr', 'he', 'ja', 'ko', 'zh']
_SDH_RE = re.compile(r'sdh|hearing|\bcc\b', re.IGNORECASE)


def _ref_rank(lang: str) -> int:
    return REFERENCE_ORDER.index(lang) if lang in REFERENCE_ORDER else len(REFERENCE_ORDER)


def file_tags(name: str) -> tuple[bool, bool]:
    """(forced, sdh) read from the tokens of a subtitle file name."""
    tokens = {t.lower() for t in re.split(r'[.\-_\s\[\]]+', os.path.splitext(name)[0])}
    return 'forced' in tokens, bool(tokens & {'sdh', 'cc', 'hi'})


def file_language(name: str, ai_code: str = 'ar') -> str:
    """'en', 'fr'..., 'en+fr' for a merged file, the AI code for the user's AI translations."""
    merged = srt_merge.parse_merged_name(name)
    if merged:
        return '+'.join(merged)
    stem = os.path.splitext(name)[0].lower()
    if ai_code and stem.endswith('.' + ai_code.lower()):
        return ai_code.lower()
    return detect_language_from_filename(name)


def track_is_sdh(track: dict) -> bool:
    return bool(track.get('sdh') or _SDH_RE.search(track.get('title') or ''))


def plan_video(video: str | None, sub_names: list[str], tracks: list[dict], n_videos: int,
               target_code: str = 'pl', ai_code: str = 'ar') -> dict:
    """Default sources for one video (or for a folder without a video).

    File entries are names inside the folder; ``*_track`` entries are track dicts
    from ``probe_subtitle_tracks``. ``source`` is filled only when nothing has to
    be prepared (an already merged file, or a single usable file).
    """
    not_reference = {BASE_LANG, (target_code or '').lower(), (ai_code or '').lower(), ''}
    stem = os.path.splitext(video)[0] if video else ''
    own = [n for n in sub_names if n_videos <= 1 or n.startswith(stem)]
    langs = {n: file_language(n, ai_code) for n in own}
    single = [n for n in own if langs[n] and '+' not in langs[n] and not file_tags(n)[0]]

    # plain before SDH: cleaning removes the notes, but a plain file has less to remove
    base_files = sorted((n for n in single if langs[n] == BASE_LANG), key=lambda n: file_tags(n)[1])
    sdh_files = [n for n in base_files if file_tags(n)[1]]
    ref_files = sorted((n for n in single if langs[n] not in not_reference),
                       key=lambda n: (_ref_rank(langs[n]), file_tags(n)[1]))
    merged = [n for n in own if '+' in langs[n] and langs[n].startswith(BASE_LANG + '+')
              and not langs[n].endswith('+' + (ai_code or 'ar').lower())]

    plan = {
        'video': video,
        'base': base_files[0] if base_files else None, 'base_track': None,
        'reference': ref_files[0] if ref_files else None,
        'reference_lang': langs[ref_files[0]] if ref_files else None, 'reference_track': None,
        'sdh': sdh_files[0] if sdh_files and sdh_files[0] != (base_files[0] if base_files else None)
        else None,
        'sdh_track': None,
        'merged': merged[0] if merged else None,
        'source': None,
    }
    if plan['merged']:
        plan['source'] = plan['merged']
        return plan

    usable = [t for t in tracks or [] if t.get('text_based') and not t.get('forced')]
    lang_of = lambda t: normalize_language(t.get('language', ''))  # noqa: E731
    en_tracks = sorted((t for t in usable if lang_of(t) == BASE_LANG),
                       key=lambda t: (track_is_sdh(t), not t.get('default')))
    if not plan['base'] and en_tracks:
        plan['base_track'] = en_tracks[0]
    en_sdh = [t for t in en_tracks if track_is_sdh(t) and t is not plan['base_track']]
    if en_sdh and not plan['sdh']:
        plan['sdh_track'] = en_sdh[0]
    ref_tracks = sorted((t for t in usable if lang_of(t) not in not_reference),
                        key=lambda t: (_ref_rank(lang_of(t)), track_is_sdh(t)))
    # a track in a better language than every file next to the video wins
    if ref_tracks and (not plan['reference']
                       or _ref_rank(lang_of(ref_tracks[0])) < _ref_rank(plan['reference_lang'])):
        plan['reference'] = None
        plan['reference_track'] = ref_tracks[0]
        plan['reference_lang'] = lang_of(ref_tracks[0])

    has_base = plan['base'] or plan['base_track']
    has_ref = plan['reference'] or plan['reference_track']
    if plan['base'] and not has_ref:
        plan['source'] = plan['base']  # nothing to merge with
    elif not has_base and plan['reference'] and not plan['reference_track']:
        plan['source'] = plan['reference']  # no English at all: the best other language
    return plan


def plan_folder(folder, target_code: str = 'pl', ai_code: str = 'ar',
                probe=probe_subtitle_tracks) -> list[dict]:
    """One plan per video in the folder (one for the folder when it has no video)."""
    folder = Path(folder)
    names = sorted(p.name for p in folder.iterdir() if p.is_file())
    videos = [n for n in names if os.path.splitext(n)[1].lower() in VIDEO_EXT]
    subs = [n for n in names if n.lower().endswith('.srt')]
    plans = []
    for video in videos:
        tracks = probe(str(folder / video)) or []
        plans.append(plan_video(video, subs, tracks, len(videos), target_code, ai_code))
    if not videos and subs:
        plans.append(plan_video(None, subs, [], 0, target_code, ai_code))
    for plan in plans:
        plan['folder'] = str(folder)
    return plans


def is_ready(plan: dict) -> bool:
    """Whether the plan leads to a file to translate from."""
    return bool(plan.get('source') or plan.get('base') or plan.get('base_track')
                or plan.get('reference') or plan.get('reference_track'))


def describe_plan(plan: dict) -> list[str]:
    """Human-readable lines for the confirmation dialog."""
    title = plan['video'] or 'Subtitles in the folder'
    lines = [f"🎬 {title}"]
    if plan.get('merged'):
        return lines + [f"   use the merged file: {plan['merged']}"]
    if not is_ready(plan):
        return lines + ["   ⚠️ no usable subtitles (no text track, no .srt file) - skipped"]

    def where(file_key, track_key):
        if plan.get(file_key):
            return f"file {plan[file_key]}"
        if plan.get(track_key):
            return f"extract track {format_track_label(plan[track_key])}"
        return None

    base, ref, sdh = where('base', 'base_track'), where('reference', 'reference_track'), \
        where('sdh', 'sdh_track')
    if base:
        lines.append(f"   base (en): {base}")
    else:
        lines.append("   ⚠️ no English subtitles - translating from the reference alone")
    if ref:
        lines.append(f"   reference ({plan['reference_lang']}): {ref}")
    if sdh and base and ref:
        lines.append(f"   speaker context (SDH): {sdh}")
    if base and ref:
        lines.append(f"   → merge into one en+{plan['reference_lang']} source, "
                     "lyrics and SDH notes removed")
    elif not ref:
        lines.append("   → no second language found: translating from the base alone")
    return lines


def _extract(folder: Path, video: str, track: dict, suffix: str, log) -> str | None:
    """Extract a track to '<video stem>.<suffix>.srt'; an existing file is reused."""
    target = folder / f"{Path(video).stem}.{suffix}.srt"
    if target.exists():
        log(f"🎞 {target.name} already exists - using it")
        return str(target)
    result = extract_subtitle_track(str(folder / video), track['type_index'], str(target))
    if result:
        log(f"🎞 Extracted '{format_track_label(track)}' → {target.name}")
    else:
        log(f"❌ Extraction failed: '{format_track_label(track)}' ({video})")
    return result


def execute_plan(plan: dict, log=print) -> str | None:
    """Extract and merge what the plan says. Returns the file to translate from, or None."""
    folder = Path(plan['folder'])
    if plan.get('source'):
        return str(folder / plan['source'])

    base = str(folder / plan['base']) if plan.get('base') else None
    ref = str(folder / plan['reference']) if plan.get('reference') else None
    sdh = str(folder / plan['sdh']) if plan.get('sdh') else None
    ref_lang = plan.get('reference_lang')

    if not base and plan.get('base_track'):
        suffix = f"{BASE_LANG}.sdh" if track_is_sdh(plan['base_track']) else BASE_LANG
        base = _extract(folder, plan['video'], plan['base_track'], suffix, log)
    if not ref and plan.get('reference_track'):
        ref = _extract(folder, plan['video'], plan['reference_track'], ref_lang, log)
    if not sdh and plan.get('sdh_track') and base and ref:
        sdh = _extract(folder, plan['video'], plan['sdh_track'], f"{BASE_LANG}.sdh", log)

    if not base or not ref:
        return base or ref

    out_path, stats = srt_merge.merge_srt(base, ref, lang_base=BASE_LANG, lang_other=ref_lang)
    log(f"🔀 Merged → {Path(out_path).name}: {stats['attached']} matched, "
        f"{stats['missing']} added from {ref_lang}, {stats['removed_cues']} song/SDH cues removed")
    if stats.get('shift_applied'):
        log(f"🔀 Reference shifted by {stats['shift_applied']:+d} ms to match timing")

    if sdh and os.path.abspath(sdh) != os.path.abspath(base):
        tmp = out_path + '.sdh.tmp'
        try:
            _, sdh_stats = srt_merge.merge_srt(out_path, sdh, out_path=tmp, lang_other='sdh')
            os.replace(tmp, out_path)
            log(f"🔀 Speaker context from {Path(sdh).name}: {sdh_stats['attached']} cues")
        except Exception as exc:  # noqa: BLE001 - the SDH context is optional
            log(f"⚠️ Could not add SDH speaker context: {exc}")
            if os.path.exists(tmp):
                os.remove(tmp)
    return out_path
