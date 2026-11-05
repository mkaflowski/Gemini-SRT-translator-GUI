#!/usr/bin/env python3
"""Helper for the translate-subtitles skill (no dependencies besides Python 3.9+ and ffmpeg).

Subcommands (all print JSON on stdout, so an AI agent can read the result):

  scan <folder>                     videos (+ embedded subtitle tracks), subtitle files and their languages
  extract <video> --tracks 0,2      extract tracks to <video>.<lang>.srt
          [--on-exists error|overwrite|number|skip]
  clean <srt> -o OUT                remove song lyrics (♪ ... ♪) and hearing-impaired notes ([door slams],
          [--keep-music --keep-sdh] 'JOHN:' labels), same as the GUI does when merging
  split <srt> --out-dir DIR         cut a subtitle file into translation batches (batch_001.json ...)
          [--batch-size 100]        at scene breaks (long pauses after a finished sentence),
          [--context 10 --context-after 5]   with read-only context from the neighbouring batches
          [--force]                 (refuses to overwrite started work without it)
  status --out-dir DIR              which batches are done / still to translate (for resuming)
  check --out-dir DIR               find slips in translated batches: copied {xx: ...} lines or
                                    markers, untranslated or empty lines, >2 lines, broken tags
  apply <srt> --out-dir DIR -o OUT  build the translated SRT from DIR/out_001.json ... (timings from <srt>)
          [--stamp "# Translated by ... #"]
  frames <srt> <video> --index 12,40 --out-dir DIR
          [--offsets=-5,0,5,10,20,60]  for unclear lines: frames around the subtitle (default 5 s before,
          [--no-text]               the moment, 5 s after, then +10, +20 and +60 s) in ONE small image
                                    per subtitle, with the subtitle shown at each moment drawn on its frame
  stamp <srt> --model NAME          add "# Translated by NAME #" as the first subtitle (like the GUI;
                                    replaces an existing stamp instead of adding a second one)
  review-split <translated> <source> --out-dir DIR [--batch-size 150]
                                    pair translation with source by time, for the review
  review-apply <translated> <fixes.json>
                                    apply corrected lines [{"index": "12", "text": "..."}], re-wrapped

The merge and fix steps use srt_merge.py / srt_fix.py from this folder
(copies of the modules used by the Gemini SRT Translator GUI).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from srt_merge import Cue, clean_cues, format_srt, parse_srt, read_text, parse_merged_name  # noqa: E402
from srt_fix import fix_cue_lines, fix_srt, has_slash_break  # noqa: E402

VIDEO_EXT = {'.mkv', '.mp4', '.avi', '.mov', '.m4v', '.webm', '.wmv', '.ts'}
SUB_EXT = {'.srt'}
TEXT_CODECS = {'subrip', 'srt', 'ass', 'ssa', 'mov_text', 'webvtt', 'text', 'mpl2', 'subviewer'}
STAMP_RE = re.compile(r"^#.*(translated by|przetłumaczone).*#$", re.IGNORECASE)

LANGS = {
    'en': {'eng', 'english'}, 'pl': {'pol', 'polish', 'polski'},
    'fr': {'fre', 'fra', 'french', 'francais'}, 'de': {'ger', 'deu', 'german'},
    'es': {'spa', 'esp', 'spanish'}, 'it': {'ita', 'italian'}, 'pt': {'por', 'portuguese'},
    'ru': {'rus', 'russian'}, 'uk': {'ukr', 'ukrainian'}, 'ja': {'jpn', 'japanese'},
    'ko': {'kor', 'korean'}, 'zh': {'chi', 'zho', 'chinese'}, 'ar': {'ara', 'arabic'},
    'nl': {'dut', 'nld', 'dutch'}, 'sv': {'swe', 'swedish'}, 'no': {'nor', 'nob', 'norwegian'},
    'da': {'dan', 'danish'}, 'fi': {'fin', 'finnish'}, 'tr': {'tur', 'turkish'},
    'he': {'heb', 'hebrew'}, 'el': {'gre', 'ell', 'greek'}, 'cs': {'cze', 'ces', 'czech'},
    'sk': {'slo', 'slk', 'slovak'}, 'hu': {'hun', 'hungarian'}, 'ro': {'rum', 'ron', 'romanian'},
    'bg': {'bul', 'bulgarian'}, 'hr': {'hrv', 'croatian'}, 'sr': {'srp', 'serbian'},
}
ALIASES = {alias: code for code, names in LANGS.items() for alias in names | {code}}


def out(data):
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(data, ensure_ascii=False, indent=2))


def norm_lang(value):
    value = (value or '').strip().lower()
    return ALIASES.get(value) or ALIASES.get(re.split(r'[-_]', value)[0], '') if value else ''


def lang_from_name(path):
    merged = parse_merged_name(path)
    if merged:
        return '+'.join(merged)
    stem = os.path.splitext(os.path.basename(path))[0]
    for token in reversed([t for t in re.split(r'[.\-_\s]+', stem) if t]):
        if token.isdigit() or token.lower() in ('forced', 'sdh', 'cc', 'hi'):
            continue
        return norm_lang(token)
    return ''


def probe_tracks(video):
    cmd = ['ffprobe', '-v', 'error', '-select_streams', 's',
           '-show_entries', 'stream=index,codec_name:stream_tags=language,title:stream_disposition=default,forced',
           '-of', 'json', video]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
        streams = json.loads(res.stdout or '{}').get('streams', [])
    except Exception as e:  # ffprobe missing etc.
        return {'error': str(e)}
    tracks = []
    for i, s in enumerate(streams):
        tags, disp = s.get('tags', {}) or {}, s.get('disposition', {}) or {}
        codec = s.get('codec_name', '') or ''
        tracks.append({
            'track': i,
            'language': norm_lang(tags.get('language', '')) or tags.get('language', '') or 'und',
            'title': tags.get('title', '') or '',
            'codec': codec,
            'default': bool(disp.get('default')),
            'forced': bool(disp.get('forced')),
            'extractable': codec.lower() in TEXT_CODECS,
        })
    return tracks


# --------------------------------------------------------------------------- #
def cmd_scan(args):
    folder = os.path.abspath(args.folder)
    videos, subs = [], []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        ext = os.path.splitext(name)[1].lower()
        if ext in VIDEO_EXT:
            videos.append({'file': name, 'tracks': probe_tracks(path) if not args.no_probe else None})
        elif ext in SUB_EXT:
            try:
                count = len(parse_srt(read_text(path)))
            except Exception:
                count = None
            subs.append({'file': name, 'language': lang_from_name(name), 'cues': count})
    out({'folder': folder, 'videos': videos, 'subtitles': subs,
         'languages_present': sorted({s['language'] for s in subs if s['language']})})


def cmd_extract(args):
    video = os.path.abspath(args.video)
    tracks = probe_tracks(video)
    if isinstance(tracks, dict):
        out({'error': tracks['error']})
        return 1
    stem = os.path.splitext(video)[0]
    wanted = [int(t) for t in args.tracks.split(',') if t.strip()]
    results, created = [], set()
    for idx in wanted:
        track = next((t for t in tracks if t['track'] == idx), None)
        if not track:
            results.append({'track': idx, 'status': 'not found'})
            continue
        if not track['extractable']:
            results.append({'track': idx, 'status': f"bitmap codec {track['codec']} - cannot convert to SRT"})
            continue
        lang = track['language'] if track['language'] != 'und' else f"track{idx}"
        target = f"{stem}.{lang}.srt"

        def numbered():
            n = 2
            while os.path.exists(f"{stem}.{n}.{lang}.srt") or f"{stem}.{n}.{lang}.srt" in created:
                n += 1
            return f"{stem}.{n}.{lang}.srt"

        if target in created:
            target = numbered()
        elif os.path.exists(target):
            if args.on_exists == 'error':
                results.append({'track': idx, 'status': 'exists', 'file': os.path.basename(target),
                                'numbered_alternative': os.path.basename(numbered())})
                continue
            if args.on_exists == 'skip':
                results.append({'track': idx, 'status': 'skipped', 'file': os.path.basename(target)})
                continue
            if args.on_exists == 'number':
                target = numbered()
        cmd = ['ffmpeg', '-y', '-v', 'error', '-i', video, '-map', f'0:s:{idx}', '-c:s', 'srt', target]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
        if res.returncode == 0 and os.path.exists(target):
            created.add(target)
            results.append({'track': idx, 'status': 'ok', 'file': os.path.basename(target)})
        else:
            results.append({'track': idx, 'status': 'failed', 'error': res.stderr.strip()[-300:]})
    out({'video': os.path.basename(video), 'results': results})
    return 0 if all(r['status'] in ('ok', 'skipped') for r in results) else 2


SENTENCE_END_RE = re.compile(r'[.!?…♪"»)\]]\s*(</[ib]>)?\s*$', re.IGNORECASE)


def _ends_sentence(cue):
    """True when the cue's main text (not the {xx: ...} reference lines) ends a sentence."""
    main = [l for l in cue.lines if not l.lstrip().startswith('{')]
    return bool(main) and bool(SENTENCE_END_RE.search(main[-1]))


