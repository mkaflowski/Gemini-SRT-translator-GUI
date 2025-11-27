"""
Translation Handler for the CLI Wrapper GUI application.
Manages the translation process, threading, and progress tracking.
"""
import threading
import time
from pathlib import Path
from tkinter import messagebox


class TranslationConfig:
    """Configuration container for translation process"""

    def __init__(self, **kwargs):
        # API Configuration
        self.gemini_api_key = kwargs.get('gemini_api_key', '')
        self.model = kwargs.get('model', 'gemini-2.5-flash')
        self.tmdb_api_key = kwargs.get('tmdb_api_key', '')
        self.tmdb_id = kwargs.get('tmdb_id', '')

        # Processing Configuration
        self.language = kwargs.get('language', 'Polish')
        self.language_code = kwargs.get('language_code', 'pl')
        self.extract_audio = kwargs.get('extract_audio', False)
        self.add_translator_info = kwargs.get('add_translator_info', True)

        # Content Information
        self.overview = kwargs.get('overview', '')
        self.movie_title = kwargs.get('movie_title', '')
        self.is_tv_series = kwargs.get('is_tv_series', False)

        # Control
        self.cancel_event = kwargs.get('cancel_event', None)

        self.translation_type = kwargs.get('translation_type', 'Default')

    def validate(self):
        """
        Validate configuration for translation

        Returns:
            tuple: (is_valid, error_message)
        """
        if not self.gemini_api_key.strip():
            return False, "Gemini API key is required"

        if not self.language.strip():
            return False, "Target language is required"

        if not self.language_code.strip():
            return False, "Language code is required"

        return True, None

    def to_dict(self):
        """Convert configuration to dictionary for CLI runner"""
        return {
            'gemini_api_key': self.gemini_api_key,
            'model': self.model,
            'tmdb_api_key': self.tmdb_api_key,
            'tmdb_id': self.tmdb_id,
            'language': self.language,
            'language_code': self.language_code,
            'extract_audio': self.extract_audio,
            'overview': self.overview,
            'movie_title': self.movie_title,
            'is_tv_series': self.is_tv_series,
            'cancel_event': self.cancel_event,
            'add_translator_info': self.add_translator_info,
            'translation_type': self.translation_type,
        }


class TranslationState:
    """Tracks the state of translation process"""

    def __init__(self):
        self.is_running = False
        self.is_cancelled = False
        self.current_file = None
        self.completed_files = 0
        self.total_files = 0
        self.errors = []
        self.start_time = None
        self.end_time = None

    def reset(self):
        """Reset state for new translation"""
        self.is_running = False
        self.is_cancelled = False
        self.current_file = None
        self.completed_files = 0
        self.total_files = 0
        self.errors = []
        self.start_time = None
        self.end_time = None

    def start(self, total_files):
        """Mark translation as started"""
        self.reset()
        self.is_running = True
        self.total_files = total_files
        self.start_time = time.time()

    def complete_file(self, filename, success=True, error=None):
        """Mark a file as completed"""
        self.completed_files += 1
        self.current_file = None

        if not success and error:
            self.errors.append({
                'file': filename,
                'error': str(error)
            })

    def cancel(self):
        """Mark translation as cancelled"""
        self.is_cancelled = True
        self.is_running = False
        self.end_time = time.time()

    def finish(self):
        """Mark translation as finished"""
        self.is_running = False
        self.end_time = time.time()

    def get_progress_percentage(self):
        """Get completion percentage"""
        if self.total_files == 0:
            return 0
        return min(100, int((self.completed_files / self.total_files) * 100))

    def get_duration(self):
        """Get translation duration in seconds"""
        if self.start_time is None:
            return 0

        end_time = self.end_time or time.time()
        return end_time - self.start_time

    def has_errors(self):
        """Check if there were any errors"""
        return len(self.errors) > 0

    def get_summary(self):
        """Get translation summary"""
        duration = self.get_duration()

        summary = {
            'total_files': self.total_files,
            'completed_files': self.completed_files,
            'failed_files': len(self.errors),
            'duration_seconds': duration,
            'duration_formatted': self._format_duration(duration),
            'was_cancelled': self.is_cancelled,
            'success_rate': (self.completed_files / self.total_files * 100) if self.total_files > 0 else 0,
            'errors': self.errors
        }

        return summary

    def _format_duration(self, seconds):
        """Format duration as human-readable string"""
        if seconds < 60:
            return f"{seconds:.1f} seconds"
        elif seconds < 3600:
            minutes = seconds / 60
            return f"{minutes:.1f} minutes"
        else:
            hours = seconds / 3600
            return f"{hours:.1f} hours"


