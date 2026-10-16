#!/usr/bin/env python3
"""Helper for the translate-subtitles skill (no dependencies besides Python 3.9+ and ffmpeg).

Subcommands (all print JSON on stdout, so an AI agent can read the result):

  scan <folder>                     videos (+ embedded subtitle tracks), subtitle files and their languages
  extract <video> --tracks 0,2      extract tracks to <video>.<lang>.srt
          [--on-exists error|overwrite|number|skip]
  split <srt> --out-dir DIR         cut a subtitle file into translation batches (batch_001.json ...)
          [--batch-size 100]        at scene breaks (long pauses after a finished sentence),
          [--context 10 --context-after 5]   with read-only context from the neighbouring batches
          [--force]                 (refuses to overwrite started work without it)
  status --out-dir DIR              which batches are done / still to translate (for resuming)
  check --out-dir DIR               find slips in translated batches: copied {xx: ...} lines or
                                    markers, untranslated or empty lines, >2 lines, broken tags
  apply <srt> --out-dir DIR -o OUT  build the translated SRT from DIR/out_001.json ... (timings from <srt>)
          [--stamp "# Translated by ... #"]
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from srt_merge import Cue, format_srt, parse_srt, read_text, parse_merged_name  # noqa: E402
from srt_fix import fix_cue_lines  # noqa: E402

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
    done = glob.glob(os.path.join(args.out_dir, 'out_*.json'))
    if done and not args.force:
        out({'error': 'work in progress', 'translated_batches': len(done),
             'hint': 'run "status" to resume, or split again with --force to start over'})
        return 2
    for old in glob.glob(os.path.join(args.out_dir, 'batch_*.json')) + done:
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


def _batch_status(out_dir):
    """[(batch info, state, problem)] for every batch in the manifest."""
    manifest = _load_json(os.path.join(out_dir, 'manifest.json'))
    rows = []
    for info in manifest['batches']:
        wanted = [i['index'] for i in _load_json(os.path.join(out_dir, info['batch']))['translate']]
        out_path = os.path.join(out_dir, info['output'])
        if not os.path.exists(out_path):
            rows.append((info, 'todo', ''))
            continue
        try:
            got = [str(i.get('index')).strip() for i in _load_json(out_path)]
        except Exception as e:
            rows.append((info, 'broken', f'invalid JSON: {e}'))
            continue
        missing = [i for i in wanted if i not in got]
        extra = [i for i in got if i not in wanted]
        if missing or extra:
            rows.append((info, 'incomplete', f'missing {missing[:10]} extra {extra[:10]}'))
        else:
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
    src_by_index = {i['index']: i['text'] for i in source}
    for item in translated:
        idx, text = str(item.get('index')), str(item.get('text', ''))
        src = src_by_index.get(idx, '')
        src_main = '\n'.join(l for l in src.split('\n') if not l.lstrip().startswith('{'))
        found = []
        if _REF_LINE_RE.search(text):
            found.append('contains a {xx: ...} reference line')
        if _MARKER_RE.search(text):
            found.append('contains the missing-subtitle marker')
        if not text.strip() and src.strip():
            found.append('empty translation')
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
        if found:
            problems.append({'index': idx, 'problems': found, 'text': text})
    return problems


def cmd_check(args):
    manifest, rows = _batch_status(args.out_dir)
    report = []
    for info, state, problem in rows:
        if state == 'todo':
            continue
        entry = {'batch': info['output']}
        if problem:
            entry['problems'] = [problem]
        if state != 'broken':
            source = _load_json(os.path.join(args.out_dir, info['batch']))['translate']
            issues = check_items(source, _load_json(os.path.join(args.out_dir, info['output'])))
            if issues:
                entry['items'] = issues
        if len(entry) > 1:
            report.append(entry)
    out({'checked_batches': sum(1 for r in rows if r[1] != 'todo'),
         'batches_with_problems': len(report), 'report': report})
    return 0


def cmd_apply(args):
    cues = parse_srt(read_text(args.srt))
    translated = {}
    for path in sorted(glob.glob(os.path.join(args.out_dir, 'out_*.json'))):
        with open(path, encoding='utf-8') as fh:
            for item in json.load(fh):
                translated[str(item['index']).strip()] = str(item.get('text', ''))
    missing = [str(i + 1) for i in range(len(cues)) if str(i + 1) not in translated]
    if missing and not args.allow_missing:
        out({'error': 'missing translations', 'missing_count': len(missing), 'missing': missing[:50]})
        return 2
    result = []
    for i, cue in enumerate(cues):
        text = translated.get(str(i + 1))
        lines = [l.rstrip() for l in (text if text is not None else cue.text).replace('\r', '').split('\n')]
        lines = [l for l in lines if l.strip()]
        if not lines:
            continue
        result.append(Cue(start=cue.start, end=cue.end, lines=lines, coords=cue.coords))
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
    for old in glob.glob(os.path.join(args.out_dir, 'review_*.json')):
        os.remove(old)
    files = []
    for b, start in enumerate(range(0, len(pairs), args.batch_size), 1):
        path = os.path.join(args.out_dir, f'review_{b:03d}.json')
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(pairs[start:start + args.batch_size], fh, ensure_ascii=False, indent=1)
        files.append(os.path.basename(path))
    out({'pairs': len(pairs), 'without_source': sum(1 for p in pairs if not p['original']),
         'batches': files, 'merged_source': parse_merged_name(args.source)})


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
    out({'applied': len(applied), 'changes': applied})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('scan'); p.add_argument('folder'); p.add_argument('--no-probe', action='store_true')
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser('extract'); p.add_argument('video'); p.add_argument('--tracks', required=True)
    p.add_argument('--on-exists', choices=['error', 'overwrite', 'number', 'skip'], default='error')
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser('split'); p.add_argument('srt'); p.add_argument('--out-dir', required=True)
    p.add_argument('--batch-size', type=int, default=100)
    p.add_argument('--context', type=int, default=10, help='read-only cues before each batch')
    p.add_argument('--context-after', type=int, default=5, help='read-only cues after each batch')
    p.add_argument('--fixed', action='store_true', help='cut every N cues instead of at scene breaks')
    p.add_argument('--force', action='store_true', help='start over even if translated batches exist')
    p.set_defaults(func=cmd_split)

    p = sub.add_parser('status'); p.add_argument('--out-dir', required=True); p.set_defaults(func=cmd_status)

    p = sub.add_parser('check'); p.add_argument('--out-dir', required=True); p.set_defaults(func=cmd_check)

    p = sub.add_parser('apply'); p.add_argument('srt'); p.add_argument('--out-dir', required=True)
    p.add_argument('-o', '--output', required=True); p.add_argument('--stamp', default='')
    p.add_argument('--allow-missing', action='store_true'); p.set_defaults(func=cmd_apply)

    p = sub.add_parser('stamp'); p.add_argument('srt'); p.add_argument('--model', required=True)
    p.set_defaults(func=cmd_stamp)

    p = sub.add_parser('review-split'); p.add_argument('translated'); p.add_argument('source')
    p.add_argument('--out-dir', required=True); p.add_argument('--batch-size', type=int, default=150)
    p.set_defaults(func=cmd_review_split)

    p = sub.add_parser('review-apply'); p.add_argument('translated'); p.add_argument('fixes')
    p.set_defaults(func=cmd_review_apply)

    args = ap.parse_args()
    return args.func(args) or 0


if __name__ == '__main__':
    raise SystemExit(main())