def scene_cuts(cues, size, slack=0.3):
    """
    Split points close to `size` cues per batch, chosen at scene boundaries:
    in the window size*(1-slack)..size*(1+slack) cut where the pause between two
    subtitles is longest, preferring a cut right after a finished sentence.
    Returns a list of (start, end) index ranges (end exclusive).
    """
    n = len(cues)
    ranges, start = [], 0
    lo_k, hi_k = max(1, int(size * (1 - slack))), max(2, int(size * (1 + slack)))
    while start < n:
        if n - start <= hi_k:
            ranges.append((start, n))
            break
        best_k, best_score = start + size, None
        for k in range(start + lo_k, min(n, start + hi_k + 1)):
            gap = cues[k].start - cues[k - 1].end  # pause before cue k, ms
            # pause length decides; a finished sentence and a size close to the target break ties
            score = gap + (2000 if _ends_sentence(cues[k - 1]) else -3000) - abs(k - (start + size)) * 10
            if best_score is None or score > best_score:
                best_k, best_score = k, score
        ranges.append((start, best_k))
        start = best_k
    return ranges


def cmd_split(args):
    cues = parse_srt(read_text(args.srt))
    os.makedirs(args.out_dir, exist_ok=True)
    done = glob.glob(os.path.join(glob.escape(args.out_dir), 'out_*.json'))
    if done and not args.force:
        out({'error': 'work in progress', 'translated_batches': len(done),
             'hint': 'run "status" to resume, or split again with --force to start over'})
        return 2
    for old in glob.glob(os.path.join(glob.escape(args.out_dir), 'batch_*.json')) + done:
        os.remove(old)

    def item(i):
        return {'index': str(i + 1), 'text': cues[i].text}

    if args.fixed:
        ranges = [(s, min(s + args.batch_size, len(cues))) for s in range(0, len(cues), args.batch_size)]
    else:
        ranges = scene_cuts(cues, args.batch_size)
    batches = []
    for b, (start, end) in enumerate(ranges, 1):
        data = {
            'translate': [item(i) for i in range(start, end)],
            # read-only context so sentences at the edges are not translated blind
            'context_before': [item(i) for i in range(max(0, start - args.context), start)],
            'context_after': [item(i) for i in range(end, min(len(cues), end + args.context_after))],
        }
        path = os.path.join(args.out_dir, f'batch_{b:03d}.json')
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        info = {'batch': os.path.basename(path), 'output': f'out_{b:03d}.json',
                'first': str(start + 1), 'last': str(end), 'count': end - start}
        if end < len(cues):
            info['pause_after_ms'] = cues[end].start - cues[end - 1].end
            info['sentence_end'] = _ends_sentence(cues[end - 1])
        batches.append(info)
    manifest = {'source': os.path.abspath(args.srt), 'cues': len(cues), 'batch_size': args.batch_size,
                'split': 'fixed' if args.fixed else 'scenes', 'batches': batches}
    with open(os.path.join(args.out_dir, 'manifest.json'), 'w', encoding='utf-8') as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
    out(manifest)


