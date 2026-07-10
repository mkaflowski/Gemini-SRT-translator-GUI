"""
Detection and extraction of subtitle tracks embedded in video files (MKV/MP4...).
Uses ffprobe/ffmpeg from PATH.
"""
import json
import os
import subprocess
import sys

# Text-based subtitle codecs that ffmpeg can convert to SRT.
# Bitmap formats (hdmv_pgs_subtitle, dvd_subtitle, dvb_subtitle, xsub) cannot.
TEXT_CODECS = {'subrip', 'srt', 'ass', 'ssa', 'mov_text', 'webvtt', 'text', 'mpl2', 'subviewer'}

# Hide console windows spawned by ffmpeg/ffprobe on Windows
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == 'win32' else 0

def probe_subtitle_tracks(video_path):
    """
    List subtitle streams embedded in a video file.

    Returns a list of dicts (empty on error / no subs):
        {
            'type_index': N,        # index among subtitle streams (for -map 0:s:N)
            'codec': 'subrip',
            'language': 'eng',      # '' if unknown
            'title': 'Forced',      # '' if none
            'default': bool,
            'forced': bool,
            'text_based': bool,     # False for bitmap subs (cannot convert to SRT)
        }
    """
    cmd = [
        'ffprobe', '-v', 'error',
        '-select_streams', 's',
        '-show_entries', 'stream=index,codec_name:stream_tags=language,title:stream_disposition=default,forced',
        '-of', 'json',
        str(video_path),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=30, creationflags=_CREATE_NO_WINDOW
        )
        if result.returncode != 0:
            return []
        streams = json.loads(result.stdout or '{}').get('streams', [])
    except Exception:
        return []

    tracks = []
    for type_index, stream in enumerate(streams):
        codec = stream.get('codec_name', '') or ''
        tags = stream.get('tags', {}) or {}
        disposition = stream.get('disposition', {}) or {}
        tracks.append({
            'type_index': type_index,
            'codec': codec,
            'language': tags.get('language', '') or '',
            'title': tags.get('title', '') or '',
            'default': bool(disposition.get('default')),
            'forced': bool(disposition.get('forced')),
            'text_based': codec.lower() in TEXT_CODECS,
        })
    return tracks


def format_track_label(track):
    """Human-readable label for a track, e.g. '#2  pol - Full (subrip) [default]'"""
    parts = [f"#{track['type_index'] + 1} "]
    parts.append(track['language'] or 'und')
    if track['title']:
        parts.append(f"- {track['title']}")
    parts.append(f"({track['codec']})")
    flags = []
    if track['default']:
        flags.append('default')
    if track['forced']:
        flags.append('forced')
    if flags:
        parts.append(f"[{', '.join(flags)}]")
    if not track['text_based']:
        parts.append('- bitmap, cannot extract')
    return ' '.join(parts)


def pick_matching_track(tracks, wanted):
    """
    Find the track in `tracks` that best matches the `wanted` track
    (chosen on another file of the same series). Match priority:
    same language+title -> same language -> same type_index -> first text track.
    Returns a track dict or None.
    """
    text_tracks = [t for t in tracks if t['text_based']]
    if not text_tracks:
        return None
    if wanted.get('language'):
        same_lang = [t for t in text_tracks if t['language'] == wanted['language']]
        for t in same_lang:
            if t['title'] == wanted.get('title', ''):
                return t
        if same_lang:
            return same_lang[0]
    for t in text_tracks:
        if t['type_index'] == wanted['type_index']:
            return t
    return text_tracks[0]


def extract_subtitle_track(video_path, type_index, output_path=None):
    """
    Extract subtitle stream 0:s:<type_index> from the video to an SRT file.
    Returns the output path on success, None on failure.
    """
    video_path = str(video_path)
    if output_path is None:
        stem = os.path.splitext(os.path.basename(video_path))[0]
        output_path = os.path.join(os.path.dirname(video_path), f"{stem}_track{type_index + 1}.srt")

    cmd = [
        'ffmpeg', '-y', '-v', 'error',
        '-i', video_path,
        '-map', f'0:s:{type_index}',
        '-c:s', 'srt',
        str(output_path),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=300, creationflags=_CREATE_NO_WINDOW
        )
        if result.returncode == 0 and os.path.exists(output_path):
            return str(output_path)
        return None
    except Exception:
        return None
