# Skills for AI agents

`translate-subtitles/` is a skill that runs the same subtitle translation workflow as this app (find or extract the subtitles, merge EN+FR, look up the movie, translate in batches, Fix subs, AI review), with the agent doing the translation itself.

It is a plain folder with `SKILL.md` and scripts, so it works anywhere that supports Agent Skills. Copy (or symlink) the whole `translate-subtitles` folder to:

| Tool | Global | Per project |
|---|---|---|
| Claude Code | `~/.claude/skills/translate-subtitles/` | `.claude/skills/translate-subtitles/` |
| OpenCode | `~/.config/opencode/skills/translate-subtitles/` (it also reads `~/.claude/skills`) | `.opencode/skills/translate-subtitles/` |
| Antigravity | `~/.gemini/antigravity/skills/translate-subtitles/` | `.agent/skills/translate-subtitles/` |

Requirements: Python 3.9+, plus `ffmpeg`/`ffprobe` on PATH (only for subtitles embedded in the video).

`scripts/srt_merge.py` and `scripts/srt_fix.py` are copies of `gst_gui/utils/srt_merge.py` and `gst_gui/utils/srt_fix.py`. After changing those modules, copy them here again.