def _load_json(path):
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def _get_batch_items(data):
    if isinstance(data, dict):
        return data.get('translate', [])
    elif isinstance(data, list):
        return data
    return []


def _batch_status(out_dir):
    """[(batch info, state, problem)] for every batch in the manifest (or discovered)."""
    manifest_path = os.path.join(out_dir, 'manifest.json')
    if os.path.exists(manifest_path):
        try:
            manifest = _load_json(manifest_path)
            batches = manifest.get('batches', [])
        except Exception:
            manifest = {'source': 'unknown', 'batches': []}
            batches = []
    else:
        manifest = {'source': 'unknown', 'batches': []}
        batches = []

    if not batches:
        batch_files = sorted(glob.glob(os.path.join(glob.escape(out_dir), 'batch_*.json')))
        for bp in batch_files:
            b_name = os.path.basename(bp)
            out_name = b_name.replace('batch_', 'out_')
            batches.append({'batch': b_name, 'output': out_name})
        manifest['batches'] = batches

    rows = []
    for info in batches:
        b_path = os.path.join(out_dir, info['batch'])
        out_path = os.path.join(out_dir, info['output'])
        if not os.path.exists(out_path):
            rows.append((info, 'todo', ''))
            continue
        try:
            b_data = _load_json(b_path)
            src_items = _get_batch_items(b_data)
            wanted = [str(i['index']).strip() for i in src_items]
        except Exception as e:
            rows.append((info, 'broken', f'invalid source JSON: {e}'))
            continue
        try:
            got_data = _load_json(out_path)
            if not isinstance(got_data, list):
                rows.append((info, 'broken', 'output is not a JSON list'))
                continue
            got = [str(i.get('index', '')).strip() for i in got_data]
        except Exception as e:
            rows.append((info, 'broken', f'invalid JSON: {e}'))
            continue

        if len(got) != len(wanted):
            rows.append((info, 'incomplete', f'count mismatch: expected {len(wanted)} items, got {len(got)} (possible merged cues!)'))
            continue

        missing = [i for i in wanted if i not in got]
        extra = [i for i in got if i not in wanted]
        if missing or extra:
            rows.append((info, 'incomplete', f'index mismatch: missing {missing[:10]} extra {extra[:10]}'))
            continue

        if got != wanted:
            rows.append((info, 'incomplete', 'indices out of sequence or duplicated'))
            continue

        src_dict = {str(i['index']).strip(): i.get('text', '') for i in src_items}
        empties = []
        for item in got_data:
            idx = str(item.get('index', '')).strip()
            text = str(item.get('text', ''))
            src_t = src_dict.get(idx, '')
            src_clean = '\n'.join(l for l in src_t.split('\n') if not l.lstrip().startswith('{')).strip()
            if src_clean and not text.strip():
                empties.append(idx)
        if empties:
            rows.append((info, 'incomplete', f'empty translation at cues {empties[:10]} (merged cues / shift desync risk!)'))
            continue

        rows.append((info, 'done', ''))
    return manifest, rows