class TranslationHandler:
    """Handles translation process execution and management"""

    def __init__(self, cli_runner, logger=None, status_callback=None,
                 button_callback=None, completion_callback=None):
        """
        Initialize translation handler

        Args:
            cli_runner: CLI runner instance for executing translations
            logger: Function to log messages
            status_callback: Function to update status (receives status string)
            button_callback: Function to toggle UI buttons (receives 'translate' or 'cancel')
            completion_callback: Function called when translation completes (receives summary)
        """
        self.cli_runner = cli_runner
        self.logger = logger or self._default_logger
        self.status_callback = status_callback
        self.button_callback = button_callback
        self.completion_callback = completion_callback

        self.state = TranslationState()
        self.processing_thread = None
        self.cancel_event = threading.Event()

    def _default_logger(self, message):
        """Default logger that prints to console"""
        print(message)

    def start_translation(self, file_pairs, config_dict):
        """
        Start translation process

        Args:
            file_pairs: List of file pairs to translate
            config_dict: Configuration dictionary

        Returns:
            bool: True if translation started successfully
        """
        # Validate we're not already running
        if self.state.is_running:
            self.logger("⚠️ Translation already in progress")
            return False

        # Create configuration object
        config = TranslationConfig(**config_dict)

        # Validate configuration
        is_valid, error_msg = config.validate()
        if not is_valid:
            self.logger(f"❌ Configuration error: {error_msg}")
            messagebox.showerror("Configuration Error", error_msg)
            return False

        # Validate file pairs
        valid_pairs = self._validate_file_pairs(file_pairs, config.extract_audio)
        if not valid_pairs:
            self.logger("❌ No valid file pairs to process")
            messagebox.showwarning("No Valid Files",
                                 "No valid file pairs found for translation.")
            return False

        # Show confirmation
        if not self._confirm_translation(valid_pairs, config):
            return False

        # Start the translation process
        self._start_translation_async(valid_pairs, config)
        return True

    def cancel_translation(self):
        """Cancel the current translation process"""
        if not self.state.is_running:
            self.logger("ℹ️ No active translation to cancel")
            return

        self.logger("🛑 Cancelling translation...")

        # Set cancel events
        self.cancel_event.set()
        if hasattr(self.state, 'cancel_event') and self.state.cancel_event:
            self.state.cancel_event.set()

        # Update status
        if self.status_callback:
            self.status_callback("Cancelling...")

        # Wait for thread to finish (max 5 seconds)
        if self.processing_thread and self.processing_thread.is_alive():
            self.processing_thread.join(timeout=5.0)

            if self.processing_thread.is_alive():
                self.logger("⚠️ Force terminating process...")

        # Update state
        self.state.cancel()

        self.logger("✅ Translation has been cancelled")

        # Update UI
        if self.status_callback:
            self.status_callback("Cancelled")
        if self.button_callback:
            self.button_callback('translate')

    def _validate_file_pairs(self, file_pairs, extract_audio):
        return file_pairs

    def _confirm_translation(self, valid_pairs, config):
        """
        Show confirmation dialog for translation

        Args:
            valid_pairs: List of valid file pairs
            config: Translation configuration

        Returns:
            bool: True if user confirmed
        """
        count = len(valid_pairs)

        if config.extract_audio:
            msg = f"Start processing {count} pairs with subtitles and video for audio extraction?\n"
        else:
            msg = f"Start processing {count} subtitle files?\n"

        msg += f"\nFiles to process:\n"

        # Show first 5 files
        for i, pair in enumerate(valid_pairs[:5]):
            if config.extract_audio:
                subtitle_name = Path(pair['subtitle']).name if pair['subtitle'] else 'None'
                video_name = Path(pair['video']).name if pair['video'] else 'None'
                msg += f"• {subtitle_name} + {video_name}\n"
            else:
                subtitle_name = Path(pair['subtitle']).name if pair['subtitle'] else 'None'
                msg += f"• {subtitle_name}\n"

        if count > 5:
            msg += f"... and {count - 5} more\n"

        return messagebox.askyesno("Translation Confirmation", msg)

    def _start_translation_async(self, valid_pairs, config):
        """Start translation in separate thread"""

        def run_translation():
            try:
                # Initialize state
                self.state.start(len(valid_pairs))

                # Update UI
                if self.status_callback:
                    self.status_callback("Processing...")
                if self.button_callback:
                    self.button_callback('cancel')

                # Log start
                self.logger("🚀 Starting translation...")
                self.logger(f"📊 Processing {len(valid_pairs)} pairs")
                self.logger("─" * 50)

                # Build full paths for pairs
                full_path_pairs = self._build_full_paths(valid_pairs)

                # Add cancel event to config
                config_dict = config.to_dict()
                config_dict['cancel_event'] = self.cancel_event

                # Run translation using CLI runner
                success = self.cli_runner.run_translation_batch(full_path_pairs, config_dict)

                # Handle results
                if self.cancel_event.is_set():
                    self.state.cancel()
                    final_status = "Cancelled"
                    self.logger("🛑 Translation cancelled")
                elif success:
                    self.state.finish()
                    final_status = "Translation completed successfully"
                    self.logger("✅ Translation completed successfully")
                else:
                    self.state.finish()
                    final_status = "Translation completed with errors"
                    self.logger("⚠️ Translation completed with errors")

                # Update UI in main thread
                if self.status_callback:
                    self.status_callback(final_status)

                # Get summary and call completion callback
                summary = self.state.get_summary()
                if self.completion_callback:
                    self.completion_callback(summary)

            except Exception as e:
                error_msg = f"Error during translation: {e}"
                self.logger(f"❌ {error_msg}")

                self.state.finish()

                if self.status_callback:
                    self.status_callback("Translation error")

                # Create error summary
                summary = self.state.get_summary()
                summary['fatal_error'] = str(e)

                if self.completion_callback:
                    self.completion_callback(summary)

            finally:
                # Always restore translate button
                if self.button_callback:
                    self.button_callback('translate')

                # Clear cancel event
                self.cancel_event.clear()

        # Reset cancel event
        self.cancel_event.clear()

        # Start thread
        self.processing_thread = threading.Thread(target=run_translation, daemon=True)
        self.processing_thread.start()

    def _build_full_paths(self, file_pairs):
        """Convert relative paths to full paths"""
        full_path_pairs = []

        for pair in file_pairs:
            full_pair = {}

            if pair.get('subtitle'):
                full_pair['subtitle'] = pair['folder'] + "/" + pair['subtitle']
            else:
                full_pair['subtitle'] = None

            if pair.get('video'):
                full_pair['video'] = pair['folder'] + "/" + pair['video']
            else:
                full_pair['video'] = None

            full_path_pairs.append(full_pair)

        return full_path_pairs

    def is_running(self):
        """Check if translation is currently running"""
        return self.state.is_running

    def get_state(self):
        """Get current translation state"""
        return self.state

    def get_progress(self):
        """Get current progress information"""
        return {
            'percentage': self.state.get_progress_percentage(),
            'completed': self.state.completed_files,
            'total': self.state.total_files,
            'current_file': self.state.current_file,
            'duration': self.state.get_duration()
        }


