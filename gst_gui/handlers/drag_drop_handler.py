"""
Drag & Drop Handler for the CLI Wrapper GUI application.
Handles file/folder drag & drop operations and file path parsing.
"""
import os
import tkinter as tk
from pathlib import Path
from tkinter import messagebox


class DragDropHandler:
    """Handles drag and drop functionality for files and folders"""

    def __init__(self, widget, logger=None, on_drop_callback=None):
        """
        Initialize the drag & drop handler

        Args:
            widget: The tkinter widget to enable drag & drop on
            logger: Function to log messages (optional)
            on_drop_callback: Function to call when files are dropped
        """
        self.widget = widget
        self.logger = logger or self._default_logger
        self.on_drop_callback = on_drop_callback
        self.dnd_available = False

        # Try to setup drag & drop
        self.setup_drag_drop()

    def _default_logger(self, message):
        """Default logger that prints to console"""
        print(message)

    def setup_drag_drop(self):
        """Configure drag & drop handling with TkinterDnD2"""
        try:
            from tkinterdnd2 import TkinterDnD, DND_FILES

            # Try to initialize TkinterDnD
            try:
                self.widget.tk.call('package', 'require', 'tkdnd')
            except tk.TclError:
                try:
                    TkinterDnD._require(self.widget)
                except:
                    raise ImportError("TkinterDnD2 cannot be initialized")

            # Configure drag & drop
            self.widget.drop_target_register(DND_FILES)
            self.widget.dnd_bind('<<Drop>>', self._handle_drop_event)

            self.dnd_available = True
            self.logger("✅ Drag & Drop enabled - you can drag files!")

        except (ImportError, tk.TclError, Exception) as e:
            self.dnd_available = False
            self.logger(f"ℹ️  Drag & Drop unavailable: {type(e).__name__}")
            self.logger("🖱️  Use button to select file")
            self.logger("💡 Try: pip uninstall tkinterdnd2 && pip install tkinterdnd2")

    def _handle_drop_event(self, event):
        """Handle drop event from TkinterDnD2"""
        files_data = event.data
        self.logger(f"🔍 Debug - raw data: {repr(files_data)}")

        if not files_data:
            return

        # Parse the dropped file paths
        file_paths = self.parse_dropped_files(files_data)

        if not file_paths:
            self.logger("❌ Cannot parse dropped files")
            messagebox.showerror("Error", f"Cannot parse file paths:\n{files_data}")
            return

        # Process each dropped file/folder
        for file_path in file_paths:
            if Path(file_path).exists():
                self.logger(f"📁 Processing: {file_path}")
                if self.on_drop_callback:
                    self.on_drop_callback(file_path)
            else:
                self.logger(f"❌ File not found: {file_path}")
                messagebox.showerror("Error", f"Cannot find file:\n{file_path}")

    def parse_dropped_files(self, files_data):
        """
        Parse file paths from dropped data

        Args:
            files_data: Raw data from drag & drop event

        Returns:
            List of file paths
        """
        if not files_data:
            return []

        file_paths = []

        # Handle different data formats from different operating systems

        # Format 1: Multiple files separated by newlines
        if '\n' in files_data:
            lines = files_data.strip().split('\n')
            for line in lines:
                path = self._parse_single_path(line.strip())
                if path:
                    file_paths.append(path)

        # Format 2: Single file or multiple files separated by spaces
        else:
            # Try to parse as single path first
            single_path = self._parse_single_path(files_data)
            if single_path and Path(single_path).exists():
                file_paths.append(single_path)
            else:
                # Try to split by spaces (for multiple files)
                parts = files_data.split()
                for part in parts:
                    path = self._parse_single_path(part)
                    if path and Path(path).exists():
                        file_paths.append(path)

        return file_paths

    def _parse_single_path(self, path_data):
        """
        Parse a single file path from various formats

        Args:
            path_data: Raw path data

        Returns:
            Cleaned file path or None
        """
        if not path_data:
            return None

        # Remove common wrapper characters
        path = path_data.strip()

        # Format with curly braces: {/path/with spaces/file.txt}
        if path.startswith('{') and path.endswith('}'):
            path = path[1:-1]

        # Format with quotes: "/path/with spaces/file.txt"
        elif path.startswith('"') and path.endswith('"'):
            path = path[1:-1]

        # Format with single quotes: '/path/file.txt'
        elif path.startswith("'") and path.endswith("'"):
            path = path[1:-1]

        # Handle escaped spaces (some systems escape spaces in paths)
        path = path.replace('\\ ', ' ')

        # Handle URL-encoded paths (rare but possible)
        try:
            import urllib.parse
            if '%' in path:
                path = urllib.parse.unquote(path)
        except:
            pass  # If urllib is not available, continue without URL decoding

        # Convert to absolute path if relative
        try:
            path = os.path.abspath(path)
        except:
            pass  # If conversion fails, use original path

        return path if path else None

    def is_available(self):
        """Check if drag & drop is available"""
        return self.dnd_available

    def set_drop_callback(self, callback):
        """Set the callback function for drop events"""
        self.on_drop_callback = callback

    def enable(self):
        """Enable drag & drop (if available)"""
        if self.dnd_available:
            try:
                self.widget.dnd_bind('<<Drop>>', self._handle_drop_event)
                self.logger("✅ Drag & Drop enabled")
            except Exception as e:
                self.logger(f"❌ Failed to enable drag & drop: {e}")

    def disable(self):
        """Disable drag & drop"""
        if self.dnd_available:
            try:
                self.widget.dnd_bind('<<Drop>>', None)
                self.logger("⏸️ Drag & Drop disabled")
            except Exception as e:
                self.logger(f"❌ Failed to disable drag & drop: {e}")

    def destroy(self):
        """Clean up drag & drop handler"""
        if self.dnd_available:
            try:
                self.widget.drop_target_unregister()
                self.logger("🗑️ Drag & Drop handler cleaned up")
            except Exception as e:
                self.logger(f"⚠️ Error during cleanup: {e}")


