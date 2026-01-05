from google import genai
import sys
import os
import time
import argparse
import re
import subprocess
import tempfile
import shutil
from pathlib import Path


def get_video_duration(video_path: str) -> float:
    """Gets video duration in seconds using ffprobe"""
    try:
        result = subprocess.run(
            [
                'ffprobe', '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                video_path
            ],
            capture_output=True,
            text=True,
            check=True
        )
        return float(result.stdout.strip())
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Cannot read video duration: {e.stderr}")
    except FileNotFoundError:
        raise RuntimeError("ffprobe is not installed. Please install ffmpeg.")


def detect_platform() -> str:
    """Detects platform: 'mac', 'windows', 'linux'"""
    import platform
    system = platform.system().lower()
    if system == 'darwin':
        return 'mac'
    elif system == 'windows':
        return 'windows'
    return 'linux'


def preprocess_video(video_path: str, output_path: str = None) -> str:
    """
    Processes video: 2x speed + downscale to 360p.
    Automatically selects the best acceleration method for the platform:
    - Windows/Linux with NVIDIA: CUDA + NVENC
    - macOS: VideoToolbox
    - Fallback: CPU (libx264)

    Args:
        video_path: Path to video file
        output_path: Output path (defaults to temporary file)

    Returns:
        Path to processed file
    """
    if output_path is None:
        video_file = Path(video_path)
        output_path = os.path.join(
            tempfile.gettempdir(),
            f"preprocessed_{video_file.stem}.mp4"
        )

    duration = get_video_duration(video_path)
    platform = detect_platform()

    print(f"📹 Original video: {duration / 60:.1f} minutes")
    print(f"⚡ Processing (2x speed + 360p)...")
    print(f"   Platform: {platform}")
    print(f"   Output file: {output_path}")
    print(f"   Estimated output duration: ~{duration / 60 / 2:.1f} minutes")

    # === NVIDIA CUDA (Windows/Linux) - as string for shell=True ===
    # Escape path for Windows
    escaped_input = video_path.replace('"', '\\"')
    escaped_output = output_path.replace('"', '\\"')

    # Version for SDR (without tonemapping)
    cmd_cuda_shell = f'ffmpeg -y -hwaccel cuda -hwaccel_output_format cuda -c:v hevc_cuvid -i "{escaped_input}" -ss 0 -vf "scale_cuda=-2:360,setpts=0.5*PTS" -af "atempo=2.0" -ac 2 -c:v h264_nvenc -preset p1 -rc constqp -qp 29 -c:a aac -b:a 128k "{escaped_output}"'

    # Version for HDR - software decode + tonemap + nvenc encode
    cmd_cuda_hdr_shell = f'ffmpeg -y -i "{escaped_input}" -ss 0 -vf "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p,scale=-2:360,setpts=0.5*PTS" -af "atempo=2.0" -ac 2 -c:v h264_nvenc -preset p1 -rc constqp -qp 29 -c:a aac -b:a 128k "{escaped_output}"'

    # Simplified version - software scale + nvenc (for HDR without full tonemapping)
    cmd_cuda_simple_shell = f'ffmpeg -y -hwaccel cuda -i "{escaped_input}" -ss 0 -vf "scale=-2:360,setpts=0.5*PTS,format=yuv420p" -af "atempo=2.0" -ac 2 -c:v h264_nvenc -preset p1 -rc constqp -qp 29 -pix_fmt yuv420p -c:a aac -b:a 128k "{escaped_output}"'

    # === macOS VideoToolbox ===
    cmd_videotoolbox = [
        'ffmpeg', '-y',
        '-hwaccel', 'videotoolbox',
        '-i', video_path,
        '-vf', 'scale=-2:360,setpts=0.5*PTS',
        '-af', 'atempo=2.0',
        '-ac', '2',
        '-c:v', 'h264_videotoolbox',
        '-q:v', '65',  # Quality (0-100, higher = better)
        '-c:a', 'aac',
        '-b:a', '128k',
        output_path
    ]

    # === CPU fallback (all platforms) ===
    cmd_cpu = [
        'ffmpeg', '-y',
        '-i', video_path,
        '-vf', 'scale=-2:360,setpts=0.5*PTS',
        '-af', 'atempo=2.0',
        '-ac', '2',
        '-c:v', 'libx264',
        '-preset', 'ultrafast',
        '-crf', '28',
        '-c:a', 'aac',
        '-b:a', '128k',
        output_path
    ]

    # Select methods depending on platform
    if platform == 'mac':
        methods = [
            (cmd_videotoolbox, "VideoToolbox (Apple GPU)", False),
            (cmd_cpu, "CPU (libx264)", False)
        ]
    else:  # Windows/Linux
        methods = [
            (cmd_cuda_shell, "CUDA + HEVC decoder (SDR)", True),
            (cmd_cuda_simple_shell, "CUDA + yuv420p (HDR compatible)", True),
            (cmd_cuda_hdr_shell, "CUDA + HDR tonemap", True),
            (cmd_cpu, "CPU (libx264)", False)
        ]

    start_time = time.time()

    for i, (cmd, method, use_shell) in enumerate(methods):
        try:
            print(f"   Attempt {i + 1}/{len(methods)}: {method}...")
            if use_shell:
                print(f"   CMD: {cmd}")

            # Use Popen to display progress in real-time
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=use_shell
            )

            # Read stderr line by line (that's where progress is)
            stderr_lines = []
            last_progress_len = 0

            while True:
                line = process.stderr.readline()
                if not line and process.poll() is not None:
                    break
                if line:
                    stderr_lines.append(line)
                    # Display progress lines (frame=, time=, speed=)
                    if 'frame=' in line or 'size=' in line:
                        # Parse time to calculate percentage
                        progress_info = line.strip()
                        time_match = re.search(r'time=(\d+):(\d+):(\d+\.?\d*)', line)
                        if time_match:
                            h, m, s = time_match.groups()
                            current_time = int(h) * 3600 + int(m) * 60 + float(s)
                            # Video is sped up 2x, so target time is duration/2
                            target_duration = duration / 2
                            percent = min(100, (current_time / target_duration) * 100)
                            progress_info = f"{percent:5.1f}% | {line.strip()}"

                        clean_line = progress_info[:120]
                        print(f"\r   {clean_line}{' ' * max(0, last_progress_len - len(clean_line))}", end='',
                              flush=True)
                        last_progress_len = len(clean_line)

            # End progress line
            if last_progress_len > 0:
                print()

            # Check exit code
            return_code = process.wait()

            if return_code != 0:
                raise subprocess.CalledProcessError(return_code, cmd, stderr=''.join(stderr_lines))
            elapsed = time.time() - start_time
            output_size = os.path.getsize(output_path) / (1024 * 1024)
            print(f"✅ Processed in {elapsed:.1f}s ({method})")
            print(f"   Output size: {output_size:.1f} MB")
            return output_path
        except subprocess.CalledProcessError as e:
            # Get last stderr lines - that's where the actual error is
            error_lines = e.stderr.strip().split('\n') if e.stderr else []
            error_msg = '\n'.join(error_lines[-5:]) if error_lines else "No error details"
            if i < len(methods) - 1:
                print(f"   ⚠️ {method} failed:\n      {error_msg.replace(chr(10), chr(10) + '      ')}")
                continue
            else:
                raise RuntimeError(f"All encoding methods failed.\nLast error:\n{error_msg}")

    return output_path


