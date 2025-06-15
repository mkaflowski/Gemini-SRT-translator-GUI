# 🎬 Gemini SRT Translator GUI

A powerful GUI application for translating SRT subtitle files using Google's Gemini AI. This is a user-friendly graphical interface for the [Gemini SRT Translator](https://github.com/MaKTaiL/gemini-srt-translator) command-line tool, featuring automatic movie/TV series detection, TMDB integration for context, and intelligent batch processing.

![1](https://github.com/user-attachments/assets/d532a1b5-2ec0-4c2b-b053-f60f5b6650b7)

## ✨ Features

### 🚀 Core Functionality
- **Drag & Drop Interface** - Simply drag subtitle files or folders
- **Automatic Translation** - Uses Google Gemini AI for high-quality translations
- **Smart Content Detection** - Automatically detects movies vs TV series
- **Batch Processing** - Handle multiple subtitle files at once
- **Language Code Support** - Customizable output language codes

### 🎭 Movie & TV Integration
- **TMDB Integration** - Automatic movie/TV series information fetching
- **Context-Aware Translation** - Uses movie plot and character info for better translations
- **Smart TV Series Detection** - Automatically detects TV series from multiple episode files
- **Overview Integration** - Includes movie/show descriptions for translation context

### ⚙️ Advanced Features
- **Multiple Gemini Models** - Support for various Gemini AI models
- **Batch Size Optimization** - Automatic batch sizing for Gemini 2.0 models
- **Audio Extraction** - Optional audio extraction from video files
- **Clean Filename Output** - Removes old language codes and adds new ones
- **Persistent Configuration** - Saves all settings between sessions

## 🛠️ Installation

### Prerequisites
- Python 3.8 or higher
- Google Gemini API key
- TMDB API key (optional, for movie context)

## 🚀 Usage

### Basic Workflow
1. **Launch** the application
2. **Enter API keys** in the settings
3. **Drag & drop** subtitle files or folders
4. **Auto-detection** identifies movies/TV series
5. **Review** extracted information
6. **Click Translate** to start processing

### Output Examples
- **Input:** `Movie.ita.srt`
- **Output:** `Movie.pl.srt` (Italian removed, Polish added)

## ⚙️ Configuration

### Settings Panel
- **Language** - Target translation language
- **Language Code** - Custom code for output filenames
- **Model Selection** - Choose Gemini AI model
- **Batch Size** - Automatic for Gemini 2.0 models
- **Audio Extraction** - Extract audio from video files

### Auto-Features
- **Auto-fetch TMDB** - Automatically get movie/TV info
- **TV Series Detection** - Smart detection from file patterns
- **Batch Size Optimization** - Optimized for different Gemini models

## 🎯 Use Cases

### Movie Translation
- Drag movie folder with subtitles
- Automatic movie detection and TMDB lookup
- Context-aware translation with plot information
- Clean output filenames

### TV Series Translation
- Drag series folder with multiple episodes
- Automatic TV series detection
- Series-wide context for character names
- Batch processing of all episodes

### Professional Subtitling
- High-quality Gemini AI translations
- Context preservation for character names
- Proper formatting maintenance
- Batch processing for efficiency

## 🔗 Related Projects

This GUI is built as a frontend for the [Gemini SRT Translator](https://github.com/MaKTaiL/gemini-srt-translator) command-line tool. For advanced users who prefer command-line interfaces or want to integrate subtitle translation into scripts, check out the original project.

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- **[MaKTaiL](https://github.com/MaKTaiL)** - For the original Gemini SRT Translator command-line tool
- **Google Gemini AI** - For powerful translation capabilities
- **TMDB** - For movie and TV series metadata
- **tkinterdnd2** - For drag and drop functionality
- **PyInstaller** - For executable creation

---

**⭐ Star this repo if you find it helpful!**