def cmd_status(args):
    manifest, rows = _batch_status(args.out_dir)
    todo = [r[0]['batch'] for r in rows if r[1] != 'done']
    out({'source': manifest['source'], 'batches': len(rows),
         'done': sum(1 for r in rows if r[1] == 'done'),
         'next': todo[0] if todo else None,
         'not_done': [{'batch': r[0]['batch'], 'state': r[1], 'problem': r[2]} for r in rows if r[1] != 'done']})


# Typical model slips that a final review would not reliably catch
_REF_LINE_RE = re.compile(r'^\s*\{[a-z]{2,3}:', re.IGNORECASE | re.MULTILINE)
_MARKER_RE = re.compile(r'BRAK NAPIS', re.IGNORECASE)
_TAG_RE = re.compile(r'<[^>]+>')


def check_items(source, translated, max_len=42):
    """Problems in translated batch items compared with their source items."""
    problems = []
    src_by_index = {str(i['index']).strip(): i.get('text', '') for i in source}
    trans_by_index = {}
    for item in translated:
        idx = str(item.get('index', '')).strip()
        text = str(item.get('text', ''))
        trans_by_index[idx] = text
        src = src_by_index.get(idx, '')
        src_main = '\n'.join(l for l in src.split('\n') if not l.lstrip().startswith('{'))
        found = []
        if _REF_LINE_RE.search(text):
            found.append('contains a {xx: ...} reference line')
        if _MARKER_RE.search(text):
            found.append('contains the missing-subtitle marker')
        if any(has_slash_break(l) for l in text.split('\n')):
            found.append('uses " / " as a line break (use a real line break "\\n")')
        if '—' in text or '–' in text:
            found.append('uses a long dash "—" or "–" (use "-")')
        if not text.strip() and src_main.strip():
            found.append('empty translation (desync/shift risk!)')
        plain = _TAG_RE.sub('', text).strip()
        if len(plain) >= 12 and plain == _TAG_RE.sub('', src_main).strip():
            found.append('identical to the source (not translated?)')
        lines = [l for l in text.split('\n') if l.strip()]
        if len(lines) > 2:
            found.append(f'{len(lines)} lines (max 2)')
        too_long = [l for l in lines if len(_TAG_RE.sub('', l)) > max_len + 8]
        if too_long:
            found.append(f'line longer than {max_len + 8} chars')
        for tag in ('i', 'b'):
            if text.count(f'<{tag}>') != text.count(f'</{tag}>'):
                found.append(f'unbalanced <{tag}> tag')
        font_open = len(re.findall(r'<font\b[^>]*>', text, re.IGNORECASE))
        font_close = len(re.findall(r'</font>', text, re.IGNORECASE))
        if font_open != font_close:
            found.append(f'unbalanced <font> tag ({font_open} open, {font_close} close)')
        if found:
            problems.append({'index': idx, 'problems': found, 'text': text})

    for idx in src_by_index:
        if idx not in trans_by_index:
            problems.append({'index': idx, 'problems': ['missing cue in translation'], 'text': ''})
    return problems