class FileSelectionHandler:
    """Handles file/folder selection through dialogs"""

    def __init__(self, parent_widget, logger=None, on_selection_callback=None):
        """
        Initialize the file selection handler

        Args:
            parent_widget: Parent widget for dialogs
            logger: Function to log messages (optional)
            on_selection_callback: Function to call when files are selected
        """
        self.parent = parent_widget
        self.logger = logger or self._default_logger
        self.on_selection_callback = on_selection_callback

    def _default_logger(self, message):
        """Default logger that prints to console"""
        print(message)

    def browse_files_or_folder(self):
        """Open file/folder selection dialog"""
        from tkinter import filedialog

        choice = messagebox.askyesnocancel(
            "Selection",
            "Yes = File\nNo = Folder\nCancel = Exit"
        )

        if choice is None:
            return
        elif choice:  # File selected
            file_path = filedialog.askopenfilename(
                parent=self.parent,
                title="Select file",
                filetypes=[
                    ("Subtitle files", "*.srt *.vtt *.ass *.ssa"),
                    ("Video files", "*.mp4 *.mkv *.avi *.mov *.wmv *.flv"),
                    ("All files", "*.*")
                ]
            )
        else:  # Folder selected
            file_path = filedialog.askdirectory(
                parent=self.parent,
                title="Select folder"
            )

        if file_path:
            self.logger(f"📁 Selected: {file_path}")
            if self.on_selection_callback:
                self.on_selection_callback(file_path)

    def browse_file(self, file_types=None):
        """
        Open file selection dialog

        Args:
            file_types: List of (description, pattern) tuples for file filtering
        """
        from tkinter import filedialog

        if file_types is None:
            file_types = [("All files", "*.*")]

        file_path = filedialog.askopenfilename(
            parent=self.parent,
            title="Select file",
            filetypes=file_types
        )

        if file_path:
            self.logger(f"📄 Selected file: {file_path}")
            if self.on_selection_callback:
                self.on_selection_callback(file_path)

        return file_path

    def browse_folder(self):
        """Open folder selection dialog"""
        from tkinter import filedialog

        folder_path = filedialog.askdirectory(
            parent=self.parent,
            title="Select folder"
        )

        if folder_path:
            self.logger(f"📁 Selected folder: {folder_path}")
            if self.on_selection_callback:
                self.on_selection_callback(folder_path)

        return folder_path

    def browse_multiple_files(self, file_types=None):
        """
        Open multiple file selection dialog

        Args:
            file_types: List of (description, pattern) tuples for file filtering
        """
        from tkinter import filedialog

        if file_types is None:
            file_types = [("All files", "*.*")]

        file_paths = filedialog.askopenfilenames(
            parent=self.parent,
            title="Select files",
            filetypes=file_types
        )

        if file_paths:
            self.logger(f"📄 Selected {len(file_paths)} files")
            for file_path in file_paths:
                if self.on_selection_callback:
                    self.on_selection_callback(file_path)

        return list(file_paths)

    def set_selection_callback(self, callback):
        """Set the callback function for selection events"""
        self.on_selection_callback = callback


