"""
Main window class for the CLI Wrapper GUI application.
Coordinates between UI components, file processing, and configuration.
"""

import tkinter as tk
from io import BytesIO
from tkinter import ttk, messagebox, scrolledtext
import threading
from pathlib import Path
import os

import requests
from PIL import ImageTk, Image

# Import our custom modules
try:
    from .config_manager import ConfigManager
except ImportError:
    from gst_gui.gui.config_manager import ConfigManager

try:
    from gst_gui.utils.file_utils import (
        extract_movie_info,
        format_movie_info,
        classify_file_type,
        scan_folder_for_files
    )
except ImportError:
    import sys
    import os

    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
    from gst_gui.utils.file_utils import (
        extract_movie_info,
        format_movie_info,
        classify_file_type,
        scan_folder_for_files
    )

try:
    from gst_gui.utils.cli_runner import CLIRunner
except ImportError:
    from gst_gui.utils.cli_runner import CLIRunner


class DragDropGUI:
    """Main GUI class that coordinates all components"""

    def __init__(self, root):
        self.root = root
        self.root.title("Gemini SRT Translator")
        self.processing_thread = None
        self.cancel_event = threading.Event()
        self.image_label = None

        icon_path = (Path(__file__).resolve().parent / "../../gst_gui/assets/icon.png").resolve()
        try:
            icon = tk.PhotoImage(file=str(icon_path))
            self.root.iconphoto(False, icon)
        except Exception as e:
            print(f"Failed to load icon: {e}")

        window_width = 1000
        window_height = 800

        self.root.geometry(f"{window_width}x{window_height}")
        self.root.configure(bg='#f0f0f0')

        # Get screen dimensions
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()

        # Calculate center position
        center_x = int(screen_width / 2 - window_width / 2)
        center_y = int(screen_height / 2 - window_height / 2)

        # Set geometry with center position
        self.root.geometry(f'{window_width}x{window_height}+{center_x}+{center_y}')

        # Initialize configuration manager
        self.config_manager = ConfigManager()

        # Initialize CLI runner with logger
        self.cli_runner = CLIRunner(logger=self.log_to_console)

        # Store current folder path for building full file paths
        self.current_folder_path = None

        # Set window to front
        self.root.lift()
        self.root.attributes('-topmost', True)
        self.root.after_idle(lambda: self.root.attributes('-topmost', False))
        self.root.focus_force()

        # Bind window close event
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Initialize UI
        self._setup_ui()

        # Setup drag & drop
        self.setup_drag_drop()

        # Additional settings for macOS to ensure window is in front
        self.root.after(100, self.ensure_front)

        # Log configuration after UI is ready
        self.root.after(200, self.log_config_loaded)

    def _setup_ui(self):
        """Setup the user interface"""
        # Main frame
        main_frame = ttk.Frame(self.root, padding="20")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # Grid configuration
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(2, weight=1)  # TreeView row
        main_frame.rowconfigure(4, weight=1)  # Console row

        # Create UI components
        self._create_drop_area(main_frame)
        self._create_treeview(main_frame)
        self._create_console(main_frame)
        self._create_config_sections(main_frame)
        self._create_action_buttons(main_frame)
        self._create_status_bar(main_frame)

    def _create_drop_area(self, parent):
        """Create the drag & drop area"""
        # Drag & drop area
        self.drop_frame = tk.Frame(parent, bg='#e8e8e8', relief='ridge', bd=2)
        self.drop_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 20))
        self.drop_frame.columnconfigure(0, weight=1)

        # Label in drop area
        self.drop_label = tk.Label(self.drop_frame,
                                   text="📁 Drag files or folders here\n\nOr click to browse",
                                   bg='#e8e8e8', fg='#666666',
                                   font=('Arial', 10), pady=40)
        self.drop_label.grid(row=0, column=0, sticky=(tk.W, tk.E))

        # Bind click to file selection
        self.drop_label.bind("<Button-1>", self.browse_file)
        self.drop_frame.bind("<Button-1>", self.browse_file)

    def _create_treeview(self, parent):
        """Create the TreeView for file pairs"""
        # TreeView section
        treeview_label = ttk.Label(parent, text="Found files:")
        treeview_label.grid(row=1, column=0, sticky=tk.W, pady=(0, 5))

        # Frame for TreeView with scrollbars
        tree_frame = ttk.Frame(parent)
        tree_frame.grid(row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 20))
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        # TreeView widget
        self.tree = ttk.Treeview(tree_frame,
                                 columns=('SubtitleFile', 'VideoFile', 'Title', 'Year', 'FolderPath', 'Status'),
                                 show='tree headings')

        # Column configuration
        self.tree.heading('#0', text='☑️ Select')
        self.tree.heading('SubtitleFile', text='📝 Subtitle File')
        self.tree.heading('VideoFile', text='🎬 Video File')
        self.tree.heading('Title', text='🎭 Title')
        self.tree.heading('Year', text='📅 Year')
        self.tree.heading('FolderPath', text='📁 Folder')
        self.tree.heading('Status', text='📊 Status')

        # Column width
        self.tree.column('#0', width=80, minwidth=60)
        self.tree.column('SubtitleFile', width=160, minwidth=120)
        self.tree.column('VideoFile', width=160, minwidth=120)
        self.tree.column('Title', width=180, minwidth=150)
        self.tree.column('Year', width=60, minwidth=50)
        self.tree.column('FolderPath', width=130, minwidth=100)
        self.tree.column('Status', width=120, minwidth=100)

        # Scrollbars
        tree_scrolly = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        tree_scrollx = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=tree_scrolly.set, xscrollcommand=tree_scrollx.set)

        # Grid layout for TreeView and scrollbars
        self.tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        tree_scrolly.grid(row=0, column=1, sticky=(tk.N, tk.S))
        tree_scrollx.grid(row=1, column=0, sticky=(tk.W, tk.E))

        # Configure TreeView tags
        self._configure_treeview_tags()

        # Bind events
        self.tree.bind('<Double-1>', self.toggle_checkbox)
        self.tree.bind('<Button-1>', self.on_tree_click)

    def _configure_treeview_tags(self):
        """Configure TreeView tags for different statuses"""
        self.tree.tag_configure('matched', background='#2d5a2d', foreground='#ffffff')
        self.tree.tag_configure('subtitle_only', background='#5a5a2d', foreground='#ffffff')
        self.tree.tag_configure('video_only', background='#2d2d5a', foreground='#ffffff')
        self.tree.tag_configure('no_match', background='#5a2d2d', foreground='#ffffff')
        self.tree.tag_configure('unchecked', background='#404040', foreground='#888888')

    def _create_console(self, parent):
        """Create the console output area"""
        console_label = ttk.Label(parent, text="Console output:")
        console_label.grid(row=3, column=0, sticky=tk.W, pady=(0, 5))

        self.console_text = scrolledtext.ScrolledText(parent, height=15, width=70)
        self.console_text.grid(row=4, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

    def _create_config_sections(self, parent):
        """Create expandable configuration sections"""
        # Configuration Sections Container
        config_container = ttk.Frame(parent)
        config_container.grid(row=5, column=0, sticky=(tk.W, tk.E), pady=(20, 10))
        config_container.columnconfigure(0, weight=1)

        # Headers frame for both API and Settings buttons
        headers_frame = ttk.Frame(config_container)
        headers_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 5))

        # Get UI config
        ui_config = self.config_manager.get_ui_config()

        # API Configuration Section - Expandable
        self.api_expanded = tk.BooleanVar(value=ui_config['api_expanded'])
        self.expand_api_button = tk.Button(headers_frame, text="▶ Show API options",
                                           bg='#e0e0e0', fg='black', font=('Arial', 10),
                                           relief='flat', bd=0, pady=5,
                                           command=self.toggle_api_section)
        self.expand_api_button.pack(side=tk.LEFT, padx=(0, 10))

        # Settings Section - Expandable
        self.settings_expanded = tk.BooleanVar(value=ui_config['settings_expanded'])
        self.expand_settings_button = tk.Button(headers_frame, text="▶ Settings",
                                                bg='#e0e0e0', fg='black', font=('Arial', 10),
                                                relief='flat', bd=0, pady=5,
                                                command=self.toggle_settings_section)
        self.expand_settings_button.pack(side=tk.LEFT)

        # Create the actual configuration forms
        self._create_api_options(config_container)
        self._create_settings_options(config_container)

        # Set initial states
        if self.api_expanded.get():
            self.toggle_api_section()
        if self.settings_expanded.get():
            self.toggle_settings_section()

    def _create_api_options(self, parent):
        """Create API configuration options"""
        # API options frame (initially hidden)
        self.api_options_frame = ttk.Frame(parent)

        api_config = self.config_manager.get_api_config()

        # Gemini API Key
        ttk.Label(self.api_options_frame, text="Gemini API Key:").grid(row=0, column=0, sticky=tk.W, pady=(10, 5))
        self.gemini_api_key = tk.StringVar(value=api_config['gemini_api_key'])
        gemini_entry = ttk.Entry(self.api_options_frame, textvariable=self.gemini_api_key, show="*", width=50)
        gemini_entry.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=(10, 0), pady=(10, 5))

        # Model
        ttk.Label(self.api_options_frame, text="Model:").grid(row=0, column=2, sticky=tk.W, padx=(20, 0), pady=(10, 5))
        self.model = tk.StringVar(value=api_config['model'])
        model_combo = ttk.Combobox(self.api_options_frame, textvariable=self.model, width=25,
                                   values=["gemini-2.5-flash-preview-05-20", "gemini-2.0-flash",
                                           "gemini-2.5-pro-preview-06-05"])
        model_combo.grid(row=0, column=3, sticky=tk.W, padx=(10, 0), pady=(10, 5))

        # TMDB API Key
        ttk.Label(self.api_options_frame, text="TMDB API Key (optional):").grid(row=1, column=0, sticky=tk.W,
                                                                                pady=(5, 5))
        self.tmdb_api_key = tk.StringVar(value=api_config['tmdb_api_key'])
        tmdb_entry = ttk.Entry(self.api_options_frame, textvariable=self.tmdb_api_key, show="*", width=50)
        tmdb_entry.grid(row=1, column=1, sticky=(tk.W, tk.E), padx=(10, 0), pady=(5, 5))

        # Configure column weights
        self.api_options_frame.columnconfigure(1, weight=1)

    def _create_settings_options(self, parent):
        """Create general settings options"""
        # Settings options frame (initially hidden)
        self.settings_options_frame = ttk.Frame(parent)

        processing_config = self.config_manager.get_processing_config()

        # Language setting
        ttk.Label(self.settings_options_frame, text="Language:").grid(row=0, column=0, sticky=tk.W, pady=(10, 5))
        self.language = tk.StringVar(value=processing_config['language'])
        language_entry = ttk.Entry(self.settings_options_frame, textvariable=self.language, width=20)
        language_entry.grid(row=0, column=1, sticky=tk.W, padx=(10, 0), pady=(10, 5))

        # Language code setting
        ttk.Label(self.settings_options_frame, text="Code:").grid(row=0, column=2, sticky=tk.W, padx=(20, 0),
                                                                  pady=(10, 5))
        self.language_code = tk.StringVar(value=processing_config.get('language_code', 'pl'))
        language_code_entry = ttk.Entry(self.settings_options_frame, textvariable=self.language_code, width=5)
        language_code_entry.grid(row=0, column=3, sticky=tk.W, padx=(10, 0), pady=(10, 5))

        # Extract audio checkbox
        self.extract_audio = tk.BooleanVar(value=processing_config['extract_audio'])
        extract_audio_check = ttk.Checkbutton(self.settings_options_frame, text="Extract audio",
                                              variable=self.extract_audio)
        extract_audio_check.grid(row=0, column=4, sticky=tk.W, padx=(20, 0), pady=(10, 5))

        # TMDB ID section
        ttk.Label(self.settings_options_frame, text="TMDB ID:").grid(row=1, column=0, sticky=tk.W, pady=(10, 5))
        self.tmdb_id = tk.StringVar(value=self.config_manager.get('tmdb_id', ''))
        tmdb_id_entry = ttk.Entry(self.settings_options_frame, textvariable=self.tmdb_id, width=15)
        tmdb_id_entry.grid(row=1, column=1, sticky=tk.W, padx=(10, 0), pady=(10, 5))

        # TV Series checkbox
        self.is_tv_series = tk.BooleanVar(value=processing_config.get('is_tv_series', False))
        tv_series_check = ttk.Checkbutton(self.settings_options_frame, text="TV Series",
                                          variable=self.is_tv_series)
        tv_series_check.grid(row=1, column=2, sticky=tk.W, padx=(10, 0), pady=(10, 5))

        self.add_translator_info = tk.BooleanVar(value=processing_config.get('add_translator_info', True))
        add_translator_info_check = ttk.Checkbutton(self.settings_options_frame, text="Add translator info",
                                          variable=self.add_translator_info)
        add_translator_info_check.grid(row=3, column=4, sticky=tk.W, padx=(10, 0), pady=(10, 5))

        # Fetch TMDB info button (using TMDB ID)
        fetch_tmdb_button = tk.Button(self.settings_options_frame, text="🎬 Fetch",
                                      bg='#d0e0ff', fg='black', font=('Arial', 9),
                                      relief='raised', bd=1, pady=3,
                                      command=self.fetch_tmdb_info)
        fetch_tmdb_button.grid(row=1, column=3, sticky=tk.W, padx=(10, 0), pady=(10, 5))

        # TMDB Overview section
        ttk.Label(self.settings_options_frame, text="Overview:").grid(row=2, column=0, sticky=tk.W, pady=(10, 5))
        self.overview = tk.StringVar(value='')
        overview_entry = ttk.Entry(self.settings_options_frame, textvariable=self.overview, width=60)
        overview_entry.grid(row=2, column=1, columnspan=4, sticky=(tk.W, tk.E), padx=(10, 0), pady=(10, 5))

        # Auto-fetch TMDB checkbox
        self.auto_fetch_tmdb = tk.BooleanVar(value=processing_config['auto_fetch_tmdb'])
        auto_fetch_check = ttk.Checkbutton(self.settings_options_frame, text="Auto-fetch TMDB ID when loading files",
                                           variable=self.auto_fetch_tmdb)
        auto_fetch_check.grid(row=3, column=0, columnspan=3, sticky=tk.W, pady=(5, 10))
        self.setup_image_display()

    def setup_image_display(self):
        ttk.Label(self.settings_options_frame, text="Poster:").grid(row=4, column=0, sticky=tk.W, pady=(10, 5))

        # Create a label to hold the image
        self.image_label = ttk.Label(self.settings_options_frame)
        self.image_label.grid(row=4, column=1, sticky=tk.W, padx=(10, 0), pady=(10, 5))

    # Function to load and display image
    def load_image(self, url, width=100, height=150):  # 100x150 is more typical for movie posters
        try:
            response = requests.get(url)
            response.raise_for_status()

            # Open image from bytes
            image = Image.open(BytesIO(response.content))

            # Resize image
            image = image.resize((width, height), Image.Resampling.LANCZOS)

            # Convert to PhotoImage
            photo = ImageTk.PhotoImage(image)

            # Update the label
            self.image_label.configure(image=photo)
            self.image_label.image = photo

        except Exception as e:
            print(f"Error loading image: {e}")
            self.image_label.configure(text="Image not available")

        # Configure column weights
        self.settings_options_frame.columnconfigure(1, weight=1)

    def _create_action_buttons(self, parent):
        """Create action buttons"""
        # Ramka dla przycisków
        buttons_frame = ttk.Frame(parent)
        buttons_frame.grid(row=6, column=0, pady=(10, 10), sticky=(tk.W, tk.E))
        buttons_frame.columnconfigure(0, weight=1)
        buttons_frame.columnconfigure(1, weight=1)

        # Translate Button
        self.translate_button = tk.Button(buttons_frame, text="🌐 TRANSLATE",
                                          bg='#f0f0f0', fg='black', font=('Arial', 12, 'bold'),
                                          relief='raised', bd=3, pady=10,
                                          activebackground='#e0e0e0', activeforeground='black',
                                          command=self.start_translation)
        self.translate_button.grid(row=0, column=0, padx=(0, 5), sticky=(tk.W, tk.E))

        # Cancel Button (initially hidden)
        self.cancel_button = tk.Button(buttons_frame, text="❌ CANCEL",
                                       bg='#ffcccc', fg='black', font=('Arial', 12, 'bold'),
                                       relief='raised', bd=3, pady=10,
                                       activebackground='#ffaaaa', activeforeground='black',
                                       command=self.cancel_translation)

    def _create_status_bar(self, parent):
        """Create status bar"""
        self.status_var = tk.StringVar(value="Ready")
        status_bar = ttk.Label(parent, textvariable=self.status_var,
                               relief=tk.SUNKEN, anchor=tk.W)
        status_bar.grid(row=7, column=0, sticky=(tk.W, tk.E), pady=(10, 0))

    def toggle_api_section(self):
        """Toggle the visibility of API configuration section"""
        if self.api_expanded.get():
            # Hide API options
            self.api_options_frame.grid_forget()
            self.expand_api_button.config(text="▶ Show API options")
            self.api_expanded.set(False)
        else:
            # Show API options
            self.api_options_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(5, 0))
            self.expand_api_button.config(text="▼ Hide API options")
            self.api_expanded.set(True)

    def toggle_settings_section(self):
        """Toggle the visibility of Settings section"""
        if self.settings_expanded.get():
            # Hide Settings options
            self.settings_options_frame.grid_forget()
            self.expand_settings_button.config(text="▶ Settings")
            self.settings_expanded.set(False)
        else:
            # Show Settings options
            self.settings_options_frame.grid(row=2, column=0, sticky=(tk.W, tk.E), pady=(5, 0))
            self.expand_settings_button.config(text="▼ Settings")
            self.settings_expanded.set(True)

    def show_cancel_button(self):
        """Show cancel button and hide translate button"""
        self.translate_button.grid_forget()
        self.cancel_button.grid(row=0, column=0, columnspan=2, sticky=(tk.W, tk.E))

    def show_translate_button(self):
        """Show translate button and hide cancel button"""
        self.cancel_button.grid_forget()
        self.translate_button.grid(row=0, column=0, columnspan=2, sticky=(tk.W, tk.E))

    def cancel_translation(self):
        """Cancel the current translation process"""
        if self.processing_thread and self.processing_thread.is_alive():
            self.log_to_console("🛑 Cancelling processing...")
            self.cancel_event.set()

            # Show cancellation status
            self.status_var.set("Cancelling...")

            # Wait for thread to finish (max 5 seconds)
            self.processing_thread.join(timeout=5.0)

            if self.processing_thread.is_alive():
                self.log_to_console("⚠️ Force terminating process...")
                # If thread is still running, mark as terminated

            self.log_to_console("✅ Processing has been cancelled")
            self.status_var.set("Cancelled")

            # Restore translate button
            self.show_translate_button()
        else:
            self.log_to_console("ℹ️ No active processing to cancel")

    def on_closing(self):
        """Handle window closing event"""
        if self.processing_thread and self.processing_thread.is_alive():
            if messagebox.askyesno("Cancel processing",
                                   "Processing subtitles...\n"
                                   "Do you want to stop?"):
                self.log_to_console("🛑 Cancelling processing...")
                self.cancel_event.set()

                self.processing_thread.join(timeout=3.0)

                if self.processing_thread.is_alive():
                    self.log_to_console("⚠️ Force close...")
            else:
                return

        self.save_current_config()
        self.log_to_console("💾 Configuration saved")
        self.root.after(100, self.root.destroy)

    def save_current_config(self):
        """Save current configuration to config manager"""
        config_updates = {
            'gemini_api_key': self.gemini_api_key.get() if hasattr(self, 'gemini_api_key') else '',
            'model': self.model.get() if hasattr(self, 'model') else 'gemini-2.0-flash',
            'tmdb_api_key': self.tmdb_api_key.get() if hasattr(self, 'tmdb_api_key') else '',
            'tmdb_id': self.tmdb_id.get() if hasattr(self, 'tmdb_id') else '',
            'api_expanded': self.api_expanded.get() if hasattr(self, 'api_expanded') else False,
            'settings_expanded': self.settings_expanded.get() if hasattr(self, 'settings_expanded') else False,
            'language': self.language.get() if hasattr(self, 'language') else 'Polish',
            'language_code': self.language_code.get() if hasattr(self, 'language_code') else 'pl',
            'extract_audio': self.extract_audio.get() if hasattr(self, 'extract_audio') else False,
            'auto_fetch_tmdb': self.auto_fetch_tmdb.get() if hasattr(self, 'auto_fetch_tmdb') else True,
            'is_tv_series': self.is_tv_series.get() if hasattr(self, 'is_tv_series') else False,
            'add_translator_info': self.add_translator_info.get() if hasattr(self, 'add_translator_info') else True
        }

        self.config_manager.update(config_updates)
        self.config_manager.save_config()

    def log_config_loaded(self):
        """Log information about loaded configuration"""
        if hasattr(self, 'console_text'):
            summary = self.config_manager.get_config_summary()

            self.log_to_console("💾 Configuration loaded:")
            self.log_to_console(f"   🤖 Model: {summary['model']}")
            self.log_to_console(f"   🔑 Gemini API: {'✅ Saved' if summary['has_gemini_key'] else '❌ Missing'}")
            self.log_to_console(f"   🎬 TMDB API: {'✅ Saved' if summary['has_tmdb_key'] else '❌ Missing (optional)'}")
            self.log_to_console(f"   🆔 TMDB ID: {'✅ Saved' if summary['has_tmdb_id'] else '❌ Missing (optional)'}")
            self.log_to_console(f"   🌐 Language: {summary['language']}")
            if hasattr(self, 'language_code'):
                self.log_to_console(f"   🏷️ Language code: {self.language_code.get()}")
            self.log_to_console(f"   🎵 Extract audio: {'✅ Enabled' if summary['extract_audio'] else '❌ Disabled'}")
            self.log_to_console("─" * 50)

    def ensure_front(self):
        """Additional security to ensure window is in front"""
        try:
            # For macOS - try AppleScript
            os.system('''/usr/bin/osascript -e 'tell app "Finder" to set frontmost of process "Python" to true' ''')
        except:
            pass

        # Alternative methods
        try:
            self.root.call('wm', 'attributes', '.', '-modified', False)
            self.root.focus_set()
        except tk.TclError:
            pass

    def setup_drag_drop(self):
        """Configure drag & drop handling"""
        try:
            from tkinterdnd2 import TkinterDnD, DND_FILES

            # Try to initialize TkinterDnD
            try:
                self.root.tk.call('package', 'require', 'tkdnd')
            except tk.TclError:
                try:
                    TkinterDnD._require(self.root)
                except:
                    raise ImportError("TkinterDnD2 cannot be initialized")

            # Configure drag & drop
            self.drop_frame.drop_target_register(DND_FILES)
            self.drop_frame.dnd_bind('<<Drop>>', self.handle_drop)
            self.root.title("Gemini SRT Translator")
            self.log_to_console("✅ Drag & Drop enabled - you can drag files!")

        except (ImportError, tk.TclError, Exception) as e:
            self.log_to_console(f"ℹ️  Drag & Drop unavailable: {type(e).__name__}")
            self.log_to_console("🖱️  Use button to select file")
            self.log_to_console("💡 Try: pip uninstall tkinterdnd2 && pip install tkinterdnd2")

    def handle_drop(self, event):
        """Handle drop event from TkinterDnD2"""
        files_data = event.data
        self.log_to_console(f"🔍 Debug - raw data: {repr(files_data)}")

        if not files_data:
            return

        file_path = self._parse_dropped_file_path(files_data)

        if file_path and Path(file_path).exists():
            self.process_dropped_item(file_path)
        else:
            self.log_to_console(f"❌ Cannot find or parse file path")
            messagebox.showerror("Error", f"Cannot find file:\n{files_data}")

    def _parse_dropped_file_path(self, files_data):
        """Parse file path from dropped data"""
        # Handle different data formats
        file_path = None

        # Format with curly braces: {/path/with spaces/file.txt}
        if files_data.startswith('{') and files_data.endswith('}'):
            file_path = files_data.strip('{}')
        # Format with quotes: "/path/with spaces/file.txt"
        elif files_data.startswith('"') and files_data.endswith('"'):
            file_path = files_data.strip('"')
        # Multi-line data - take first line
        elif '\n' in files_data:
            lines = files_data.strip().split('\n')
            if lines:
                first_line = lines[0]
                file_path = first_line.strip('{}').strip('"')
        # List of files separated by spaces
        elif files_data.count(' ') > 0:
            if files_data.startswith('/') or files_data[1:3] == ':\\':  # Unix or Windows path
                file_path = files_data.strip()
            else:
                parts = files_data.split()
                if parts:
                    file_path = parts[0].strip('{}').strip('"')
        else:
            file_path = files_data.strip()

        return file_path

    def browse_file(self, event=None):
        """Open file/folder selection dialog"""
        from tkinter import filedialog

        choice = messagebox.askyesnocancel("Selection",
                                           "Yes = File\nNo = Folder\nCancel = Exit")

        if choice is None:
            return
        elif choice:  # File
            file_path = filedialog.askopenfilename(
                title="Select file",
                filetypes=[("All files", "*.*")]
            )
        else:  # Folder
            file_path = filedialog.askdirectory(title="Select folder")

        if file_path:
            self.process_dropped_item(file_path)

    def process_dropped_item(self, path):
        """Process dropped/selected item"""
        path = Path(path)

        if not path.exists():
            messagebox.showerror("Error", f"Path does not exist: {path}")
            return

        self.log_to_console(f"Processing: {path}")

        if path.is_file():
            self._process_single_file(path)
        elif path.is_dir():
            self._process_folder(path)
        else:
            self.log_to_console("Unknown item type")
            messagebox.showwarning("Warning", "Unknown item type")

    def _process_single_file(self, file_path):
        """Process a single file"""
        self.log_to_console("File detected")
        self.clear_treeview()
        self.current_folder_path = file_path.parent

        # Add single file to TreeView
        file_type = classify_file_type(file_path)
        movie_name, year = extract_movie_info(file_path.name)
        title, year_display = format_movie_info(movie_name, year)

        self.log_to_console(f"🎭 Extracted: '{file_path.name}' → Title: '{title}', Year: '{year_display}'")

        if file_type == 'text':
            # Subtitle file
            self.tree.insert('', 'end', text='☑️ Single file',
                             values=(file_path.name, "No match", title, year_display,
                                     str(self.current_folder_path), "📝 Subtitle file"),
                             tags=('subtitle_only',))
        elif file_type == 'video':
            # Video file
            self.tree.insert('', 'end', text='☑️ Single file',
                             values=(None, file_path.name, title, year_display,
                                     str(self.current_folder_path), "🎬 Video file"),
                             tags=('video_only',))
        else:
            # Other file type
            self.tree.insert('', 'end', text='☑️ Single file',
                             values=(file_path.name if file_type == 'text' else "N/A",
                                     file_path.name if file_type == 'video' else "N/A",
                                     title, year_display, str(self.current_folder_path),
                                     f"📄 {file_type.title()}"),
                             tags=('no_match',))

        # Auto-fetch TMDB ID after adding to TreeView (with small delay to ensure UI is updated)
        self.root.after(100, lambda: self._auto_fetch_tmdb_for_movie(title, year_display))

    def _process_folder(self, folder_path):
        """Process a folder"""
        self.log_to_console("Folder detected - scanning contents...")
        found_files = scan_folder_for_files(folder_path)
        self.add_subtitle_matches_to_treeview(found_files, folder_path)

        # Auto-fetch TMDB ID after adding files to TreeView (with small delay to ensure UI is updated)
        self.root.after(100, self._auto_fetch_tmdb_from_first_file)

    def _auto_fetch_tmdb_for_movie(self, title, year):
        """Auto-fetch TMDB ID for a specific movie title and year"""
        # Check if we should auto-fetch
        if not self._should_auto_fetch_tmdb():
            return

        if not title or title in ["Unknown Movie", "No files found"]:
            self.log_to_console("⚠️ Cannot auto-fetch TMDB ID: Invalid movie title")
            return

        # Start background fetch
        self.log_to_console(f"🔍 Auto-fetching TMDB ID for: {title}" + (f" ({year})" if year else ""))
        self._start_tmdb_search_async(title, year, self.tmdb_api_key.get().strip(), silent=True)

    def _auto_fetch_tmdb_from_first_file(self):
        """Auto-fetch TMDB ID from the first file in TreeView"""
        # Check if we should auto-fetch
        if not self._should_auto_fetch_tmdb():
            return

        # Get the first item from TreeView
        items = self.tree.get_children()
        if not items:
            return

        first_item = items[0]
        values = self.tree.item(first_item, 'values')

        if len(values) < 3:
            return

        # Extract movie title and year from TreeView
        movie_title = values[2]  # Title column
        movie_year = values[3]  # Year column

        if not movie_title or movie_title in ["Unknown Movie", "No files found"]:
            return

        # Start background fetch
        self.log_to_console(f"🔍 Auto-fetching TMDB ID for: {movie_title}" + (f" ({movie_year})" if movie_year else ""))
        self._start_tmdb_search_async(movie_title, movie_year, self.tmdb_api_key.get().strip(), silent=True)

    def _should_auto_fetch_tmdb(self):
        """Check if we should auto-fetch TMDB ID"""
        # Check if auto-fetch is enabled in settings
        if not self.auto_fetch_tmdb.get():
            return False

        # Check if TMDB API key is available
        tmdb_api_key = self.tmdb_api_key.get().strip()
        if not tmdb_api_key:
            return False

        # Check if TMDB ID is already set
        current_tmdb_id = self.tmdb_id.get().strip()
        if current_tmdb_id:
            self.log_to_console(f"ℹ️ TMDB ID already set ({current_tmdb_id}), skipping auto-fetch")
            return False

        return True

    # TreeView management methods
    def clear_treeview(self):
        """Clear TreeView and reset TMDB fields for new content"""
        for item in self.tree.get_children():
            self.tree.delete(item)

        # Clear TMDB fields when starting fresh with new content
        if hasattr(self, 'overview'):
            self._clear_overview_field()
        if hasattr(self, 'tmdb_id'):
            self.tmdb_id.set('')  # Clear TMDB ID for new movie

    def toggle_checkbox(self, event):
        """Toggle checkbox state on double-click"""
        item = self.tree.selection()[0] if self.tree.selection() else None
        if item:
            self.toggle_item_checkbox(item)

    def on_tree_click(self, event):
        """Handle single click on tree"""
        item = self.tree.identify('item', event.x, event.y)
        if item and event.x <= 30:  # Clicked on checkbox area
            self.toggle_item_checkbox(item)

    def toggle_item_checkbox(self, item):
        """Toggle checkbox state for specific item"""
        current_text = self.tree.item(item, 'text')
        values = self.tree.item(item, 'values')

        if current_text.startswith('☑️'):
            # Uncheck
            new_text = '☐' + current_text[1:]
            new_values = list(values)
            if len(new_values) >= 6:
                original_status = new_values[5]
                if not original_status.startswith('⏸️'):
                    new_values[5] = f"⏸️ Skipped ({original_status})"
            self.tree.item(item, text=new_text, values=new_values, tags=('unchecked',))

        elif current_text.startswith('☐'):
            # Check
            new_text = '☑️' + current_text[1:]
            new_values = list(values)
            if len(new_values) >= 6:
                current_status = new_values[5]
                if current_status.startswith('⏸️ Skipped ('):
                    original_status = current_status[12:-1]
                    new_values[5] = original_status

            # Determine original tag
            original_tag = self._determine_tag_from_status(new_values[5] if len(new_values) >= 6 else "")
            self.tree.item(item, text=new_text, values=new_values, tags=(original_tag,))
        else:
            # Add checkbox
            new_text = '☑️ ' + current_text
            self.tree.item(item, text=new_text)

    def _determine_tag_from_status(self, status):
        """Determine TreeView tag based on status"""
        if "Matched" in status:
            return 'matched'
        elif "No match" in status:
            return 'no_match'
        elif "No subtitles" in status:
            return 'video_only'
        elif "Subtitle file" in status:
            return 'subtitle_only'
        else:
            return 'matched'

    def find_video_matches(self, subtitle_files, video_files, folder_path):
        """Find matching video files for subtitle files"""
        matches = []

        # Convert to sets for faster lookup
        video_stems = {v.stem.lower(): v for v in video_files}

        # Find matches based on filename similarity
        for subtitle_file in subtitle_files:
            subtitle_stem = subtitle_file.stem.lower()
            matched_video = None

            # Direct match
            if subtitle_stem in video_stems:
                matched_video = video_stems[subtitle_stem]
            else:
                # Try to find partial matches
                best_match = None
                best_score = 0

                for video_stem, video_file in video_stems.items():
                    # Calculate similarity
                    common_length = len(os.path.commonprefix([subtitle_stem, video_stem]))
                    similarity = common_length / max(len(subtitle_stem), len(video_stem))

                    if similarity > best_score and similarity > 0.7:  # 70% similarity threshold
                        best_score = similarity
                        best_match = video_file

                matched_video = best_match

            status = "✅ Matched" if matched_video else "⚠️ No match"
            tag = "matched" if matched_video else "no_match"

            matches.append({
                'subtitle': subtitle_file,
                'video': matched_video,
                'status': status,
                'tag': tag
            })

        # Add video files without subtitles
        matched_videos = {match['video'] for match in matches if match['video']}
        for video_file in video_files:
            if video_file not in matched_videos:
                matches.append({
                    'subtitle': None,
                    'video': video_file,
                    'status': "ℹ️ No subtitles",
                    'tag': "video_only"
                })

        return matches

    def add_subtitle_matches_to_treeview(self, found_files, folder_path):
        """Add subtitle-video matches to TreeView"""
        self.clear_treeview()
        self.current_folder_path = folder_path

        subtitle_files = found_files.get('text', [])
        video_files = found_files.get('video', [])

        if not subtitle_files and not video_files:
            self.tree.insert('', 'end', text='ℹ️ No subtitle or video files',
                             values=('', '', 'No files found', '', str(folder_path), 'Drag folder with files'),
                             tags=('no_match',))
            return

        # Find matches
        matches = self.find_video_matches(subtitle_files, video_files, folder_path)

        # Add matches to TreeView
        for i, match in enumerate(matches):
            subtitle_name = match['subtitle'].name if match['subtitle'] else "None"
            video_name = match['video'].name if match['video'] else "None"

            # Extract movie info
            primary_file = match['video'] if match['video'] else match['subtitle']
            if primary_file:
                movie_name, year = extract_movie_info(primary_file.name)
                title, year_display = format_movie_info(movie_name, year)
                self.log_to_console(f"🎭 Extracted: '{primary_file.name}' → Title: '{title}', Year: '{year_display}'")
            else:
                title = "Unknown Movie"
                year_display = "11"

            item_text = f"☑️ Pair {i + 1}"

            self.tree.insert('', 'end', text=item_text,
                             values=(subtitle_name, video_name, title, year_display, str(folder_path), match['status']),
                             tags=(match['tag'],))

        # Log summary
        self._log_matching_summary(matches)

    def _log_matching_summary(self, matches):
        """Log matching summary"""
        matched_pairs = len([m for m in matches if m['subtitle'] and m['video']])
        subtitle_only = len([m for m in matches if m['subtitle'] and not m['video']])
        video_only = len([m for m in matches if m['video'] and not m['subtitle']])

        self.log_to_console(f"📊 Matching summary:")
        self.log_to_console(f"   ✅ Matched pairs: {matched_pairs}")
        self.log_to_console(f"   ⚠️ Subtitles without video: {subtitle_only}")
        self.log_to_console(f"   ℹ️ Video without subtitles: {video_only}")
        self.log_to_console(f"   📝 Total items: {len(matches)}")

    def get_selected_pairs(self):
        """Get list of selected subtitle-video pairs"""
        selected_pairs = []

        for item in self.tree.get_children():
            item_text = self.tree.item(item, 'text')
            if item_text.startswith('☑️'):
                values = self.tree.item(item, 'values')
                if len(values) >= 2:
                    subtitle_file = values[0] if values[0] != "None" else None
                    video_file = values[1] if values[1] != "None" else None
                    selected_pairs.append({
                        'subtitle': subtitle_file,
                        'video': video_file
                    })

        return selected_pairs

    def start_translation(self):
        """Start translation process with selected pairs"""
        # Debug: check what's in TreeView
        self.log_to_console("🔍 Debug - checking TreeView...")
        total_items = len(self.tree.get_children())
        self.log_to_console(f"Total items in TreeView: {total_items}")

        if total_items == 0:
            messagebox.showwarning("Warning", "TreeView is empty. First drag a folder with files.")
            return

        # Get selected pairs
        selected_pairs = self.get_selected_pairs()

        if not selected_pairs:
            messagebox.showwarning("Warning",
                                   "No pairs selected for translation.\nMake sure items have ☑️ checkmark")
            return

        # Filter pairs based on extract audio setting
        # valid_pairs = self._filter_valid_pairs(selected_pairs)
        valid_pairs = selected_pairs

        if not valid_pairs:
            extract_audio = self.extract_audio.get()
            if extract_audio:
                messagebox.showwarning("Warning",
                                       "No pairs with both subtitles and video found.\nSelect pairs that have both files, or disable 'Extract audio' to process subtitle-only files.")
            else:
                messagebox.showwarning("Warning",
                                       "No pairs with subtitle files found.\nSelect pairs that have subtitle files.")
            return

        # Show confirmation and start processing
        if self._confirm_translation(valid_pairs, len(selected_pairs)):
            self.save_current_config()
            self._run_translation_async(valid_pairs)

    def _filter_valid_pairs(self, selected_pairs):
        """Filter pairs based on current settings"""
        valid_pairs = []
        extract_audio = self.extract_audio.get()

        for pair in selected_pairs:
            if extract_audio:
                # Need both subtitle and video files
                if pair['subtitle'] and pair['video']:
                    valid_pairs.append(pair)
            else:
                # Only need subtitle files
                if pair['subtitle']:
                    valid_pairs.append(pair)

        return valid_pairs

    def _confirm_translation(self, valid_pairs, total_selected):
        """Show confirmation dialog for translation"""
        checked_count = len(valid_pairs)
        extract_audio = self.extract_audio.get()

        if extract_audio:
            confirmation_msg = f"Start processing {checked_count} pairs with subtitles and video for audio extraction?\n"
        else:
            confirmation_msg = f"Start processing {checked_count} subtitle files?\n"

        confirmation_msg += f"(Selected {total_selected}, {checked_count} are valid for current settings)\n\n"

        if extract_audio:
            confirmation_msg += "Files to process:\n" + "\n".join([
                f"• {pair['subtitle']} + {pair['video']}"
                for pair in valid_pairs[:5]  # Show first 5
            ]) + (f"\n... and {checked_count - 5} more" if checked_count > 5 else "")
        else:
            confirmation_msg += "Subtitle files to process:\n" + "\n".join([
                f"• {pair['subtitle']}"
                for pair in valid_pairs[:5]  # Show first 5
            ]) + (f"\n... and {checked_count - 5} more" if checked_count > 5 else "")

        return messagebox.askyesno("Translation confirmation", confirmation_msg)

    def _run_translation_async(self, valid_pairs):
        """Run translation in separate thread"""

        def run_translation():
            try:
                self.status_var.set("Processing...")
                self.log_to_console("🚀 Starting processing...")
                self.log_to_console(f"📊 Processing {len(valid_pairs)} pairs")
                self.log_to_console("─" * 50)

                # Build full paths for pairs
                full_path_pairs = []
                for pair in valid_pairs:
                    full_pair = {}
                    if pair['subtitle']:
                        full_pair['subtitle'] = self.current_folder_path / pair['subtitle']
                    else:
                        full_pair['subtitle'] = None

                    if pair['video']:
                        full_pair['video'] = self.current_folder_path / pair['video']
                    else:
                        full_pair['video'] = None

                    full_path_pairs.append(full_pair)

                # Get current configuration
                config = {
                    'gemini_api_key': self.gemini_api_key.get(),
                    'model': self.model.get(),
                    'tmdb_api_key': self.tmdb_api_key.get(),
                    'tmdb_id': self.tmdb_id.get(),
                    'language': self.language.get(),
                    'language_code': self.language_code.get() if hasattr(self, 'language_code') else 'pl',
                    'extract_audio': self.extract_audio.get(),
                    'overview': self.overview.get() if hasattr(self, 'overview') else '',
                    'movie_title': self._get_movie_title_from_treeview(),
                    'is_tv_series': self.is_tv_series.get() if hasattr(self, 'is_tv_series') else False,
                    'cancel_event': self.cancel_event,
                    'add_translator_info': self.add_translator_info.get() if hasattr(self, 'is_tv_series') else True
                }

                # Run translation using CLI runner
                success = self.cli_runner.run_translation_batch(full_path_pairs, config)

                if self.cancel_event.is_set():
                    self.root.after(0, lambda: self.status_var.set("Cancelled"))
                    self.root.after(0, lambda: self.log_to_console("🛑 Processing cancelled"))
                elif success:
                    self.root.after(0, lambda: self.status_var.set("Processing completed successfully"))
                else:
                    self.root.after(0, lambda: self.status_var.set("Processing completed with errors"))

            except Exception as e:
                error_msg = f"Error during processing: {e}"
                self.root.after(0, lambda: self.log_to_console(error_msg))
                self.root.after(0, lambda: self.status_var.set("Processing error"))
            finally:
                self.root.after(0, self.show_translate_button)
                self.cancel_event.clear()

        self.show_cancel_button()

        # Reset cancel event przed rozpoczęciem
        self.cancel_event.clear()

        # Run in separate thread
        self.processing_thread = threading.Thread(target=run_translation, daemon=True)
        self.processing_thread.start()

    def fetch_tmdb_info(self):
        """Fetch TMDB info using the TMDB ID in the field"""
        # Check if TMDB API key is provided
        tmdb_api_key = self.tmdb_api_key.get().strip()
        if not tmdb_api_key:
            messagebox.showwarning("TMDB API Key Required",
                                   "Please enter your TMDB API key first.\n\n"
                                   "You can get a free API key from:\n"
                                   "https://www.themoviedb.org/settings/api")
            return

        # Check if TMDB ID is provided
        tmdb_id = self.tmdb_id.get().strip()
        if not tmdb_id:
            messagebox.showwarning("TMDB ID Required",
                                   "Please enter a TMDB ID first.\n\n"
                                   "You can find movie/TV IDs on:\n"
                                   "https://www.themoviedb.org/")
            return

        # Validate TMDB ID is numeric
        try:
            int(tmdb_id)
        except ValueError:
            messagebox.showwarning("Invalid TMDB ID",
                                   f"TMDB ID must be a number.\n\n"
                                   f"You entered: '{tmdb_id}'")
            return

        # Get content type from checkbox
        is_tv_series = self.is_tv_series.get()
        content_type = "TV Series" if is_tv_series else "Movie"

        # Show confirmation dialog
        confirm_msg = f"Fetch {content_type.lower()} information for TMDB ID: {tmdb_id}?\n\nContinue?"

        if not messagebox.askyesno("Confirm TMDB Fetch", confirm_msg):
            return

        # Start the fetch (not silent for manual trigger)
        self._start_tmdb_fetch_by_id_async(tmdb_id, tmdb_api_key, is_tv_series, silent=False)

    def _start_tmdb_fetch_by_id_async(self, tmdb_id, api_key, is_tv_series, silent=False):
        """Start TMDB fetch by ID in separate thread"""

        def fetch_tmdb():
            try:
                content_type = "TV Series" if is_tv_series else "Movie"

                if not silent:
                    self.log_to_console("🔍 Starting TMDB fetch...")
                    self.log_to_console(f"   🆔 TMDB ID: {tmdb_id}")
                    self.log_to_console(f"   📺 Content Type: {content_type}")
                    self.log_to_console("─" * 30)

                # Import TMDB helper
                try:
                    from gst_gui.utils.tmdb_helper import TMDBHelper
                except ImportError:
                    if not silent:
                        self.log_to_console("❌ Could not import TMDB helper")
                        messagebox.showerror("Import Error", "Could not import TMDB helper module.")
                    return

                # Create TMDB helper
                tmdb = TMDBHelper(api_key, logger=self.log_to_console if not silent else None)

                # Test API key
                if not tmdb.test_api_key():
                    if not silent:
                        messagebox.showerror("Invalid API Key",
                                             "TMDB API key is invalid.\n\n"
                                             "Please check your API key and try again.")
                    else:
                        self.log_to_console("❌ TMDB API key is invalid")
                    return

                # Fetch content using the specific endpoint
                import requests

                if is_tv_series:
                    url = f"https://api.themoviedb.org/3/tv/{tmdb_id}"
                else:
                    url = f"https://api.themoviedb.org/3/movie/{tmdb_id}"

                params = {
                    'api_key': api_key,
                    'language': 'en-US'
                }

                response = requests.get(url, params=params)

                if response.status_code == 200:
                    data = response.json()

                    # Format the data based on content type
                    if is_tv_series:
                        movie = {
                            'id': data.get('id'),
                            'title': data.get('name', ''),  # TV shows use 'name'
                            'year': data.get('first_air_date', '')[:4] if data.get('first_air_date') else '',
                            'overview': data.get('overview', ''),
                            'type': 'TV Series',
                            'poster_path': data.get('poster_path', '')
                        }
                    else:
                        movie = {
                            'id': data.get('id'),
                            'title': data.get('title', ''),  # Movies use 'title'
                            'year': data.get('release_date', '')[:4] if data.get('release_date') else '',
                            'overview': data.get('overview', ''),
                            'type': 'Movie',
                            'poster_path': data.get('poster_path', '')
                        }

                    if not silent:
                        self.log_to_console(f"✅ Found {content_type.lower()}: {movie['title']}")

                    # Update the overview field in the main thread
                    self.root.after(0, self._update_overview_only, movie, silent)

                elif response.status_code == 404:
                    if not silent:
                        self.log_to_console(f"❌ {content_type} not found")
                        messagebox.showwarning(f"{content_type} Not Found",
                                               f"Could not find {content_type.lower()} with TMDB ID: {tmdb_id}\n\n"
                                               f"Please check the ID and content type.")
                    else:
                        self.log_to_console(f"❌ No {content_type.lower()} found with TMDB ID: {tmdb_id}")
                else:
                    error_msg = f"TMDB API error: {response.status_code}"
                    if not silent:
                        self.log_to_console(f"❌ {error_msg}")
                        messagebox.showerror("API Error", f"Error fetching {content_type.lower()}: {error_msg}")
                    else:
                        self.log_to_console(f"❌ {error_msg}")

            except Exception as e:
                error_msg = f"Error during TMDB fetch: {e}"
                self.log_to_console(f"❌ {error_msg}")
                if not silent:
                    messagebox.showerror("Fetch Error", error_msg)

        # Start fetch in background thread
        thread = threading.Thread(target=fetch_tmdb, daemon=True)
        thread.start()

    def _update_overview_only(self, movie, silent=False):
        """Update only the overview field with found movie (runs in main thread)"""
        try:
            # Update only the overview field (keep existing TMDB ID)
            overview = movie.get('overview', '')
            self._update_overview_field(overview)

            # Log success
            year_text = f" ({movie['year']})" if movie.get('year') else ""
            self.log_to_console(f"✅ Fetched movie info: {movie['title']}{year_text}")

            if not silent:
                # Show detailed information
                details_msg = f"Movie information fetched!\n\n"
                details_msg += f"Title: {movie['title']}\n"
                if movie.get('year'):
                    details_msg += f"Year: {movie['year']}\n"
                details_msg += f"TMDB ID: {movie['id']}\n"

                if movie["poster_path"]:
                    self.load_image("https://image.tmdb.org/t/p/w154" + movie["poster_path"])

                if overview:
                    # Truncate overview for popup (keep full version in the field)
                    display_overview = overview
                    if len(display_overview) > 200:
                        display_overview = display_overview[:200] + "..."
                    details_msg += f"\nOverview:\n{display_overview}"

                messagebox.showinfo("Movie Info Fetched!", details_msg)

            # Save configuration with the updated overview
            self.save_current_config()

        except Exception as e:
            self.log_to_console(f"❌ Error updating movie info: {e}")

    def _start_tmdb_search_async(self, title, year, api_key, silent=False):
        """Start TMDB search in separate thread"""

        def search_tmdb():
            try:
                if not silent:
                    self.log_to_console("🔍 Starting TMDB search...")
                    self.log_to_console(f"   🎭 Title: {title}")
                    if year:
                        self.log_to_console(f"   📅 Year: {year}")
                    self.log_to_console("─" * 30)

                # Import TMDB helper
                try:
                    from gst_gui.utils.tmdb_helper import TMDBHelper
                except ImportError:
                    if not silent:
                        self.log_to_console("❌ Could not import TMDB helper")
                        messagebox.showerror("Import Error", "Could not import TMDB helper module.")
                    return

                # Create TMDB helper
                tmdb = TMDBHelper(api_key, logger=self.log_to_console if not silent else None)

                # Test API key
                if not tmdb.test_api_key():
                    if not silent:
                        messagebox.showerror("Invalid API Key",
                                             "TMDB API key is invalid.\n\n"
                                             "Please check your API key and try again.")
                    else:
                        self.log_to_console("❌ TMDB API key is invalid")
                    return

                # Search for movie
                movie = tmdb.find_best_match(title, is_series=self.is_tv_series.get(), year=year)

                if movie:
                    # Update the TMDB ID field in the main thread
                    self.root.after(0, self._update_tmdb_id_field, movie, silent)
                else:
                    if not silent:
                        self.log_to_console("❌ No matching movie found")
                        messagebox.showwarning("No Match Found",
                                               f"Could not find a matching movie for:\n'{title}'{' (' + year + ')' if year else ''}")
                    else:
                        self.log_to_console(f"❌ No TMDB match found for: {title}" + (f" ({year})" if year else ""))

            except Exception as e:
                error_msg = f"Error during TMDB search: {e}"
                self.log_to_console(f"❌ {error_msg}")
                if not silent:
                    messagebox.showerror("Search Error", error_msg)

        # Start search in background thread
        thread = threading.Thread(target=search_tmdb, daemon=True)
        thread.start()

    def _get_movie_title_from_treeview(self):
        """Get movie title from the first item in TreeView"""
        items = self.tree.get_children()
        if items:
            first_item = items[0]
            values = self.tree.item(first_item, 'values')
            if len(values) >= 3:
                return values[2]  # Title column
        return ""

    def _update_overview_field(self, overview_text):
        """Update the overview entry field"""
        if hasattr(self, 'overview'):
            self.overview.set(overview_text or '')

    def _clear_overview_field(self):
        """Clear the overview entry field"""
        if hasattr(self, 'overview'):
            self.overview.set('')

    def _update_tmdb_id_field(self, movie, silent=False):
        """Update TMDB ID field with found movie (runs in main thread)"""
        try:
            # Update the TMDB ID field
            movie_id = str(movie['id'])
            self.tmdb_id.set(movie_id)

            # Update the overview field
            overview = movie.get('overview', '')
            self._update_overview_field(overview)
            if movie["poster_path"]:
                self.load_image("https://image.tmdb.org/t/p/w154" + movie["poster_path"])

            # Log success
            year_text = f" ({movie['year']})" if movie['year'] else ""
            self.log_to_console(f"✅ Auto-found TMDB ID: {movie['title']}{year_text} → ID: {movie_id}")

            if not silent:
                # Show detailed information
                details_msg = f"Movie found and TMDB ID updated!\n\n"
                details_msg += f"Title: {movie['title']}\n"
                if movie['year']:
                    details_msg += f"Year: {movie['year']}\n"
                details_msg += f"TMDB ID: {movie['id']}\n"
                if overview:
                    # Truncate overview for popup (keep full version in the field)
                    display_overview = overview
                    if len(display_overview) > 200:
                        display_overview = display_overview[:200] + "..."
                    details_msg += f"\nOverview:\n{display_overview}"

                messagebox.showinfo("Movie Found!", details_msg)

            # Save configuration with the new TMDB ID
            self.save_current_config()

        except Exception as e:
            self.log_to_console(f"❌ Error updating TMDB ID: {e}")

    def log_to_console(self, message):
        """Add message to console"""

        def update_console():
            if hasattr(self, 'console_text'):
                self.console_text.insert(tk.END, message + "\n")
                self.console_text.see(tk.END)
                self.root.update_idletasks()

        # Make sure GUI update happens in main thread
        self.root.after(0, update_console)
