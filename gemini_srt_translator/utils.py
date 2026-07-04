from datetime import timedelta

# NOTE (vendored copy): the original package contained a PyPI self-upgrade
# mechanism here (upgrade_package + helpers). It was removed - this copy ships
# inside Gemini-SRT-translator-GUI and must never upgrade itself from PyPI.


def convert_timedelta_to_timestamp(td, offset=0):
    """Converts a timedelta object to a string in the format MM:SS."""
    if not isinstance(td, timedelta):
        raise TypeError("Expected a timedelta object.")

    total_seconds = td.seconds - offset
    minutes, seconds = divmod(total_seconds, 60)

    return f"{minutes:02}:{seconds:02}"


def convert_timestamp_to_timedelta(timestamp, offset=0):
    """Converts a timestamp string in the format MM:SS to a timedelta object."""
    if not isinstance(timestamp, str):
        raise TypeError("Expected a string in the format MM:SS.")

    parts = timestamp.split(":")
    if len(parts) != 2:
        raise ValueError("Timestamp must be in the format MM:SS.")

    try:
        minutes = int(parts[0])
        seconds = int(parts[1])
    except ValueError:
        raise ValueError("Minutes and seconds must be integers.")

    return timedelta(minutes=minutes, seconds=seconds + offset)
