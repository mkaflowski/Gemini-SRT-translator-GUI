"""
Main entry point for the CLI Wrapper GUI application.
Enhanced version with better error handling, fallback options, and CustomTkinter support.
"""

import os
import sys
import tkinter as tk
from pathlib import Path


def check_python_requirements():
    """Check Python version and basic requirements"""
    issues = []

    # Check Python version
    if sys.version_info < (3, 7):
        issues.append(f"Python 3.7+ required, current version: {sys.version}")

    # Check tkinter
    try:
        import tkinter
    except ImportError:
        issues.append("tkinter not available - required for GUI")

    return issues


def check_external_dependencies():
    """Check external dependencies with installation suggestions"""
    warnings = []

    # Check requests (required for TMDB)
    try:
        import requests
    except ImportError:
        warnings.append("requests not installed - TMDB auto-fetch will not work")
        warnings.append("Install with: pip install requests")

    # Check customtkinter (required for modern UI)
    try:
        import customtkinter
    except ImportError:
        warnings.append("customtkinter not installed - falling back to regular tkinter")
        warnings.append("Install with: pip install customtkinter")

    # Check tkinterdnd2 (optional for drag & drop)
    try:
        import tkinterdnd2
    except ImportError:
        warnings.append("tkinterdnd2 not installed - enhanced drag & drop may not work")
        warnings.append("Install with: pip install tkinterdnd2")

    return warnings


def test_imports():
    """Test if all modules can be imported"""
    import_issues = []

    modules_to_test = [
        ('gui.config_manager', 'ConfigManager'),
        ('utils.file_utils', 'extract_movie_info'),
        ('utils.cli_runner', 'CLIRunner')
    ]

    for module_name, class_name in modules_to_test:
        try:
            module = __import__(module_name, fromlist=[class_name])
            getattr(module, class_name)
        except ImportError as e:
            import_issues.append(f"Cannot import {module_name}: {e}")
        except AttributeError as e:
            import_issues.append(f"Missing {class_name} in {module_name}: {e}")

    return import_issues


def setup_python_path():
    """Ensure current directory is in Python path"""
    current_dir = str(Path(__file__).parent.absolute())
    if current_dir not in sys.path:
        sys.path.insert(0, current_dir)


def setup_macos_focus():
    """Set up macOS-specific window focusing"""
    try:
        os.system('''/usr/bin/osascript -e 'tell app "Finder" to set frontmost of process "Python" to true' ''')
    except Exception:
        pass


def check_customtkinter_support():
    """Check if CustomTkinter is available and working"""
    try:
        import customtkinter as ctk
        # Test if CustomTkinter can be initialized
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        return True, ctk
    except ImportError:
        return False, None
    except Exception as e:
        print(f"⚠️  CustomTkinter available but initialization failed: {e}")
        return False, None


def create_root_window(use_customtkinter=True):
    """Create appropriate root window based on available libraries"""
    has_ctk, ctk = check_customtkinter_support()

    if use_customtkinter and has_ctk:
        try:
            # Set CustomTkinter appearance BEFORE creating window
            ctk.set_appearance_mode("dark")  # "System", "Dark", "Light"
            ctk.set_default_color_theme("blue")  # "blue", "green", "dark-blue"

            # Create CustomTkinter root window
            root = ctk.CTk()
            print("✅ Using CustomTkinter (modern dark UI)")
            return root, "customtkinter"
        except Exception as e:
            print(f"⚠️  CustomTkinter failed, falling back to tkinter: {e}")

    # Fallback to regular tkinter
    root = tk.Tk()
    # Try to set dark-ish colors for regular tkinter
    try:
        root.configure(bg='#2b2b2b')
        print("✅ Using regular tkinter (basic UI)")
    except Exception:
        print("✅ Using regular tkinter (system UI)")

    return root, "tkinter"