def cmd_check(args):
    manifest, rows = _batch_status(args.out_dir)
    report = []
    critical_count = 0
    for info, state, problem in rows:
        if state == 'todo':
            continue
        entry = {'batch': info['output']}
        if problem:
            entry['problems'] = [problem]
            critical_count += 1
        if state != 'broken':
            b_path = os.path.join(args.out_dir, info['batch'])
            o_path = os.path.join(args.out_dir, info['output'])
            src_items = _get_batch_items(_load_json(b_path))
            out_items = _load_json(o_path)
            issues = check_items(src_items, out_items)
            if issues:
                entry['items'] = issues
        if len(entry) > 1:
            report.append(entry)

    checked = sum(1 for r in rows if r[1] != 'todo')

    coverage_errors = []
    if getattr(args, 'srt', None) and os.path.exists(args.srt):
        cues = parse_srt(read_text(args.srt))
        translated_all = {}
        for r in rows:
            out_p = os.path.join(args.out_dir, r[0]['output'])
            if os.path.exists(out_p):
                try:
                    for it in _load_json(out_p):
                        idx = str(it.get('index', '')).strip()
                        if idx:
                            translated_all[idx] = str(it.get('text', ''))
                except Exception:
                    pass
        missing_cues = [str(i + 1) for i in range(len(cues)) if str(i + 1) not in translated_all]
        if missing_cues:
            coverage_errors.append(f'missing {len(missing_cues)} cues from full movie: {missing_cues[:10]}')
            critical_count += len(missing_cues)

    status_str = 'error' if critical_count else ('warning' if report else 'ok')
    out({'checked_batches': checked,
         'total_batches': len(rows),
         'batches_with_problems': len(report),
         'critical_errors': critical_count,
         'status': status_str,
         'coverage_errors': coverage_errors,
         'report': report})
    return 2 if critical_count else 0


def cmd_apply(args):
    cues = parse_srt(read_text(args.srt))
    manifest, rows = _batch_status(args.out_dir)

    # 1. Automatic strict validation before applying (prevents desynchronization)
    if not getattr(args, 'no_check', False):
        critical_issues = []
        for info, state, problem in rows:
            if state == 'todo':
                critical_issues.append(f"{info['output']}: batch not translated yet")
            elif state in ('broken', 'incomplete'):
                critical_issues.append(f"{info['output']}: {problem}")
        if critical_issues and not args.allow_missing:
            out({
                'status': 'error',
                'error': 'Batch validation failed before apply: subtitle desynchronization or missing cues detected!',
                'critical_count': len(critical_issues),
                'critical_errors': critical_issues,
                'hint': 'Fix the batches or use subs_tool.py check for details. Do not merge cues!'
            })
            return 2

    # 2. Collect translations from all batch output files
    translated = {}
    for info in manifest.get('batches', []):
        path = os.path.join(args.out_dir, info.get('output', ''))
        if os.path.exists(path):
            try:
                for item in _load_json(path):
                    idx = str(item.get('index', '')).strip()
                    if idx:
                        translated[idx] = str(item.get('text', ''))
            except Exception:
                pass

    if not translated:
        for path in sorted(glob.glob(os.path.join(glob.escape(args.out_dir), 'out_*.json'))):
            with open(path, encoding='utf-8') as fh:
                for item in json.load(fh):
                    idx = str(item.get('index', '')).strip()
                    if idx:
                        translated[idx] = str(item.get('text', ''))

    missing = [str(i + 1) for i in range(len(cues)) if str(i + 1) not in translated]
    if missing and not args.allow_missing:
        out({'status': 'error', 'error': 'missing translations', 'missing_count': len(missing), 'missing': missing[:50]})
        return 2

    # 3. Assemble cues: NEVER silently drop empty cues!
    result = []
    dropped_cues = []
    empty_errors = []
    for i, cue in enumerate(cues):
        idx_str = str(i + 1)
        text = translated.get(idx_str)
        if text is None:
            if args.allow_missing:
                text = cue.text
            else:
                out({'status': 'error', 'error': f'Missing translation for cue {idx_str}'})
                return 2

        lines = [l.rstrip() for l in text.replace('\r', '').split('\n')]
        lines = [l for l in lines if l.strip()]
        if not lines:
            src_clean = '\n'.join(l for l in cue.text.split('\n') if not l.lstrip().startswith('{')).strip()
            if src_clean and not getattr(args, 'allow_empty', False):
                empty_errors.append({'cue': idx_str, 'source': src_clean[:60]})
                continue
            if not getattr(args, 'allow_empty', False) and not src_clean:
                # Source was also blank, safe to omit
                continue
            dropped_cues.append(idx_str)
            continue
        result.append(Cue(start=cue.start, end=cue.end, lines=lines, coords=cue.coords))

    if empty_errors:
        out({
            'status': 'error',
            'error': f'{len(empty_errors)} cue(s) have empty translations while source has text! '
                     f'Silently dropping them causes off-by-one subtitle desynchronization. '
                     f'Fix the translation or specify --allow-empty if intentional.',
            'empty_cues': empty_errors[:20]
        })
        return 2

    if len(result) != len(cues) and not args.allow_missing and not getattr(args, 'allow_empty', False):
        out({
            'status': 'error',
            'error': f'Result cue count ({len(result)}) does not match source count ({len(cues)})! '
                     f'Dropped cues: {dropped_cues[:20]}. Desynchronization prevented.'
        })
        return 2

    if args.stamp:
        result = add_stamp(result, args.stamp)
    with open(args.output, 'w', encoding='utf-8', newline='') as fh:
        fh.write(format_srt(result))
    out({'output': os.path.abspath(args.output), 'cues': len(result), 'untranslated': len(missing)})
    return 0


