"""
Configuration manager for the GUI application.
Handles loading, saving, and managing application settings.
"""

import json
from pathlib import Path
import os


class ConfigManager:
    """Manages application configuration persistence"""

    def __init__(self, app_name="SubtitleGenerator", config_file="gui_config.json"):
        self.app_name = app_name
        self.config_dir = self._get_config_dir()
        self.config_file = self.config_dir / config_file
        self.config = {}
        self._default_config = {
            'gemini_api_key': '',
            'model': 'gemini-2.0-flash',
            'tmdb_api_key': '',
            'tmdb_id': '',
            'api_expanded': False,
            'settings_expanded': False,
            'language': 'Polish',
            'language_code': 'pl',
            'extract_audio': False,
            'auto_fetch_tmdb': True,
            'is_tv_series': False,
            'translation_type': 'Default'
        }
        self._ensure_config_dir()
        self.load_config()

    def _get_config_dir(self):
        """Get platform-specific configuration directory"""
        if os.name == 'nt':  # Windows
            base = os.getenv('APPDATA')
            if not base:
                base = Path.home() / 'AppData' / 'Roaming'
            else:
                base = Path(base)
        elif os.name == 'posix':  # macOS and Linux
            if os.uname().sysname == 'Darwin':  # macOS
                base = Path.home() / 'Library' / 'Application Support'
            else:  # Linux
                base = os.getenv('XDG_CONFIG_HOME')
                if not base:
                    base = Path.home() / '.config'
                else:
                    base = Path(base)
        else:
            # Fallback to home directory
            base = Path.home() / '.config'

        print(f'Config file saved in {base / self.app_name}')
        return base / self.app_name

    def _ensure_config_dir(self):
        """Create configuration directory if it doesn't exist"""
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"Error creating configuration directory: {e}")
            # Fallback to current directory
            self.config_dir = Path.cwd()
            self.config_file = self.config_dir / "gui_config.json"

    def load_config(self):
        """Load configuration from JSON file"""
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    loaded_config = json.load(f)
                    # Merge with defaults to ensure all keys exist
                    self.config = {**self._default_config, **loaded_config}
            else:
                self.config = self._default_config.copy()
        except Exception as e:
            print(f"Error loading configuration: {e}")
            self.config = self._default_config.copy()

    def save_config(self):
        """Save current configuration to JSON file"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"Error saving configuration: {e}")
            return False

    def get(self, key, default=None):
        """Get configuration value"""
        return self.config.get(key, default)

    def set(self, key, value):
        """Set configuration value"""
        self.config[key] = value

    def update(self, updates):
        """Update multiple configuration values"""
        self.config.update(updates)

    def get_api_config(self):
        """Get API-related configuration"""
        return {
            'gemini_api_key': self.get('gemini_api_key', ''),
            'model': self.get('model', 'gemini-pro'),
            'tmdb_api_key': self.get('tmdb_api_key', '')
        }

    def get_ui_config(self):
        """Get UI-related configuration"""
        return {
            'api_expanded': self.get('api_expanded', False),
            'settings_expanded': self.get('settings_expanded', False)
        }

    def get_processing_config(self):
        """Get processing-related configuration"""
        return {
            'language': self.get('language', 'Polish'),
            'extract_audio': self.get('extract_audio', False),
            'auto_fetch_tmdb': self.get('auto_fetch_tmdb', True),
            'language_code': self.get('language_code', True),
            'tmdb_id': self.get('tmdb_id', ''),
            'translation_type': self.get('translation_type', 'Default')
        }

    def has_gemini_api_key(self):
        """Check if Gemini API key is configured"""
        return bool(self.get('gemini_api_key', '').strip())

    def has_tmdb_api_key(self):
        """Check if TMDB API key is configured"""
        return bool(self.get('tmdb_api_key', '').strip())

    def has_tmdb_id(self):
        """Check if TMDB ID is configured"""
        return bool(self.get('tmdb_id', '').strip())

    def get_config_summary(self):
        """Get a summary of current configuration for logging"""
        return {
            'model': self.get('model', 'gemini-pro'),
            'has_gemini_key': self.has_gemini_api_key(),
            'has_tmdb_key': self.has_tmdb_api_key(),
            'has_tmdb_id': self.has_tmdb_id(),
            'language': self.get('language', 'Polish'),
            'extract_audio': self.get('extract_audio', False),
            'auto_fetch_tmdb': self.get('auto_fetch_tmdb', True),
            'config_location': str(self.config_file)
        }

    def validate_config(self):
        """Validate current configuration and return any issues"""
        issues = []

        if not self.has_gemini_api_key():
            issues.append("Gemini API key is missing")

        model = self.get('model', '')
        valid_models = ["gemini-2.5-flash-preview-05-20", "gemini-2.0-flash", "gemini-2.5-pro-preview-06-05"]
        if model not in valid_models:
            issues.append(f"Invalid model: {model}")

        language = self.get('language', '')
        if not language.strip():
            issues.append("Language is not set")

        return issues

    def reset_to_defaults(self):
        """Reset configuration to default values"""
        self.config = self._default_config.copy()

    def export_config(self, file_path):
        """Export configuration to a different file"""
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"Error exporting configuration: {e}")
            return False

    def import_config(self, file_path):
        """Import configuration from a file"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                imported_config = json.load(f)
                # Validate and merge with defaults
                self.config = {**self._default_config, **imported_config}
            return True
        except Exception as e:
            print(f"Error importing configuration: {e}")
            return False

    def get_config_path(self):
        """Get the full path to the configuration file"""
        return str(self.config_file)