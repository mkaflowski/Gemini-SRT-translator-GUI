"""
Main window class for the CLI Wrapper GUI application.
Coordinates between UI handlers, file processing, and configuration.
"""
import re
import tkinter as tk
from io import BytesIO
import customtkinter as ctk
from tkinter import messagebox, scrolledtext
import threading
from pathlib import Path
import os
from gst_gui.handlers.drag_drop_handler import DropAreaHandler

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

from gst_gui.utils.subtitle_tracks import (
    probe_subtitle_tracks,
    format_track_label,
    pick_matching_track,
    extract_subtitle_track,
)
from gst_gui.utils import video_description_with_splitting as vdesc


class _ConsoleStdout:
    """
    File-like object that forwards captured stdout to a GUI logger, line by line.
    Handles '\\r' progress updates (keeps only the latest partial line, like a terminal).
    """

    def __init__(self, log_func):
        self._log = log_func
        self._buffer = ""

    def write(self, text):
        if not text:
            return
        self._buffer += text
        # Normalise carriage returns: keep only text after the last '\r' on a line
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if "\r" in line:
                line = line.rsplit("\r", 1)[-1]
            line = line.rstrip()
            if line:
                self._log(line)

    def flush(self):
        rest = self._buffer
        self._buffer = ""
        if "\r" in rest:
            rest = rest.rsplit("\r", 1)[-1]
        rest = rest.rstrip()
        if rest:
            self._log(rest)