def add_stamp(cues, text):
    """Put the translator info first, timed like the GUI does: it ends at the first
    subtitle (max 5 s) and starts at 1 s when there is room, else at 0 s."""
    cues = [c for c in cues if not (len(c.lines) == 1 and STAMP_RE.match(c.lines[0].strip()))]
    end = min(cues[0].start if cues else 5000, 5000)
    return [Cue(start=1000 if end == 5000 else 0, end=end, lines=[text])] + cues


def _fmt_seconds(sec):
    return f"{int(sec // 3600)}:{int(sec // 60 % 60):02d}:{sec % 60:06.3f}"


_FONT_CANDIDATES = (
    'C:/Windows/Fonts/arial.ttf', 'C:/Windows/Fonts/segoeui.ttf',
    '/System/Library/Fonts/Supplemental/Arial.ttf', '/Library/Fonts/Arial.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', '/usr/share/fonts/TTF/DejaVuSans.ttf',
    '/usr/share/fonts/dejavu/DejaVuSans.ttf', '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
)


def _find_font():
    return next((f for f in _FONT_CANDIDATES if os.path.exists(f)), None)


def _filter_path(path):
    """Path for an ffmpeg filter option: forward slashes, ':' escaped (Windows drive letters)."""
    return path.replace('\\', '/').replace(':', '\\:')


def _subtitle_at(cues, seconds):
    """Main text (no {xx: ...} reference lines) of the subtitle shown at `seconds`, or ''."""
    ms = seconds * 1000
    for cue in cues:
        if cue.start <= ms < cue.end:  # a subtitle is gone at its end time
            return '\n'.join(l for l in cue.lines if not l.lstrip().startswith('{'))
    return ''


PER_ROW = 3


def _offset_label(offset):
    return '0' if offset == 0 else f'{offset:+g}s'