def try_import_gui():
    """Try to import the GUI with multiple fallback strategies"""
    gui_class = None
    import_source = None

    # Strategy 1: Try new modular structure
    try:
        from gui.main_window import DragDropGUI
        gui_class = DragDropGUI
        import_source = "modular structure (gui/main_window.py)"
    except ImportError as e:
        print(f"❌ Modular structure failed: {e}")

    # Strategy 2: Try importing from original file (if renamed)
    if gui_class is None:
        try:
            from paste import DragDropGUI
            gui_class = DragDropGUI
            import_source = "original file (paste.py)"
        except ImportError:
            print("❌ Original paste.py not found")

    # Strategy 3: Try if the original file was renamed to something else
    if gui_class is None:
        possible_names = ['paste_original', 'original_gui', 'gui_original']
        for name in possible_names:
            try:
                module = __import__(name)
                gui_class = getattr(module, 'DragDropGUI')
                import_source = f"backup file ({name}.py)"
                break
            except (ImportError, AttributeError):
                continue

    return gui_class, import_source


def main():
    """Enhanced main entry point with comprehensive error handling and CustomTkinter support"""
    print("🚀 Starting CLI Wrapper GUI...")
    print("=" * 50)

    # Setup Python path
    setup_python_path()

    # Check Python requirements
    python_issues = check_python_requirements()
    if python_issues:
        print("❌ Python Requirements Issues:")
        for issue in python_issues:
            print(f"   • {issue}")
        return 1

    # Check external dependencies
    dep_warnings = check_external_dependencies()
    if dep_warnings:
        print("⚠️  Dependency Status:")
        for warning in dep_warnings:
            print(f"   • {warning}")
        print()

    # Test imports
    import_issues = test_imports()
    if import_issues:
        print("⚠️  Import Issues:")
        for issue in import_issues:
            print(f"   • {issue}")
        print()

    # Try to import GUI with fallback strategies
    print("🔍 Looking for GUI class...")
    gui_class, import_source = try_import_gui()

    if gui_class is None:
        print("❌ Could not find GUI class. Options:")
        print("   1. Run 'python migrate.py' to set up modular structure")
        print("   2. Rename original file to paste.py")
        print("   3. Check that all files are in the correct locations")
        return 1

    print(f"✅ Found GUI class from: {import_source}")

    try:
        # Create root window (try CustomTkinter first, fallback to tkinter)
        print("🎨 Setting up GUI window...")
        root, ui_type = create_root_window(use_customtkinter=True)

        # macOS-specific setup
        if sys.platform == "darwin":
            setup_macos_focus()

        # Create application
        print("🔧 Initializing application...")
        app = gui_class(root)

        print(f"✅ GUI started successfully using {ui_type}!")
        if ui_type == "customtkinter":
            print("🌙 Dark theme enabled - enjoy the modern UI!")
        print("=" * 50)

        # Start main loop
        root.mainloop()

        return 0

    except KeyboardInterrupt:
        print("\n👋 Application closed by user")
        return 0
    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("\n🔧 Troubleshooting:")
        print("   • Check that all files are in the correct directories")
        print("   • Verify __init__.py files exist in gui/ and utils/")
        print("   • Run from the project root directory")
        print("   • Try: pip install customtkinter")
        return 1
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        print(f"   Error type: {type(e).__name__}")

        # Show stack trace for debugging
        import traceback
        print("\n🐛 Stack trace:")
        traceback.print_exc()

        print("\n🔧 Troubleshooting:")
        print("   • Check the console output above for specific errors")
        print("   • Ensure all dependencies are installed")
        print("   • Try: pip install customtkinter requests tkinterdnd2")
        print("   • Try running 'python migrate.py' first")
        return 1


if __name__ == "__main__":
    if sys.platform.startswith('win'):
        # Try to set UTF-8 encoding for console output
        try:
            sys.stdout.reconfigure(encoding='utf-8')
            sys.stderr.reconfigure(encoding='utf-8')
        except AttributeError:
            # For older Python versions
            import codecs
            sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'replace')
            sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'replace')

    exit_code = main()
    sys.exit(exit_code)