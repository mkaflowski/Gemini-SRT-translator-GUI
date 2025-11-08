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


class DragDropGUI:
    """Main GUI class that coordinates all handlers"""

    def __init__(self, root):
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
        # Create a scrollable frame with default styling
        self.scrollable_frame = ctk.CTkScrollableFrame(
            self.root,
            width=1160,  # Slightly smaller than window
            height=860,  # Slightly smaller than window
            corner_radius=0,
            fg_color="transparent"  # Make it blend with the background
        )
        self.scrollable_frame.pack(fill="both", expand=True, padx=20, pady=20)

        # Use scrollable_frame as the main container
        self.main_frame = self.scrollable_frame

        # Create UI handlers (they'll now be inside the scrollable frame)
        self._create_drop_area()
        self._create_treeview()
        self._create_console()
        self._create_config_sections()
        self._create_action_buttons()
        self._create_status_bar()

        # Hide scrollbar by default on startup
        self.root.after(100, self._hide_scrollbar_initially)
        # Then start monitoring for when it's needed
        self.root.after(500, self._manage_scrollbar_visibility)

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
        # TreeView section label with reduced margins
        treeview_label = ctk.CTkLabel(self.main_frame, text="Found files:", font=ctk.CTkFont(size=14, weight="bold"))
        treeview_label.pack(anchor="w", pady=(0, 5))  # Reduced from (0, 10) to (0, 5)

        # Frame for TreeView (still using tkinter TreeView as CustomTkinter doesn't have equivalent)
        self.tree_frame = ctk.CTkFrame(self.main_frame, height=200)  # Fixed height to make it 50% shorter
        self.tree_frame.pack(fill="x", pady=(0, 10))  # Reduced from (0, 20) to (0, 10)
        self.tree_frame.pack_propagate(False)  # Prevent frame from shrinking

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
                       background="#2b2b2b",           # Dark background
                       foreground="#ffffff",           # White text
                       fieldbackground="#2b2b2b",      # Dark field background
                       borderwidth=0,                  # No borders
                       relief="flat",                  # Flat appearance
                       rowheight=25)                   # Row height

        # Configure TreeView headings
        style.configure("Dark.Treeview.Heading",
                       background="#404040",           # Dark gray headers
                       foreground="#ffffff",           # White text
                       borderwidth=1,                  # Thin border
                       relief="solid",                 # Solid border style
                       font=('Arial', 9, 'bold'))      # Bold font

        # Configure selection and hover effects
        style.map("Dark.Treeview",
                 background=[('selected', '#1f538d'),    # Blue when selected
                            ('active', '#404040')],      # Gray when hovered
                 foreground=[('selected', '#ffffff'),    # White text when selected
                            ('active', '#ffffff')])      # White text when hovered

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

    def _create_console(self):
        """Create the console output area"""
        console_label = ctk.CTkLabel(self.main_frame, text="Console output:", font=ctk.CTkFont(size=14, weight="bold"))
        console_label.pack(anchor="w", pady=(0, 5))  # Reduced from (0, 10) to (0, 5)

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
        self.console_text = scrolledtext.ScrolledText(
            self.console_container,
            height=6,  # Reduced from 12 to 6 lines (50% shorter)
            bg='#2b2b2b',
            fg='#ffffff',
            font=('Consolas', 10),
            insertbackground='white'
        )
        self.console_text.pack(fill="both", expand=True)

    def _create_config_sections(self):
        """Create expandable configuration sections"""
        # Configuration Sections Container
        self.config_container = ctk.CTkFrame(self.main_frame)
        self.config_container.pack(fill="x", pady=(0, 20))

        # Get UI config
        ui_config = self.config_manager.get_ui_config()

        # Headers frame for both API and Settings buttons
        self.headers_frame = ctk.CTkFrame(self.config_container)
        self.headers_frame.pack(fill="x", padx=10, pady=10)

        # API Configuration Section - Expandable
        self.api_expanded = tk.BooleanVar(value=ui_config['api_expanded'])
        self.expand_api_button = ctk.CTkButton(
            self.headers_frame,
            text="▶ Show API options",
            command=self.toggle_api_section,
            width=150,
            height=32
        )
        self.expand_api_button.pack(side="left", padx=(0, 10))

        # Settings Section - Expandable
        self.settings_expanded = tk.BooleanVar(value=ui_config['settings_expanded'])
        self.expand_settings_button = ctk.CTkButton(
            self.headers_frame,
            text="▶ Settings",
            command=self.toggle_settings_section,
            width=100,
            height=32
        )
        self.expand_settings_button.pack(side="left")

        # Create the actual configuration forms
        self._create_api_options()
        self._create_settings_options()

        # Set initial states
        if self.api_expanded.get():
            self.toggle_api_section()
        if self.settings_expanded.get():
            self.toggle_settings_section()

    def _create_api_options(self):
        """Create API configuration options"""
        # API options frame (initially hidden)
        self.api_options_frame = ctk.CTkFrame(self.config_container)

        api_config = self.config_manager.get_api_config()

        # Row 1: Gemini API Key and Model
        row1_frame = ctk.CTkFrame(self.api_options_frame)
        row1_frame.pack(fill="x", padx=10, pady=(10, 5))

        # Gemini API Key
        ctk.CTkLabel(row1_frame, text="Gemini API Key:").pack(side="left", padx=(10, 5))
        self.gemini_api_key = tk.StringVar(value=api_config['gemini_api_key'])
        self.gemini_entry = ctk.CTkEntry(row1_frame, textvariable=self.gemini_api_key, show="*", width=300)
        self.gemini_entry.pack(side="left", padx=(0, 20))

        # Model
        ctk.CTkLabel(row1_frame, text="Model:").pack(side="left", padx=(10, 5))
        self.model = tk.StringVar(value=api_config['model'])
        self.model_combo = ctk.CTkComboBox(
            row1_frame,
            variable=self.model,
            width=250,
            values=["gemini-2.5-flash", "gemini-2.0-flash", "gemini-2.5-pro"]
        )
        self.model_combo.pack(side="left", padx=(0, 10))

        # Row 2: TMDB API Key
        row2_frame = ctk.CTkFrame(self.api_options_frame)
        row2_frame.pack(fill="x", padx=10, pady=(5, 10))

        ctk.CTkLabel(row2_frame, text="TMDB API Key (optional):").pack(side="left", padx=(10, 5))
        self.tmdb_api_key = tk.StringVar(value=api_config['tmdb_api_key'])
        self.tmdb_entry = ctk.CTkEntry(row2_frame, textvariable=self.tmdb_api_key, show="*", width=300)
        self.tmdb_entry.pack(side="left", padx=(0, 10))

    def _create_settings_options(self):
        """Create general settings options"""
        # Settings options frame (initially hidden)
        self.settings_options_frame = ctk.CTkFrame(self.config_container)

        processing_config = self.config_manager.get_processing_config()

        # Row 1: Language and Language Code
        row1_frame = ctk.CTkFrame(self.settings_options_frame)
        row1_frame.pack(fill="x", padx=10, pady=(10, 5))

        ctk.CTkLabel(row1_frame, text="Language:").pack(side="left", padx=(10, 5))
        self.language = tk.StringVar(value=processing_config['language'])
        self.language_entry = ctk.CTkEntry(row1_frame, textvariable=self.language, width=150)
        self.language_entry.pack(side="left", padx=(0, 20))

        ctk.CTkLabel(row1_frame, text="Code:").pack(side="left", padx=(10, 5))
        self.language_code = tk.StringVar(value=processing_config.get('language_code', 'pl'))
        self.language_code_entry = ctk.CTkEntry(row1_frame, textvariable=self.language_code, width=60)
        self.language_code_entry.pack(side="left", padx=(0, 20))

        # Extract audio checkbox
        self.extract_audio = tk.BooleanVar(value=processing_config['extract_audio'])
        self.extract_audio_check = ctk.CTkCheckBox(row1_frame, text="Extract audio", variable=self.extract_audio)
        self.extract_audio_check.pack(side="left", padx=(10, 0))

        # Row 2: TMDB ID and TV Series
        row2_frame = ctk.CTkFrame(self.settings_options_frame)
        row2_frame.pack(fill="x", padx=10, pady=(5, 5))

        ctk.CTkLabel(row2_frame, text="TMDB ID:").pack(side="left", padx=(10, 5))
        self.tmdb_id = tk.StringVar(value=self.config_manager.get('tmdb_id', ''))
        self.tmdb_id_entry = ctk.CTkEntry(row2_frame, textvariable=self.tmdb_id, width=120)
        self.tmdb_id_entry.pack(side="left", padx=(0, 10))

        row2_5_frame = ctk.CTkFrame(self.settings_options_frame)
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
        row3_frame = ctk.CTkFrame(self.settings_options_frame)
        row3_frame.pack(fill="x", padx=10, pady=(5, 5))

        ctk.CTkLabel(row3_frame, text="Overview:").pack(side="left", padx=(10, 5))
        self.overview = tk.StringVar(value='')
        self.overview_entry = ctk.CTkEntry(row3_frame, textvariable=self.overview, width=500)
        self.overview_entry.pack(side="left", padx=(0, 10), fill="x", expand=True)

        # Row 4: Auto-fetch and Add translator info
        row4_frame = ctk.CTkFrame(self.settings_options_frame)
        row4_frame.pack(fill="x", padx=10, pady=(5, 5))

        self.auto_fetch_tmdb = tk.BooleanVar(value=processing_config['auto_fetch_tmdb'])
        self.auto_fetch_check = ctk.CTkCheckBox(row4_frame, text="Auto-fetch TMDB ID when loading files", variable=self.auto_fetch_tmdb)
        self.auto_fetch_check.pack(side="left", padx=(10, 20))

        self.add_translator_info = tk.BooleanVar(value=processing_config.get('add_translator_info', True))
        self.add_translator_info_check = ctk.CTkCheckBox(row4_frame, text="Add translator info", variable=self.add_translator_info)
        self.add_translator_info_check.pack(side="left", padx=(10, 0))

        # Row 5: Poster image
        row5_frame = ctk.CTkFrame(self.settings_options_frame)
        row5_frame.pack(fill="x", padx=10, pady=(5, 10))

        ctk.CTkLabel(row5_frame, text="Poster:").pack(side="left", padx=(10, 5))
        self.image_label = ctk.CTkLabel(row5_frame, text="No image", width=100, height=150)
        self.image_label.pack(side="left", padx=(0, 10))

    def _create_action_buttons(self):
        """Create action buttons"""
        self.buttons_frame = ctk.CTkFrame(self.main_frame)
        self.buttons_frame.pack(fill="x", pady=(0, 10))

        # Translate Button
        self.translate_button = ctk.CTkButton(
            self.buttons_frame,
            text="🌐 TRANSLATE",
            command=self._start_translation,  # New simplified method
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
            command=self._cancel_translation,  # New simplified method
            font=ctk.CTkFont(size=16, weight="bold"),
            height=50,
            fg_color=("red", "darkred"),
            hover_color=("lightcoral", "red")
        )

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
        return {
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
            'add_translator_info': self.add_translator_info.get() if hasattr(self, 'add_translator_info') else True,
            'translation_type': self.translation_type.get() if hasattr(self, 'translation_type') else 'Default',
        }

    # Keep these methods for the translation manager to call:
    def show_cancel_button(self):
        """Show cancel button and hide translate button"""
        self.translate_button.pack_forget()
        self.cancel_button.pack(fill="x", padx=20, pady=10)

    def show_translate_button(self):
        """Show translate button and hide cancel button"""
        self.cancel_button.pack_forget()
        self.translate_button.pack(fill="x", padx=20, pady=10)

    def _hide_dropdown_menus(self):
        """Hide both API and Settings dropdown menus"""
        if self.api_expanded.get():
            self.api_options_frame.pack_forget()
            self.expand_api_button.configure(text="▶ Show API options")
            self.api_expanded.set(False)

        if self.settings_expanded.get():
            self.settings_options_frame.pack_forget()
            self.expand_settings_button.configure(text="▶ Settings")
            self.settings_expanded.set(False)

    def _create_status_bar(self):
        """Create status bar"""
        self.status_var = tk.StringVar(value="Ready")
        self.status_bar = ctk.CTkLabel(
            self.main_frame,
            textvariable=self.status_var,
            font=ctk.CTkFont(size=12),
            corner_radius=0,
            fg_color="transparent"
        )
        self.status_bar.pack(fill="x", pady=(0, 5))  # Reduced from (0, 10) to (0, 5)

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
        """Toggle the visibility of API configuration section"""
        if self.api_expanded.get():
            # Hide API options
            self.api_options_frame.pack_forget()
            self.expand_api_button.configure(text="▶ Show API options")
            self.api_expanded.set(False)
        else:
            # Show API options
            self.api_options_frame.pack(fill="x", padx=10, pady=(5, 0))
            self.expand_api_button.configure(text="▼ Hide API options")
            self.api_expanded.set(True)

        # Update scrollbar visibility after layout change
        self.root.after(100, self._manage_scrollbar_visibility)

    def toggle_settings_section(self):
        """Toggle the visibility of Settings section"""
        if self.settings_expanded.get():
            # Hide Settings options
            self.settings_options_frame.pack_forget()
            self.expand_settings_button.configure(text="▶ Settings")
            self.settings_expanded.set(False)
        else:
            # Show Settings options
            self.settings_options_frame.pack(fill="x", padx=10, pady=(5, 0))
            self.expand_settings_button.configure(text="▼ Settings")
            self.settings_expanded.set(True)

        # Update scrollbar visibility after layout change
        self.root.after(100, self._manage_scrollbar_visibility)


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
                dark_image=image,   # Same image for dark mode
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
            'model': self.model.get() if hasattr(self, 'model') else 'gemini-2.5-flash',
            'tmdb_api_key': self.tmdb_api_key.get() if hasattr(self, 'tmdb_api_key') else '',
            'tmdb_id': self.tmdb_id.get() if hasattr(self, 'tmdb_id') else '',
            'api_expanded': self.api_expanded.get() if hasattr(self, 'api_expanded') else False,
            'settings_expanded': self.settings_expanded.get() if hasattr(self, 'settings_expanded') else False,
            'language': self.language.get() if hasattr(self, 'language') else 'Polish',
            'language_code': self.language_code.get() if hasattr(self, 'language_code') else 'pl',
            'extract_audio': self.extract_audio.get() if hasattr(self, 'extract_audio') else False,
            'auto_fetch_tmdb': self.auto_fetch_tmdb.get() if hasattr(self, 'auto_fetch_tmdb') else True,
            'is_tv_series': self.is_tv_series.get() if hasattr(self, 'is_tv_series') else False,
            'add_translator_info': self.add_translator_info.get() if hasattr(self, 'add_translator_info') else True,
            'translation_type': self.translation_type.get() if hasattr(self, 'translation_type') else 'Default',
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

        # Collect all files for TV series detection
        all_files = []
        for file_type, files in found_files.items():
            all_files.extend(files)

        # Auto-detect TV series from all files in folder
        self._auto_detect_and_set_tv_series(all_files)

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
            # Uncheck - remove the checkmark and any following space
            if current_text.startswith('☑️ '):
                new_text = '☐ ' + current_text[3:]  # Remove "☑️ " and add "☐ "
            else:
                new_text = '☐' + current_text[1:]   # Remove just "☑️" and add "☐"

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
                new_text = '☑️' + current_text[1:]   # Remove just "☐" and add "☑️"

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