class DragDropGUI:
    """Main GUI class that coordinates all handlers"""

    def __init__(self, root):
        # Initialize log buffer FIRST - before anything that might log
        self._log_buffer = []
        self._log_lock = threading.Lock()
        self._log_scheduled = False
        self._max_console_lines = 1000

        # Set CustomTkinter appearance before doing anything else
        ctk.set_appearance_mode("dark")  # Modes: "System" (default), "Dark", "Light"
        ctk.set_default_color_theme("blue")  # Themes: "blue" (default), "green", "dark-blue"

        # Convert root to CTk if it's not already
        if not isinstance(root, ctk.CTk):
            # If root is a regular tkinter window, we need to handle this differently
            self.root = root
            # Configure the tkinter root to have dark colors
            root.configure(bg='#212121')
        else:
            self.root = root

        self.root.title("Gemini SRT Translator")
        self.image_label = None

        # Enhanced icon loading with multiple fallback paths
        self._load_window_icon()

        window_width = 1200
        window_height = 900

        self.root.geometry(f"{window_width}x{window_height}")

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

        # Initialize CLI runner with logger and progress/status callbacks
        self.cli_runner = CLIRunner(
            logger=self.log_to_console,
            progress_callback=self._on_translation_progress,
            pair_status_callback=self._on_pair_status,
            line_progress_callback=self._on_line_progress
        )

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

        # Additional settings for macOS to ensure window is in front
        self.root.after(100, self.ensure_front)

        # Log configuration after UI is ready
        self.root.after(200, self.log_config_loaded)

        from gst_gui.handlers.translation_handler import TranslationManager
        self.translation_manager = TranslationManager(
            cli_runner=self.cli_runner,
            main_window=self
        )

    def _load_window_icon(self):
        """Load window icon with multiple fallback paths"""
        # List of possible icon paths to try
        possible_paths = [
            # Original path from your code
            Path(__file__).resolve().parent / "../../gst_gui/assets/icon.png",
            # Alternative paths
            Path(__file__).resolve().parent / "../assets/icon.png",
            Path(__file__).resolve().parent.parent / "assets/icon.png",
            Path(__file__).resolve().parent / "assets/icon.png",
            # Current directory
            Path("icon.png"),
            Path("assets/icon.png"),
            Path("gst_gui/assets/icon.png"),
            # Common icon names
            Path("app_icon.png"),
            Path("logo.png")
        ]

        icon_loaded = False

        for icon_path in possible_paths:
            try:
                if icon_path.exists():
                    # Try different methods to load the icon
                    try:
                        # Method 1: Standard PhotoImage
                        icon = tk.PhotoImage(file=str(icon_path))
                        self.root.iconphoto(False, icon)
                        print(f"✅ Icon loaded from: {icon_path}")
                        icon_loaded = True
                        break
                    except Exception as e1:
                        try:
                            # Method 2: For CustomTkinter, try wm_iconbitmap (Windows)
                            if hasattr(self.root, 'wm_iconbitmap'):
                                # Convert PNG to ICO if needed (Windows only)
                                if str(icon_path).endswith('.png'):
                                    continue  # Skip PNG for iconbitmap
                                self.root.wm_iconbitmap(str(icon_path))
                                print(f"✅ Icon loaded via iconbitmap from: {icon_path}")
                                icon_loaded = True
                                break
                        except Exception as e2:
                            print(f"⚠️ Failed to load icon from {icon_path}: {e1}")
                            continue
            except Exception as e:
                continue

        if not icon_loaded:
            print("ℹ️ No icon file found - using default window icon")
            print("💡 To add an icon, place 'icon.png' in one of these locations:")
            for path in possible_paths[:5]:  # Show first 5 paths
                print(f"   • {path}")

        return icon_loaded

    def _setup_ui(self):
        """Setup the user interface with hidden scrollbar when not needed"""
        # Sticky bottom action bar - packed FIRST (side=bottom) so it is always
        # visible regardless of how much content the scrollable area holds.
        self._create_bottom_bar()

        # Create a scrollable frame with default styling
        self.scrollable_frame = ctk.CTkScrollableFrame(
            self.root,
            corner_radius=0,
            fg_color="transparent"  # Make it blend with the background
        )
        self.scrollable_frame.pack(fill="both", expand=True, padx=20, pady=(20, 0))

        # Use scrollable_frame as the main container
        self.main_frame = self.scrollable_frame

        # Create UI handlers (they'll now be inside the scrollable frame)
        self._create_drop_area()
        self._create_treeview()
        self._create_console()
        self._create_config_sections()

        # Hide scrollbar by default on startup
        self.root.after(100, self._hide_scrollbar_initially)
        # Then start monitoring for when it's needed
        self.root.after(500, self._manage_scrollbar_visibility)

        # Check for app updates in background (non-blocking)
        self.root.after(2000, self._check_app_update)

    def _create_drop_area(self):
        # Create the drop frame
        self.drop_frame = ctk.CTkFrame(self.main_frame, height=120, corner_radius=10)
        self.drop_frame.pack(fill="x", pady=(0, 20))

        # Create the label
        self.drop_label = ctk.CTkLabel(
            self.drop_frame,
            text="📁 Drag files or folders here\n\nOr click to browse",
            font=ctk.CTkFont(size=14),
            text_color=("gray60", "gray40")
        )
        self.drop_label.pack(expand=True)

        # Initialize the drop area handler
        self.drop_handler = DropAreaHandler(
            widget=self.drop_frame,
            logger=self.log_to_console,
            on_file_callback=self.process_dropped_item
        )

    def _create_treeview(self):
        """Create the TreeView for file pairs"""
        # TreeView header: label + selection controls
        tree_header = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        tree_header.pack(fill="x", pady=(0, 5))

        treeview_label = ctk.CTkLabel(tree_header, text="Found files:", font=ctk.CTkFont(size=14, weight="bold"))
        treeview_label.pack(side="left")

        self.selected_count_label = ctk.CTkLabel(
            tree_header, text="", text_color="gray", font=ctk.CTkFont(size=11)
        )
        self.selected_count_label.pack(side="left", padx=(10, 0))

        deselect_all_button = ctk.CTkButton(
            tree_header, text="☐ None", command=self.deselect_all_items,
            width=70, height=24, font=ctk.CTkFont(size=11),
            fg_color="transparent", border_width=1, text_color=("gray30", "gray70")
        )
        deselect_all_button.pack(side="right", padx=(5, 0))

        select_all_button = ctk.CTkButton(
            tree_header, text="☑ All", command=self.select_all_items,
            width=70, height=24, font=ctk.CTkFont(size=11),
            fg_color="transparent", border_width=1, text_color=("gray30", "gray70")
        )
        select_all_button.pack(side="right")

        self.embedded_subs_button = ctk.CTkButton(
            tree_header, text="🎞 Embedded subs", command=self.choose_embedded_subtitles,
            width=130, height=24, font=ctk.CTkFont(size=11),
            fg_color="transparent", border_width=1, text_color=("gray30", "gray70")
        )
        self.embedded_subs_button.pack(side="right", padx=(0, 10))

        self.describe_video_button = ctk.CTkButton(
            tree_header, text="📝 Describe video", command=self.describe_video,
            width=130, height=24, font=ctk.CTkFont(size=11),
            fg_color="transparent", border_width=1, text_color=("gray30", "gray70")
        )
        self.describe_video_button.pack(side="right", padx=(0, 10))

        # Cache of probed subtitle tracks: {full video path: [track dicts]}
        self._embedded_tracks_cache = {}

        # Frame for TreeView (still using tkinter TreeView as CustomTkinter doesn't have equivalent)
        self.tree_frame = ctk.CTkFrame(self.main_frame, height=200)
        self.tree_frame.pack(fill="x", pady=(0, 10))  # Reduced from (0, 20) to (0, 10)
        self.tree_frame.pack_propagate(False)  # Prevent frame from shrinking

        # Grow the tree with the window height (extra vertical space goes here)
        self._tree_resize_job = None
        self.root.bind('<Configure>', self._on_root_resize, add='+')

        # Create a tkinter frame inside the CustomTkinter frame for the TreeView
        self.tree_container = tk.Frame(self.tree_frame, bg="#1a1a1a")  # Dark background
        self.tree_container.pack(fill="both", expand=True, padx=10, pady=10)

        # Configure TreeView style BEFORE creating the TreeView
        self._configure_treeview_style()

        # TreeView widget (keeping tkinter TreeView for functionality)
        self.tree = tk.ttk.Treeview(
            self.tree_container,
            columns=('SubtitleFile', 'VideoFile', 'Title', 'Year', 'FolderPath', 'Status'),
            show='tree headings',
            style="Dark.Treeview"  # Use our custom dark style
        )

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

        # Scrollbars with dark styling
        tree_scrolly = tk.ttk.Scrollbar(self.tree_container, orient=tk.VERTICAL, command=self.tree.yview)
        tree_scrollx = tk.ttk.Scrollbar(self.tree_container, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=tree_scrolly.set, xscrollcommand=tree_scrollx.set)

        # Grid layout for TreeView and scrollbars
        self.tree.grid(row=0, column=0, sticky="nsew")
        tree_scrolly.grid(row=0, column=1, sticky="ns")
        tree_scrollx.grid(row=1, column=0, sticky="ew")

        # Configure grid weights
        self.tree_container.grid_rowconfigure(0, weight=1)
        self.tree_container.grid_columnconfigure(0, weight=1)

        # Configure TreeView tags AFTER creating the TreeView
        self._configure_treeview_tags()

        # Bind events
        self.tree.bind('<Double-1>', self.toggle_checkbox)
        self.tree.bind('<Button-1>', self.on_tree_click)

    def _configure_treeview_style(self):
        """Configure TreeView style for dark theme"""
        style = tk.ttk.Style()

        # Create a custom dark style for TreeView
        style.theme_use('clam')  # Use clam theme as base for better customization

        # Configure the dark TreeView style
        style.configure("Dark.Treeview",
                        background="#2b2b2b",  # Dark background
                        foreground="#ffffff",  # White text
                        fieldbackground="#2b2b2b",  # Dark field background
                        borderwidth=0,  # No borders
                        relief="flat",  # Flat appearance
                        rowheight=25)  # Row height

        # Configure TreeView headings
        style.configure("Dark.Treeview.Heading",
                        background="#404040",  # Dark gray headers
                        foreground="#ffffff",  # White text
                        borderwidth=1,  # Thin border
                        relief="solid",  # Solid border style
                        font=('Arial', 9, 'bold'))  # Bold font

        # Configure selection and hover effects
        style.map("Dark.Treeview",
                  background=[('selected', '#1f538d'),  # Blue when selected
                              ('active', '#404040')],  # Gray when hovered
                  foreground=[('selected', '#ffffff'),  # White text when selected
                              ('active', '#ffffff')])  # White text when hovered

        # Configure scrollbars to be dark
        style.configure("Vertical.TScrollbar",
                        background="#404040",
                        troughcolor="#2b2b2b",
                        borderwidth=0,
                        arrowcolor="#ffffff",
                        darkcolor="#404040",
                        lightcolor="#404040")

        style.configure("Horizontal.TScrollbar",
                        background="#404040",
                        troughcolor="#2b2b2b",
                        borderwidth=0,
                        arrowcolor="#ffffff",
                        darkcolor="#404040",
                        lightcolor="#404040")

    def _configure_treeview_tags(self):
        """Configure TreeView tags for different statuses"""
        # Configure TreeView style for dark theme
        style = tk.ttk.Style()

        # Configure the TreeView to work with dark theme
        if ctk.get_appearance_mode() == "Dark":
            # Dark theme colors
            style.configure("Treeview",
                            background="#2b2b2b",
                            foreground="#ffffff",
                            fieldbackground="#2b2b2b",
                            borderwidth=0,
                            relief="flat")
            style.configure("Treeview.Heading",
                            background="#404040",
                            foreground="#ffffff",
                            borderwidth=1,
                            relief="solid")
            # Configure selection colors
            style.map("Treeview",
                      background=[('selected', '#1f538d')],
                      foreground=[('selected', '#ffffff')])
        else:
            # Light theme colors (fallback)
            style.configure("Treeview",
                            background="#ffffff",
                            foreground="#000000",
                            fieldbackground="#ffffff")
            style.configure("Treeview.Heading",
                            background="#f0f0f0",
                            foreground="#000000")

        # Configure tags with proper colors for dark theme
        self.tree.tag_configure('matched',
                                background='#2d5a2d',
                                foreground='#ffffff')
        self.tree.tag_configure('subtitle_only',
                                background='#5a5a2d',
                                foreground='#ffffff')
        self.tree.tag_configure('video_only',
                                background='#2d2d5a',
                                foreground='#ffffff')
        self.tree.tag_configure('no_match',
                                background='#5a2d2d',
                                foreground='#ffffff')
        self.tree.tag_configure('unchecked',
                                background='#404040',
                                foreground='#888888')
        # Live translation status tags
        self.tree.tag_configure('translating',
                                background='#1f538d',
                                foreground='#ffffff')
        self.tree.tag_configure('done',
                                background='#2d5a2d',
                                foreground='#ffffff')
        self.tree.tag_configure('failed',
                                background='#7a2d2d',
                                foreground='#ffffff')

    def _create_console(self):
        """Create the console output area"""
        console_header = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        console_header.pack(fill="x", pady=(0, 5))

        console_label = ctk.CTkLabel(console_header, text="Console output:", font=ctk.CTkFont(size=14, weight="bold"))
        console_label.pack(side="left")

        self.clear_console_button = ctk.CTkButton(
            console_header,
            text="🗑 Clear",
            command=self.clear_console,
            width=70,
            height=24,
            font=ctk.CTkFont(size=11),
            fg_color="transparent",
            border_width=1,
            text_color=("gray30", "gray70")
        )
        self.clear_console_button.pack(side="right")

        # Console frame - made 50% shorter with fixed height
        self.console_frame = ctk.CTkFrame(self.main_frame, height=150)  # Fixed height to make it 50% shorter
        self.console_frame.pack(fill="x", pady=(0, 10))  # Reduced from (0, 20) to (0, 10)
        self.console_frame.pack_propagate(False)  # Prevent frame from shrinking

        # Create tkinter frame for the ScrolledText widget
        # Get the current appearance mode colors
        if ctk.get_appearance_mode() == "Dark":
            bg_color = "#212121"
        else:
            bg_color = "#f0f0f0"

        self.console_container = tk.Frame(self.console_frame, bg=bg_color)
        self.console_container.pack(fill="both", expand=True, padx=10, pady=10)

        # Console text widget - reduced height from 12 to 6 lines
        self.console_text = ctk.CTkTextbox(
            self.console_container,
            height=150,
            font=ctk.CTkFont(family="Consolas", size=10),
            wrap="word"
        )
        self.console_text.pack(fill="both", expand=True)

    def _create_config_sections(self):
        """Create configuration area: always-visible language row + tabbed sections"""
        # Configuration Sections Container
        self.config_container = ctk.CTkFrame(self.main_frame)
        self.config_container.pack(fill="x", pady=(0, 20))

        # Get UI config (kept for config-file compatibility)
        ui_config = self.config_manager.get_ui_config()
        self.api_expanded = tk.BooleanVar(value=ui_config['api_expanded'])
        self.settings_expanded = tk.BooleanVar(value=ui_config['settings_expanded'])

        processing_config = self.config_manager.get_processing_config()

        # Always-visible row: target language (the most frequently changed setting)
        language_row = ctk.CTkFrame(self.config_container, fg_color="transparent")
        language_row.pack(fill="x", padx=10, pady=(10, 0))

        ctk.CTkLabel(language_row, text="Target language:",
                     font=ctk.CTkFont(size=13, weight="bold")).pack(side="left", padx=(10, 5))
        self.language = tk.StringVar(value=processing_config['language'])
        self.language_entry = ctk.CTkEntry(language_row, textvariable=self.language, width=150)
        self.language_entry.pack(side="left", padx=(0, 20))

        ctk.CTkLabel(language_row, text="Code:").pack(side="left", padx=(0, 5))
        self.language_code = tk.StringVar(value=processing_config.get('language_code', 'pl'))
        self.language_code_entry = ctk.CTkEntry(language_row, textvariable=self.language_code, width=60)
        self.language_code_entry.pack(side="left", padx=(0, 20))

        # Tabbed sections instead of expandable buttons
        self.config_tabview = ctk.CTkTabview(self.config_container, height=280)
        self.config_tabview.pack(fill="x", padx=10, pady=(5, 10))
        self.config_tabview.add("Settings")
        self.config_tabview.add("API keys")

        # Create the actual configuration forms inside the tabs
        self._create_api_options()
        self._create_settings_options()

    def _make_key_reveal_button(self, parent, entry):
        """Create a small eye-toggle button that reveals/hides a masked entry"""
        def toggle():
            if entry.cget("show") == "*":
                entry.configure(show="")
                button.configure(text="🙈")
            else:
                entry.configure(show="*")
                button.configure(text="👁")

        button = ctk.CTkButton(
            parent,
            text="👁",
            command=toggle,
            width=32,
            height=28,
            fg_color="transparent",
            border_width=1,
            text_color=("gray30", "gray70")
        )
        return button

    def _create_api_options(self):
        """Create API configuration options (inside the 'API keys' tab)"""
        self.api_options_frame = self.config_tabview.tab("API keys")

        api_config = self.config_manager.get_api_config()

        # Row 1: Gemini API Key and Model
        row1_frame = ctk.CTkFrame(self.api_options_frame, fg_color="transparent")
        row1_frame.pack(fill="x", padx=10, pady=(10, 5))

        # Gemini API Key
        ctk.CTkLabel(row1_frame, text="Gemini API Key:").pack(side="left", padx=(10, 5))
        self.gemini_api_key = tk.StringVar(value=api_config['gemini_api_key'])
        self.gemini_entry = ctk.CTkEntry(row1_frame, textvariable=self.gemini_api_key, show="*", width=300)
        self.gemini_entry.pack(side="left", padx=(0, 5))
        self._make_key_reveal_button(row1_frame, self.gemini_entry).pack(side="left", padx=(0, 20))

        row1_5_frame = ctk.CTkFrame(self.api_options_frame, fg_color="transparent")
        row1_5_frame.pack(fill="x", padx=10, pady=(5, 5))

        ctk.CTkLabel(row1_5_frame, text="Gemini API Key 2 (optional):").pack(side="left", padx=(10, 5))
        self.gemini_api_key2 = tk.StringVar(value=api_config.get('gemini_api_key2', ''))
        self.gemini_entry2 = ctk.CTkEntry(row1_5_frame, textvariable=self.gemini_api_key2, show="*", width=300)
        self.gemini_entry2.pack(side="left", padx=(0, 5))
        self._make_key_reveal_button(row1_5_frame, self.gemini_entry2).pack(side="left", padx=(0, 5))

        # Info label for second key
        info_label = ctk.CTkLabel(row1_5_frame, text="ℹ️ Used as fallback", text_color="gray",
                                  font=ctk.CTkFont(size=10))
        info_label.pack(side="left", padx=(0, 10))

        # Row 1.6: Fallback models
        row1_6_frame = ctk.CTkFrame(self.api_options_frame, fg_color="transparent")
        row1_6_frame.pack(fill="x", padx=10, pady=(5, 5))

        ctk.CTkLabel(row1_6_frame, text="Fallback models (optional):").pack(side="left", padx=(10, 5))
        self.fallback_models = tk.StringVar(value=api_config.get('fallback_models', ''))
        self.fallback_models_entry = ctk.CTkEntry(row1_6_frame, textvariable=self.fallback_models, width=300,
                                                  placeholder_text="gemini-3.5-flash,gemini-3-flash-preview")
        self.fallback_models_entry.pack(side="left", padx=(0, 20))

        fallback_info_label = ctk.CTkLabel(
            row1_6_frame,
            text="ℹ️ Comma-separated, tried in order when the model fails (error/overloaded/quota)",
            text_color="gray",
            font=ctk.CTkFont(size=10)
        )
        fallback_info_label.pack(side="left", padx=(0, 10))

        # Model
        ctk.CTkLabel(row1_frame, text="Model:").pack(side="left", padx=(10, 5))
        self.model = tk.StringVar(value=api_config['model'])
        self.model_combo = ctk.CTkComboBox(
            row1_frame,
            variable=self.model,
            width=250,
            values=["gemini-3-flash", "gemini-3-pro", "gemini-2.5-flash", "gemini-2.0-flash"]
        )
        self.model_combo.pack(side="left", padx=(0, 5))

        # Fetch models button
        self.fetch_models_button = ctk.CTkButton(
            row1_frame,
            text="🔄",
            command=self.fetch_gemini_models,
            width=32,
            height=28,
            font=ctk.CTkFont(size=14)
        )
        self.fetch_models_button.pack(side="left", padx=(0, 10))

        # Tooltip for fetch models button
        self.fetch_models_button.bind("<Enter>", lambda e: self._show_fetch_models_tooltip(e))
        self.fetch_models_button.bind("<Leave>", lambda e: self._hide_fetch_models_tooltip())

        # Row 2: TMDB API Key
        row2_frame = ctk.CTkFrame(self.api_options_frame, fg_color="transparent")
        row2_frame.pack(fill="x", padx=10, pady=(5, 10))

        ctk.CTkLabel(row2_frame, text="TMDB API Key (optional):").pack(side="left", padx=(10, 5))
        self.tmdb_api_key = tk.StringVar(value=api_config['tmdb_api_key'])
        self.tmdb_entry = ctk.CTkEntry(row2_frame, textvariable=self.tmdb_api_key, show="*", width=300)
        self.tmdb_entry.pack(side="left", padx=(0, 5))
        self._make_key_reveal_button(row2_frame, self.tmdb_entry).pack(side="left", padx=(0, 10))

    def _create_settings_options(self):
        """Create general settings options (inside the 'Settings' tab)"""
        self.settings_options_frame = self.config_tabview.tab("Settings")

        processing_config = self.config_manager.get_processing_config()

        # Row 1: processing options
        row1_frame = ctk.CTkFrame(self.settings_options_frame, fg_color="transparent")
        row1_frame.pack(fill="x", padx=10, pady=(10, 5))

        # Extract audio checkbox
        self.extract_audio = tk.BooleanVar(value=processing_config['extract_audio'])
        self.extract_audio_check = ctk.CTkCheckBox(row1_frame, text="Extract audio", variable=self.extract_audio)
        self.extract_audio_check.pack(side="left", padx=(10, 10))

        # Include timestamps checkbox
        self.include_timestamps = tk.BooleanVar(value=processing_config.get('include_timestamps', False))
        self.include_timestamps_check = ctk.CTkCheckBox(row1_frame, text="Include timestamps",
                                                        variable=self.include_timestamps)
        self.include_timestamps_check.pack(side="left", padx=(0, 20))

        # Batch size field (optional)
        ctk.CTkLabel(row1_frame, text="Batch size:").pack(side="left", padx=(10, 5))
        self.batch_size = tk.StringVar(value=processing_config.get('batch_size', ''))
        self.batch_size_entry = ctk.CTkEntry(row1_frame, textvariable=self.batch_size, width=60,
                                             placeholder_text="auto")
        self.batch_size_entry.pack(side="left", padx=(0, 10))

        # Batch size info tooltip
        batch_info_label = ctk.CTkLabel(row1_frame, text="ℹ️", text_color="gray",
                                        font=ctk.CTkFont(size=12))
        batch_info_label.pack(side="left", padx=(0, 5))
        # Create tooltip behavior
        batch_info_label.bind("<Enter>", lambda e: self._show_batch_tooltip(e, batch_info_label))
        batch_info_label.bind("<Leave>", lambda e: self._hide_batch_tooltip())

        # Row 2: TMDB ID and TV Series
        row2_frame = ctk.CTkFrame(self.settings_options_frame, fg_color="transparent")
        row2_frame.pack(fill="x", padx=10, pady=(5, 5))

        ctk.CTkLabel(row2_frame, text="TMDB ID:").pack(side="left", padx=(10, 5))
        self.tmdb_id = tk.StringVar(value=self.config_manager.get('tmdb_id', ''))
        self.tmdb_id_entry = ctk.CTkEntry(row2_frame, textvariable=self.tmdb_id, width=120)
        self.tmdb_id_entry.pack(side="left", padx=(0, 10))

        row2_5_frame = ctk.CTkFrame(self.settings_options_frame, fg_color="transparent")
        row2_5_frame.pack(fill="x", padx=10, pady=(5, 5))

        ctk.CTkLabel(row2_5_frame, text="Translation type:").pack(side="left", padx=(10, 5))
        self.translation_type = tk.StringVar(value=processing_config.get('translation_type', 'Default'))
        self.translation_type_combo = ctk.CTkComboBox(
            row2_5_frame,
            variable=self.translation_type,
            width=180,
            values=["Default", "Concise translation"]
        )
        self.translation_type_combo.pack(side="left", padx=(0, 10))

        # Fetch TMDB info button
        self.fetch_tmdb_button = ctk.CTkButton(
            row2_frame,
            text="🎬 Fetch",
            command=self.fetch_tmdb_info,
            width=80,
            height=28
        )
        self.fetch_tmdb_button.pack(side="left", padx=(0, 20))

        # TV Series checkbox
        self.is_tv_series = tk.BooleanVar(value=processing_config.get('is_tv_series', False))
        self.tv_series_check = ctk.CTkCheckBox(row2_frame, text="TV Series", variable=self.is_tv_series)
        self.tv_series_check.pack(side="left", padx=(10, 0))

        # Row 3: Overview
        row3_frame = ctk.CTkFrame(self.settings_options_frame, fg_color="transparent")
        row3_frame.pack(fill="x", padx=10, pady=(5, 5))

        ctk.CTkLabel(row3_frame, text="Overview:").pack(side="left", padx=(10, 5), anchor="n")
        self.overview_textbox = ctk.CTkTextbox(row3_frame, width=500, height=80, wrap="word")
        self.overview_textbox.pack(side="left", padx=(0, 10), fill="x", expand=True)

        # Row 4: Auto-fetch and Add translator info
        row4_frame = ctk.CTkFrame(self.settings_options_frame, fg_color="transparent")
        row4_frame.pack(fill="x", padx=10, pady=(5, 5))

        self.auto_fetch_tmdb = tk.BooleanVar(value=processing_config['auto_fetch_tmdb'])
        self.auto_fetch_check = ctk.CTkCheckBox(row4_frame, text="Auto-fetch TMDB ID when loading files",
                                                variable=self.auto_fetch_tmdb)
        self.auto_fetch_check.pack(side="left", padx=(10, 20))

        self.add_translator_info = tk.BooleanVar(value=processing_config.get('add_translator_info', True))
        self.add_translator_info_check = ctk.CTkCheckBox(row4_frame, text="Add translator info",
                                                         variable=self.add_translator_info)
        self.add_translator_info_check.pack(side="left", padx=(10, 0))

        # Row 5: Poster image
        row5_frame = ctk.CTkFrame(self.settings_options_frame, fg_color="transparent")
        row5_frame.pack(fill="x", padx=10, pady=(5, 10))

        ctk.CTkLabel(row5_frame, text="Poster:").pack(side="left", padx=(10, 5))
        self.image_label = ctk.CTkLabel(row5_frame, text="No image", width=100, height=150)
        self.image_label.pack(side="left", padx=(0, 10))

    def _show_batch_tooltip(self, event, widget):
        """Show tooltip for batch size info"""
        self.batch_tooltip = ctk.CTkToplevel(self.root)
        self.batch_tooltip.wm_overrideredirect(True)
        self.batch_tooltip.wm_geometry(f"+{event.x_root + 10}+{event.y_root + 10}")

        tooltip_label = ctk.CTkLabel(
            self.batch_tooltip,
            text="Number of subtitles per API request.\n"
                 "Leave empty for auto (100 for Gemini 2.0).\n"
                 "Lower values = more requests, higher = faster.",
            font=ctk.CTkFont(size=11),
            corner_radius=6,
            fg_color=("#404040", "#2b2b2b"),
            padx=10,
            pady=5
        )
        tooltip_label.pack()

    def _hide_batch_tooltip(self):
        """Hide batch size tooltip"""
        if hasattr(self, 'batch_tooltip') and self.batch_tooltip:
            self.batch_tooltip.destroy()
            self.batch_tooltip = None

    def _show_fetch_models_tooltip(self, event):
        """Show tooltip for fetch models button"""
        self.fetch_models_tooltip = ctk.CTkToplevel(self.root)
        self.fetch_models_tooltip.wm_overrideredirect(True)
        self.fetch_models_tooltip.wm_geometry(f"+{event.x_root + 10}+{event.y_root + 10}")

        tooltip_label = ctk.CTkLabel(
            self.fetch_models_tooltip,
            text="Fetch available models from Gemini API",
            font=ctk.CTkFont(size=11),
            corner_radius=6,
            fg_color=("#404040", "#2b2b2b"),
            padx=10,
            pady=5
        )
        tooltip_label.pack()

    def _hide_fetch_models_tooltip(self):
        """Hide fetch models tooltip"""
        if hasattr(self, 'fetch_models_tooltip') and self.fetch_models_tooltip:
            self.fetch_models_tooltip.destroy()
            self.fetch_models_tooltip = None

    def _check_app_update(self):
        """Check GitHub for a newer version of this GUI app (background thread)."""
        import threading

        def _fetch():
            try:
                import importlib.metadata
                import re
                import requests

                current = importlib.metadata.version('gst_gui')
                url = (
                    'https://raw.githubusercontent.com/'
                    'mkaflowski/Gemini-SRT-translator-GUI/main/pyproject.toml'
                )
                resp = requests.get(url, timeout=5)
                if resp.status_code != 200:
                    return
                match = re.search(r'^version\s*=\s*"([^"]+)"', resp.text, re.MULTILINE)
                if not match:
                    return
                latest = match.group(1)

                from packaging.version import Version
                if Version(latest) > Version(current):
                    self.root.after(0, lambda: self._notify_app_update(current, latest))
            except Exception:
                pass

        threading.Thread(target=_fetch, daemon=True).start()

    def _notify_app_update(self, current, latest):
        """Show a non-blocking app update notice (console + status bar)."""
        self.log_to_console(
            f"🆕 Dostępna nowa wersja aplikacji: {current} → {latest}\n"
            f"   Pobierz: https://github.com/mkaflowski/Gemini-SRT-translator-GUI"
        )
        if hasattr(self, 'status_var'):
            self.status_var.set(f"Update available: {current} → {latest} (see console)")

    def fetch_gemini_models(self):
        """Fetch available models from Gemini API"""
        api_key = self.gemini_api_key.get().strip()

        if not api_key:
            messagebox.showwarning("API Key Required",
                                   "Please enter your Gemini API key first.")
            return

        self.log_to_console("🔄 Fetching available Gemini models...")
        self.fetch_models_button.configure(state="disabled", text="⏳")

        # Run in background thread
        def fetch_models():
            try:
                import requests

                url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
                response = requests.get(url, timeout=10)

                if response.status_code == 200:
                    data = response.json()
                    models = data.get('models', [])

                    # Filter for generateContent supported models (translation capable)
                    translation_models = []
                    for model in models:
                        supported_methods = model.get('supportedGenerationMethods', [])
                        if 'generateContent' in supported_methods:
                            # Extract model name (remove 'models/' prefix)
                            model_name = model.get('name', '').replace('models/', '')
                            if model_name:
                                translation_models.append(model_name)

                    translation_models.reverse()

                    # Update UI in main thread
                    self.root.after(0, lambda: self._update_models_list(translation_models))

                elif response.status_code == 400:
                    self.root.after(0, lambda: self._on_fetch_models_error("Invalid API key"))
                elif response.status_code == 403:
                    self.root.after(0, lambda: self._on_fetch_models_error("API key doesn't have access"))
                else:
                    self.root.after(0, lambda: self._on_fetch_models_error(f"API error: {response.status_code}"))

            except requests.exceptions.Timeout:
                self.root.after(0, lambda: self._on_fetch_models_error("Request timeout"))
            except requests.exceptions.ConnectionError:
                self.root.after(0, lambda: self._on_fetch_models_error("Connection error"))
            except Exception as e:
                self.root.after(0, lambda: self._on_fetch_models_error(str(e)))

        thread = threading.Thread(target=fetch_models, daemon=True)
        thread.start()

    def _update_models_list(self, models):
        """Update the models combobox with fetched models"""
        self.fetch_models_button.configure(state="normal", text="🔄")

        if models:
            # Keep current selection if it's in the new list
            current_model = self.model.get()

            # Update combobox values
            self.model_combo.configure(values=models)

            # Restore selection or set to first model
            if current_model in models:
                self.model.set(current_model)
            elif models:
                self.model.set(models[0])

            self.log_to_console(f"✅ Found {len(models)} available models")
            self.log_to_console(f"   Top models: {', '.join(models[:5])}")
        else:
            self.log_to_console("⚠️ No translation-capable models found")

    def _on_fetch_models_error(self, error_msg):
        """Handle fetch models error"""
        self.fetch_models_button.configure(state="normal", text="🔄")
        self.log_to_console(f"❌ Failed to fetch models: {error_msg}")
        messagebox.showerror("Fetch Models Error", f"Could not fetch models:\n{error_msg}")

    def _create_bottom_bar(self):
        """Create the sticky bottom action bar (always visible, outside scroll area)"""
        self.bottom_bar = ctk.CTkFrame(self.root, corner_radius=0)
        self.bottom_bar.pack(side="bottom", fill="x")

        # Progress bar (hidden while idle)
        self.progress_bar = ctk.CTkProgressBar(self.bottom_bar, height=8)
        self.progress_bar.set(0)
        # not packed initially - shown when translation starts

        # Buttons container
        self.buttons_frame = ctk.CTkFrame(self.bottom_bar, fg_color="transparent")
        self.buttons_frame.pack(fill="x")

        # Translate Button
        self.translate_button = ctk.CTkButton(
            self.buttons_frame,
            text="🌐 TRANSLATE",
            command=self._start_translation,
            font=ctk.CTkFont(size=16, weight="bold"),
            height=50,
            fg_color=("green", "darkgreen"),
            hover_color=("lightgreen", "green")
        )
        self.translate_button.pack(fill="x", padx=20, pady=10)

        # Cancel Button
        self.cancel_button = ctk.CTkButton(
            self.buttons_frame,
            text="❌ CANCEL",
            command=self._cancel_translation,
            font=ctk.CTkFont(size=16, weight="bold"),
            height=50,
            fg_color=("red", "darkred"),
            hover_color=("lightcoral", "red")
        )

        # Status label at the very bottom
        self.status_var = tk.StringVar(value="Ready")
        self.status_bar = ctk.CTkLabel(
            self.bottom_bar,
            textvariable=self.status_var,
            font=ctk.CTkFont(size=12),
            fg_color="transparent"
        )
        self.status_bar.pack(fill="x", pady=(0, 6))

    def _show_progress_bar(self):
        """Show and reset the progress bar (call when translation starts)"""
        self.progress_bar.set(0)
        self.progress_bar.pack(fill="x", padx=20, pady=(10, 0), before=self.buttons_frame)

    def _hide_progress_bar(self):
        """Hide the progress bar (call when translation ends)"""
        self.progress_bar.pack_forget()

    def _on_translation_progress(self, completed, total):
        """Progress callback from CLIRunner (worker thread - marshal to UI thread)"""
        def update():
            if total > 0:
                self.progress_bar.set(completed / total)
                if 0 < completed < total:
                    self.status_var.set(f"Translating... {completed}/{total} done")
        self.root.after(0, update)

    def _on_line_progress(self, pair_index, total_pairs, current_line, total_lines):
        """Within-file line progress from CLIRunner (worker thread - marshal to UI thread).

        Combines completed pairs with the current file's line fraction so the
        bar advances smoothly across a multi-file batch without jumping back.
        """
        def update():
            if total_lines <= 0 or total_pairs <= 0:
                return
            file_fraction = current_line / total_lines
            overall = ((pair_index - 1) + file_fraction) / total_pairs
            overall = max(0.0, min(1.0, overall))
            self.progress_bar.set(overall)
            self.status_var.set(
                f"Translating... {current_line}/{total_lines} lines"
                + (f" (file {pair_index}/{total_pairs})" if total_pairs > 1 else "")
            )
        self.root.after(0, update)

    def _on_pair_status(self, pair, status):
        """Per-pair status callback from CLIRunner (worker thread - marshal to UI thread)"""
        status_map = {
            'translating': ('⏳ Translating...', 'translating'),
            'done': ('✅ Translated', 'done'),
            'failed': ('❌ Error', 'failed'),
            'cancelled': ('🛑 Cancelled', 'unchecked'),
        }
        text, tag = status_map.get(status, (status, 'matched'))

        subtitle_name = Path(pair['subtitle']).name if pair.get('subtitle') else None
        video_name = Path(pair['video']).name if pair.get('video') else None

        def update():
            for item in self.tree.get_children():
                values = list(self.tree.item(item, 'values'))
                if len(values) < 6:
                    continue
                if (subtitle_name and values[0] == subtitle_name) or \
                        (not subtitle_name and video_name and values[1] == video_name):
                    values[5] = text
                    self.tree.item(item, values=values, tags=(tag,))
                    break
        self.root.after(0, update)

    def _start_translation(self):
        """Start translation using translation manager"""
        selected_pairs = self.get_selected_pairs()
        if not selected_pairs:
            messagebox.showwarning("Warning",
                                   "No pairs selected for translation.")
            return

        config = self._get_current_config()
        self.translation_manager.start_translation(selected_pairs, config)

    def _cancel_translation(self):
        """Cancel translation using translation manager"""
        self.translation_manager.cancel_translation()

    def _get_current_config(self):
        """Get current configuration as dictionary"""
        # Get batch size value, validate it
        batch_size_str = self.batch_size.get().strip() if hasattr(self, 'batch_size') else ''
        batch_size = None
        if batch_size_str:
            try:
                batch_size = int(batch_size_str)
                if batch_size <= 0:
                    batch_size = None
            except ValueError:
                batch_size = None

        return {
            'gemini_api_key': self.gemini_api_key.get(),
            'gemini_api_key2': self.gemini_api_key2.get() if hasattr(self, 'gemini_api_key2') else '',
            'model': self.model.get(),
            'fallback_models': self.fallback_models.get() if hasattr(self, 'fallback_models') else '',
            'tmdb_api_key': self.tmdb_api_key.get(),
            'tmdb_id': self.tmdb_id.get(),
            'language': self.language.get(),
            'language_code': self.language_code.get() if hasattr(self, 'language_code') else 'pl',
            'extract_audio': self.extract_audio.get(),
            'include_timestamps': self.include_timestamps.get() if hasattr(self, 'include_timestamps') else False,
            'overview': self.overview_textbox.get("1.0", "end-1c").strip() if hasattr(self, 'overview_textbox') else '',
            'movie_title': self._get_movie_title_from_treeview(),
            'is_tv_series': self.is_tv_series.get() if hasattr(self, 'is_tv_series') else False,
            'add_translator_info': self.add_translator_info.get() if hasattr(self, 'add_translator_info') else True,
            'translation_type': self.translation_type.get() if hasattr(self, 'translation_type') else 'Default',
            'batch_size': batch_size,
        }

    # Keep these methods for the translation manager to call:
    def show_cancel_button(self):
        """Show cancel button and hide translate button (translation started)"""
        def update():
            self.translate_button.pack_forget()
            self.cancel_button.pack(fill="x", padx=20, pady=10)
            self._show_progress_bar()
        # May be called from the translation worker thread - marshal to UI thread
        self.root.after(0, update)

    def show_translate_button(self):
        """Show translate button and hide cancel button (translation ended)"""
        def update():
            self.cancel_button.pack_forget()
            self.translate_button.pack(fill="x", padx=20, pady=10)
            self._hide_progress_bar()
        self.root.after(0, update)

    def _hide_dropdown_menus(self):
        """No-op: config sections are permanent tabs now (kept for compatibility)"""
        pass

    def _on_root_resize(self, event):
        """Give extra vertical space to the file tree when the window grows (debounced)"""
        if event.widget is not self.root:
            return
        if self._tree_resize_job:
            self.root.after_cancel(self._tree_resize_job)
        self._tree_resize_job = self.root.after(150, self._apply_tree_height)

    def _apply_tree_height(self):
        """Resize tree frame based on current window height"""
        self._tree_resize_job = None
        try:
            window_height = self.root.winfo_height()
            # ~700px is taken by drop area, console, config tabs and bottom bar
            new_height = max(200, window_height - 700)
            self.tree_frame.configure(height=new_height)
        except Exception:
            pass

    def _hide_scrollbar_initially(self):
        """Hide scrollbar on app startup for a clean initial appearance"""
        try:
            # Access and hide the scrollbar immediately on startup
            if hasattr(self.scrollable_frame, '_scrollbar'):
                scrollbar = self.scrollable_frame._scrollbar
                if scrollbar:
                    scrollbar.grid_remove()  # Hide scrollbar by default
                    print("🎨 Scrollbar hidden on startup for clean appearance")
        except Exception as e:
            # If we can't hide it, that's okay - it will still work normally
            pass

    def _manage_scrollbar_visibility(self):
        """Hide/show scrollbar based on content height"""
        try:
            # Update layout to get accurate measurements
            self.root.update_idletasks()

            # Access the internal scrollbar of CTkScrollableFrame
            if hasattr(self.scrollable_frame, '_scrollbar'):
                scrollbar = self.scrollable_frame._scrollbar

                # Get the internal canvas
                if hasattr(self.scrollable_frame, '_parent_canvas'):
                    canvas = self.scrollable_frame._parent_canvas
                    canvas.update_idletasks()

                    # Get canvas dimensions
                    canvas_height = canvas.winfo_height()

                    # Get scrollable region
                    scroll_region = canvas.cget("scrollregion")
                    if scroll_region:
                        # Parse scroll region (format: "x1 y1 x2 y2")
                        coords = scroll_region.split()
                        if len(coords) >= 4:
                            content_height = float(coords[3])

                            # Check if scrolling is needed (with small buffer)
                            if content_height > canvas_height + 10:  # 10px buffer
                                # Content exceeds canvas - show scrollbar
                                if scrollbar:
                                    scrollbar.grid()  # Make it visible
                            else:
                                # Content fits - hide scrollbar
                                if scrollbar:
                                    scrollbar.grid_remove()  # Keep it hidden

        except Exception as e:
            # If there's an error accessing internals, just leave scrollbar as-is
            pass

        # Check again after a delay
        self.root.after(2000, self._manage_scrollbar_visibility)  # Check every 2 seconds

    def toggle_api_section(self):
        """Switch to the API keys tab (sections are tabs now)"""
        self.config_tabview.set("API keys")

    def toggle_settings_section(self):
        """Switch to the Settings tab (sections are tabs now)"""
        self.config_tabview.set("Settings")

    def load_image(self, url, width=100, height=150):
        """Load and display image using CTkImage for proper scaling"""
        try:
            response = requests.get(url)
            response.raise_for_status()

            # Open image from bytes
            image = Image.open(BytesIO(response.content))

            # Create CTkImage instead of PhotoImage for proper CustomTkinter support
            ctk_image = ctk.CTkImage(
                light_image=image,  # Image for light mode
                dark_image=image,  # Same image for dark mode
                size=(width, height)  # CTkImage handles scaling automatically
            )

            # Update the label with CTkImage
            self.image_label.configure(image=ctk_image, text="")
            # Store reference to prevent garbage collection
            self.image_label.image = ctk_image

        except Exception as e:
            print(f"Error loading image: {e}")
            self.image_label.configure(text="Image not available", image=None)

    def on_closing(self):
        """Handle window closing event"""
        # Check if translation is running using the translation manager
        if self.translation_manager.is_running():
            if messagebox.askyesno("Cancel processing",
                                   "Processing subtitles...\n"
                                   "Do you want to stop?"):
                # Use translation manager to cancel (handles all threading complexity)
                self.translation_manager.cancel_translation()
            else:
                # User chose not to stop - don't close the window
                return

        # Save configuration before closing
        self.save_current_config()
        self.log_to_console("💾 Configuration saved")

        # Close the window after a short delay
        self.root.after(100, self.root.destroy)

    def save_current_config(self):
        """Save current configuration to config manager"""
        config_updates = {
            'gemini_api_key': self.gemini_api_key.get() if hasattr(self, 'gemini_api_key') else '',
            'gemini_api_key2': self.gemini_api_key2.get() if hasattr(self, 'gemini_api_key2') else '',
            'model': self.model.get() if hasattr(self, 'model') else 'gemini-3-flash',
            'fallback_models': self.fallback_models.get() if hasattr(self, 'fallback_models') else '',
            'tmdb_api_key': self.tmdb_api_key.get() if hasattr(self, 'tmdb_api_key') else '',
            'tmdb_id': self.tmdb_id.get() if hasattr(self, 'tmdb_id') else '',
            'api_expanded': self.api_expanded.get() if hasattr(self, 'api_expanded') else False,
            'settings_expanded': self.settings_expanded.get() if hasattr(self, 'settings_expanded') else False,
            'language': self.language.get() if hasattr(self, 'language') else 'Polish',
            'language_code': self.language_code.get() if hasattr(self, 'language_code') else 'pl',
            'extract_audio': self.extract_audio.get() if hasattr(self, 'extract_audio') else False,
            'include_timestamps': self.include_timestamps.get() if hasattr(self, 'include_timestamps') else False,
            'auto_fetch_tmdb': self.auto_fetch_tmdb.get() if hasattr(self, 'auto_fetch_tmdb') else True,
            'is_tv_series': self.is_tv_series.get() if hasattr(self, 'is_tv_series') else False,
            'add_translator_info': self.add_translator_info.get() if hasattr(self, 'add_translator_info') else True,
            'translation_type': self.translation_type.get() if hasattr(self, 'translation_type') else 'Default',
            'batch_size': self.batch_size.get() if hasattr(self, 'batch_size') else '',
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
            self.log_to_console(
                f"   🔑 Gemini API Key 2: {'✅ Saved' if hasattr(self, 'gemini_api_key2') and self.gemini_api_key2.get().strip() else '❌ Not configured (optional)'}")
            self.log_to_console(f"   🎬 TMDB API: {'✅ Saved' if summary['has_tmdb_key'] else '❌ Missing (optional)'}")
            self.log_to_console(f"   🆔 TMDB ID: {'✅ Saved' if summary['has_tmdb_id'] else '❌ Missing (optional)'}")
            self.log_to_console(f"   🌐 Language: {summary['language']}")
            if hasattr(self, 'language_code'):
                self.log_to_console(f"   🏷️ Language code: {self.language_code.get()}")
            self.log_to_console(f"   🎵 Extract audio: {'✅ Enabled' if summary['extract_audio'] else '❌ Disabled'}")
            if hasattr(self, 'batch_size') and self.batch_size.get().strip():
                self.log_to_console(f"   📦 Batch size: {self.batch_size.get()}")
            self.log_to_console("─" * 50)

    def ensure_front(self):
        """Additional security to ensure window is in front"""
        try:
            # For macOS - try AppleScript
            if os.name == "posix":
                os.system('''/usr/bin/osascript -e 'tell app "Finder" to set frontmost of process "Python" to true' ''')
        except:
            pass

        # Alternative methods
        try:
            self.root.call('wm', 'attributes', '.', '-modified', False)
            self.root.focus_set()
        except tk.TclError:
            pass


        except (ImportError, tk.TclError, Exception) as e:
            self.log_to_console(f"ℹ️  Drag & Drop unavailable: {type(e).__name__}")
            self.log_to_console("🖱️  Use button to select file")
            self.log_to_console("💡 Try: pip uninstall tkinterdnd2 && pip install tkinterdnd2")

    def process_dropped_item(self, path):
        """Process dropped/selected item (called by DropAreaHandler)"""
        # path is already validated by the handler, so we can trust it exists
        self.log_to_console(f"Processing: {path}")

        if path.is_file():
            self._process_single_file(path)
        elif path.is_dir():
            self._process_folder(path)

    def _detect_tv_series_pattern(self, filename):
        """
        Detect if filename contains TV series patterns like S01E01, S12E03, etc.
        Returns True if TV series pattern is found, False otherwise.
        """
        if not filename:
            return False

        # Convert to lowercase for case-insensitive matching
        filename_lower = filename.lower()

        # Common TV series patterns
        tv_patterns = [
            r's\d{1,2}e\d{1,2}',  # S01E01, S12E03, S1E1, etc.
            r'season\s*\d+',  # Season 1, Season 12, etc.
            r'episode\s*\d+',  # Episode 1, Episode 12, etc.
            r'\d{1,2}x\d{1,2}',  # 1x01, 12x03, etc.
        ]

        # Check each pattern
        for pattern in tv_patterns:
            if re.search(pattern, filename_lower):
                return True

        return False

    def _auto_detect_and_set_tv_series(self, files_to_check):
        """
        Check files for TV series patterns and automatically set TV Series checkbox.
        files_to_check can be a list of filenames or file paths.
        """
        tv_series_detected = False
        detected_patterns = []

        # Check each file for TV series patterns
        for file_item in files_to_check:
            # Handle both Path objects and strings
            filename = file_item.name if hasattr(file_item, 'name') else str(file_item)

            if self._detect_tv_series_pattern(filename):
                tv_series_detected = True
                # Extract the pattern that was found for logging
                filename_lower = filename.lower()
                for pattern in [r's\d{1,2}e\d{1,2}', r'season\s*\d+', r'episode\s*\d+', r'\d{1,2}x\d{1,2}']:
                    match = re.search(pattern, filename_lower)
                    if match:
                        detected_patterns.append(match.group())
                        break

        # If TV series pattern detected, enable checkbox and log
        if tv_series_detected:
            self.is_tv_series.set(True)
            patterns_text = ", ".join(set(detected_patterns))  # Remove duplicates
            self.log_to_console(f"📺 TV Series detected! Found patterns: {patterns_text}")
            self.log_to_console("✅ Automatically enabled 'TV Series' checkbox")
            return True
        else:
            # Reset to movie mode if no TV patterns found
            self.is_tv_series.set(False)
            self.log_to_console("🎬 No TV series patterns detected - set to Movie mode")
            return False

    def _load_transcription_if_exists(self, folder_path):
        """
        Check if transcription.txt exists in folder and load its content to overview.

        Args:
            folder_path: Path to the folder to check for transcription.txt
        """
        try:
            transcription_file = Path(folder_path) / "transcription.txt"

            if transcription_file.exists():
                self.log_to_console(f"📄 Found transcription.txt in folder")

                # Read the transcription file
                with open(transcription_file, 'r', encoding='utf-8', errors='ignore') as f:
                    transcription_content = f.read().strip()

                if transcription_content:
                    # Update the overview field with transcription content
                    self._update_overview_field(transcription_content)

                    # Log success with preview
                    preview = transcription_content[:100] + "..." if len(
                        transcription_content) > 100 else transcription_content
                    self.log_to_console(f"✅ Loaded transcription to overview: {preview}")
                else:
                    self.log_to_console(f"⚠️ transcription.txt is empty")

        except Exception as e:
            self.log_to_console(f"⚠️ Error loading transcription.txt: {e}")

    def _process_single_file(self, file_path):
        """Process a single file"""
        self.log_to_console("File detected")
        self.clear_treeview()
        self.current_folder_path = file_path.parent

        # Auto-detect TV series from single file
        self._auto_detect_and_set_tv_series([file_path])

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
                             values=("No match", file_path.name, title, year_display,
                                     str(self.current_folder_path), "🎬 Video file"),
                             tags=('video_only',))
        else:
            # Other file type
            item_id = self.tree.insert('', 'end', text='☑️ Single file',
                                       values=(file_path.name if file_type == 'text' else "N/A",
                                               file_path.name if file_type == 'video' else "N/A",
                                               title, year_display, str(self.current_folder_path),
                                               f"📄 {file_type.title()}"),
                                       tags=('no_match',))

            if file_type == 'video':
                self._auto_probe_embedded([(item_id, str(file_path))])

        # Check for transcription.txt in the file's folder
        self._load_transcription_if_exists(self.current_folder_path)
        # Auto-fetch TMDB ID after adding to TreeView (with small delay to ensure UI is updated)
        self.root.after(100, lambda: self._auto_fetch_tmdb_for_movie(title, year_display))

    def _process_folder(self, folder_path):
        """Process a folder"""
        self.log_to_console("Folder detected - scanning contents...")
        found_files = scan_folder_for_files(folder_path)

        # Collect all files for TV series detection
        all_files = []
        for file_type, files in found_files.items():
            all_files.extend(files)

        # Auto-detect TV series from all files in folder
        self._auto_detect_and_set_tv_series(all_files)

        self.add_subtitle_matches_to_treeview(found_files, folder_path)

        # Check for transcription.txt in the folder
        self._load_transcription_if_exists(folder_path)
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
        if hasattr(self, 'overview_textbox'):
            self._clear_overview_field()
        if hasattr(self, 'tmdb_id'):
            self.tmdb_id.set('')  # Clear TMDB ID for new movie

        self._update_selected_count()

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
            # Uncheck - remove the checkmark and any following space
            if current_text.startswith('☑️ '):
                new_text = '☐ ' + current_text[3:]  # Remove "☑️ " and add "☐ "
            else:
                new_text = '☐' + current_text[1:]  # Remove just "☑️" and add "☐"

            new_values = list(values)
            if len(new_values) >= 6:
                original_status = new_values[5]
                if not original_status.startswith('⏸️'):
                    new_values[5] = f"⏸️ Skipped ({original_status})"
            self.tree.item(item, text=new_text, values=new_values, tags=('unchecked',))

        elif current_text.startswith('☐'):
            # Check - remove the unchecked box and any following space
            if current_text.startswith('☐ '):
                new_text = '☑️ ' + current_text[2:]  # Remove "☐ " and add "☑️ "
            else:
                new_text = '☑️' + current_text[1:]  # Remove just "☐" and add "☑️"

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
            # Add checkbox - ensure proper spacing
            new_text = '☑️ ' + current_text
            self.tree.item(item, text=new_text)

        self._update_selected_count()

    def select_all_items(self):
        """Check all items in the tree"""
        for item in self.tree.get_children():
            if self.tree.item(item, 'text').startswith('☐'):
                self.toggle_item_checkbox(item)
        self._update_selected_count()

    def deselect_all_items(self):
        """Uncheck all items in the tree"""
        for item in self.tree.get_children():
            if self.tree.item(item, 'text').startswith('☑️'):
                self.toggle_item_checkbox(item)
        self._update_selected_count()

    def _update_selected_count(self):
        """Update the 'N of M selected' label above the tree"""
        if not hasattr(self, 'selected_count_label'):
            return
        items = self.tree.get_children()
        total = len(items)
        selected = sum(1 for i in items if self.tree.item(i, 'text').startswith('☑️'))
        self.selected_count_label.configure(
            text=f"{selected} of {total} selected" if total else ""
        )

    # ---------- Embedded subtitle tracks ----------

    def _video_path_for_item(self, item):
        """Full video path for a tree row, or None"""
        values = self.tree.item(item, 'values')
        if len(values) < 5 or not values[1] or values[1] in ('None', 'N/A'):
            return None
        return str(Path(values[4]) / values[1])

    def _probe_tracks_cached(self, video_path):
        """Probe subtitle tracks with caching (worker thread safe)"""
        if video_path not in self._embedded_tracks_cache:
            self._embedded_tracks_cache[video_path] = probe_subtitle_tracks(video_path)
        return self._embedded_tracks_cache[video_path]

    def choose_embedded_subtitles(self):
        """Detect embedded subtitle tracks in selected videos and let the user pick one"""
        items = [
            item for item in self.tree.get_children()
            if self.tree.item(item, 'text').startswith('☑️') and self._video_path_for_item(item)
        ]
        if not items:
            messagebox.showinfo("Embedded subtitles",
                                "No selected rows with a video file.\n"
                                "Select rows containing a video (e.g. MKV) first.")
            return

        first_video = self._video_path_for_item(items[0])
        self.status_var.set("Scanning embedded subtitle tracks...")
        self.embedded_subs_button.configure(state="disabled")
        self.log_to_console(f"🎞 Scanning subtitle tracks in: {Path(first_video).name}")

        def probe():
            tracks = self._probe_tracks_cached(first_video)

            def show():
                self.embedded_subs_button.configure(state="normal")
                self.status_var.set("Ready")
                if not tracks:
                    self.log_to_console("🎞 No embedded subtitle tracks found")
                    messagebox.showinfo("Embedded subtitles",
                                        f"No embedded subtitle tracks found in:\n{Path(first_video).name}")
                    return
                self._show_track_dialog(tracks, items, first_video)

            self.root.after(0, show)

        threading.Thread(target=probe, daemon=True).start()

    def _show_track_dialog(self, tracks, items, first_video):
        """Dialog with a radio list of subtitle tracks found in the first selected video"""
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Choose embedded subtitle track")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.geometry(f"+{self.root.winfo_rootx() + 300}+{self.root.winfo_rooty() + 200}")

        header = f"Subtitle tracks in {Path(first_video).name}"
        if len(items) > 1:
            header += f"\n(applied to all {len(items)} selected videos, matched by language)"
        ctk.CTkLabel(dialog, text=header, font=ctk.CTkFont(size=13, weight="bold"),
                     justify="left").pack(anchor="w", padx=20, pady=(15, 10))

        # Preselect: default text track, else first text track
        text_tracks = [t for t in tracks if t['text_based']]
        preselected = next((t for t in text_tracks if t['default']), text_tracks[0] if text_tracks else None)
        choice = tk.IntVar(value=preselected['type_index'] if preselected else -1)

        for track in tracks:
            rb = ctk.CTkRadioButton(
                dialog,
                text=format_track_label(track),
                variable=choice,
                value=track['type_index'],
                state="normal" if track['text_based'] else "disabled"
            )
            rb.pack(anchor="w", padx=25, pady=4)

        buttons = ctk.CTkFrame(dialog, fg_color="transparent")
        buttons.pack(fill="x", padx=20, pady=(15, 15))

        def on_extract():
            selected = next((t for t in tracks if t['type_index'] == choice.get()), None)
            dialog.destroy()
            if selected:
                self._extract_tracks_for_items(items, selected)

        ctk.CTkButton(buttons, text="Extract && use", command=on_extract,
                      fg_color=("green", "darkgreen")).pack(side="left", padx=(0, 10))
        ctk.CTkButton(buttons, text="Cancel", command=dialog.destroy,
                      fg_color="transparent", border_width=1,
                      text_color=("gray30", "gray70")).pack(side="left")

        if not text_tracks:
            ctk.CTkLabel(dialog, text="⚠️ Only bitmap tracks found - they cannot be converted to SRT",
                         text_color="orange").pack(padx=20, pady=(0, 10))

    def _extract_tracks_for_items(self, items, wanted_track):
        """Extract the chosen track from every selected video (background thread)"""
        self.status_var.set("Extracting embedded subtitles...")
        self.embedded_subs_button.configure(state="disabled")

        def worker():
            ok_count = 0
            for item in items:
                video_path = self._video_path_for_item(item)
                if not video_path:
                    continue

                tracks = self._probe_tracks_cached(video_path)
                track = pick_matching_track(tracks, wanted_track)
                if not track:
                    self.log_to_console(f"⚠️ No matching text subtitle track in: {Path(video_path).name}")
                    continue

                lang = track['language'] or f"track{track['type_index'] + 1}"
                stem = Path(video_path).stem
                output = str(Path(video_path).parent / f"{stem}_extracted_{lang}.srt")
                result = extract_subtitle_track(video_path, track['type_index'], output)

                if result:
                    ok_count += 1
                    self.log_to_console(
                        f"🎞 Extracted '{format_track_label(track)}' → {Path(result).name}")

                    def update_row(item=item, srt_name=Path(result).name):
                        if not self.tree.exists(item):
                            return
                        values = list(self.tree.item(item, 'values'))
                        values[0] = srt_name
                        values[5] = "✅ Matched"
                        self.tree.item(item, values=values, tags=('matched',))
                    self.root.after(0, update_row)
                else:
                    self.log_to_console(f"❌ Extraction failed for: {Path(video_path).name}")

            def finish():
                self.embedded_subs_button.configure(state="normal")
                self.status_var.set(f"Extracted subtitles from {ok_count}/{len(items)} videos")
            self.root.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _auto_probe_embedded(self, video_items):
        """Background: check videos without subtitles for embedded tracks, update status"""
        def worker():
            found_any = False
            for item, video_path in video_items:
                tracks = self._probe_tracks_cached(video_path)
                text_count = sum(1 for t in tracks if t['text_based'])
                if not text_count:
                    continue
                found_any = True

                def update_row(item=item, count=text_count):
                    if not self.tree.exists(item):
                        return
                    values = list(self.tree.item(item, 'values'))
                    # Only annotate rows that still have no subtitle file
                    if values[0] in ('', 'None', 'N/A'):
                        values[5] = f"🎞 {count} embedded subs"
                        self.tree.item(item, values=values)
                self.root.after(0, update_row)

            if found_any:
                self.root.after(0, lambda: self.log_to_console(
                    "🎞 Embedded subtitle tracks detected - select rows and click "
                    "'🎞 Embedded subs' to choose which one to translate"))

        threading.Thread(target=worker, daemon=True).start()

    # ---------- Video description / transcription (gst_transcribe) ----------

    def describe_video(self):
        """Analyze the selected video with Gemini and load the result into Overview"""
        if self.translation_manager.is_running():
            messagebox.showinfo("Describe video", "Wait for the current translation to finish first.")
            return

        api_key = self.gemini_api_key.get().strip()
        if not api_key:
            messagebox.showwarning("API Key Required", "Enter your Gemini API key first (API keys tab).")
            return

        items = [
            item for item in self.tree.get_children()
            if self.tree.item(item, 'text').startswith('☑️') and self._video_path_for_item(item)
        ]
        if not items:
            messagebox.showinfo("Describe video",
                                "No selected rows with a video file.\n"
                                "Select a row containing a video first.")
            return

        video_path = self._video_path_for_item(items[0])
        if not os.path.exists(video_path):
            messagebox.showerror("Describe video", f"Video file not found:\n{video_path}")
            return

        self._show_describe_dialog(video_path, extra_count=len(items) - 1)

    def _show_describe_dialog(self, video_path, extra_count=0):
        """Options dialog before running the video description"""
        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Describe video")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.geometry(f"+{self.root.winfo_rootx() + 300}+{self.root.winfo_rooty() + 200}")

        header = f"Analyze: {Path(video_path).name}"
        if extra_count > 0:
            header += f"\n(only this first video; {extra_count} other selected will be ignored)"
        ctk.CTkLabel(dialog, text=header, font=ctk.CTkFont(size=13, weight="bold"),
                     justify="left").pack(anchor="w", padx=20, pady=(15, 10))

        ctk.CTkLabel(
            dialog,
            text="Uploads the video to Gemini and generates a description + transcription,\n"
                 "then loads it into Overview (used as translation context).\n"
                 "The result is also saved as transcription.txt next to the video.",
            justify="left", text_color="gray", font=ctk.CTkFont(size=11)
        ).pack(anchor="w", padx=20, pady=(0, 10))

        preprocess_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            dialog, text="Preprocess (2x speed + 360p) — much faster upload/analysis",
            variable=preprocess_var
        ).pack(anchor="w", padx=25, pady=(0, 10))

        seg_row = ctk.CTkFrame(dialog, fg_color="transparent")
        seg_row.pack(anchor="w", fill="x", padx=25, pady=(0, 5))
        ctk.CTkLabel(seg_row, text="Split long videos every (min):").pack(side="left")
        seg_var = tk.StringVar(value="30")
        ctk.CTkEntry(seg_row, textvariable=seg_var, width=60).pack(side="left", padx=(8, 0))

        buttons = ctk.CTkFrame(dialog, fg_color="transparent")
        buttons.pack(fill="x", padx=20, pady=(15, 15))

        def on_start():
            try:
                seg_minutes = int(seg_var.get().strip() or "30")
                if seg_minutes <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showwarning("Describe video", "Segment length must be a positive number of minutes.")
                return
            dialog.destroy()
            self._run_describe(
                video_path,
                preprocess=preprocess_var.get(),
                segment_duration=seg_minutes * 60,
                lang=self.language.get().strip() or "Polish",
            )

        ctk.CTkButton(buttons, text="Start", command=on_start,
                      fg_color=("green", "darkgreen")).pack(side="left", padx=(0, 10))
        ctk.CTkButton(buttons, text="Cancel", command=dialog.destroy,
                      fg_color="transparent", border_width=1,
                      text_color=("gray30", "gray70")).pack(side="left")

    def _run_describe(self, video_path, preprocess, segment_duration, lang):
        """Run video analysis in a background thread, streaming progress to the console"""
        api_key = self.gemini_api_key.get().strip()
        self.status_var.set("Describing video... (this can take a while)")
        self.describe_video_button.configure(state="disabled")
        self.log_to_console("─" * 40)
        self.log_to_console(f"📝 Describing video: {Path(video_path).name}")
        self.log_to_console(f"   Preprocess: {preprocess} | Segment: {segment_duration // 60} min | Lang: {lang}")

        def worker():
            redirect = _ConsoleStdout(self.log_to_console)
            error = None
            try:
                import contextlib
                with contextlib.redirect_stdout(redirect):
                    result = vdesc.analyze_video(
                        video_path,
                        api_key,
                        segment_duration=segment_duration,
                        preprocess=preprocess,
                        lang=lang,
                    )
                redirect.flush()

                analysis = result.get("analysis", "").strip()

                # Save transcription.txt next to the video (same as the CLI does)
                out_file = Path(video_path).parent / "transcription.txt"
                try:
                    with open(out_file, "w", encoding="utf-8") as f:
                        f.write(f"VIDEO ANALYSIS: {result['file_name']}\n")
                        f.write(f"Model: {result['model']}\n")
                        f.write(f"Language: {result['lang']}\n")
                        if result.get('segments', 1) > 1:
                            f.write(f"Processed in {result['segments']} parts\n")
                        f.write("=" * 60 + "\n\n")
                        f.write(analysis)
                except Exception as e:
                    self.root.after(0, lambda e=e: self.log_to_console(f"⚠️ Could not write transcription.txt: {e}"))
            except Exception as e:
                error = e
                redirect.flush()

            def finish():
                self.describe_video_button.configure(state="normal")
                if error:
                    self.status_var.set("Video description failed")
                    self.log_to_console(f"❌ Video description failed: {error}")
                    messagebox.showerror("Describe video", f"Analysis failed:\n{error}")
                    return
                self._update_overview_field(analysis)
                self.config_tabview.set("Settings")
                self.status_var.set("Video description done - loaded into Overview")
                self.log_to_console(f"✅ Description loaded into Overview and saved to transcription.txt")
            self.root.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

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
        videos_to_probe = []
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

            item_id = self.tree.insert('', 'end', text=item_text,
                                       values=(subtitle_name, video_name, title, year_display, str(folder_path),
                                               match['status']),
                                       tags=(match['tag'],))

            # Videos without a matched subtitle: check for embedded tracks in background
            # (scan returns paths relative to the folder, so join with folder_path)
            if match['video'] and not match['subtitle']:
                videos_to_probe.append((item_id, str(Path(folder_path) / match['video'])))

        # Log summary
        self._log_matching_summary(matches)
        self._update_selected_count()

        if videos_to_probe:
            self._auto_probe_embedded(videos_to_probe)

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
                    folder = values[4] if values[4] != "None" else ""
                    selected_pairs.append({
                        'subtitle': subtitle_file,
                        'video': video_file,
                        'folder': folder
                    })

        return selected_pairs

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
            # BUT only if overview is currently empty (preserve transcription.txt)
            overview = movie.get('overview', '')
            current_overview = self.overview_textbox.get("1.0", "end-1c").strip() if hasattr(self,
                                                                                             'overview_textbox') else ""

            if not current_overview and overview:
                self._update_overview_field(overview)
            elif current_overview:
                self.log_to_console("ℹ️ Overview already filled - keeping existing content")

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
        """Update the overview textbox field"""
        if hasattr(self, 'overview_textbox'):
            self.overview_textbox.delete("1.0", "end")
            if overview_text:
                self.overview_textbox.insert("1.0", overview_text)

    def _clear_overview_field(self):
        """Clear the overview textbox field"""
        if hasattr(self, 'overview_textbox'):
            self.overview_textbox.delete("1.0", "end")

    def _update_tmdb_id_field(self, movie, silent=False):
        """Update TMDB ID field with found movie (runs in main thread)"""
        try:
            # Update the TMDB ID field
            movie_id = str(movie['id'])
            self.tmdb_id.set(movie_id)

            # Update the overview field ONLY if it's currently empty
            # (preserve transcription.txt content if already loaded)
            overview = movie.get('overview', '')
            current_overview = self.overview_textbox.get("1.0", "end-1c").strip() if hasattr(self,
                                                                                             'overview_textbox') else ""

            if not current_overview and overview:
                self._update_overview_field(overview)
            elif current_overview and overview:
                self.log_to_console("ℹ️ Overview already filled (transcription.txt?) - keeping existing content")

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
        """Thread-safe logging to console with throttling"""
        with self._log_lock:
            self._log_buffer.append(message)

            # Schedule UI update if not already scheduled
            if not self._log_scheduled:
                self._log_scheduled = True
                self.root.after(50, self._flush_log_buffer)  # 50ms throttle

    def clear_console(self):
        """Clear all console output"""
        if hasattr(self, 'console_text'):
            try:
                self.console_text.configure(state='normal')
                self.console_text.delete('1.0', 'end')
            except Exception:
                pass

    def _flush_log_buffer(self):
        """Flush log buffer to console (runs in main thread)"""
        with self._log_lock:
            if not self._log_buffer:
                self._log_scheduled = False
                return

            # Get all messages and clear buffer
            messages = self._log_buffer.copy()
            self._log_buffer.clear()
            self._log_scheduled = False

        if not hasattr(self, 'console_text'):
            return

        try:
            # Disable auto-scroll temporarily for performance
            self.console_text.configure(state='normal')

            # Add all messages at once
            text_to_add = '\n'.join(messages) + '\n'
            self.console_text.insert('end', text_to_add)

            # Limit total lines to prevent memory issues
            total_lines = int(self.console_text.index('end-1c').split('.')[0])
            if total_lines > self._max_console_lines:
                # Remove oldest lines
                lines_to_remove = total_lines - self._max_console_lines
                self.console_text.delete('1.0', f'{lines_to_remove}.0')

            # Scroll to end
            self.console_text.see('end')

        except Exception as e:
            print(f"Console log error: {e}")