def _frames_cmd(video, times, labels, height, target, texts=None, font=None):
    """Frames scaled to `height`, PER_ROW per row (grid filled with black), as one JPEG."""
    cmd = ['ffmpeg', '-y', '-v', 'error']
    for t in times:
        cmd += ['-ss', f'{t:.3f}', '-i', video]
    n = len(times)
    chains = []
    for k in range(n):
        chain = f'[{k}:v]scale=-2:{height},setsar=1'
        if texts is not None:
            size = max(10, height // 18)
            common = (f"fontfile='{_filter_path(font)}':expansion=none:fontcolor=white"
                      f":box=1:boxcolor=black@0.65:boxborderw=3")
            chain += f",drawtext={common}:fontsize={size}:text='{labels[k]}':x=4:y=4"
            if texts[k]:
                chain += (f",drawtext={common}:fontsize={size + 1}:textfile='{_filter_path(texts[k])}'"
                          f":line_spacing=2:x=(w-text_w)/2:y=h-text_h-8")
        chains.append(chain + f'[v{k}]')
    inputs = ''.join(f'[v{k}]' for k in range(n))
    if n == 1:
        stack = f'{inputs}null[out]'
    elif n <= PER_ROW:
        stack = f'{inputs}hstack=inputs={n}[out]'
    else:
        # all frames have the same size, so the grid is regular: x = col * w0, y = row * h0
        cells = []
        for k in range(n):
            row, col = divmod(k, PER_ROW)
            x = '+'.join(['w0'] * col) or '0'
            y = '+'.join(['h0'] * row) or '0'
            cells.append(f'{x}_{y}')
        stack = f"{inputs}xstack=inputs={n}:layout={'|'.join(cells)}:fill=black[out]"
    cmd += ['-filter_complex', ';'.join(chains) + ';' + stack,
            '-map', '[out]', '-frames:v', '1', '-q:v', '5', target]
    return cmd


def cmd_frames(args):
    """One image per subtitle with frames at the given offsets around its middle (default:
    -5 s, the moment, +5 s in the top row and +10, +20, +60 s below), each scaled to --height,
    with the subtitle shown at that moment drawn at the bottom of the frame."""
    cues = parse_srt(read_text(args.srt))
    os.makedirs(args.out_dir, exist_ok=True)
    font = None if args.no_text else _find_font()
    offsets = [float(o) for o in args.offsets.split(',') if o.strip()]
    labels = [_offset_label(o) for o in offsets]
    results = []
    for raw in args.index.split(','):
        raw = raw.strip()
        if not raw:
            continue
        pos = int(raw) - 1
        if not 0 <= pos < len(cues):
            results.append({'index': raw, 'status': 'no such subtitle'})
            continue
        cue = cues[pos]
        moment = (cue.start + cue.end) / 2000  # middle of the subtitle, seconds
        times = [max(0.0, moment + o) for o in offsets]
        target = os.path.join(args.out_dir, f'frame_{int(raw):04d}.jpg')

        text_files = None
        shown = [_subtitle_at(cues, t) for t in times]
        if font:
            text_files = []
            for k, text in enumerate(shown):
                if not text:
                    text_files.append(None)
                    continue
                path = os.path.abspath(os.path.join(args.out_dir, f'.text_{int(raw):04d}_{k}.txt'))
                with open(path, 'w', encoding='utf-8') as fh:
                    fh.write(_TAG_RE.sub('', text))
                text_files.append(path)

        res = subprocess.run(_frames_cmd(args.video, times, labels, args.height, target, text_files, font),
                             capture_output=True, text=True, encoding='utf-8', errors='replace')
        with_text = text_files is not None
        if res.returncode != 0 and with_text:
            # drawtext unavailable in this ffmpeg build: plain frames are still useful
            res = subprocess.run(_frames_cmd(args.video, times, labels, args.height, target),
                                 capture_output=True, text=True, encoding='utf-8', errors='replace')
            with_text = False
        for path in text_files or []:
            if path and os.path.exists(path):
                os.remove(path)

        if res.returncode == 0 and os.path.exists(target):
            results.append({'index': raw, 'status': 'ok', 'image': os.path.abspath(target),
                            'time': _fmt_seconds(moment), 'text': cue.text,
                            'subtitles_drawn': with_text, 'shown': shown})
        else:
            results.append({'index': raw, 'status': 'failed', 'error': res.stderr.strip()[-300:]})
    out({'layout': f'frames left to right, {PER_ROW} per row, labelled in the top-left corner with the '
                   f'offset from the subtitle ({", ".join(labels)}); the subtitle shown at each moment '
                   'is drawn at the bottom of its frame. Frames far from the moment may be a different shot or scene.',
         'frames': results})
    return 0 if all(r['status'] == 'ok' for r in results) else 2


def cmd_stamp(args):
    text = f"# Translated by {args.model.strip()} #"
    cues = add_stamp(parse_srt(read_text(args.srt)), text)
    with open(args.srt, 'w', encoding='utf-8', newline='') as fh:
        fh.write(format_srt(cues))
    out({'output': os.path.abspath(args.srt), 'stamp': text})


def _overlap(a, b):
    return min(a.end, b.end) - max(a.start, b.start)


def cmd_review_split(args):
    translated = parse_srt(read_text(args.translated))
    original = sorted(parse_srt(read_text(args.source)), key=lambda c: (c.start, c.end))
    import bisect
    starts = [c.start for c in original]
    pairs = []
    for n, cue in enumerate(translated, 1):
        if len(cue.lines) == 1 and STAMP_RE.match(cue.lines[0].strip()):
            continue
        lo = max(0, bisect.bisect_left(starts, cue.start) - 2)
        best, best_ratio = None, 0.0
        for cand in original[lo:]:
            if cand.start >= cue.end:
                break
            ov = _overlap(cand, cue)
            if ov > 0:
                ratio = ov / (min(cand.duration, cue.duration) or 1)
                if ratio > best_ratio:
                    best, best_ratio = cand, ratio
        pairs.append({'index': str(n), 'original': best.text if best and best_ratio >= 0.2 else '',
                      'translation': cue.text})
    os.makedirs(args.out_dir, exist_ok=True)
    for old in glob.glob(os.path.join(glob.escape(args.out_dir), 'review_*.json')):
        os.remove(old)
    files = []
    for b, start in enumerate(range(0, len(pairs), args.batch_size), 1):
        path = os.path.join(args.out_dir, f'review_{b:03d}.json')
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(pairs[start:start + args.batch_size], fh, ensure_ascii=False, indent=1)
        files.append(os.path.basename(path))
    out({'pairs': len(pairs), 'without_source': sum(1 for p in pairs if not p['original']),
         'batches': files, 'merged_source': parse_merged_name(args.source)})


def cmd_clean(args):
    cues = parse_srt(read_text(args.srt))
    removed_examples = [c.text for c in cues
                        if not clean_cues([Cue(c.start, c.end, list(c.lines))],
                                          music=not args.keep_music, sdh=not args.keep_sdh)[0]][:8]
    cleaned, stats = clean_cues(cues, music=not args.keep_music, sdh=not args.keep_sdh)
    with open(args.output, 'w', encoding='utf-8', newline='') as fh:
        fh.write(format_srt(cleaned))
    out({'output': os.path.abspath(args.output), 'cues_before': len(cues), 'cues_after': len(cleaned),
         'removed_cues': stats['removed_cues'], 'cleaned_cues': stats['cleaned_cues'],
         'removed_examples': removed_examples})


def cmd_review_apply(args):
    cues = parse_srt(read_text(args.translated))
    with open(args.fixes, encoding='utf-8') as fh:
        fixes = json.load(fh)
    applied = []
    for fix in fixes:
        try:
            pos = int(str(fix['index']).strip()) - 1
        except (KeyError, ValueError):
            continue
        text = str(fix.get('text', '')).replace('\r', '').strip()
        if not (0 <= pos < len(cues)) or not text:
            continue
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        lines, _ = fix_cue_lines(lines)
        if lines != cues[pos].lines:
            applied.append({'index': fix['index'], 'before': cues[pos].text, 'after': '\n'.join(lines)})
            cues[pos].lines = lines
    with open(args.translated, 'w', encoding='utf-8', newline='') as fh:
        fh.write(format_srt(cues))
    # corrections change text lengths, so display times are fixed again afterwards
    _, fix_stats = fix_srt(args.translated, timing=not getattr(args, 'no_timing', False))
    out({'applied': len(applied), 'changes': applied,
         'fix': {k: fix_stats.get(k, 0) for k in ('rewrapped', 'extended', 'gaps_fixed')}})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('scan'); p.add_argument('folder'); p.add_argument('--no-probe', action='store_true')
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser('extract'); p.add_argument('video'); p.add_argument('--tracks', required=True)
    p.add_argument('--on-exists', choices=['error', 'overwrite', 'number', 'skip'], default='error')
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser('clean'); p.add_argument('srt'); p.add_argument('-o', '--output', required=True)
    p.add_argument('--keep-music', action='store_true'); p.add_argument('--keep-sdh', action='store_true')
    p.set_defaults(func=cmd_clean)

    p = sub.add_parser('split'); p.add_argument('srt'); p.add_argument('--out-dir', required=True)
    p.add_argument('--batch-size', type=int, default=100)
    p.add_argument('--context', type=int, default=10, help='read-only cues before each batch')
    p.add_argument('--context-after', type=int, default=5, help='read-only cues after each batch')
    p.add_argument('--fixed', action='store_true', help='cut every N cues instead of at scene breaks')
    p.add_argument('--force', action='store_true', help='start over even if translated batches exist')
    p.set_defaults(func=cmd_split)

    p = sub.add_parser('status'); p.add_argument('--out-dir', required=True); p.set_defaults(func=cmd_status)

    p = sub.add_parser('check'); p.add_argument('--out-dir', required=True)
    p.add_argument('--srt', help='source SRT file to verify full movie coverage')
    p.set_defaults(func=cmd_check)

    p = sub.add_parser('apply'); p.add_argument('srt'); p.add_argument('--out-dir', required=True)
    p.add_argument('-o', '--output', required=True); p.add_argument('--stamp', default='')
    p.add_argument('--allow-missing', action='store_true')
    p.add_argument('--no-check', action='store_true', help='skip batch validation check')
    p.add_argument('--allow-empty', action='store_true', help='allow empty cue translations without failing')
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser('frames'); p.add_argument('srt'); p.add_argument('video')
    p.add_argument('--index', required=True, help='subtitle numbers, e.g. 12,40')
    p.add_argument('--out-dir', required=True)
    p.add_argument('--offsets', default='-5,0,5,10,20,60',
                   help='seconds from the middle of the subtitle, one frame each (default -5,0,5,10,20,60; '
                        'write it as --offsets=-5,0,5 because of the leading minus)')
    p.add_argument('--height', type=int, default=240, help='height of each frame in px (default 240)')
    p.add_argument('--no-text', action='store_true', help='do not draw the subtitles on the frames')
    p.set_defaults(func=cmd_frames)

    p = sub.add_parser('stamp'); p.add_argument('srt'); p.add_argument('--model', required=True)
    p.set_defaults(func=cmd_stamp)

    p = sub.add_parser('review-split'); p.add_argument('translated'); p.add_argument('source')
    p.add_argument('--out-dir', required=True); p.add_argument('--batch-size', type=int, default=150)
    p.set_defaults(func=cmd_review_split)

    p = sub.add_parser('review-apply'); p.add_argument('translated'); p.add_argument('fixes'); p.add_argument('--no-timing', action='store_true', help='do not alter display times')
    p.set_defaults(func=cmd_review_apply)

    args = ap.parse_args()
    return args.func(args) or 0


if __name__ == '__main__':
    raise SystemExit(main())
