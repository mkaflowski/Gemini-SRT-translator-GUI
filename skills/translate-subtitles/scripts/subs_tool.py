#!/usr/bin/env python3
"""Helper for the translate-subtitles skill (no dependencies besides Python 3.9+ and ffmpeg).

Subcommands (all print JSON on stdout, so an AI agent can read the result):

  scan <folder>                     videos (+ embedded subtitle tracks), subtitle files and their languages
  extract <video> --tracks 0,2      extract tracks to <video>.<lang>.srt
          [--on-exists error|overwrite|number|skip]
  split <srt> --out-dir DIR         cut a subtitle file into translation batches (batch_001.json ...)
          [--batch-size 300]
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


def cmd_split(args):
    cues = parse_srt(read_text(args.srt))
    os.makedirs(args.out_dir, exist_ok=True)
    for old in glob.glob(os.path.join(args.out_dir, 'batch_*.json')):
        os.remove(old)
    batches = []
    for b, start in enumerate(range(0, len(cues), args.batch_size), 1):
        items = [{'index': str(i + 1), 'text': cues[i].text}
                 for i in range(start, min(start + args.batch_size, len(cues)))]
        path = os.path.join(args.out_dir, f'batch_{b:03d}.json')
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(items, fh, ensure_ascii=False, indent=1)
        batches.append({'batch': os.path.basename(path), 'output': f'out_{b:03d}.json',
                        'first': items[0]['index'], 'last': items[-1]['index']})
    manifest = {'source': os.path.abspath(args.srt), 'cues': len(cues), 'batch_size': args.batch_size,
                'batches': batches}
    with open(os.path.join(args.out_dir, 'manifest.json'), 'w', encoding='utf-8') as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
    out(manifest)


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
    p.add_argument('--batch-size', type=int, default=300); p.set_defaults(func=cmd_split)

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
