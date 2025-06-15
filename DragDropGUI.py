import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import subprocess
import os
import sys
from pathlib import Path
import threading
import json
import re


class DragDropGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("CLI Wrapper - Drag & Drop")
        self.root.geometry("900x700")  # Made wider for new column
        self.root.configure(bg='#f0f0f0')

        # Configuration file path
        self.config_file = Path("gui_config.json")

        # Load saved configuration
        self.load_config()

        # Initialize expanded states from config
        self.api_expanded = tk.BooleanVar(value=self.config.get('api_expanded', False))
        self.settings_expanded = tk.BooleanVar(value=self.config.get('settings_expanded', False))

        # Set window to front
        self.root.lift()
        self.root.attributes('-topmost', True)
        self.root.after_idle(lambda: self.root.attributes('-topmost', False))
        self.root.focus_force()

        # Bind window close event
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Store current folder path for building full file paths
        self.current_folder_path = None

        # Main frame
        main_frame = ttk.Frame(root, padding="20")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # Grid configuration
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(2, weight=1)  # TreeView row (moved up)
        main_frame.rowconfigure(4, weight=1)  # Console row (moved up)

        # Drag & drop area
        self.drop_frame = tk.Frame(main_frame, bg='#e8e8e8', relief='ridge', bd=2)
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

        # Drag & drop handling for macOS
        self.setup_macos_drag_drop()

        # TreeView section
        treeview_label = ttk.Label(main_frame, text="Found files:")
        treeview_label.grid(row=1, column=0, sticky=tk.W, pady=(0, 5))

        # Frame for TreeView with scrollbars
        tree_frame = ttk.Frame(main_frame)
        tree_frame.grid(row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=(0, 20))
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        # TreeView widget - Added Title and Year columns
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

        # Column width - adjusted for new columns
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

        # Tag definitions for different statuses
        self.tree.tag_configure('matched', background='#2d5a2d', foreground='#ffffff')  # Green for matched
        self.tree.tag_configure('subtitle_only', background='#5a5a2d', foreground='#ffffff')  # Yellow for subtitle only
        self.tree.tag_configure('video_only', background='#2d2d5a', foreground='#ffffff')  # Blue for video only
        self.tree.tag_configure('no_match', background='#5a2d2d', foreground='#ffffff')  # Red for unmatched
        self.tree.tag_configure('unchecked', background='#404040', foreground='#888888')  # Gray for unchecked

        # Bind double-click to toggle checkbox
        self.tree.bind('<Double-1>', self.toggle_checkbox)
        self.tree.bind('<Button-1>', self.on_tree_click)

        # Console output
        console_label = ttk.Label(main_frame, text="Console output:")
        console_label.grid(row=3, column=0, sticky=tk.W, pady=(0, 5))

        self.console_text = scrolledtext.ScrolledText(main_frame, height=15, width=70)
        self.console_text.grid(row=4, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        # Configuration Sections Container
        config_container = ttk.Frame(main_frame)
        config_container.grid(row=5, column=0, sticky=(tk.W, tk.E), pady=(20, 10))
        config_container.columnconfigure(0, weight=1)

        # Headers frame for both API and Settings buttons
        headers_frame = ttk.Frame(config_container)
        headers_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 5))

        # API Configuration Section - Expandable
        self.expand_api_button = tk.Button(headers_frame, text="▶ Show API options",
                                           bg='#e0e0e0', fg='black', font=('Arial', 10),
                                           relief='flat', bd=0, pady=5,
                                           command=self.toggle_api_section)
        self.expand_api_button.pack(side=tk.LEFT, padx=(0, 10))

        # Settings Section - Expandable
        self.expand_settings_button = tk.Button(headers_frame, text="▶ Settings",
                                                bg='#e0e0e0', fg='black', font=('Arial', 10),
                                                relief='flat', bd=0, pady=5,
                                                command=self.toggle_settings_section)
        self.expand_settings_button.pack(side=tk.LEFT)

        # API options frame (initially hidden)
        self.api_options_frame = ttk.Frame(config_container)
        # Don't grid it initially - it will be shown/hidden by toggle function

        # Gemini API Key
        ttk.Label(self.api_options_frame, text="Gemini API Key:").grid(row=0, column=0, sticky=tk.W, pady=(10, 5))
        self.gemini_api_key = tk.StringVar(value=self.config.get('gemini_api_key', ''))
        gemini_entry = ttk.Entry(self.api_options_frame, textvariable=self.gemini_api_key, show="*", width=50)
        gemini_entry.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=(10, 0), pady=(10, 5))

        # Model
        ttk.Label(self.api_options_frame, text="Model:").grid(row=0, column=2, sticky=tk.W, padx=(20, 0), pady=(10, 5))
        self.model = tk.StringVar(value=self.config.get('model', 'gemini-pro'))
        model_combo = ttk.Combobox(self.api_options_frame, textvariable=self.model, width=15,
                                   values=["gemini-pro", "gemini-pro-vision", "gemini-1.5-pro", "gemini-1.5-flash"])
        model_combo.grid(row=0, column=3, sticky=tk.W, padx=(10, 0), pady=(10, 5))

        # TMDB API Key
        ttk.Label(self.api_options_frame, text="TMDB API Key (optional):").grid(row=1, column=0, sticky=tk.W,
                                                                                pady=(5, 5))
        self.tmdb_api_key = tk.StringVar(value=self.config.get('tmdb_api_key', ''))
        tmdb_entry = ttk.Entry(self.api_options_frame, textvariable=self.tmdb_api_key, show="*", width=50)
        tmdb_entry.grid(row=1, column=1, sticky=(tk.W, tk.E), padx=(10, 0), pady=(5, 5))

        # ID
        ttk.Label(self.api_options_frame, text="TMDB ID (optional):").grid(row=1, column=2, sticky=tk.W, padx=(20, 0),
                                                                           pady=(5, 5))
        self.tmdb_id = tk.StringVar(value=self.config.get('tmdb_id', ''))
        id_entry = ttk.Entry(self.api_options_frame, textvariable=self.tmdb_id, width=15)
        id_entry.grid(row=1, column=3, sticky=tk.W, padx=(10, 0), pady=(5, 5))

        # Configure column weights for the API options frame
        self.api_options_frame.columnconfigure(1, weight=1)

        # Settings options frame (initially hidden)
        self.settings_options_frame = ttk.Frame(config_container)
        # Don't grid it initially - it will be shown/hidden by toggle function

        # Language setting
        ttk.Label(self.settings_options_frame, text="Language:").grid(row=0, column=0, sticky=tk.W, pady=(10, 5))
        self.language = tk.StringVar(value=self.config.get('language', 'Polish'))
        language_entry = ttk.Entry(self.settings_options_frame, textvariable=self.language, width=20)
        language_entry.grid(row=0, column=1, sticky=tk.W, padx=(10, 0), pady=(10, 5))

        # Extract audio checkbox
        self.extract_audio = tk.BooleanVar(value=self.config.get('extract_audio', False))
        extract_audio_check = ttk.Checkbutton(self.settings_options_frame, text="Extract audio",
                                              variable=self.extract_audio)
        extract_audio_check.grid(row=0, column=2, sticky=tk.W, padx=(20, 0), pady=(10, 5))

        # Configure column weights for the settings options frame
        self.settings_options_frame.columnconfigure(1, weight=1)

        # Set initial states based on saved config
        if self.api_expanded.get():
            self.toggle_api_section()  # This will show the section if it was previously expanded

        if self.settings_expanded.get():
            self.toggle_settings_section()  # This will show the section if it was previously expanded

        # Translate Button (always visible)
        translate_button = tk.Button(main_frame, text="🌐 TRANSLATE",
                                     bg='#f0f0f0', fg='black', font=('Arial', 12, 'bold'),
                                     relief='raised', bd=3, pady=10,
                                     activebackground='#e0e0e0', activeforeground='black',
                                     command=self.start_translation)
        translate_button.grid(row=6, column=0, pady=(10, 10), sticky=(tk.W, tk.E))

        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        status_bar = ttk.Label(main_frame, textvariable=self.status_var,
                               relief=tk.SUNKEN, anchor=tk.W)
        status_bar.grid(row=7, column=0, sticky=(tk.W, tk.E), pady=(10, 0))

        # Alternative approach to drag & drop
        self.setup_drag_drop()

        # Additional settings for macOS to ensure window is in front
        self.root.after(100, self.ensure_front)

    def extract_movie_info(self, filename):
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

    def format_movie_info(self, movie_name, year):
        """Format movie name and year for display - returns tuple (title, year)"""
        if not movie_name:
            title = "Unknown Movie"
        else:
            title = movie_name

        # Return year without parentheses, or empty string if no year
        year_display = year if year else ""

        return title, year_display

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

    def load_config(self):
        """Load configuration from JSON file"""
        self.config = {}
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    self.config = json.load(f)
                self.log_config_loaded()
        except Exception as e:
            print(f"Error loading configuration: {e}")
            self.config = {}

    def save_config(self):
        """Save current configuration to JSON file"""
        try:
            config_data = {
                'gemini_api_key': self.gemini_api_key.get() if hasattr(self, 'gemini_api_key') else '',
                'model': self.model.get() if hasattr(self, 'model') else 'gemini-pro',
                'tmdb_api_key': self.tmdb_api_key.get() if hasattr(self, 'tmdb_api_key') else '',
                'tmdb_id': self.tmdb_id.get() if hasattr(self, 'tmdb_id') else '',
                'api_expanded': self.api_expanded.get() if hasattr(self, 'api_expanded') else False,
                'settings_expanded': self.settings_expanded.get() if hasattr(self, 'settings_expanded') else False,
                'language': self.language.get() if hasattr(self, 'language') else 'Polish',
                'extract_audio': self.extract_audio.get() if hasattr(self, 'extract_audio') else False
            }

            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, indent=2, ensure_ascii=False)

        except Exception as e:
            print(f"Error saving configuration: {e}")

    def log_config_loaded(self):
        """Log information about loaded configuration (only after GUI is ready)"""

        def delayed_log():
            if hasattr(self, 'console_text'):
                has_gemini = bool(self.config.get('gemini_api_key', '').strip())
                has_tmdb_key = bool(self.config.get('tmdb_api_key', '').strip())
                has_tmdb_id = bool(self.config.get('tmdb_id', '').strip())
                model = self.config.get('model', 'gemini-pro')
                language = self.config.get('language', 'Polish')
                extract_audio = self.config.get('extract_audio', False)

                self.log_to_console("💾 Configuration loaded:")
                self.log_to_console(f"   🤖 Model: {model}")
                self.log_to_console(f"   🔑 Gemini API: {'✅ Saved' if has_gemini else '❌ Missing'}")
                self.log_to_console(f"   🎬 TMDB API: {'✅ Saved' if has_tmdb_key else '❌ Missing (optional)'}")
                self.log_to_console(f"   🆔 TMDB ID: {'✅ Saved' if has_tmdb_id else '❌ Missing (optional)'}")
                self.log_to_console(f"   🌐 Language: {language}")
                self.log_to_console(f"   🎵 Extract audio: {'✅ Enabled' if extract_audio else '❌ Disabled'}")
                self.log_to_console("─" * 50)

        # Delay logging until GUI is ready
        self.root.after(200, delayed_log)

    def on_closing(self):
        """Handle window closing event"""
        self.save_config()
        self.log_to_console("💾 Configuration saved")
        self.root.after(100, self.root.destroy)  # Small delay to ensure log is shown

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

    def setup_macos_drag_drop(self):
        """Configure drag & drop using TkinterDnD2"""
        try:
            from tkinterdnd2 import TkinterDnD, DND_FILES

            # Try to initialize TkinterDnD
            try:
                # Check if TkinterDnD can be initialized
                self.root.tk.call('package', 'require', 'tkdnd')
            except tk.TclError:
                # Try alternative initialization
                try:
                    TkinterDnD._require(self.root)
                except:
                    raise ImportError("TkinterDnD2 cannot be initialized")

            # Configure drag & drop
            self.drop_frame.drop_target_register(DND_FILES)
            self.drop_frame.dnd_bind('<<Drop>>', self.handle_drop)
            self.root.title("CLI Wrapper - Drag & Drop (Enhanced)")
            self.log_to_console("✅ Drag & Drop enabled - you can drag files!")

        except (ImportError, tk.TclError, Exception) as e:
            self.log_to_console(f"ℹ️  Drag & Drop unavailable: {type(e).__name__}")
            self.log_to_console("🖱️  Use button to select file")
            self.log_to_console("💡 Try: pip uninstall tkinterdnd2 && pip install tkinterdnd2")

    def handle_drop(self, event):
        """Handle drop event from TkinterDnD2"""
        # Get raw data
        files_data = event.data

        # Log for debugging
        self.log_to_console(f"🔍 Debug - raw data: {repr(files_data)}")

        if not files_data:
            return

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
        # List of files separated by spaces - but preserve paths with spaces
        elif files_data.count(' ') > 0:
            # Check if it might be a single path with spaces
            if files_data.startswith('/') or files_data[1:3] == ':\\':  # Unix or Windows path
                file_path = files_data.strip()
            else:
                # Probably a list of files - take first
                # But try to preserve spaces in names
                parts = files_data.split()
                if parts:
                    file_path = parts[0].strip('{}').strip('"')
        else:
            # Standard format without problems
            file_path = files_data.strip()

        self.log_to_console(f"✅ Processed path: {repr(file_path)}")

        if file_path:
            # Check if path exists
            if Path(file_path).exists():
                self.process_dropped_item(file_path)
            else:
                self.log_to_console(f"❌ Path does not exist: {file_path}")
                # Try different path repair variants

                # Maybe encoding problem?
                for encoding in ['utf-8', 'latin-1', 'cp1252']:
                    try:
                        if isinstance(file_path, str):
                            decoded_path = file_path.encode('latin-1').decode(encoding)
                            if Path(decoded_path).exists():
                                self.log_to_console(f"✅ Fixed path (encoding {encoding})")
                                self.process_dropped_item(decoded_path)
                                return
                    except:
                        continue

                # Maybe need to merge fragments back?
                if ' ' in files_data:
                    # Try to combine all parts
                    full_path = files_data.strip('{}').strip('"')
                    if Path(full_path).exists():
                        self.log_to_console("✅ Fixed path (merged fragments)")
                        self.process_dropped_item(full_path)
                        return

                messagebox.showerror("Error", f"Cannot find file:\n{file_path}")
        else:
            self.log_to_console("❌ Failed to extract path")

    def setup_drag_drop(self):
        """Configure drag & drop - uses native Tkinter events"""
        # Bind drag & drop events
        self.drop_frame.bind("<Button-1>", self.on_click)
        self.drop_frame.bind("<B1-Motion>", self.on_drag)
        self.drop_frame.bind("<ButtonRelease-1>", self.on_drop_release)

        # Handle dragging files from outside (Windows)
        try:
            self.root.tk.call('tk', 'windowingsystem')  # Test if it works
            # For Windows - handle through system events
            self.root.bind('<Map>', self.on_map)
        except:
            pass

    def on_map(self, event):
        """Handle window mapping"""
        # Register as target for drag & drop
        try:
            self.root.tk.call('tk', 'windowingsystem')
        except:
            pass

    def on_click(self, event):
        """Handle click"""
        pass

    def on_drag(self, event):
        """Handle dragging"""
        pass

    def on_drop_release(self, event):
        """Handle drop release"""
        pass

    def on_drop(self, event):
        """Handle file/folder drop"""
        try:
            # Get path from event
            files = event.data
            if files:
                # Could be a list of files
                if isinstance(files, (list, tuple)):
                    file_path = files[0]
                else:
                    file_path = files

                self.process_dropped_item(file_path)
        except Exception as e:
            messagebox.showerror("Error", f"Cannot process dropped item: {e}")

    def browse_file(self, event=None):
        """Open file/folder selection dialog"""
        from tkinter import filedialog

        # First ask if file or folder
        choice = messagebox.askyesnocancel("Selection",
                                           "Yes = File\nNo = Folder\nCancel = Exit")

        if choice is None:  # Cancel
            return
        elif choice:  # Yes - file
            file_path = filedialog.askopenfilename(
                title="Select file",
                filetypes=[("All files", "*.*")]
            )
        else:  # No - folder
            file_path = filedialog.askdirectory(title="Select folder")

        if file_path:
            self.process_dropped_item(file_path)

    def clear_treeview(self):
        """Clear TreeView"""
        for item in self.tree.get_children():
            self.tree.delete(item)

    def format_file_size(self, size_bytes):
        """Format file size in readable way"""
        if size_bytes == 0:
            return "0 B"

        size_names = ["B", "KB", "MB", "GB"]
        i = 0
        while size_bytes >= 1024.0 and i < len(size_names) - 1:
            size_bytes /= 1024.0
            i += 1

        return f"{size_bytes:.1f} {size_names[i]}"

    def toggle_checkbox(self, event):
        """Toggle checkbox state on double-click"""
        item = self.tree.selection()[0] if self.tree.selection() else None
        if item:
            self.toggle_item_checkbox(item)

    def on_tree_click(self, event):
        """Handle single click on tree"""
        item = self.tree.identify('item', event.x, event.y)
        if item:
            # Check if click was on the checkbox area (first 30 pixels)
            if event.x <= 30:
                self.toggle_item_checkbox(item)

    def toggle_item_checkbox(self, item):
        """Toggle checkbox state for specific item"""
        current_text = self.tree.item(item, 'text')
        current_tags = self.tree.item(item, 'tags')
        values = self.tree.item(item, 'values')

        if current_text.startswith('☑️'):
            # Uncheck - change to empty checkbox and gray background
            new_text = '☐' + current_text[1:]
            new_values = list(values)

            # Update status to show unchecked state
            if len(new_values) >= 5:  # Updated index for status column (now at index 5)
                original_status = new_values[4]
                if not original_status.startswith('⏸️'):
                    new_values[4] = f"⏸️ Skipped ({original_status})"

            self.tree.item(item, text=new_text, values=new_values, tags=('unchecked',))

        elif current_text.startswith('☐'):
            # Check - restore original background and status
            new_text = '☑️' + current_text[1:]
            new_values = list(values)

            # Restore original status
            if len(new_values) >= 5:  # Updated index for status column (now at index 5)
                current_status = new_values[4]
                if current_status.startswith('⏸️ Skipped ('):
                    # Extract original status
                    original_status = current_status[12:-1]  # Remove "⏸️ Skipped (" and ")"
                    new_values[4] = original_status

            # Determine original tag based on status
            original_tag = 'matched'  # default
            if len(new_values) >= 5:  # Updated index for status column (now at index 5)
                status = new_values[4]
                if "Matched" in status:
                    original_tag = 'matched'
                elif "No match" in status:
                    original_tag = 'no_match'
                elif "No subtitles" in status:
                    original_tag = 'video_only'
                elif "Subtitle file" in status:
                    original_tag = 'subtitle_only'

            self.tree.item(item, text=new_text, values=new_values, tags=(original_tag,))

        else:
            # Add checkbox if not present (checked by default)
            new_text = '☑️ ' + current_text
            self.tree.item(item, text=new_text)

    def find_video_matches(self, subtitle_files, video_files, folder_path):
        """Find matching video files for subtitle files"""
        matches = []

        # Convert to sets for faster lookup
        video_stems = {v.stem.lower(): v for v in video_files}
        subtitle_stems = {s.stem.lower(): s for s in subtitle_files}

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
                    # Calculate similarity (simple approach)
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

        # Store folder path for later use
        self.current_folder_path = folder_path

        subtitle_files = found_files.get('text', [])
        video_files = found_files.get('video', [])

        if not subtitle_files and not video_files:
            # Show message that no relevant files were found
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

            # Extract movie info from the file that exists
            primary_file = match['video'] if match['video'] else match['subtitle']
            if primary_file:
                movie_name, year = self.extract_movie_info(primary_file.name)
                title, year_display = self.format_movie_info(movie_name, year)

                # Log the extraction for debugging
                self.log_to_console(f"🎭 Extracted: '{primary_file.name}' → Title: '{title}', Year: '{year_display}'")
            else:
                title = "Unknown Movie"
                year_display = ""

            # Default to checked
            checkbox = "☑️"

            item_text = f"{checkbox} Pair {i + 1}"

            self.tree.insert('', 'end', text=item_text,
                             values=(subtitle_name, video_name, title, year_display, str(folder_path), match['status']),
                             tags=(match['tag'],))

        # Add summary
        total_pairs = len(matches)
        matched_pairs = len([m for m in matches if m['subtitle'] and m['video']])
        subtitle_only = len([m for m in matches if m['subtitle'] and not m['video']])
        video_only = len([m for m in matches if m['video'] and not m['subtitle']])

        self.log_to_console(f"📊 Matching summary:")
        self.log_to_console(f"   ✅ Matched pairs: {matched_pairs}")
        self.log_to_console(f"   ⚠️ Subtitles without video: {subtitle_only}")
        self.log_to_console(f"   ℹ️ Video without subtitles: {video_only}")
        self.log_to_console(f"   📝 Total items: {total_pairs}")

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

        # Debug: show all items
        for i, item in enumerate(self.tree.get_children()):
            item_text = self.tree.item(item, 'text')
            values = self.tree.item(item, 'values')
            tags = self.tree.item(item, 'tags')
            self.log_to_console(f"Item {i + 1}: text='{item_text}', values={values}, tags={tags}")

        # Get selected pairs
        selected_pairs = self.get_selected_pairs()

        if not selected_pairs:
            messagebox.showwarning("Warning",
                                   "No pairs selected for translation.\nMake sure items have ☑️ checkmark")
            return

        # Filter pairs based on extract audio setting
        valid_pairs = []
        extract_audio = self.extract_audio.get() if hasattr(self, 'extract_audio') else False

        for pair in selected_pairs:
            if extract_audio:
                # If extract audio is enabled, we need both subtitle and video files
                if pair['subtitle'] and pair['video']:
                    valid_pairs.append(pair)
            else:
                # If extract audio is disabled, we only need subtitle files
                if pair['subtitle']:
                    valid_pairs.append(pair)

        if not valid_pairs:
            if extract_audio:
                messagebox.showwarning("Warning",
                                       "No pairs with both subtitles and video found.\nSelect pairs that have both files, or disable 'Extract audio' to process subtitle-only files.")
            else:
                messagebox.showwarning("Warning",
                                       "No pairs with subtitle files found.\nSelect pairs that have subtitle files.")
            return

        # Show confirmation
        checked_count = len(valid_pairs)
        total_selected = len(selected_pairs)

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

        result = messagebox.askyesno("Translation confirmation", confirmation_msg)

        if result:
            # Save configuration before starting
            self.save_config()
            self.run_cli_commands(valid_pairs)

    def run_cli_commands(self, valid_pairs):
        """Run CLI commands for each selected subtitle-video pair"""

        def run_commands():
            try:
                self.status_var.set("Processing...")
                self.log_to_console("🚀 Starting processing...")
                self.log_to_console(f"📊 Processing {len(valid_pairs)} pairs")
                self.log_to_console("─" * 50)

                # Check if gst exists
                gst_cmd = "gst"

                # Try to find gst program
                try:
                    # Check if gst is in PATH
                    result = subprocess.run(['which', 'gst'], capture_output=True, text=True)
                    if result.returncode != 0:
                        # Try Windows where command
                        result = subprocess.run(['where', 'gst'], capture_output=True, text=True, shell=True)
                        if result.returncode != 0:
                            raise FileNotFoundError("gst not found")
                except Exception:
                    # gst not found in PATH, check local directory
                    gst_path = Path("gst")
                    gst_exe_path = Path("gst.exe")

                    if gst_path.exists():
                        gst_cmd = str(gst_path)
                    elif gst_exe_path.exists():
                        gst_cmd = str(gst_exe_path)
                    else:
                        self.log_to_console("ERROR: 'gst' program not found")
                        self.log_to_console("Check if 'gst' is installed or available in PATH")
                        self.status_var.set("Error - missing gst")
                        return

                self.log_to_console(f"✅ Found gst: {gst_cmd}")
                self.log_to_console("─" * 30)

                # Get settings
                language = self.language.get() if hasattr(self, 'language') else 'Polish'
                extract_audio = self.extract_audio.get() if hasattr(self, 'extract_audio') else False
                gemini_api_key = self.gemini_api_key.get() if hasattr(self, 'gemini_api_key') else ''
                model = self.model.get() if hasattr(self, 'model') else 'gemini-pro'

                # Process each selected pair
                for i, pair in enumerate(valid_pairs, 1):
                    subtitle_filename = pair['subtitle']
                    video_filename = pair['video']

                    # Build full paths
                    if subtitle_filename:
                        subtitle_file = str(self.current_folder_path / subtitle_filename)
                    else:
                        subtitle_file = None

                    if video_filename:
                        video_file = str(self.current_folder_path / video_filename)
                    else:
                        video_file = None

                    self.log_to_console(f"🔄 Pair {i}/{len(valid_pairs)}:")
                    self.log_to_console(f"   📝 Subtitles: {subtitle_file}")
                    if video_file:
                        self.log_to_console(f"   🎬 Video: {video_file}")

                    # Prepare base command: gst translate -i subtitlefile -l [language] -k [api_key] --model [model]
                    cmd = [gst_cmd, 'translate', '-i', subtitle_file, '-l', language]

                    # Add Gemini API key if provided
                    if gemini_api_key.strip():
                        cmd.extend(['-k', gemini_api_key])
                        self.log_to_console(f"   🔑 Using Gemini API key")
                    else:
                        self.log_to_console(f"   ⚠️ No Gemini API key provided")

                    # Add model
                    cmd.extend(['--model', model])
                    self.log_to_console(f"   🤖 Model: {model}")

                    # Add video file if available and extract audio is enabled
                    if video_file and extract_audio:
                        cmd.extend(['-v', video_file])
                        cmd.append('--extract-audio')
                        self.log_to_console(f"   🎬 Video file added for audio extraction")
                        self.log_to_console(f"   🎵 Extract audio: enabled")
                    elif video_file and not extract_audio:
                        self.log_to_console(f"   🎬 Video file available but extract audio disabled")
                    elif not video_file:
                        self.log_to_console(f"   ℹ️ No video file - processing subtitle only")

                    self.log_to_console(f"Executing: {' '.join(cmd)}")

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
                        output_line = line.rstrip()
                        if output_line:  # Only log non-empty lines
                            self.log_to_console(f"   {output_line}")

                    # Wait for completion
                    return_code = process.wait()

                    if return_code == 0:
                        self.log_to_console(f"✅ Pair {i} processed successfully")
                    else:
                        self.log_to_console(f"❌ Pair {i} finished with error (code: {return_code})")

                    self.log_to_console("─" * 30)

                self.log_to_console("🎉 Processing completed!")
                self.status_var.set("Processing completed")

            except Exception as e:
                error_msg = f"Error during processing: {e}"
                self.log_to_console(error_msg)
                self.status_var.set("Processing error")
                messagebox.showerror("Error", error_msg)

        # Run in separate thread
        thread = threading.Thread(target=run_commands, daemon=True)
        thread.start()

    def process_dropped_item(self, path):
        """Process dropped/selected item"""
        path = Path(path)

        if not path.exists():
            messagebox.showerror("Error", f"Path does not exist: {path}")
            return

        self.log_to_console(f"Processing: {path}")

        if path.is_file():
            self.log_to_console("File detected - running main.py -f")
            # Clear TreeView for single file
            self.clear_treeview()
            # Store single file's parent directory
            self.current_folder_path = path.parent
            # Add single file to TreeView
            try:
                file_type = self.classify_file_type(path)

                # Extract movie info from the file
                movie_name, year = self.extract_movie_info(path.name)
                title, year_display = self.format_movie_info(movie_name, year)

                self.log_to_console(f"🎭 Extracted: '{path.name}' → Title: '{title}', Year: '{year_display}'")

                if file_type == 'text':
                    # Subtitle file
                    self.tree.insert('', 'end', text='☑️ Single file',
                                     values=(path.name, "No match", title, year_display, str(self.current_folder_path),
                                             "📝 Subtitle file"),
                                     tags=('subtitle_only',))
                elif file_type == 'video':
                    # Video file
                    self.tree.insert('', 'end', text='☑️ Single file',
                                     values=("No match", path.name, title, year_display, str(self.current_folder_path),
                                             "🎬 Video file"),
                                     tags=('video_only',))
                else:
                    # Other file type
                    self.tree.insert('', 'end', text='☑️ Single file',
                                     values=(path.name if file_type == 'text' else "N/A",
                                             path.name if file_type == 'video' else "N/A",
                                             title,
                                             year_display,
                                             str(self.current_folder_path),
                                             f"📄 {file_type.title()}"),
                                     tags=('no_match',))
            except Exception as e:
                self.log_to_console(f"Error adding file to TreeView: {e}")

            self.run_cli_command(str(path), is_file=True)
        elif path.is_dir():
            self.log_to_console("Folder detected - scanning contents...")
            found_files = self.scan_folder_contents(path)
            self.add_subtitle_matches_to_treeview(found_files, path)
            self.log_to_console("Running main.py -d")
            self.run_cli_command(str(path), is_file=False)
        else:
            self.log_to_console("Unknown item type")
            messagebox.showwarning("Warning", "Unknown item type")

    def classify_file_type(self, file_path):
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

    def scan_folder_contents(self, folder_path):
        """Scan folder contents and return found files"""
        try:
            # Define extensions we're interested in
            text_extensions = {'.txt', '.srt', '.vtt', '.sub', '.ass', '.ssa'}
            video_extensions = {'.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm', '.m4v'}
            audio_extensions = {'.mp3', '.wav', '.flac', '.aac', '.ogg', '.m4a', '.wma'}
            image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp'}

            # Counters and file lists
            found_files = {
                'text': [],
                'video': [],
                'audio': [],
                'image': [],
                'other': []
            }

            total_files = 0

            # Scan folder (including subfolders)
            self.log_to_console(f"📂 Scanning folder: {folder_path.name}")

            for file_path in folder_path.rglob('*'):
                if file_path.is_file():
                    total_files += 1
                    ext = file_path.suffix.lower()
                    relative_path = file_path.relative_to(folder_path)

                    if ext in text_extensions:
                        found_files['text'].append(relative_path)
                    elif ext in video_extensions:
                        found_files['video'].append(relative_path)
                    elif ext in audio_extensions:
                        found_files['audio'].append(relative_path)
                    elif ext in image_extensions:
                        found_files['image'].append(relative_path)
                    else:
                        found_files['other'].append(relative_path)

            # Display summary in console
            self.log_to_console(f"📊 Found total: {total_files} files")

            # Category emoji mapping
            category_emojis = {
                'text': '📝',
                'video': '🎬',
                'audio': '🎵',
                'image': '🖼️',
                'other': '📄'
            }

            for category, files in found_files.items():
                if files:
                    emoji = category_emojis.get(category, '📄')
                    self.log_to_console(f"{emoji} {category.title()}: {len(files)} files")

            # Special warnings
            if not found_files['text'] and not found_files['video']:
                self.log_to_console("⚠️  No text or video files found")
            elif found_files['text'] and found_files['video']:
                self.log_to_console("✅ Found video and subtitle files - ready for processing!")
            elif found_files['text']:
                self.log_to_console("ℹ️  Found only text files")
            elif found_files['video']:
                self.log_to_console("ℹ️  Found only video files (no subtitles)")

            self.log_to_console("─" * 50)

            return found_files

        except Exception as e:
            self.log_to_console(f"❌ Error scanning folder: {e}")
            return {
                'text': [],
                'video': [],
                'audio': [],
                'image': [],
                'other': []
            }

    def run_cli_command(self, path, is_file=True):
        """Run CLI command in separate thread"""

        def run_command():
            try:
                self.status_var.set("Running...")

                # Check if main.py exists
                main_py_path = Path("main.py")
                if not main_py_path.exists():
                    self.log_to_console("ERROR: main.py not found in current directory")
                    self.status_var.set("Error - missing main.py")
                    return

                # Prepare command
                if is_file:
                    cmd = [sys.executable, "main.py", "-f", path]
                else:
                    cmd = [sys.executable, "main.py", "-d", path]  # Assuming -d for folder

                self.log_to_console(f"Executing: {' '.join(cmd)}")

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
                    self.log_to_console(line.rstrip())

                # Wait for completion
                return_code = process.wait()

                if return_code == 0:
                    self.log_to_console("✅ Command executed successfully")
                    self.status_var.set("Executed successfully")
                else:
                    self.log_to_console(f"❌ Command finished with error code: {return_code}")
                    self.status_var.set(f"Error (code: {return_code})")

            except Exception as e:
                error_msg = f"Error during execution: {e}"
                self.log_to_console(error_msg)
                self.status_var.set("Execution error")
                messagebox.showerror("Error", error_msg)

        # Run in separate thread to not block GUI
        thread = threading.Thread(target=run_command, daemon=True)
        thread.start()

    def log_to_console(self, message):
        """Add message to console"""

        def update_console():
            self.console_text.insert(tk.END, message + "\n")
            self.console_text.see(tk.END)
            self.root.update_idletasks()

        # Make sure GUI update happens in main thread
        self.root.after(0, update_console)


def main():
    # Check if main.py exists
    if not Path("main.py").exists():
        print("Warning: main.py not found in current directory")
        print("Make sure you're running GUI from the same directory as main.py")

    root = tk.Tk()

    # Additional settings for macOS to ensure window is in front
    try:
        # Try to set focus on macOS
        os.system('''/usr/bin/osascript -e 'tell app "Finder" to set frontmost of process "Python" to true' ''')
    except:
        pass

    app = DragDropGUI(root)

    try:
        root.mainloop()
    except KeyboardInterrupt:
        print("\nClosing application...")
    except Exception as e:
        print(f"Application error: {e}")


if __name__ == "__main__":
    main()