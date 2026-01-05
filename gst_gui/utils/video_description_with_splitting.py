from google import genai
import sys
import os
import time
import argparse
import re
import subprocess
import tempfile
import shutil
from pathlib import Path


def get_video_duration(video_path: str) -> float:
    """Pobiera długość wideo w sekundach używając ffprobe"""
    try:
        result = subprocess.run(
            [
                'ffprobe', '-v', 'error',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                video_path
            ],
            capture_output=True,
            text=True,
            check=True
        )
        return float(result.stdout.strip())
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Nie można odczytać długości wideo: {e.stderr}")
    except FileNotFoundError:
        raise RuntimeError("ffprobe nie jest zainstalowany. Zainstaluj ffmpeg.")


def detect_platform() -> str:
    """Wykrywa platformę: 'mac', 'windows', 'linux'"""
    import platform
    system = platform.system().lower()
    if system == 'darwin':
        return 'mac'
    elif system == 'windows':
        return 'windows'
    return 'linux'


def preprocess_video(video_path: str, output_path: str = None) -> str:
    """
    Przetwarza wideo: przyspieszenie 2x + zmniejszenie do 360p.
    Automatycznie wybiera najlepszą metodę akceleracji dla platformy:
    - Windows/Linux z NVIDIA: CUDA + NVENC
    - macOS: VideoToolbox
    - Fallback: CPU (libx264)

    Args:
        video_path: Ścieżka do pliku wideo
        output_path: Ścieżka wyjściowa (domyślnie tymczasowy plik)

    Returns:
        Ścieżka do przetworzonego pliku
    """
    if output_path is None:
        video_file = Path(video_path)
        output_path = os.path.join(
            tempfile.gettempdir(),
            f"preprocessed_{video_file.stem}.mp4"
        )

    duration = get_video_duration(video_path)
    platform = detect_platform()

    print(f"📹 Oryginalny film: {duration / 60:.1f} minut")
    print(f"⚡ Przetwarzanie (2x speed + 360p)...")
    print(f"   Platforma: {platform}")
    print(f"   Plik wyjściowy: {output_path}")
    print(f"   Przewidywany czas wynikowy: ~{duration / 60 / 2:.1f} minut")

    # === NVIDIA CUDA (Windows/Linux) - jako string dla shell=True ===
    # Escapowanie ścieżki dla Windows
    escaped_input = video_path.replace('"', '\\"')
    escaped_output = output_path.replace('"', '\\"')

    # Wersja dla SDR (bez tonemappingu)
    cmd_cuda_shell = f'ffmpeg -y -hwaccel cuda -hwaccel_output_format cuda -c:v hevc_cuvid -i "{escaped_input}" -ss 0 -vf "scale_cuda=-2:360,setpts=0.5*PTS" -af "atempo=2.0" -ac 2 -c:v h264_nvenc -preset p1 -rc constqp -qp 29 -c:a aac -b:a 128k "{escaped_output}"'

    # Wersja dla HDR - software decode + tonemap + nvenc encode
    cmd_cuda_hdr_shell = f'ffmpeg -y -i "{escaped_input}" -ss 0 -vf "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p,scale=-2:360,setpts=0.5*PTS" -af "atempo=2.0" -ac 2 -c:v h264_nvenc -preset p1 -rc constqp -qp 29 -c:a aac -b:a 128k "{escaped_output}"'

    # Wersja uproszczona - software scale + nvenc (dla HDR bez pełnego tonemappingu)
    cmd_cuda_simple_shell = f'ffmpeg -y -hwaccel cuda -i "{escaped_input}" -ss 0 -vf "scale=-2:360,setpts=0.5*PTS,format=yuv420p" -af "atempo=2.0" -ac 2 -c:v h264_nvenc -preset p1 -rc constqp -qp 29 -pix_fmt yuv420p -c:a aac -b:a 128k "{escaped_output}"'

    # === macOS VideoToolbox ===
    cmd_videotoolbox = [
        'ffmpeg', '-y',
        '-hwaccel', 'videotoolbox',
        '-i', video_path,
        '-vf', 'scale=-2:360,setpts=0.5*PTS',
        '-af', 'atempo=2.0',
        '-ac', '2',
        '-c:v', 'h264_videotoolbox',
        '-q:v', '65',  # Jakość (0-100, wyższa = lepsza)
        '-c:a', 'aac',
        '-b:a', '128k',
        output_path
    ]

    # === CPU fallback (wszystkie platformy) ===
    cmd_cpu = [
        'ffmpeg', '-y',
        '-i', video_path,
        '-vf', 'scale=-2:360,setpts=0.5*PTS',
        '-af', 'atempo=2.0',
        '-ac', '2',
        '-c:v', 'libx264',
        '-preset', 'ultrafast',
        '-crf', '28',
        '-c:a', 'aac',
        '-b:a', '128k',
        output_path
    ]

    # Wybór metod w zależności od platformy
    if platform == 'mac':
        methods = [
            (cmd_videotoolbox, "VideoToolbox (Apple GPU)", False),
            (cmd_cpu, "CPU (libx264)", False)
        ]
    else:  # Windows/Linux
        methods = [
            (cmd_cuda_shell, "CUDA + HEVC decoder (SDR)", True),
            (cmd_cuda_simple_shell, "CUDA + yuv420p (HDR compatible)", True),
            (cmd_cuda_hdr_shell, "CUDA + HDR tonemap", True),
            (cmd_cpu, "CPU (libx264)", False)
        ]

    start_time = time.time()

    for i, (cmd, method, use_shell) in enumerate(methods):
        try:
            print(f"   Próba {i + 1}/{len(methods)}: {method}...")
            if use_shell:
                print(f"   CMD: {cmd}")

            # Użyj Popen żeby wyświetlać postęp w czasie rzeczywistym
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=use_shell
            )

            # Czytaj stderr linia po linii (tam jest postęp)
            stderr_lines = []
            last_progress_len = 0

            while True:
                line = process.stderr.readline()
                if not line and process.poll() is not None:
                    break
                if line:
                    stderr_lines.append(line)
                    # Wyświetl linie z postępem (frame=, time=, speed=)
                    if 'frame=' in line or 'size=' in line:
                        # Parsuj czas do obliczenia procentu
                        progress_info = line.strip()
                        time_match = re.search(r'time=(\d+):(\d+):(\d+\.?\d*)', line)
                        if time_match:
                            h, m, s = time_match.groups()
                            current_time = int(h) * 3600 + int(m) * 60 + float(s)
                            # Film jest przyspieszany 2x, więc docelowy czas to duration/2
                            target_duration = duration / 2
                            percent = min(100, (current_time / target_duration) * 100)
                            progress_info = f"{percent:5.1f}% | {line.strip()}"

                        clean_line = progress_info[:120]
                        print(f"\r   {clean_line}{' ' * max(0, last_progress_len - len(clean_line))}", end='',
                              flush=True)
                        last_progress_len = len(clean_line)

            # Zakończ linię postępu
            if last_progress_len > 0:
                print()

            # Sprawdź kod wyjścia
            return_code = process.wait()

            if return_code != 0:
                raise subprocess.CalledProcessError(return_code, cmd, stderr=''.join(stderr_lines))
            elapsed = time.time() - start_time
            output_size = os.path.getsize(output_path) / (1024 * 1024)
            print(f"✅ Przetworzono w {elapsed:.1f}s ({method})")
            print(f"   Rozmiar wyjściowy: {output_size:.1f} MB")
            return output_path
        except subprocess.CalledProcessError as e:
            # Weź ostatnie linie stderr - tam jest właściwy błąd
            error_lines = e.stderr.strip().split('\n') if e.stderr else []
            error_msg = '\n'.join(error_lines[-5:]) if error_lines else "Brak szczegółów błędu"
            if i < len(methods) - 1:
                print(f"   ⚠️ {method} nie zadziałało:\n      {error_msg.replace(chr(10), chr(10) + '      ')}")
                continue
            else:
                raise RuntimeError(f"Wszystkie metody enkodowania zawiodły.\nOstatni błąd:\n{error_msg}")

    return output_path


