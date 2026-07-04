"""
CLI command runner for executing gst translation commands.
Handles process management and output streaming.
"""
import datetime
import os
import queue
import subprocess
import sys
import threading
import traceback
from pathlib import Path

import srt


class CLIRunner:
    """Handles execution of CLI commands with real-time output"""

    def __init__(self, logger=None, progress_callback=None, pair_status_callback=None):
        self.logger = logger
        # progress_callback(completed, total) - overall batch progress
        self.progress_callback = progress_callback
        # pair_status_callback(pair, status) - status is one of:
        # 'translating', 'done', 'failed', 'cancelled'
        self.pair_status_callback = pair_status_callback
        # Root that must be on PYTHONPATH so `-m gemini_srt_translator`
        # resolves to the vendored copy shipped inside this repo.
        self.gst_root = None
        self.gst_cmd = self._find_gst_command()

    def _notify_progress(self, completed, total):
        """Report overall progress (safe to call from worker thread)"""
        if self.progress_callback:
            try:
                self.progress_callback(completed, total)
            except Exception:
                pass

    def _notify_pair_status(self, pair, status):
        """Report per-pair status (safe to call from worker thread)"""
        if self.pair_status_callback:
            try:
                self.pair_status_callback(pair, status)
            except Exception:
                pass

    def _find_gst_command(self):
        """
        Resolve the command used to invoke gemini-srt-translator.

        Prefer the copy of the package vendored into this repository
        (../../gemini_srt_translator relative to this file) and run it as a
        module via the current interpreter. Fall back to a globally installed
        `gst` executable if the vendored copy is not present.

        Returns a list of command tokens (prefix), or None if nothing is found.
        """
        # Repo root: gst_gui/utils/cli_runner.py -> <root>
        repo_root = Path(__file__).resolve().parents[2]
        vendored_pkg = repo_root / "gemini_srt_translator"
        if (vendored_pkg / "__init__.py").exists():
            self.gst_root = repo_root
            return [sys.executable, '-m', 'gemini_srt_translator']

        # Fallback: globally installed `gst` executable
        try:
            # Try Windows where command
            result = subprocess.run(['where', 'gst'], capture_output=True, text=True, shell=True)
            if result.returncode == 0:
                return ['gst']
        except Exception:
            pass

        try:
            # Check if gst is in PATH (POSIX)
            result = subprocess.run(['which', 'gst'], capture_output=True, text=True)
            if result.returncode == 0:
                return ['gst']
        except Exception:
            pass

        # Check local directory
        local_paths = ['gst', 'gst.exe', './gst', './gst.exe']
        for path in local_paths:
            if Path(path).exists():
                return [str(Path(path).resolve())]

        return None

    def log(self, message):
        """Log a message using the provided logger or print"""
        if self.logger:
            self.logger(message)
        else:
            print(message)

    def is_gst_available(self):
        """Check if gst command is available"""
        return self.gst_cmd is not None

    def run_translation_batch(self, file_pairs, config):
        """
        Run translation for multiple file pairs.

        Args:
            file_pairs (list): List of dicts with 'subtitle' and 'video' keys
            config (dict): Configuration dictionary with API keys, model, etc.

        Returns:
            bool: True if all translations succeeded, False otherwise
        """
        if not self.is_gst_available():
            self.log("ERROR: 'gst' program not found")
            self.log("Check if 'gst' is installed or available in PATH")
            return False

        self.log(f"✅ Found gst: {' '.join(self.gst_cmd)}")
        self.log("─" * 30)

        success_count = 0
        total_count = len(file_pairs)
        cancel_event = config.get('cancel_event')

        self._notify_progress(0, total_count)

        for i, pair in enumerate(file_pairs, 1):
            if cancel_event and cancel_event.is_set():
                self.log(f"🛑 Anulowanie przetwarzania na parze {i}/{total_count}")
                self._notify_pair_status(pair, 'cancelled')
                break

            self.log(f"🔄 Processing pair {i}/{total_count}:")
            self._notify_pair_status(pair, 'translating')

            success = self._run_single_translation(pair, config, i)
            if success:
                success_count += 1
                self._notify_pair_status(pair, 'done')
            elif cancel_event and cancel_event.is_set():
                self._notify_pair_status(pair, 'cancelled')
                break
            else:
                self._notify_pair_status(pair, 'failed')

            self._notify_progress(i, total_count)
            self.log("─" * 30)

        if cancel_event and cancel_event.is_set():
            self.log(f"🛑 Przetwarzanie anulowane!")
            self.log(f"✅ Przetworzone przed anulowaniem: {success_count}/{i - 1}")
            return False
        else:
            self.log(f"🎉 Processing completed!")
            self.log(f"✅ Successful: {success_count}/{total_count}")
            return success_count == total_count

    def _run_single_translation(self, pair, config, pair_number):
        """
        Run translation for a single file pair.

        Args:
            pair (dict): Dictionary with 'subtitle' and 'video' keys
            config (dict): Configuration dictionary
            pair_number (int): Current pair number for logging

        Returns:
            bool: True if successful, False otherwise
        """
        cancel_event = config.get('cancel_event')

        # Check cancellation before starting
        if cancel_event and cancel_event.is_set():
            self.log(f"🛑 Canceling before pair {pair_number}")
            return False

        subtitle_file = pair.get('subtitle')
        video_file = pair.get('video')

        if not subtitle_file:
            self.log(f"No subtitle file for pair {pair_number}")

        self.log(f"   📝 Subtitles: {subtitle_file}")
        if video_file:
            self.log(f"   🎬 Video: {video_file}")

        # Build the list of models to try: primary first, then fallbacks
        # (comma-separated, e.g. "gemini-3.5-flash,gemini-3-flash-preview").
        models_to_try = self._get_models_to_try(config)

        success = False
        used_model = models_to_try[0]

        for attempt, model in enumerate(models_to_try):
            if cancel_event and cancel_event.is_set():
                return False

            attempt_config = dict(config)
            attempt_config['model'] = model

            if attempt > 0:
                self.log(f"🔁 Fallback model {attempt}/{len(models_to_try) - 1}: '{model}'")

            # Build command; on fallback attempts add --resume so a partially
            # translated file continues from the saved progress instead of
            # hanging on the interactive "resume?" prompt.
            cmd = self._build_gst_command(subtitle_file, video_file, attempt_config,
                                          resume=attempt > 0)

            if not cmd:
                self.log(f"❌ Failed to build command for pair {pair_number}")
                return False

            # Execute command with cancellation support
            success = self._execute_command(cmd, pair_number, cancel_event)

            if success:
                used_model = model
                break

            if cancel_event and cancel_event.is_set():
                return False

            if attempt < len(models_to_try) - 1:
                self.log(f"⚠️ Model '{model}' failed (error/overloaded/quota) - trying next fallback model")
            else:
                self.log(f"❌ All models failed for pair {pair_number}: {', '.join(models_to_try)}")

        # Report the actually used model so translator info is accurate
        config = dict(config)
        config['model'] = used_model

        # ADD TRANSLATOR INFO HERE - after successful processing
        if success and not (cancel_event and cancel_event.is_set()):
            try:
                # Get the output file path that was created by gst command
                if subtitle_file:
                    output_file = self._get_output_file_path(subtitle_file, config)
                else:
                    output_file = self._get_output_file_path(video_file, config)

                # Check if we should add translator info
                if config.get('add_translator_info', True):
                    # Generate translator info text
                    model_name = config.get('model', 'Unknown Model')

                    translator_info = f"# Translated by {model_name} #"

                    # Add translator info to the processed subtitle file
                    self.add_translator_info(output_file, translator_info)

            except Exception as e:
                self.log(f"⚠️ Could not add translator info: {e}")
                # Don't fail the whole process for this

        return success

    def _get_output_file_path(self, subtitle_file, config):
        """Get the output file path that would be created by gst command"""
        language_code = config.get('language_code', 'pl')
        subtitle_path = Path(subtitle_file)

        # Clean the original filename from language codes (same logic as in _build_gst_command)
        cleaned_stem = self._clean_filename_from_language_codes(subtitle_path.stem)
        output_filename = f"{cleaned_stem}.{language_code}.srt"
        output_path = subtitle_path.parent / output_filename

        return output_path

    def _get_language_code(self, language):
        """Convert language name to short code"""
        language_map = {
            'polish': 'pl',
            'english': 'en',
            'spanish': 'es',
            'french': 'fr',
            'german': 'de',
            'italian': 'it',
            'portuguese': 'pt',
            'russian': 'ru',
            'japanese': 'ja',
            'korean': 'ko',
            'chinese': 'zh',
            'arabic': 'ar',
            'hindi': 'hi',
            'dutch': 'nl',
            'swedish': 'sv',
            'norwegian': 'no',
            'danish': 'da',
            'finnish': 'fi',
            'turkish': 'tr',
            'hebrew': 'he',
            'greek': 'el',
            'czech': 'cs',
            'hungarian': 'hu',
            'romanian': 'ro',
            'bulgarian': 'bg',
            'croatian': 'hr',
            'slovak': 'sk',
            'slovenian': 'sl',
            'estonian': 'et',
            'latvian': 'lv',
            'lithuanian': 'lt'
        }

        # Try to match language (case insensitive)
        language_lower = language.lower().strip()

        # Direct match
        if language_lower in language_map:
            return language_map[language_lower]

        # If already a short code, return as is
        if len(language_lower) == 2 and language_lower.isalpha():
            return language_lower

        # Try to find partial matches
        for lang_name, code in language_map.items():
            if lang_name.startswith(language_lower) or language_lower.startswith(lang_name):
                return code

        # Default: use first 2 characters
        return language_lower[:2] if language_lower else 'en'

    def _clean_filename_from_language_codes(self, filename_stem):
        """Remove common language codes from filename stem"""
        # Common language codes to remove (case insensitive)
        language_codes = {
            'en', 'eng', 'english',
            'it', 'ita', 'italian', 'italiano',
            'pl', 'pol', 'polish', 'polski',
            'es', 'esp', 'spanish', 'espanol',
            'fr', 'fra', 'french', 'francais',
            'de', 'ger', 'german', 'deutsch',
            'pt', 'por', 'portuguese', 'portugues',
            'ru', 'rus', 'russian',
            'ja', 'jpn', 'japanese',
            'ko', 'kor', 'korean',
            'zh', 'chi', 'chinese',
            'ar', 'ara', 'arabic',
            'hi', 'hin', 'hindi',
            'nl', 'dut', 'dutch',
            'sv', 'swe', 'swedish',
            'no', 'nor', 'norwegian',
            'da', 'dan', 'danish',
            'fi', 'fin', 'finnish',
            'tr', 'tur', 'turkish',
            'he', 'heb', 'hebrew',
            'el', 'gre', 'greek',
            'cs', 'cze', 'czech',
            'hu', 'hun', 'hungarian',
            'ro', 'rum', 'romanian',
            'bg', 'bul', 'bulgarian',
            'hr', 'cro', 'croatian',
            'sk', 'slo', 'slovak',
            'sl', 'slv', 'slovenian',
            'et', 'est', 'estonian',
            'lv', 'lat', 'latvian',
            'lt', 'lit', 'lithuanian'
        }

        import re
        result = filename_stem

        # Remove language codes while preserving spaces and case
        for lang_code in language_codes:
            # Remove language codes with various separators but preserve spaces
            patterns = [
                rf'\.{re.escape(lang_code)}(?=\.|$)',  # .lang at end or before dot
                rf'[\-_]{re.escape(lang_code)}(?=[\-_\.]|$)',  # -lang, _lang (not spaces)
                rf'^{re.escape(lang_code)}[\-_\.]',  # lang at start with separator
            ]

            for pattern in patterns:
                result = re.sub(pattern, '', result, flags=re.IGNORECASE)

        # Clean up multiple dots, hyphens, underscores (but preserve spaces)
        result = re.sub(r'\.+', '.', result)  # Multiple dots to single dot
        result = re.sub(r'\-+', '-', result)  # Multiple hyphens to single hyphen
        result = re.sub(r'_+', '_', result)  # Multiple underscores to single underscore
        result = result.strip('.-_ ')  # Remove separators from start/end

        return result if result else filename_stem

    def _get_models_to_try(self, config):
        """
        Return the ordered list of models to try: primary model first,
        then fallback models parsed from a comma-separated string
        (e.g. "gemini-3.5-flash,gemini-3-flash-preview").
        """
        primary_model = config.get('model', 'gemini-2.5-flash')
        fallback_raw = config.get('fallback_models', '') or ''
        fallbacks = [m.strip() for m in str(fallback_raw).split(',') if m.strip()]

        models = [primary_model]
        for model in fallbacks:
            if model not in models:
                models.append(model)

        if len(models) > 1:
            self.log(f"   🛟 Fallback models: {', '.join(models[1:])}")

        return models

    def _build_gst_command(self, subtitle_file, video_file, config, resume=False):
        """Build the gst command based on configuration"""
        cmd = [*self.gst_cmd, 'translate']
        # Vendored copy: never try to pip-upgrade the package at runtime.
        cmd.append('--skip-upgrade')
        if resume:
            # Fallback retry: auto-resume from saved progress without the
            # interactive prompt (which would hang the GUI subprocess).
            cmd.append('--resume')
            self.log(f"   ⏯️ Resume: continuing from saved progress")
        if subtitle_file:
            subtitle_path = Path(subtitle_file)
            if not subtitle_path.name.endswith(("No match", "None")):
                cmd.extend(['-i', str(subtitle_file)])
            else:
                subtitle_file = None

        # Add output filename with language code (removing old language codes)
        language = config.get('language', 'Polish')
        language_code = config.get('language_code', 'pl')  # Use code from GUI instead of converting

        if subtitle_file:
            subtitle_path = Path(subtitle_file)
        else:
            subtitle_path = Path(video_file)

        # Clean the original filename from language codes
        cleaned_stem = self._clean_filename_from_language_codes(subtitle_path.stem)
        output_filename = f"{cleaned_stem}.{language_code}.srt"
        output_path = subtitle_path.parent / output_filename

        cmd.extend(['-o', str(output_path)])
        self.log(f"   📝 Output: {output_filename}")
        if cleaned_stem != subtitle_path.stem:
            self.log(f"   🧹 Cleaned: '{subtitle_path.stem}' → '{cleaned_stem}'")
        self.log(f"   🏷️ Language code: {language_code}")

        # Add language
        cmd.extend(['-l', language])
        self.log(f"   🌐 Language: {language}")

        # Add Gemini API key if provided
        gemini_api_key = config.get('gemini_api_key', '').strip()
        if gemini_api_key:
            cmd.extend(['-k', gemini_api_key])
            self.log(f"   🔑 Using Gemini API key")
        else:
            self.log(f"   ⚠️ No Gemini API key provided")

        gemini_api_key2 = config.get('gemini_api_key2', '').strip()
        if gemini_api_key2:
            cmd.extend(['-k2', gemini_api_key2])
            self.log(f"   🔑 Using second Gemini API key (fallback)")

        # Add model
        model = config.get('model', 'gemini-2.5-flash')
        batch_size = config.get('batch_size', 300)
        cmd.extend(['--model', model])
        self.log(f"   🤖 Model: {model}")

        cmd.extend(['--batch-size', str(batch_size)])

        # Add batch size for Gemini 2.0 models
        if '2.0' in model:
            cmd.extend(['--batch-size', '100'])
            self.log(f"   📦 Batch size: 100 (Gemini 2.0 optimization)")

        # Add description if overview is available
        overview = config.get('overview', '').strip()
        movie_title = config.get('movie_title', '').strip()
        is_tv_series = config.get('is_tv_series', False)

        additional_tranlation_info = ""
        translation_type = config.get('translation_type', 'Default')
        if translation_type == 'Concise translation':
            self.log(f"   📝 Translation type: Concise")
            additional_tranlation_info = """ IMPORTANT:
            1. Keep sentences as short and clear as possible while preserving the full sense.
            2. Focus on meaning, not word-for-word translation.
            3. Remove filler words, repetitions, and unnecessary expressions (e.g., "uhm," "well," "you know," etc.).
            4. Maintain a natural tone and make the translation easy to read.
            5. If a sentence can be expressed more briefly without losing meaning, shorten it.
            """
        else:
            self.log(f"   📝 Translation type: Default")

        if overview and movie_title:
            # Format description with content type and title
            content_type = "TV series" if is_tv_series else "movie"
            translation_prompt = """When translating text, follow these formatting rules:
            1. Line length: Keep lines to 40-50 characters when possible, breaking at natural phrase boundaries or punctuation marks.
            2. Dialogue formatting: When text contains dialogue between multiple speakers, format each speaker's lines separately, starting each with a dash (-).
            3. Spacing: Ensure proper spacing between words and after punctuation marks.
            4. Sentence breaks: If a sentence continues on the next line, maintain proper spacing between the end of one line and the beginning of the next.
            """

            description = f"{translation_prompt}{additional_tranlation_info} It is a {content_type} called {movie_title}. Description: {overview}"
            cmd.extend(['--description', description])
            self.log(f"   📄 Description: It is a {content_type} called {movie_title}...")
        elif overview:
            # Fallback to just overview if no title available
            cmd.extend(['--description', overview])
            self.log(f"   📄 Description: {overview[:50]}{'...' if len(overview) > 50 else ''}")
        elif movie_title:
            # Just title if no overview available
            content_type = "TV series" if is_tv_series else "movie"
            description = f"It is a {content_type} called {movie_title}."
            cmd.extend(['--description', description])
            self.log(f"   📄 Description: It is a {content_type} called {movie_title}.")

        # Add video file and audio extraction if configured
        extract_audio = config.get('extract_audio', False)
        if video_file:
            video_path = Path(video_file)
            if not video_path.name.endswith(("No match", "None")):
                if  extract_audio:
                    cmd.extend(['-v', str(video_file)])
                    cmd.append('--extract-audio')
                    self.log(f"   🎵 Extract audio: enabled")
                elif not extract_audio:
                    self.log(f"   🎬 Video file available but extract audio disabled")
                    self.log(f"   🎬 Trying to extract subtitles")
                    cmd.extend(['-v', str(video_file)])
                elif not video_file:
                    self.log(f"   ℹ️ No video file - processing subtitle only")

        # Add include timestamps flag if configured.
        # NOTE: the vendored gemini_srt_translator CLI does not expose an
        # --include-timestamps option, so we must not pass it (argparse would
        # reject the whole command). Warn instead of silently failing.
        include_timestamps = config.get('include_timestamps', False)
        if include_timestamps:
            self.log(f"   ⚠️ 'Include timestamps' is not supported by this version - ignoring")

        return cmd

    def _execute_command(self, cmd, pair_number, cancel_event=None):
        """Execute a command and stream its output"""
        try:
            self.log(f"Executing: {' '.join(cmd)}")

            # Set environment variables for the subprocess
            env = os.environ.copy()
            env['PYTHONIOENCODING'] = 'utf-8'
            env['PYTHONUTF8'] = '1'
            # Remove GOOGLE_API_KEY from env: gst CLI treats it as a Vertex AI
            # Enterprise cloud_api_key (requiring OAuth2), which conflicts with
            # regular Gemini API key auth passed via -k flag.
            env.pop('GOOGLE_API_KEY', None)
            # Ensure the vendored gemini_srt_translator package (in the repo
            # root) takes precedence when running `python -m gemini_srt_translator`.
            if self.gst_root:
                existing_pp = env.get('PYTHONPATH', '')
                env['PYTHONPATH'] = (
                    str(self.gst_root) + os.pathsep + existing_pp
                    if existing_pp
                    else str(self.gst_root)
                )

            # Start process with explicit encoding
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1,
                universal_newlines=True,
                env=env
            )

            # Read stdout in a background thread so readline() doesn't block
            # the cancellation check in the main loop.
            output_queue = queue.Queue()

            def _reader():
                try:
                    for line in process.stdout:
                        output_queue.put(line)
                finally:
                    output_queue.put(None)  # sentinel

            reader_thread = threading.Thread(target=_reader, daemon=True)
            reader_thread.start()

            while True:
                # Check for cancellation first (responsive even during API waits)
                if cancel_event and cancel_event.is_set():
                    self.log(f"🛑 Canceling process for pair {pair_number}")
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self.log(f"⚠️ Force killing process for pair {pair_number}")
                        process.kill()
                        process.wait()
                    reader_thread.join(timeout=2)
                    return False

                try:
                    line = output_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                if line is None:  # sentinel - stdout closed
                    break

                output_line = line.rstrip()
                if output_line:
                    self.log(f"   {output_line}")

            # Wait for completion (with timeout for cancellation)
            try:
                return_code = process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                if cancel_event and cancel_event.is_set():
                    process.terminate()
                    return_code = process.wait()
                else:
                    return_code = process.wait()

            if return_code == 0:
                self.log(f"✅ Pair {pair_number} processed successfully")
                return True
            else:
                self.log(f"❌ Pair {pair_number} finished with error (code: {return_code})")
                return False

        except Exception as e:
            print(traceback.format_exc())
            self.log(f"❌ Error executing command for pair {pair_number}: {e}")
            return False

    def safe_subprocess_run(*args, **kwargs):
        """Wrapper for subprocess calls with proper encoding"""
        if 'encoding' not in kwargs:
            kwargs['encoding'] = 'utf-8'
        if 'errors' not in kwargs:
            kwargs['errors'] = 'replace'
        return subprocess.run(*args, **kwargs)

    def run_legacy_command(self, path, is_file=True):
        """
        Run the legacy main.py command (for backward compatibility).

        Args:
            path (str): Path to file or directory
            is_file (bool): True if path is a file, False if directory

        Returns:
            bool: True if successful, False otherwise
        """
        # Check if main.py exists
        main_py_path = Path("main.py")
        if not main_py_path.exists():
            self.log("ERROR: main.py not found in current directory")
            return False

        # Prepare command
        if is_file:
            cmd = [sys.executable, "main.py", "-f", path]
        else:
            cmd = [sys.executable, "main.py", "-d", path]

        return self._execute_legacy_command(cmd)

    def _execute_legacy_command(self, cmd):
        """Execute legacy main.py command"""
        try:
            self.log(f"Executing: {' '.join(cmd)}")

            # Run process
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )

            # Read output in real-time
            for line in process.stdout:
                self.log(line.rstrip())

            # Wait for completion
            return_code = process.wait()

            if return_code == 0:
                self.log("✅ Command executed successfully")
                return True
            else:
                self.log(f"❌ Command finished with error code: {return_code}")
                return False

        except Exception as e:
            self.log(f"Error during execution: {e}")
            return False

    def add_translator_info(self, dest_srt_file, info):
        """Add translator information as the first subtitle entry"""
        try:
            if not Path(dest_srt_file).exists():
                if self.logger:
                    self.logger(f"❌ SRT file not found: {dest_srt_file}")
                return

            # Load the SRT content with explicit UTF-8 encoding
            with open(dest_srt_file, "r", encoding="utf-8", errors='ignore') as f:
                srt_content = f.read()

            # Parse subtitles
            subtitles = list(srt.parse(srt_content))

            if subtitles:
                first_start = subtitles[0].start
            else:
                # If no subtitles exist, set an arbitrary end time for the info subtitle
                first_start = datetime.timedelta(seconds=5)

            # Determine the end time as the minimum of first_start and 5s
            end_time = min(first_start, datetime.timedelta(seconds=5))

            # If end time is exactly 5s, start at 1s. Otherwise, start at 0s.
            if end_time == datetime.timedelta(seconds=5):
                start_time = datetime.timedelta(seconds=1)
            else:
                start_time = datetime.timedelta(seconds=0)

            # Add the info subtitle
            new_sub = srt.Subtitle(
                index=1,  # temporary, will be reindexed
                start=start_time,
                end=end_time,
                content=info
            )

            subtitles.insert(0, new_sub)

            # Re-index and sort
            subtitles = list(srt.sort_and_reindex(subtitles))

            # Write back to file with explicit UTF-8 encoding
            with open(dest_srt_file, "w", encoding="utf-8", errors='replace') as f:
                f.write(srt.compose(subtitles))

            if self.logger:
                self.logger(f"✅ Added translator info to: {Path(dest_srt_file).name}")

        except Exception as e:
            if self.logger:
                self.logger(f"❌ Error adding translator info: {e}")
                # Log more details about the encoding error
                if 'charmap' in str(e):
                    self.logger("💡 Hint: This appears to be a Unicode encoding issue on Windows")
                    self.logger("💡 Try setting PYTHONIOENCODING=utf-8 environment variable")