def split_video(video_path: str, segment_duration: int = 1800, output_dir: str = None) -> list[tuple[str, float]]:
    """
    Splits video into segments of specified duration.

    Args:
        video_path: Path to video file
        segment_duration: Segment duration in seconds (default 1800 = 30 minutes)
        output_dir: Directory for segments (defaults to temporary)

    Returns:
        List of tuples (segment_path, offset_in_seconds)
    """
    duration = get_video_duration(video_path)

    if duration <= segment_duration:
        return [(video_path, 0.0)]

    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="video_segments_")
        print(f"📁 Segments saved in: {output_dir}")

    video_file = Path(video_path)
    segments = []

    num_segments = int(duration // segment_duration) + (1 if duration % segment_duration > 0 else 0)
    print(f"Video duration: {duration / 3600:.2f}h - splitting into {num_segments} parts...")

    for i in range(num_segments):
        start_time = i * segment_duration
        segment_path = os.path.join(output_dir, f"segment_{i:03d}{video_file.suffix}")

        print(f"  Extracting part {i + 1}/{num_segments} (from {format_time(start_time)})...")

        cmd = [
            'ffmpeg', '-y', '-v', 'error',
            '-ss', str(start_time),
            '-i', video_path,
            '-t', str(segment_duration),
            '-c', 'copy',  # Fast copy without re-encoding
            segment_path
        ]

        try:
            subprocess.run(cmd, check=True, capture_output=True)
            segments.append((segment_path, start_time))
        except subprocess.CalledProcessError as e:
            # If copy doesn't work, try with re-encoding
            cmd = [
                'ffmpeg', '-y', '-v', 'error',
                '-ss', str(start_time),
                '-i', video_path,
                '-t', str(segment_duration),
                '-c:v', 'libx264', '-preset', 'ultrafast',
                '-c:a', 'aac',
                segment_path
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            segments.append((segment_path, start_time))

    return segments


def format_time(seconds: float) -> str:
    """Formats seconds to HH:MM:SS or MM:SS"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def parse_time_to_seconds(time_str: str) -> float:
    """Parses timestamp to seconds"""
    parts = time_str.split(':')
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    elif len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return 0


def adjust_timestamps_with_offset(text: str, offset_seconds: float) -> str:
    """Adds offset to all timestamps in text"""

    def add_offset_bracketed(match):
        time_str = match.group(1)
        total_seconds = parse_time_to_seconds(time_str) + offset_seconds
        return f"[{format_time(total_seconds)}]"

    def add_offset_range(match):
        prefix = match.group(1) or ""
        start_seconds = parse_time_to_seconds(match.group(2)) + offset_seconds
        end_seconds = parse_time_to_seconds(match.group(3)) + offset_seconds
        suffix = match.group(4) or ""
        return f"{prefix}{format_time(start_seconds)} - {format_time(end_seconds)}{suffix}"

    # Format [MM:SS] or [HH:MM:SS]
    text = re.sub(r'\[(\d+:\d+(?::\d+)?)\]', add_offset_bracketed, text)

    # Range format
    text = re.sub(
        r'(\*\*)?(\d{1,2}:\d{2}(?::\d{2})?)\s*-\s*(\d{1,2}:\d{2}(?::\d{2}?)?)(\*\*)?',
        add_offset_range,
        text
    )

    return text


def fix_timestamps(text: str, speed_multiplier: float = 2.0) -> str:
    """Multiplies all timestamps by speed factor"""

    def parse_and_multiply(time_str: str) -> tuple[int, int, int] | None:
        parts = time_str.split(':')

        if len(parts) == 2:
            minutes, seconds = int(parts[0]), int(parts[1])
            total_seconds = (minutes * 60 + seconds) * speed_multiplier
        elif len(parts) == 3:
            hours, minutes, seconds = int(parts[0]), int(parts[1]), int(parts[2])
            total_seconds = (hours * 3600 + minutes * 60 + seconds) * speed_multiplier
        else:
            return None

        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        seconds = int(total_seconds % 60)
        return (hours, minutes, seconds)

    def format_result(h, m, s):
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    def multiply_bracketed(match):
        result = parse_and_multiply(match.group(1))
        if result:
            return f"[{format_result(*result)}]"
        return match.group(0)

    def multiply_range(match):
        prefix = match.group(1) or ""
        start = parse_and_multiply(match.group(2))
        end = parse_and_multiply(match.group(3))
        suffix = match.group(4) or ""

        if start and end:
            return f"{prefix}{format_result(*start)} - {format_result(*end)}{suffix}"
        return match.group(0)

    # Format [MM:SS] or [HH:MM:SS]
    text = re.sub(r'\[(\d+:\d+(?::\d+)?)\]', multiply_bracketed, text)

    # Range format
    text = re.sub(
        r'(\*\*)?(\d{1,2}:\d{2}(?::\d{2})?)\s*-\s*(\d{1,2}:\d{2}(?::\d{2}?)?)(\*\*)?',
        multiply_range,
        text
    )

    return text


def analyze_single_segment(video_path: str, api_key: str, lang: str = "Polish", part_num: int = None, total_parts: int = None) -> str:
    """Analyzes a single video segment"""
    from google import genai

    client = genai.Client(api_key=api_key)

    part_info = f" (part {part_num}/{total_parts})" if part_num else ""
    print(f"Uploading file{part_info}: {Path(video_path).name}...")

    uploaded_file = client.files.upload(file=video_path)

    print(f"Waiting for processing{part_info}...")
    while uploaded_file.state.name == "PROCESSING":
        time.sleep(2)
        uploaded_file = client.files.get(name=uploaded_file.name)

    if uploaded_file.state.name == "FAILED":
        raise RuntimeError(f"File processing failed: {uploaded_file.state.name}")

    print(f"Analyzing{part_info}...")

    prompt = f"""Analyze this video and provide. Do not add translation:

    ⚠️ IMPORTANT ABOUT TIMESTAMPS: Provide timestamps EXACTLY as you see them in the video, without any conversions or modifications. Report raw time from video.
    ⚠️ CRITICAL: You must transcribe the ENTIRE video from beginning to end.
    Do not end prematurely. Do not summarize. Continue until the last second of the video.
    If you're approaching the limit, still continue - do not stop in the middle.

    1. **VIDEO DESCRIPTION**: Detailed description of what happens in the video - scenes, people, actions, locations, mood.

    2. **TRANSCRIPTION**: Full transcription of all spoken words, dialogues and narration. Must be verbatim - in original language. I care most about transcription, so list the entire video! Skip songs.

       IMPORTANT - for each utterance provide:
       - WHO speaks (name if known, or description e.g. "Man in blue shirt", "Presenter", "Narrator")
       - TO WHOM they speak (to specific person, to group, to camera/viewer, to themselves)
       - Content of utterance

       Format:
       [Time] SPEAKER (to RECIPIENT): "content of utterance"

       Examples:
       [0:15] Host (to viewers): "Welcome to today's episode..."
       [0:32] Anna (to Mark): "Can you help me?"
       [0:45] Man in suit (to group at table): "We need to make a decision..."
       [1:20] Narrator (narration): "Three years have passed since those events..."

       If there is no speech, write "No dialogues/narration".

    3. **ADDITIONAL INFORMATION**: 
       - Estimated duration of individual scenes

    Respond in {lang} but keep transcription in original language - unchaged."""

    response = client.models.generate_content(
        model="gemini-3-flash-preview",
        contents=[uploaded_file, prompt],
        config={
            "temperature": 0.4,
            "max_output_tokens": 65536,
        }
    )

    if hasattr(response, 'usage_metadata'):
        usage = response.usage_metadata
        print(f"   📊 Tokens{part_info}: {usage.prompt_token_count:,} input, {usage.candidates_token_count:,} output")

    print(f"Deleting file from server{part_info}...")
    client.files.delete(name=uploaded_file.name)

    return response.text

def merge_analyses(analyses: list[tuple[str, float]], speed_multiplier: float = 1.0) -> str:
    """
    Merges analyses from multiple segments into one.

    Args:
        analyses: List of tuples (analysis_text, offset_in_seconds)
        speed_multiplier: Speed factor for timestamp correction

    Returns:
        Merged analysis text
    """
    if len(analyses) == 1:
        text = analyses[0][0]
        if speed_multiplier != 1.0:
            text = fix_timestamps(text, speed_multiplier)
        return text

    merged_descriptions = []
    merged_transcriptions = []
    merged_additional = []

    for i, (analysis, offset) in enumerate(analyses):
        part_header = f"\n{'=' * 40}\nPART {i + 1} (from {format_time(offset * speed_multiplier)})\n{'=' * 40}\n"

        # First adjust offset, then speed
        adjusted = adjust_timestamps_with_offset(analysis, offset)
        if speed_multiplier != 1.0:
            adjusted = fix_timestamps(adjusted, speed_multiplier)

        # Try to extract sections
        desc_match = re.search(r'\*\*VIDEO DESCRIPTION\*\*[:\s]*(.*?)(?=\*\*TRANSCRIPTION\*\*|\*\*2\.|$)', adjusted,
                               re.DOTALL | re.IGNORECASE)
        trans_match = re.search(r'\*\*TRANSCRIPTION\*\*[:\s]*(.*?)(?=\*\*ADDITIONAL|\*\*3\.|$)', adjusted,
                                re.DOTALL | re.IGNORECASE)
        add_match = re.search(r'\*\*ADDITIONAL INFORMATION\*\*[:\s]*(.*?)$', adjusted, re.DOTALL | re.IGNORECASE)

        if desc_match:
            merged_descriptions.append(f"{part_header}{desc_match.group(1).strip()}")
        if trans_match:
            merged_transcriptions.append(f"{part_header}{trans_match.group(1).strip()}")
        if add_match:
            merged_additional.append(f"{part_header}{add_match.group(1).strip()}")

        # If sections couldn't be extracted, add everything to transcription
        if not trans_match:
            merged_transcriptions.append(f"{part_header}{adjusted}")

    result = []

    result.append("**Use this description for better translation and gender recognition for proper declension in translation:\n")
    result.append("\n".join(merged_descriptions))

    if merged_descriptions:
        result.append("**VIDEO DESCRIPTION** (merged from all parts):\n")
        result.append("\n".join(merged_descriptions))

    if merged_transcriptions:
        result.append("\n\n**TRANSCRIPTION** (merged from all parts):\n")
        result.append("\n".join(merged_transcriptions))

    if merged_additional:
        result.append("\n\n**ADDITIONAL INFORMATION** (merged from all parts):\n")
        result.append("\n".join(merged_additional))

    return "\n".join(result)


def analyze_video(video_path: str, api_key: str, speed_multiplier: float = 1.0,
                  segment_duration: int = 1800, preprocess: bool = False, lang: str = "Polish") -> dict:
    """
    Sends video to Gemini and receives description and transcription.
    For videos longer than segment_duration, splits them into parts.

    Args:
        video_path: Path to video file
        api_key: Google AI API key
        speed_multiplier: Video speed factor (e.g. 2.0 for 2x sped up)
        segment_duration: Maximum segment duration in seconds (default 1800 = 30 minutes)
        preprocess: Whether to preprocess video before analysis (2x speed + 360p)
        lang: Language for the response (default: Polish)

    Returns:
        Dictionary with description and transcription
    """
    video_file = Path(video_path)
    if not video_file.exists():
        raise FileNotFoundError(f"Video file does not exist: {video_path}")

    preprocessed_path = None
    temp_dir = None

    try:
        # Preprocessing if enabled
        if preprocess:
            preprocessed_path = preprocess_video(video_path)
            video_path = preprocessed_path
            speed_multiplier = 2.0  # Force 2x because video is sped up
            print()

        # Check duration and split if needed
        segments = split_video(video_path, segment_duration)

        if len(segments) > 1:
            temp_dir = os.path.dirname(segments[0][0]) if segments[0][0] != video_path else None

        # Analyze each segment
        analyses = []
        for i, (segment_path, offset) in enumerate(segments):
            part_num = i + 1 if len(segments) > 1 else None
            total_parts = len(segments) if len(segments) > 1 else None

            analysis = analyze_single_segment(segment_path, api_key, lang, part_num, total_parts)
            analyses.append((analysis, offset))

            # Short break between segments to not exceed rate limit
            if i < len(segments) - 1:
                print("Waiting 5s before next segment...")
                time.sleep(5)

        # Merge results
        print("\nMerging results...")
        merged_analysis = merge_analyses(analyses, speed_multiplier)

        return {
            "analysis": merged_analysis,
            "file_name": video_file.name,
            "model": "gemini-3-flash-preview",
            "segments": len(segments),
            "preprocessed": preprocess,
            "lang": lang
        }

    finally:
        # Delete temporary files
        if temp_dir and os.path.exists(temp_dir):
            print("Deleting temporary segments...")
            shutil.rmtree(temp_dir)

        if preprocessed_path and os.path.exists(preprocessed_path):
            print("Deleting temporary preprocessed file...")
            os.remove(preprocessed_path)


def main():
    parser = argparse.ArgumentParser(
        description="Video analyzer using Gemini 2.5 Flash Preview (with automatic splitting of long videos)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Usage examples:
  python video_analyzer.py --api_key YOUR_KEY
  python video_analyzer.py  # will use GOOGLE_API_KEY variable
  python video_analyzer.py --segment_duration 3600  # split every 1 hour
  python video_analyzer.py --lang English  # response in English
        """
    )

    parser.add_argument(
        "--api_key", "-k",
        help="Google AI API key (defaults to GOOGLE_API_KEY environment variable)",
        default=None
    )

    parser.add_argument(
        "--segment_duration", "-s",
        type=int,
        default=None,
        help="Maximum segment duration in seconds (defaults to interactive prompt)"
    )

    parser.add_argument(
        "--lang", "-l",
        type=str,
        default="Polish",
        help="Language for the response (default: Polish)"
    )

    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("GOOGLE_API_KEY")

    if not api_key:
        print("Error: No API key provided.")
        print("Use --api_key or set the GOOGLE_API_KEY environment variable")
        sys.exit(1)

    video_path = input("Enter path to video file: ").strip().strip('"\'')

    if not video_path:
        print("Error: No video file path provided.")
        sys.exit(1)

    # Preprocessing question
    preprocess_input = input(
        "Do you want to preprocess video before analysis? (2x speed + 360p, much faster) [y/N]: ").strip().lower()
    preprocess = preprocess_input in ('t', 'tak', 'y', 'yes')

    if preprocess:
        speed_multiplier = 2.0
        print("📌 Video will be sped up 2x - timestamps will be automatically corrected.")
    else:
        # Ask about speed only when there's no preprocessing
        speed_input = input("Enter video speed (e.g. 2.0 for 2x sped up, Enter for 1.0): ").strip()

        if speed_input:
            try:
                speed_multiplier = float(speed_input)
                if speed_multiplier <= 0:
                    print("Error: Speed must be greater than 0.")
                    sys.exit(1)
            except ValueError:
                print("Error: Invalid speed value.")
                sys.exit(1)
        else:
            speed_multiplier = 1.0

    # Segment duration question (if not provided in arguments)
    if args.segment_duration is not None:
        segment_duration = args.segment_duration
    else:
        print("\n📐 Segment duration (for long videos):")
        print("   Hints: 900 = 15 min, 1800 = 30 min, 2700 = 45 min, 3600 = 1h")
        segment_input = input("Enter maximum segment duration in seconds (Enter for 1800 = 30 min): ").strip()

        if segment_input:
            try:
                segment_duration = int(segment_input)
                if segment_duration <= 0:
                    print("Error: Segment duration must be greater than 0.")
                    sys.exit(1)
                if segment_duration < 60:
                    print("⚠️ Warning: Very short segments (<60s) may be inefficient.")
            except ValueError:
                print("Error: Invalid segment duration value.")
                sys.exit(1)
        else:
            segment_duration = 1800

        print(f"📌 Segments: {segment_duration}s ({segment_duration // 60} min)")

    try:
        result = analyze_video(
            video_path,
            api_key,
            speed_multiplier=speed_multiplier,
            segment_duration=segment_duration,
            preprocess=preprocess,
            lang=args.lang
        )

        # Save to file next to original
        video_dir = os.path.dirname(os.path.abspath(video_path))
        output_file = os.path.join(video_dir, "transcription.txt")

        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(f"VIDEO ANALYSIS: {result['file_name']}\n")
            f.write(f"Model: {result['model']}\n")
            f.write(f"Language: {result['lang']}\n")
            if result['segments'] > 1:
                f.write(f"Processed in {result['segments']} parts\n")
            # if result.get('preprocessed'):
            #     f.write("Preprocessing: 2x speed + 360p (hardware accelerated)\n")
            # if speed_multiplier != 1.0:
            #     f.write(f"Speed correction: x{speed_multiplier}\n")
            f.write("=" * 60 + "\n\n")
            f.write(result['analysis'])

        print("\n" + "=" * 60)
        print(f"VIDEO ANALYSIS: {result['file_name']}")
        print(f"Model: {result['model']}")
        print(f"Language: {result['lang']}")
        if result['segments'] > 1:
            print(f"Processed in {result['segments']} parts")
        if result.get('preprocessed'):
            print("Preprocessing: 2x speed + 360p (hardware accelerated)")
        if speed_multiplier != 1.0:
            print(f"Speed correction: x{speed_multiplier}")
        print("=" * 60 + "\n")
        print(result['analysis'])
        print("\n" + "=" * 60)
        print(f"\n💾 Saved to: {output_file}")

    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error during analysis: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()