# gui/__init__.py
"""
GUI package for the CLI Wrapper application.
Contains the main window and UI components.
"""

from .main_window import DragDropGUI
from .config_manager import ConfigManager

__all__ = ['DragDropGUI', 'ConfigManager']


# utils/__init__.py
"""
Utility package for the CLI Wrapper application.
Contains file utilities and CLI execution helpers.
"""

from utils.file_utils import (
    extract_movie_info,
    format_movie_info,
    classify_file_type,
    format_file_size,
    scan_folder_for_files
)
from utils.cli_runner import CLIRunner
from utils.tmdb_helper import TMDBHelper, get_tmdb_id_for_file

__all__ = [
    'extract_movie_info',
    'format_movie_info',
    'classify_file_type',
    'format_file_size',
    'scan_folder_for_files',
    'CLIRunner',
    'TMDBHelper',
    'get_tmdb_id_for_file'
]