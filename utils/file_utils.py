"""
File utility functions for the GUI application.
Handles file classification, movie info extraction, and file system operations.
"""

import re
from pathlib import Path


def extract_movie_info(filename):
    """Extract movie name and year from filename using regex patterns"""
    if not filename:
        return "Unknown Movie", None

    # Remove file extension
    name_without_ext = Path(filename).stem

    # Common patterns to clean up filename
    # Remove common patterns like [codec], (group), {quality}, etc.
    clean_name = re.sub(r'[\[\({].*?[\]\)}]', '', name_without_ext)

    # Remove common words and patterns
    clean_patterns = [
        r'\b(BluRay|BDRip|DVDRip|WEBRip|HDRip|CAMRip|TS|TC|SCR|R5|R6)\b',
        r'\b(720p|1080p|2160p|4K|HD|FHD|UHD)\b',
        r'\b(x264|x265|H\.?264|H\.?265|HEVC|AVC)\b',
        r'\b(AAC|AC3|DTS|MP3|FLAC)\b',
        r'\b(EXTENDED|UNRATED|DIRECTORS?\.?CUT|REMASTERED)\b',
        r'\b(PROPER|REPACK|INTERNAL|LIMITED|FESTIVAL)\b',
        r'\b\d+MB\b|\b\d+GB\b',  # File sizes
        r'\b[A-Z]{2,}$',  # Release groups at the end
    ]

    for pattern in clean_patterns:
        clean_name = re.sub(pattern, '', clean_name, flags=re.IGNORECASE)

    # Replace dots, underscores, and dashes with spaces
    clean_name = re.sub(r'[._-]+', ' ', clean_name)

    # Extract year (4 digits, typically 19xx or 20xx)
    year_match = re.search(r'\b(19|20)\d{2}\b', clean_name)
    year = year_match.group() if year_match else None

    # Remove year from movie name if found
    if year:
        movie_name = re.sub(r'\b' + re.escape(year) + r'\b', '', clean_name)
    else:
        movie_name = clean_name

    # Clean up extra whitespace and common remaining artifacts
    movie_name = re.sub(r'\s+', ' ', movie_name).strip()
    movie_name = re.sub(r'^[.\-_\s]+|[.\-_\s]+$', '', movie_name)

    # If movie name is empty or too short, use original filename
    if not movie_name or len(movie_name) < 2:
        movie_name = name_without_ext

    return movie_name, year


def format_movie_info(movie_name, year):
    """Format movie name and year for display - returns tuple (title, year)"""
    if not movie_name:
        title = "Unknown Movie"
    else:
        title = movie_name

    # Return year without parentheses, or empty string if no year
    year_display = year if year else ""

    return title, year_display


def classify_file_type(file_path):
    """Classify file type based on extension"""
    ext = file_path.suffix.lower()

    text_extensions = {'.txt', '.srt', '.vtt', '.sub', '.ass', '.ssa'}
    video_extensions = {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v'}
    audio_extensions = {'.mp3', '.wav', '.flac', '.aac', '.ogg', '.m4a', '.wma'}
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp'}

    if ext in text_extensions:
        return 'text'
    elif ext in video_extensions:
        return 'video'
    elif ext in audio_extensions:
        return 'audio'
    elif ext in image_extensions:
        return 'image'
    else:
        return 'other'


def format_file_size(size_bytes):
    """Format file size in readable way"""
    if size_bytes == 0:
        return "0 B"

    size_names = ["B", "KB", "MB", "GB"]
    i = 0
    while size_bytes >= 1024.0 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1

    return f"{size_bytes:.1f} {size_names[i]}"


def get_file_extensions():
    """Get dictionaries of file extensions by category"""
    return {
        'text': {'.txt', '.srt', '.vtt', '.sub', '.ass', '.ssa'},
        'video': {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v'},
        'audio': {'.mp3', '.wav', '.flac', '.aac', '.ogg', '.m4a', '.wma'},
        'image': {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp'}
    }


def scan_folder_for_files(folder_path, include_subfolders=True):
    """
    Scan folder for files and categorize them by type.

    Args:
        folder_path (Path): Path to folder to scan
        include_subfolders (bool): Whether to include subfolders in scan

    Returns:
        dict: Dictionary with categorized file lists
    """
    extensions = get_file_extensions()

    found_files = {
        'text': [],
        'video': [],
        'audio': [],
        'image': [],
        'other': []
    }

    total_files = 0

    # Choose scanning method based on include_subfolders
    if include_subfolders:
        file_iterator = folder_path.rglob('*')
    else:
        file_iterator = folder_path.glob('*')

    for file_path in file_iterator:
        if file_path.is_file():
            total_files += 1
            ext = file_path.suffix.lower()
            relative_path = file_path.relative_to(folder_path)

            # Categorize file
            categorized = False
            for category, exts in extensions.items():
                if ext in exts:
                    found_files[category].append(relative_path)
                    categorized = True
                    break

            if not categorized:
                found_files['other'].append(relative_path)

    # Add metadata
    found_files['_metadata'] = {
        'total_files': total_files,
        'folder_path': folder_path
    }

    return found_files