class TranslationManager:
    """Higher-level manager that coordinates translation handler with UI"""

    def __init__(self, cli_runner, main_window):
        """
        Initialize translation manager

        Args:
            cli_runner: CLI runner instance
            main_window: Main window instance (for accessing UI elements)
        """
        self.main_window = main_window

        # Create translation handler with callbacks
        self.handler = TranslationHandler(
            cli_runner=cli_runner,
            logger=main_window.log_to_console,
            status_callback=self._update_status,
            button_callback=self._toggle_buttons,
            completion_callback=self._on_translation_complete
        )

    def start_translation(self, selected_pairs, config_dict):
        """
        Start translation with UI integration

        Args:
            selected_pairs: List of selected file pairs from TreeView
            config_dict: Configuration dictionary from UI

        Returns:
            bool: True if translation started successfully
        """
        # Hide dropdown menus when starting
        self._hide_dropdown_menus()

        # Save current configuration
        self.main_window.save_current_config()

        # Start translation
        return self.handler.start_translation(selected_pairs, config_dict)

    def cancel_translation(self):
        """Cancel current translation"""
        self.handler.cancel_translation()

    def is_running(self):
        """Check if translation is running"""
        return self.handler.is_running()

    def _update_status(self, status):
        """Update status bar"""
        if hasattr(self.main_window, 'status_var'):
            self.main_window.status_var.set(status)

    def _toggle_buttons(self, button_type):
        """Toggle between translate and cancel buttons"""
        if button_type == 'cancel':
            self.main_window.show_cancel_button()
        else:
            self.main_window.show_translate_button()

    def _hide_dropdown_menus(self):
        """Hide API and Settings dropdown menus"""
        if hasattr(self.main_window, '_hide_dropdown_menus'):
            self.main_window._hide_dropdown_menus()

    def _on_translation_complete(self, summary):
        """Handle translation completion"""
        duration_str = summary['duration_formatted']

        if summary['was_cancelled']:
            self.main_window.log_to_console("🛑 Translation was cancelled")
        elif summary.get('fatal_error'):
            self.main_window.log_to_console(f"💥 Fatal error: {summary['fatal_error']}")
        else:
            # Log completion summary
            self.main_window.log_to_console("─" * 50)
            self.main_window.log_to_console("🎉 Translation Summary:")
            self.main_window.log_to_console(f"   📊 Total files: {summary['total_files']}")
            self.main_window.log_to_console(f"   ✅ Completed: {summary['completed_files']}")
            self.main_window.log_to_console(f"   ❌ Failed: {summary['failed_files']}")
            self.main_window.log_to_console(f"   ⏱️ Duration: {duration_str}")
            self.main_window.log_to_console(f"   📈 Success rate: {summary['success_rate']:.1f}%")

            # Log errors if any
            if summary['errors']:
                self.main_window.log_to_console("❌ Errors encountered:")
                for error in summary['errors']:
                    self.main_window.log_to_console(f"   • {error['file']}: {error['error']}")