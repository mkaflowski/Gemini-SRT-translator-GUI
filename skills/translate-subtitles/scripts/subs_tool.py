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
  check --out-dir DIR [--srt SRC]   (--batch N: only out_N.json, right after writing it) find slips in translated batches: copied {xx: ...} lines or
                                    markers, untranslated or empty lines, >2 lines, broken tags,
                                    "src" anchors that do not match their cue (shifted text) and
                                    runs of cues that look shifted by one (heuristic)
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
  review-apply <translated> <fixes.json> [--force]
                                    apply corrected lines [{"index": "12", "text": "..."}], re-wrapped;
                                    refuses when the file changed after review-split
  verify <srt> [--json-out FILE]    mechanical checks of the finished file (after Fix subs): timing,
                                    empty cues, line length, reading speed, capital letters left
                                    mid-sentence by joined cues, one-letter words at line ends,
                                    tags, spacing, leftovers, mixed quote styles

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
from srt_fix import CPS, MAX_LEN, MAX_LINES, MIN_GAP, char_count, fix_cue_lines, fix_srt, has_slash_break  # noqa: E402

# How long Fix subs may keep a subtitle on screen after the speech ends, counted in the
# character budget. Kept well under its 2 s limit, so the subtitle stays near its line.
BUDGET_TRAIL_MS = 700
BUDGET_MIN = 14
BUDGET_TOLERANCE = 4

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


def char_budget(cues, i, cps=CPS):
    """How many characters the viewer can read while cue i is on screen: its time plus a
    short stay after the speech (up to the next subtitle), at the reading speed Fix subs
    uses. Never more than two full lines."""
    cue = cues[i]
    end = cue.end + BUDGET_TRAIL_MS
    if i + 1 < len(cues):
        end = min(end, max(cue.end, cues[i + 1].start - MIN_GAP))
    budget = int((end - cue.start) / 1000 * cps)
    return max(BUDGET_MIN, min(budget, MAX_LEN * MAX_LINES))


MAX_BATCH = 150  # above this a model loses track of the cues and shifts them (seen at 300)


