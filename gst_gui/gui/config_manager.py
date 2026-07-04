"""
Configuration manager for the GUI application.
Handles loading, saving, and managing application settings.

API keys are stored encrypted: on Windows via DPAPI (tied to the current
Windows user account, no extra dependencies), elsewhere via light
base64+XOR obfuscation. Legacy plaintext configs load transparently and
get encrypted on the next save.
"""

import base64
import getpass
import json
from pathlib import Path
import platform

# Config fields that hold secrets and get encrypted on disk
_SENSITIVE_KEYS = ('gemini_api_key', 'gemini_api_key2', 'tmdb_api_key')
_DPAPI_PREFIX = 'dpapi:'
_OBF_PREFIX = 'obf:'


def _dpapi_crypt(data, encrypt):
    """Encrypt/decrypt bytes with Windows DPAPI (current user scope)."""
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD),
                    ('pbData', ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()

    func = (ctypes.windll.crypt32.CryptProtectData if encrypt
            else ctypes.windll.crypt32.CryptUnprotectData)
    if not func(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError('DPAPI call failed')
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _xor_bytes(data):
    """Light XOR obfuscation keyed on the OS username (non-Windows fallback)."""
    key = (getpass.getuser() + 'gst-gui-cfg').encode('utf-8')
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def _encrypt_value(value):
    """Encrypt a secret string for storage. Empty values stay empty."""
    if not value:
        return value
    raw = value.encode('utf-8')
    if platform.system() == 'Windows':
        try:
            return _DPAPI_PREFIX + base64.b64encode(_dpapi_crypt(raw, encrypt=True)).decode('ascii')
        except Exception:
            pass  # fall back to obfuscation
    return _OBF_PREFIX + base64.b64encode(_xor_bytes(raw)).decode('ascii')


def _decrypt_value(stored):
    """Decrypt a stored secret. Plain (legacy) values pass through unchanged."""
    if not isinstance(stored, str) or not stored:
        return stored
    try:
        if stored.startswith(_DPAPI_PREFIX):
            raw = base64.b64decode(stored[len(_DPAPI_PREFIX):])
            return _dpapi_crypt(raw, encrypt=False).decode('utf-8')
        if stored.startswith(_OBF_PREFIX):
            raw = base64.b64decode(stored[len(_OBF_PREFIX):])
            return _xor_bytes(raw).decode('utf-8')
    except Exception:
        print('⚠️ Could not decrypt a stored API key (config copied from '
              'another machine/user account?) - please re-enter it')
        return ''
    return stored  # legacy plaintext value


class ConfigManager:
    """Manages application configuration persistence"""

    def __init__(self, config_file=None):
        """
        Initialize config manager.
        If config_file is not provided, uses platform-specific user config directory.
        """
        if config_file is None:
            self.config_file = self._get_config_path()
        else:
            self.config_file = Path(config_file)

        # Ensure config directory exists
        self.config_file.parent.mkdir(parents=True, exist_ok=True)

        self.config = {}
        self._default_config = {
            'gemini_api_key': '',
            'gemini_api_key2': '',
            'model': 'gemini-2.5-flash',
            'fallback_models': '',
            'tmdb_api_key': '',
            'tmdb_id': '',
            'api_expanded': False,
            'settings_expanded': False,
            'language': 'Polish',
            'language_code': 'pl',
            'extract_audio': False,
            'include_timestamps': False,
            'auto_fetch_tmdb': True,
            'is_tv_series': False,
            'translation_type': 'Default',
            'add_translator_info': True,
            'batch_size': 300
        }
        self.load_config()
        print(f"📁 Config file: {self.config_file}")

    @staticmethod
    def _get_config_path():
        r"""
        Get platform-specific config directory path.
        - Windows: %APPDATA%\Gemini-SRT-Translator\
        - macOS: ~/Library/Application Support/Gemini-SRT-Translator/
        - Linux: ~/.config/gemini-srt-translator/
        """
        system = platform.system()

        if system == "Windows":
            config_dir = Path.home() / "AppData" / "Roaming" / "Gemini-SRT-Translator"
        elif system == "Darwin":  # macOS
            config_dir = Path.home() / "Library" / "Application Support" / "Gemini-SRT-Translator"
        else:  # Linux and others
            config_dir = Path.home() / ".config" / "gemini-srt-translator"

        return config_dir / "gui_config.json"

    def has_gemini_api_key2(self):
        """Check if second Gemini API key is configured"""
        return bool(self.get('gemini_api_key2', '').strip())

    def _decrypt_sensitive(self):
        """Decrypt sensitive fields in the in-memory config (in place)"""
        for key in _SENSITIVE_KEYS:
            if key in self.config:
                self.config[key] = _decrypt_value(self.config[key])

    def load_config(self):
        """Load configuration from JSON file"""
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    loaded_config = json.load(f)
                    # Merge with defaults to ensure all keys exist
                    self.config = {**self._default_config, **loaded_config}
                    self._decrypt_sensitive()
                    print(f"✅ Configuration loaded from: {self.config_file}")
            else:
                self.config = self._default_config.copy()
                print(f"ℹ️ No existing config, using defaults")
        except Exception as e:
            print(f"⚠️ Error loading configuration: {e}")
            self.config = self._default_config.copy()

    def save_config(self):
        """Save current configuration to JSON file (API keys encrypted)"""
        try:
            to_save = dict(self.config)
            for key in _SENSITIVE_KEYS:
                if to_save.get(key):
                    to_save[key] = _encrypt_value(to_save[key])
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(to_save, f, indent=2, ensure_ascii=False)
            print(f"✅ Configuration saved to: {self.config_file}")
            return True
        except Exception as e:
            print(f"❌ Error saving configuration: {e}")
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
            'gemini_api_key2': self.get('gemini_api_key2', ''),
            'model': self.get('model', 'gemini-pro'),
            'fallback_models': self.get('fallback_models', ''),
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
            'include_timestamps': self.get('include_timestamps', False),
            'auto_fetch_tmdb': self.get('auto_fetch_tmdb', True),
            'language_code': self.get('language_code', 'pl'),
            'tmdb_id': self.get('tmdb_id', ''),
            'is_tv_series': self.get('is_tv_series', False),
            'translation_type': self.get('translation_type', 'Default'),
            'add_translator_info': self.get('add_translator_info', True),
            'batch_size': self.get('batch_size', True)
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
            'has_gemini_key2': self.has_gemini_api_key2(),
            'has_tmdb_key': self.has_tmdb_api_key(),
            'has_tmdb_id': self.has_tmdb_id(),
            'language': self.get('language', 'Polish'),
            'extract_audio': self.get('extract_audio', False),
            'auto_fetch_tmdb': self.get('auto_fetch_tmdb', True)
        }

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
            print(f"❌ Error exporting configuration: {e}")
            return False

    def import_config(self, file_path):
        """Import configuration from a file"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                imported_config = json.load(f)
                # Validate and merge with defaults
                self.config = {**self._default_config, **imported_config}
                self._decrypt_sensitive()
            print(f"✅ Configuration imported from: {file_path}")
            return True
        except Exception as e:
            print(f"❌ Error importing configuration: {e}")
            return False

    def get_config_directory(self):
        """Get the directory where config is stored"""
        return self.config_file.parent