# Example usage and integration class
class DropAreaHandler:
    """Combined handler for both drag & drop and file selection"""

    def __init__(self, widget, logger=None, on_file_callback=None):
        """
        Initialize combined drop area handler

        Args:
            widget: Widget to enable drag & drop on
            logger: Function to log messages
            on_file_callback: Function to call when files are processed
        """
        self.widget = widget
        self.logger = logger or print
        self.on_file_callback = on_file_callback

        # Initialize handlers
        self.drag_drop = DragDropHandler(
            widget=widget,
            logger=logger,
            on_drop_callback=self._handle_file_or_folder
        )

        self.file_selection = FileSelectionHandler(
            parent_widget=widget,
            logger=logger,
            on_selection_callback=self._handle_file_or_folder
        )

        # Bind click events for manual file selection
        self._bind_click_events()

    def _bind_click_events(self):
        """Bind click events to widget for file selection"""
        try:
            self.widget.bind("<Button-1>", self._on_click)
            # Also bind to child widgets if they exist
            for child in self.widget.winfo_children():
                child.bind("<Button-1>", self._on_click)
        except Exception as e:
            self.logger(f"⚠️ Could not bind click events: {e}")

    def _on_click(self, event):
        """Handle click events for file selection"""
        self.file_selection.browse_files_or_folder()

    def _handle_file_or_folder(self, path):
        """Handle file or folder from either drag & drop or selection"""
        try:
            path_obj = Path(path)

            if not path_obj.exists():
                messagebox.showerror("Error", f"Path does not exist: {path}")
                return

            self.logger(f"📁 Processing: {path}")

            if self.on_file_callback:
                self.on_file_callback(path_obj)

        except Exception as e:
            error_msg = f"Error processing path '{path}': {e}"
            self.logger(f"❌ {error_msg}")
            messagebox.showerror("Processing Error", error_msg)

    def is_drag_drop_available(self):
        """Check if drag & drop is available"""
        return self.drag_drop.is_available()

    def set_file_callback(self, callback):
        """Set the callback for file processing"""
        self.on_file_callback = callback
        self.drag_drop.set_drop_callback(self._handle_file_or_folder)
        self.file_selection.set_selection_callback(self._handle_file_or_folder)

    def enable(self):
        """Enable the drop area handler"""
        self.drag_drop.enable()

    def disable(self):
        """Disable the drop area handler"""
        self.drag_drop.disable()

    def destroy(self):
        """Clean up the handler"""
        self.drag_drop.destroy()