def cmd_split(args):
    if args.batch_size > MAX_BATCH and not args.large_batches:
        out({'error': f'batch size {args.batch_size} is over {MAX_BATCH}',
             'hint': f'long batches are where translations shift against their cues; use '
                     f'--batch-size 100 (the default) or at most {MAX_BATCH}'})
        return 2
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

    def to_translate(i):
        return dict(item(i), max_chars=char_budget(cues, i))

    if args.fixed:
        ranges = [(s, min(s + args.batch_size, len(cues))) for s in range(0, len(cues), args.batch_size)]
    else:
        ranges = scene_cuts(cues, args.batch_size)
    batches = []
    for b, (start, end) in enumerate(ranges, 1):
        data = {
            'translate': [to_translate(i) for i in range(start, end)],
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

        bad = anchor_mismatches(src_items, got_data)
        if bad:
            first = bad[0]
            hint = f" - it is the text of cue {first['looks_like']}" if first.get('looks_like') else ''
            rows.append((info, 'incomplete',
                         f"src anchor mismatch at cues {[b['index'] for b in bad][:10]}: translations are "
                         f"shifted against their cues from cue {first['index']}{hint}. Retranslate from there."))
            continue

        rows.append((info, 'done', ''))
    return manifest, rows


# --------------------------------------------------------------------------- #
# Shift (desync) detection.
#
# A model writes "index" as a counter, so after merging or splitting a cue it can keep
# numbering correctly while every text sits one cue off. Two guards:
#  1. the "src" anchor: every output item repeats the first words of its source cue,
#     so a text written for another cue shows up as an exact mismatch;
#  2. a heuristic for outputs without anchors: in a run of cues, a translation that
#     fits the neighbouring source cue better than its own one (length, numbers,
#     names, final punctuation, dialogue dashes, italics) means the run is shifted.

_ANCHOR_WORD_RE = re.compile(r'[^\W_]+', re.UNICODE)  # letters and digits
ANCHOR_WORDS = 3


def _main_text(text):
    """Source text without the {xx: ...} reference lines of a merged file."""
    return '\n'.join(l for l in str(text).split('\n') if not l.lstrip().startswith('{'))


def _words(text):
    return _ANCHOR_WORD_RE.findall(_TAG_RE.sub(' ', str(text)).lower())


def anchor_of(src_text, n=ANCHOR_WORDS):
    """The expected "src" anchor: the first n words of the cue's main text."""
    return ' '.join(_words(_main_text(src_text))[:n])


def _anchor_fits(anchor_words, src_text):
    words = _words(_main_text(src_text)) or _words(src_text)
    if not words:
        return not anchor_words
    n = len(anchor_words)
    if anchor_words == words[:n]:
        return True
    # tolerate a skipped speaker label or a leading word ("JOHN: Wow" written as "Wow")
    return any(anchor_words == words[k:k + n] for k in range(1, min(4, len(words) - n + 1)))


def anchor_mismatches(src_items, out_items):
    """Items whose "src" anchor does not match their own source cue.
    Items without a "src" field are not checked (old outputs)."""
    src_list = [(str(i['index']).strip(), i.get('text', '')) for i in src_items]
    pos = {idx: k for k, (idx, _) in enumerate(src_list)}
    bad = []
    for item in out_items:
        if 'src' not in item:
            continue
        idx = str(item.get('index', '')).strip()
        k = pos.get(idx)
        if k is None:
            continue
        anchor = _words(item.get('src', ''))
        if not anchor:
            if _words(_main_text(src_list[k][1])):
                bad.append({'index': idx, 'src': item.get('src', ''), 'expected': anchor_of(src_list[k][1])})
            continue
        if _anchor_fits(anchor, src_list[k][1]):
            continue
        entry = {'index': idx, 'src': item.get('src', ''), 'expected': anchor_of(src_list[k][1])}
        for off in (1, -1, 2, -2, 3, -3):
            j = k + off
            if 0 <= j < len(src_list) and _anchor_fits(anchor, src_list[j][1]):
                entry['looks_like'] = src_list[j][0]
                break
        bad.append(entry)
    return bad


_NUM_RE = re.compile(r'\d+')
_CAP_RE = re.compile(r'[A-Z][a-z]{2,}')
_SENT_END_RE = re.compile(r'[.!?…:]["\'»”)\]]*$')


def _names(raw):
    """Capitalised words that do not start a sentence (likely names), as 4-letter stems,
    so that inflected forms in the translation still match (John -> Johna)."""
    names, sentence_start = set(), True
    for tok in _TAG_RE.sub(' ', raw).split():
        word = tok.strip('-"\'(«“[')
        if not sentence_start and _CAP_RE.match(word):
            names.add(word[:4])
        sentence_start = tok == '-' or bool(_SENT_END_RE.search(tok))
    return names


def _plain(text):
    return re.sub(r'\s+', ' ', _TAG_RE.sub('', str(text))).strip()


def _end_class(text):
    t = _plain(text).rstrip('"\'»” )]')
    if not t:
        return ''
    if t.endswith(('...', '…')):
        return '…'
    last = t[-1]
    return {'?': '?', '؟': '?', '¿': '?', '!': '!', '.': '.', '。': '.', ',': ',', '،': ','}.get(last, 'w')


def _features(text, main=True):
    raw = _main_text(text) if main else str(text)
    plain = _plain(raw)
    lines = [l for l in raw.split('\n') if l.strip()]
    return {
        'len': len(plain),
        'end': _end_class(raw),
        'nums': set(_NUM_RE.findall(plain)),
        'names': _names(raw),
        'dialog': sum(1 for l in lines if _plain(l).startswith('-')) >= 2,
        'italic': '<i>' in raw.lower(),
    }


def _similarity(t, s, ratio):
    """How well translation features t fit source features s (higher is better)."""
    import math
    score = -abs(math.log((t['len'] + 8) / (s['len'] + 8)) - ratio) * 2.5
    if t['end'] and s['end']:
        if t['end'] == s['end']:
            score += 0.3
        elif '?' in (t['end'], s['end']) or '!' in (t['end'], s['end']):
            score -= 0.3
    if t['nums'] or s['nums']:
        score += 1.0 if t['nums'] == s['nums'] else -0.7
    if s['names']:
        found = sum(1 for n in s['names'] if n in t['plain'])
        score += min(found, 2) * 0.6
    score += 0.3 if t['dialog'] == s['dialog'] else -0.3
    score += 0.2 if t['italic'] == s['italic'] else -0.3
    return score


def shift_suspects(src_items, out_items, window=6, margin=0.35, majority=0.75):
    """Runs of cues whose translations fit the neighbouring source cues better than
    their own: [{'from': idx, 'to': idx, 'offset': +1/-1, 'gain': float}].
    offset +1 means cue N carries the translation of cue N+1 (subtitles come too early)."""
    import math
    import statistics
    by_idx = {str(i.get('index', '')).strip(): str(i.get('text', '')) for i in out_items}
    pairs = [(str(i['index']).strip(), i.get('text', '')) for i in src_items]
    pairs = [(idx, s, by_idx[idx]) for idx, s in pairs if idx in by_idx and _plain(_main_text(s))]
    n = len(pairs)
    if n < window + 2:
        return []
    src_f = [_features(s) for _, s, _ in pairs]
    out_f = []
    for _, _, t in pairs:
        f = _features(t, main=False)
        f['plain'] = _plain(t)
        out_f.append(f)
    ratio = statistics.median(math.log((t['len'] + 8) / (s['len'] + 8)) for t, s in zip(out_f, src_f))
    own = [_similarity(out_f[k], src_f[k], ratio) for k in range(n)]
    runs = []
    for off in (1, -1):
        gain = [(_similarity(out_f[k], src_f[k + off], ratio) - own[k]) if 0 <= k + off < n else None
                for k in range(n)]
        flagged = [False] * n
        for start in range(0, n - window + 1):
            win = gain[start:start + window]
            if any(g is None for g in win):
                continue
            if sum(win) / window > margin and sum(1 for g in win if g > 0) >= majority * window:
                for k in range(start, start + window):
                    flagged[k] = True
        k = 0
        while k < n:
            if not flagged[k]:
                k += 1
                continue
            j = k
            while j + 1 < n and flagged[j + 1]:
                j += 1
            seg = [g for g in gain[k:j + 1] if g is not None]
            runs.append({'from': pairs[k][0], 'to': pairs[j][0], 'offset': off,
                         'gain': round(sum(seg) / len(seg), 2)})
            k = j + 1
    return sorted(runs, key=lambda r: int(r['from']) if r['from'].isdigit() else 0)


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
    budget_by_index = {str(i['index']).strip(): i['max_chars'] for i in source if i.get('max_chars')}
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
        budget = budget_by_index.get(idx)
        if budget and lines:
            chars = char_count(lines)
            if chars > budget + BUDGET_TOLERANCE:
                found.append(f'too long to read: {chars} chars, {budget} fit in its time (condense it)')
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


def _batch_number(name):
    m = re.search(r'(\d+)', name or '')
    return int(m.group(1)) if m else None


def cmd_check(args):
    manifest, rows = _batch_status(args.out_dir)
    only = getattr(args, 'batch', None)
    if only is not None:
        # right after one batch: check it alone, before the next one builds on it
        rows = [r for r in rows if _batch_number(r[0]['output']) == only]
        if not rows:
            out({'status': 'error', 'error': f'no batch {only} in {args.out_dir}'})
            return 2
        if rows[0][1] == 'todo':
            out({'status': 'error', 'error': f"{rows[0][0]['output']} does not exist yet"})
            return 2
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
            if out_items and not any(isinstance(i, dict) and 'src' in i for i in out_items):
                entry['warnings'] = ['no "src" anchors: an exact shift check is not possible for this batch']
            suspects = shift_suspects(src_items, out_items)
            if suspects:
                entry['shift_suspects'] = [
                    dict(s, hint=f"cues {s['from']}-{s['to']} look like the translations of the cues "
                                 f"{'after' if s['offset'] > 0 else 'before'} them: compare them with the "
                                 f"source and, if shifted, retranslate the run")
                    for s in suspects]
        if len(entry) > 1:
            report.append(entry)

    checked = sum(1 for r in rows if r[1] != 'todo')

    coverage_errors = []
    if only is None and getattr(args, 'srt', None) and os.path.exists(args.srt):
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
    blank_source = 0  # cues with no text in the source either: nothing to show, nothing shifts
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
            if not src_clean:
                blank_source += 1  # source was also blank, safe to omit
                continue
            dropped_cues.append(idx_str)
            continue
        result.append(Cue(start=cue.start, end=cue.end, lines=lines, coords=cue.coords))
    expected = len(cues) - blank_source

    if empty_errors:
        out({
            'status': 'error',
            'error': f'{len(empty_errors)} cue(s) have empty translations while source has text! '
                     f'Silently dropping them causes off-by-one subtitle desynchronization. '
                     f'Fix the translation or specify --allow-empty if intentional.',
            'empty_cues': empty_errors[:20]
        })
        return 2

    if len(result) != expected and not args.allow_missing and not getattr(args, 'allow_empty', False):
        out({
            'status': 'error',
            'error': f'Result cue count ({len(result)}) does not match source count ({expected})! '
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
        pair = {'index': str(n), 'original': best.text if best and best_ratio >= 0.2 else '',
                'translation': cue.text}
        cps = char_count(cue.lines) / max(cue.end - cue.start, 1) * 1000
        if cps > CPS:
            pair['cps'] = round(cps, 1)  # faster than a viewer reads: a candidate for condensing
        pairs.append(pair)
    os.makedirs(args.out_dir, exist_ok=True)
    for old in glob.glob(os.path.join(glob.escape(args.out_dir), 'review_*.json')):
        os.remove(old)
    files = []
    for b, start in enumerate(range(0, len(pairs), args.batch_size), 1):
        path = os.path.join(args.out_dir, f'review_{b:03d}.json')
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(pairs[start:start + args.batch_size], fh, ensure_ascii=False, indent=1)
        files.append(os.path.basename(path))
    with open(os.path.join(args.out_dir, REVIEW_META), 'w', encoding='utf-8') as fh:
        json.dump({'translated': os.path.abspath(args.translated),
                   'sha256': _file_hash(args.translated)}, fh)
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


def _file_hash(path):
    import hashlib
    with open(path, 'rb') as fh:
        return hashlib.sha256(fh.read()).hexdigest()


REVIEW_META = 'review_meta.json'


# Polish words that must not end a line (typography: a one-letter word or a short
# preposition is moved to the next line together with the word it belongs to).
_HANGING = {'a', 'i', 'o', 'u', 'w', 'z', 'we', 'ze', 'na', 'do', 'od', 'po', 'za', 'ku'}
_UPPER = 'A-ZĄĆĘŁŃÓŚŹŻ'
_LOWER = 'a-ząćęłńóśźż'
_MIDCAP_RE = re.compile(rf'[,;]\s+([{_UPPER}][{_LOWER}]+)')
_WORD_RE = re.compile(rf'[{_UPPER}{_LOWER}]+')


def verify_cues(cues, source=None, max_len=MAX_LEN, cps_limit=CPS):
    """Mechanical checks of a finished subtitle file (after Fix subs or review-apply).

    Returns a dict of issue lists; every issue has the 1-based `index` used by
    review-apply and the cue `text`, so a correction can go straight to fixes.json.
    """
    tag = re.compile(r'</?[a-zA-Z][^>]*>')
    plain = lambda s: tag.sub('', s)
    lower_words = {}
    for c in cues:
        for w in _WORD_RE.findall(plain(c.text)):
            if w[0].islower():
                lower_words[w] = lower_words.get(w, 0) + 1
    issues = {k: [] for k in (
        'timing', 'empty', 'too_many_lines', 'line_too_long', 'too_fast', 'midsentence_capital',
        'hanging_word', 'tags', 'spacing', 'leftovers', 'quotes')}

    def add(kind, i, cue, note=''):
        item = {'index': str(i), 'text': cue.text}
        if note:
            item['note'] = note
        issues[kind].append(item)

    quote_styles = {'„”': 0, '""': 0}
    prev = None
    for i, c in enumerate(cues, 1):
        if len(c.lines) == 1 and STAMP_RE.match(c.lines[0].strip()):
            prev = c
            continue
        text = plain(c.text)
        if c.end <= c.start:
            add('timing', i, c, 'end before start')
        if prev is not None and c.start < prev.end:
            add('timing', i, c, f'overlaps the previous cue by {prev.end - c.start} ms')
        prev = c
        if not text.strip():
            add('empty', i, c)
            continue
        if len(c.lines) > MAX_LINES:
            add('too_many_lines', i, c, f'{len(c.lines)} lines')
        for line in c.lines:
            n = len(plain(line))
            if n > max_len:
                add('line_too_long', i, c, f'{n} characters')
                break
        cps = char_count(c.lines) / max(c.duration, 1) * 1000
        if cps > cps_limit + BUDGET_TOLERANCE:
            add('too_fast', i, c, f'{cps:.1f} characters per second')
        # a capital letter after a comma: typical leftover of Fix subs joining two pieces
        # ("...historii, Proszę śmiało"). Names are skipped: the word must appear in
        # lower case elsewhere in the file.
        for m in _MIDCAP_RE.finditer(' '.join(plain(l) for l in c.lines)):
            word = m.group(1)
            if lower_words.get(word[0].lower() + word[1:], 0):
                add('midsentence_capital', i, c, f'"{word}" after a comma')
                break
        for line in c.lines[:-1]:
            words = plain(line).split()
            if words and words[-1].lower() in _HANGING:
                add('hanging_word', i, c, f'line ends with "{words[-1]}"')
                break
        for t in ('i', 'b', 'u'):
            if len(re.findall(rf'<{t}>', c.text)) != len(re.findall(rf'</{t}>', c.text)):
                add('tags', i, c, f'unbalanced <{t}>')
        for line in c.lines:
            pl = plain(line)
            if re.search(r'  |\s[,.?!:;](?!\.)|[,;:](?=[^\s\d"”»])|(?<!\.)[.?!](?=[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż])|,,|\?\?\?|!!!', pl):
                add('spacing', i, c, 'double space, space before punctuation or none after it')
                break
        if re.search(r'\{[a-z]{2}:|BRAK NAPISÓW| / |[—–]', c.text):
            add('leftovers', i, c, 'reference line, marker, slash break or long dash')
        quote_styles['„”'] += len(re.findall('[„”]', text))
        quote_styles['""'] += text.count('"')
    if quote_styles['„”'] and quote_styles['""']:
        issues['quotes'].append({'note': 'mixed quote styles', 'counts': quote_styles})
    result = {k: v for k, v in issues.items() if v}
    return result


def cmd_verify(args):
    cues = parse_srt(read_text(args.srt))
    found = verify_cues(cues, max_len=args.max_len)
    limit = args.limit
    report = {'file': os.path.abspath(args.srt), 'cues': len(cues),
              'issues': {k: len(v) for k, v in found.items()},
              'ok': not any(k in found for k in ('timing', 'empty', 'too_many_lines', 'line_too_long',
                                                    'tags', 'leftovers'))}
    report['details'] = {k: v[:limit] for k, v in found.items()}
    if args.json_out:
        with open(args.json_out, 'w', encoding='utf-8') as fh:
            json.dump(found, fh, ensure_ascii=False, indent=1)
        report['all_details'] = os.path.abspath(args.json_out)
    out(report)


def cmd_review_apply(args):
    meta_path = os.path.join(os.path.dirname(os.path.abspath(args.fixes)), REVIEW_META)
    if os.path.exists(meta_path) and not args.force:
        with open(meta_path, encoding='utf-8') as fh:
            meta = json.load(fh)
        if (os.path.abspath(args.translated) == meta.get('translated')
                and _file_hash(args.translated) != meta.get('sha256')):
            out({'error': 'the subtitle file changed after review-split (another tool or session edited '
                          'it), so the review indexes may point at different cues. Run review-split '
                          'again and redo the review on the current file, or pass --force if you are '
                          'sure the changes do not move cues.',
                 'translated': os.path.abspath(args.translated)})
            return 2
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
    if os.path.exists(meta_path):
        with open(meta_path, 'w', encoding='utf-8') as fh:  # the file is ours again after applying
            json.dump({'translated': os.path.abspath(args.translated),
                       'sha256': _file_hash(args.translated)}, fh)
    found = verify_cues(parse_srt(read_text(args.translated)))
    out({'applied': len(applied), 'changes': applied,
         'fix': {k: fix_stats.get(k, 0) for k in ('rewrapped', 'extended', 'gaps_fixed')},
         'verify': {k: len(v) for k, v in found.items()}})


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
    p.add_argument('--large-batches', action='store_true', help=argparse.SUPPRESS)  # only if the user insists
    p.set_defaults(func=cmd_split)

    p = sub.add_parser('status'); p.add_argument('--out-dir', required=True); p.set_defaults(func=cmd_status)

    p = sub.add_parser('check'); p.add_argument('--out-dir', required=True)
    p.add_argument('--srt', help='source SRT file to verify full movie coverage')
    p.add_argument('--batch', type=int, help='check only this batch (e.g. 3 for out_003.json), right after writing it')
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
    p.add_argument('--force', action='store_true', help='apply even if the file changed after review-split')
    p.set_defaults(func=cmd_review_apply)

    p = sub.add_parser('verify'); p.add_argument('srt')
    p.add_argument('--max-len', type=int, default=MAX_LEN)
    p.add_argument('--limit', type=int, default=15, help='examples shown per issue type')
    p.add_argument('--json-out', help='write every issue to this JSON file')
    p.set_defaults(func=cmd_verify)

    args = ap.parse_args()
    return args.func(args) or 0


if __name__ == '__main__':
    raise SystemExit(main())