def split_video(video_path: str, segment_duration: int = 1800, output_dir: str = None) -> list[tuple[str, float]]:
    """
    Dzieli wideo na segmenty o określonej długości.

    Args:
        video_path: Ścieżka do pliku wideo
        segment_duration: Długość segmentu w sekundach (domyślnie 1800 = 30 minut)
        output_dir: Katalog na segmenty (domyślnie tymczasowy)

    Returns:
        Lista krotek (ścieżka_segmentu, offset_w_sekundach)
    """
    duration = get_video_duration(video_path)

    if duration <= segment_duration:
        return [(video_path, 0.0)]

    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="video_segments_")
        print(f"📁 Segmenty zapisane w: {output_dir}")

    video_file = Path(video_path)
    segments = []

    num_segments = int(duration // segment_duration) + (1 if duration % segment_duration > 0 else 0)
    print(f"Film trwa {duration / 3600:.2f}h - dzielenie na {num_segments} części...")

    for i in range(num_segments):
        start_time = i * segment_duration
        segment_path = os.path.join(output_dir, f"segment_{i:03d}{video_file.suffix}")

        print(f"  Wycinanie części {i + 1}/{num_segments} (od {format_time(start_time)})...")

        cmd = [
            'ffmpeg', '-y', '-v', 'error',
            '-ss', str(start_time),
            '-i', video_path,
            '-t', str(segment_duration),
            '-c', 'copy',  # Szybkie kopiowanie bez re-enkodowania
            segment_path
        ]

        try:
            subprocess.run(cmd, check=True, capture_output=True)
            segments.append((segment_path, start_time))
        except subprocess.CalledProcessError as e:
            # Jeśli copy nie działa, spróbuj z re-enkodowaniem
            cmd = [
                'ffmpeg', '-y', '-v', 'error',
                '-ss', str(start_time),
                '-i', video_path,
                '-t', str(segment_duration),
                '-c:v', 'libx264', '-preset', 'ultrafast',
                '-c:a', 'aac',
                segment_path
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            segments.append((segment_path, start_time))

    return segments


def format_time(seconds: float) -> str:
    """Formatuje sekundy do HH:MM:SS lub MM:SS"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def parse_time_to_seconds(time_str: str) -> float:
    """Parsuje timestamp do sekund"""
    parts = time_str.split(':')
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    elif len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return 0


def adjust_timestamps_with_offset(text: str, offset_seconds: float) -> str:
    """Dodaje offset do wszystkich timestampów w tekście"""

    def add_offset_bracketed(match):
        time_str = match.group(1)
        total_seconds = parse_time_to_seconds(time_str) + offset_seconds
        return f"[{format_time(total_seconds)}]"

    def add_offset_range(match):
        prefix = match.group(1) or ""
        start_seconds = parse_time_to_seconds(match.group(2)) + offset_seconds
        end_seconds = parse_time_to_seconds(match.group(3)) + offset_seconds
        suffix = match.group(4) or ""
        return f"{prefix}{format_time(start_seconds)} - {format_time(end_seconds)}{suffix}"

    # Format [MM:SS] lub [HH:MM:SS]
    text = re.sub(r'\[(\d+:\d+(?::\d+)?)\]', add_offset_bracketed, text)

    # Format zakresów
    text = re.sub(
        r'(\*\*)?(\d{1,2}:\d{2}(?::\d{2})?)\s*-\s*(\d{1,2}:\d{2}(?::\d{2}?)?)(\*\*)?',
        add_offset_range,
        text
    )

    return text


def fix_timestamps(text: str, speed_multiplier: float = 2.0) -> str:
    """Przemnaża wszystkie timestampy przez współczynnik prędkości"""

    def parse_and_multiply(time_str: str) -> tuple[int, int, int] | None:
        parts = time_str.split(':')

        if len(parts) == 2:
            minutes, seconds = int(parts[0]), int(parts[1])
            total_seconds = (minutes * 60 + seconds) * speed_multiplier
        elif len(parts) == 3:
            hours, minutes, seconds = int(parts[0]), int(parts[1]), int(parts[2])
            total_seconds = (hours * 3600 + minutes * 60 + seconds) * speed_multiplier
        else:
            return None

        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        seconds = int(total_seconds % 60)
        return (hours, minutes, seconds)

    def format_result(h, m, s):
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    def multiply_bracketed(match):
        result = parse_and_multiply(match.group(1))
        if result:
            return f"[{format_result(*result)}]"
        return match.group(0)

    def multiply_range(match):
        prefix = match.group(1) or ""
        start = parse_and_multiply(match.group(2))
        end = parse_and_multiply(match.group(3))
        suffix = match.group(4) or ""

        if start and end:
            return f"{prefix}{format_result(*start)} - {format_result(*end)}{suffix}"
        return match.group(0)

    # Format [MM:SS] lub [HH:MM:SS]
    text = re.sub(r'\[(\d+:\d+(?::\d+)?)\]', multiply_bracketed, text)

    # Format zakresów
    text = re.sub(
        r'(\*\*)?(\d{1,2}:\d{2}(?::\d{2})?)\s*-\s*(\d{1,2}:\d{2}(?::\d{2}?)?)(\*\*)?',
        multiply_range,
        text
    )

    return text


def analyze_single_segment(video_path: str, api_key: str, part_num: int = None, total_parts: int = None) -> str:
    """Analizuje pojedynczy segment wideo"""
    from google import genai

    client = genai.Client(api_key=api_key)

    part_info = f" (część {part_num}/{total_parts})" if part_num else ""
    print(f"Wysyłanie pliku{part_info}: {Path(video_path).name}...")

    uploaded_file = client.files.upload(file=video_path)

    print(f"Oczekiwanie na przetworzenie{part_info}...")
    while uploaded_file.state.name == "PROCESSING":
        time.sleep(2)
        uploaded_file = client.files.get(name=uploaded_file.name)

    if uploaded_file.state.name == "FAILED":
        raise RuntimeError(f"Przetwarzanie pliku nie powiodło się: {uploaded_file.state.name}")

    print(f"Analizowanie{part_info}...")

    prompt = """Przeanalizuj to wideo i podaj. Nie dodawaj tłumaczenia:

    ⚠️ WAŻNE O TIMESTAMPACH: Podawaj timestampy DOKŁADNIE tak jak je widzisz w filmie, bez żadnych przeliczeń ani modyfikacji. Raportuj surowy czas z wideo.
    ⚠️ KRYTYCZNE: Musisz transkrybować CAŁY film od początku do końca.
    Nie kończ przedwcześnie. Nie streszczaj. Kontynuuj aż do ostatniej sekundy filmu.
    Jeśli zbliżasz się do limitu, nadal kontynuuj - nie przerywaj w połowie.

    1. **OPIS FILMU**: Szczegółowy opis tego, co dzieje się na filmie - sceny, osoby, akcje, lokalizacje, nastrój.

    2. **TRANSKRYPCJA**: Pełna transkrypcja wszystkich wypowiadanych słów, dialogów i narracji. Ma być dosłowna - w oryginalnym języku. Najbardziej mi zależy na trankskrypcji, dlatego wypisz cały film! Pomiń piosenki.

       WAŻNE - dla każdej wypowiedzi podaj:
       - KTO mówi (imię jeśli znane, lub opis np. "Mężczyzna w niebieskiej koszuli", "Prezenterka", "Narrator")
       - DO KOGO mówi (do konkretnej osoby, do grupy, do kamery/widza, do siebie)
       - Treść wypowiedzi

       Format:
       [Czas] MÓWCA (do ODBIORCY): "treść wypowiedzi"

       Przykłady:
       [0:15] Prowadzący (do widzów): "Witajcie w dzisiejszym odcinku..."
       [0:32] Anna (do Marka): "Czy możesz mi pomóc?"
       [0:45] Mężczyzna w garniturze (do grupy przy stole): "Musimy podjąć decyzję..."
       [1:20] Narrator (narracja): "Minęły trzy lata od tamtych wydarzeń..."

       Jeśli nie ma mowy, napisz "Brak dialogów/narracji".

    3. **DODATKOWE INFORMACJE**: 
       - Szacowany czas trwania poszczególnych scen

    Odpowiedz w języku polskim."""

    response = client.models.generate_content(
        model="gemini-3-flash-preview",
        contents=[uploaded_file, prompt],
        config={
            "temperature": 0.4,
            "max_output_tokens": 65536,
        }
    )

    if hasattr(response, 'usage_metadata'):
        usage = response.usage_metadata
        print(f"   📊 Tokeny{part_info}: {usage.prompt_token_count:,} wejście, {usage.candidates_token_count:,} wyjście")

    print(f"Usuwanie pliku z serwera{part_info}...")
    client.files.delete(name=uploaded_file.name)

    return response.text

def merge_analyses(analyses: list[tuple[str, float]], speed_multiplier: float = 1.0) -> str:
    """
    Scala analizy z wielu segmentów w jedną całość.

    Args:
        analyses: Lista krotek (tekst_analizy, offset_w_sekundach)
        speed_multiplier: Współczynnik prędkości do korekty timestampów

    Returns:
        Scalony tekst analizy
    """
    if len(analyses) == 1:
        text = analyses[0][0]
        if speed_multiplier != 1.0:
            text = fix_timestamps(text, speed_multiplier)
        return text

    merged_descriptions = []
    merged_transcriptions = []
    merged_additional = []

    for i, (analysis, offset) in enumerate(analyses):
        part_header = f"\n{'=' * 40}\nCZĘŚĆ {i + 1} (od {format_time(offset * speed_multiplier)})\n{'=' * 40}\n"

        # Najpierw koryguj offset, potem prędkość
        adjusted = adjust_timestamps_with_offset(analysis, offset)
        if speed_multiplier != 1.0:
            adjusted = fix_timestamps(adjusted, speed_multiplier)

        # Próba wydzielenia sekcji
        desc_match = re.search(r'\*\*OPIS FILMU\*\*[:\s]*(.*?)(?=\*\*TRANSKRYPCJA\*\*|\*\*2\.|$)', adjusted,
                               re.DOTALL | re.IGNORECASE)
        trans_match = re.search(r'\*\*TRANSKRYPCJA\*\*[:\s]*(.*?)(?=\*\*DODATKOWE|\*\*3\.|$)', adjusted,
                                re.DOTALL | re.IGNORECASE)
        add_match = re.search(r'\*\*DODATKOWE INFORMACJE\*\*[:\s]*(.*?)$', adjusted, re.DOTALL | re.IGNORECASE)

        if desc_match:
            merged_descriptions.append(f"{part_header}{desc_match.group(1).strip()}")
        if trans_match:
            merged_transcriptions.append(f"{part_header}{trans_match.group(1).strip()}")
        if add_match:
            merged_additional.append(f"{part_header}{add_match.group(1).strip()}")

        # Jeśli nie udało się wydzielić sekcji, dodaj całość do transkrypcji
        if not trans_match:
            merged_transcriptions.append(f"{part_header}{adjusted}")

    result = []

    result.append("**Użyj tego opisu dla lepszego tłumaczenia i rozpoznawania płci dla dobrej odmiany w tłumaczeniu:\n")
    result.append("\n".join(merged_descriptions))

    if merged_descriptions:
        result.append("**OPIS FILMU** (scalony z wszystkich części):\n")
        result.append("\n".join(merged_descriptions))

    if merged_transcriptions:
        result.append("\n\n**TRANSKRYPCJA** (scalona z wszystkich części):\n")
        result.append("\n".join(merged_transcriptions))

    if merged_additional:
        result.append("\n\n**DODATKOWE INFORMACJE** (scalone z wszystkich części):\n")
        result.append("\n".join(merged_additional))

    return "\n".join(result)


def analyze_video(video_path: str, api_key: str, speed_multiplier: float = 1.0,
                  segment_duration: int = 1800, preprocess: bool = False) -> dict:
    """
    Wysyła wideo do Gemini i odbiera opis oraz transkrypcję.
    Dla filmów dłuższych niż segment_duration, dzieli je na części.

    Args:
        video_path: Ścieżka do pliku wideo
        api_key: Klucz API Google AI
        speed_multiplier: Współczynnik prędkości wideo (np. 2.0 dla 2x przyspieszonego)
        segment_duration: Maksymalna długość segmentu w sekundach (domyślnie 1800 = 30 minut)
        preprocess: Czy przetworzyć wideo przed analizą (2x speed + 360p)

    Returns:
        Słownik z opisem i transkrypcją
    """
    video_file = Path(video_path)
    if not video_file.exists():
        raise FileNotFoundError(f"Plik wideo nie istnieje: {video_path}")

    preprocessed_path = None
    temp_dir = None

    try:
        # Preprocessing jeśli włączony
        if preprocess:
            preprocessed_path = preprocess_video(video_path)
            video_path = preprocessed_path
            speed_multiplier = 2.0  # Wymuszamy 2x bo wideo jest przyspieszone
            print()

        # Sprawdź długość i ewentualnie podziel
        segments = split_video(video_path, segment_duration)

        if len(segments) > 1:
            temp_dir = os.path.dirname(segments[0][0]) if segments[0][0] != video_path else None

        # Analizuj każdy segment
        analyses = []
        for i, (segment_path, offset) in enumerate(segments):
            part_num = i + 1 if len(segments) > 1 else None
            total_parts = len(segments) if len(segments) > 1 else None

            analysis = analyze_single_segment(segment_path, api_key, part_num, total_parts)
            analyses.append((analysis, offset))

            # Krótka przerwa między segmentami aby nie przekroczyć rate limitu
            if i < len(segments) - 1:
                print("Przerwa 5s przed następnym segmentem...")
                time.sleep(5)

        # Scal wyniki
        print("\nScalanie wyników...")
        merged_analysis = merge_analyses(analyses, speed_multiplier)

        return {
            "analysis": merged_analysis,
            "file_name": video_file.name,
            "model": "gemini-3-flash-preview",
            "segments": len(segments),
            "preprocessed": preprocess
        }

    finally:
        # Usuń tymczasowe pliki
        if temp_dir and os.path.exists(temp_dir):
            print("Usuwanie tymczasowych segmentów...")
            shutil.rmtree(temp_dir)

        if preprocessed_path and os.path.exists(preprocessed_path):
            print("Usuwanie przetworzonego pliku tymczasowego...")
            os.remove(preprocessed_path)


def main():
    parser = argparse.ArgumentParser(
        description="Analizator wideo używający Gemini 2.5 Flash Preview (z automatycznym dzieleniem długich filmów)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Przykłady użycia:
  python video_analyzer.py --api_key TWOJ_KLUCZ
  python video_analyzer.py  # użyje zmiennej GOOGLE_API_KEY
  python video_analyzer.py --segment_duration 3600  # dziel co 1 godzinę
        """
    )

    parser.add_argument(
        "--api_key", "-k",
        help="Klucz API Google AI (domyślnie pobierany ze zmiennej GOOGLE_API_KEY)",
        default=None
    )

    parser.add_argument(
        "--segment_duration", "-s",
        type=int,
        default=None,
        help="Maksymalna długość segmentu w sekundach (domyślnie pytanie interaktywne)"
    )

    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("GOOGLE_API_KEY")

    if not api_key:
        print("Błąd: Nie podano klucza API.")
        print("Użyj --api_key lub ustaw zmienną środowiskową GOOGLE_API_KEY")
        sys.exit(1)

    video_path = input("Podaj ścieżkę do pliku wideo: ").strip().strip('"\'')

    if not video_path:
        print("Błąd: Nie podano ścieżki do pliku wideo.")
        sys.exit(1)

    # Pytanie o preprocessing
    preprocess_input = input(
        "Czy chcesz przetworzyć wideo przed analizą? (2x speed + 360p, znacznie szybsze) [t/N]: ").strip().lower()
    preprocess = preprocess_input in ('t', 'tak', 'y', 'yes')

    if preprocess:
        speed_multiplier = 2.0
        print("📌 Wideo zostanie przyspieszone 2x - timestampy będą automatycznie skorygowane.")
    else:
        # Pytaj o prędkość tylko gdy nie ma preprocessingu
        speed_input = input("Podaj prędkość wideo (np. 2.0 dla 2x przyspieszonego, Enter dla 1.0): ").strip()

        if speed_input:
            try:
                speed_multiplier = float(speed_input)
                if speed_multiplier <= 0:
                    print("Błąd: Prędkość musi być większa od 0.")
                    sys.exit(1)
            except ValueError:
                print("Błąd: Nieprawidłowa wartość prędkości.")
                sys.exit(1)
        else:
            speed_multiplier = 1.0

    # Pytanie o długość segmentów (jeśli nie podano w argumentach)
    if args.segment_duration is not None:
        segment_duration = args.segment_duration
    else:
        print("\n📐 Długość segmentów (dla długich filmów):")
        print("   Podpowiedzi: 900 = 15 min, 1800 = 30 min, 2700 = 45 min, 3600 = 1h")
        segment_input = input("Podaj maksymalną długość segmentu w sekundach (Enter dla 1800 = 30 min): ").strip()

        if segment_input:
            try:
                segment_duration = int(segment_input)
                if segment_duration <= 0:
                    print("Błąd: Długość segmentu musi być większa od 0.")
                    sys.exit(1)
                if segment_duration < 60:
                    print("⚠️ Uwaga: Bardzo krótkie segmenty (<60s) mogą być nieefektywne.")
            except ValueError:
                print("Błąd: Nieprawidłowa wartość długości segmentu.")
                sys.exit(1)
        else:
            segment_duration = 1800

        print(f"📌 Segmenty: {segment_duration}s ({segment_duration // 60} min)")

    try:
        result = analyze_video(
            video_path,
            api_key,
            speed_multiplier=speed_multiplier,
            segment_duration=segment_duration,
            preprocess=preprocess
        )

        # Zapis do pliku obok oryginału
        video_dir = os.path.dirname(os.path.abspath(video_path))
        output_file = os.path.join(video_dir, "transcription.txt")

        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(f"ANALIZA WIDEO: {result['file_name']}\n")
            f.write(f"Model: {result['model']}\n")
            if result['segments'] > 1:
                f.write(f"Przetworzono w {result['segments']} częściach\n")
            # if result.get('preprocessed'):
            #     f.write("Preprocessing: 2x speed + 360p (hardware accelerated)\n")
            # if speed_multiplier != 1.0:
            #     f.write(f"Korekta prędkości: x{speed_multiplier}\n")
            f.write("=" * 60 + "\n\n")
            f.write(result['analysis'])

        print("\n" + "=" * 60)
        print(f"ANALIZA WIDEO: {result['file_name']}")
        print(f"Model: {result['model']}")
        if result['segments'] > 1:
            print(f"Przetworzono w {result['segments']} częściach")
        if result.get('preprocessed'):
            print("Preprocessing: 2x speed + 360p (hardware accelerated)")
        if speed_multiplier != 1.0:
            print(f"Korekta prędkości: x{speed_multiplier}")
        print("=" * 60 + "\n")
        print(result['analysis'])
        print("\n" + "=" * 60)
        print(f"\n💾 Zapisano do: {output_file}")

    except FileNotFoundError as e:
        print(f"Błąd: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Błąd podczas analizy: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()