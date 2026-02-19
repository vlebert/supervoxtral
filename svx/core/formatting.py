"""
Transcript formatting utilities for SuperVoxtral.

Formats diarized transcription segments into human-readable text with
speaker labels and timestamps.
"""

from __future__ import annotations

from svx.providers.base import TranscriptionSegment

__all__ = [
    "format_diarized_transcript",
]


def _format_timestamp(seconds: float) -> str:
    """Format seconds into MM:SS or HH:MM:SS depending on duration."""
    total_seconds = int(seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_diarized_transcript(segments: list[TranscriptionSegment]) -> str:
    """
    Format diarized transcription segments into readable text with speaker labels.

    Groups consecutive segments from the same speaker and formats them as:
        [00:00:12 - 00:00:45] Speaker 1:
        Hello everyone, welcome to the meeting.

    Args:
        segments: List of TranscriptionSegment with timing and speaker info.

    Returns:
        Formatted transcript string.
    """
    if not segments:
        return ""

    # Group consecutive segments by speaker
    groups: list[tuple[str | None, float, float, list[str]]] = []
    current_speaker: str | None = None
    current_start = 0.0
    current_end = 0.0
    current_texts: list[str] = []

    for seg in segments:
        speaker = seg["speaker_id"]
        if speaker != current_speaker and current_texts:
            groups.append((current_speaker, current_start, current_end, current_texts))
            current_texts = []
            current_start = seg["start"]

        if not current_texts:
            current_start = seg["start"]

        current_speaker = speaker
        current_end = seg["end"]
        text = seg["text"].strip()
        if text:
            current_texts.append(text)

    # Don't forget the last group
    if current_texts:
        groups.append((current_speaker, current_start, current_end, current_texts))

    # Format output
    lines: list[str] = []
    for speaker, start, end, texts in groups:
        ts_start = _format_timestamp(start)
        ts_end = _format_timestamp(end)
        combined_text = " ".join(texts)

        if speaker:
            # Capitalize speaker_id for display (e.g. "speaker_0" -> "Speaker 0")
            display_name = speaker.replace("_", " ").title()
            lines.append(f"[{ts_start} - {ts_end}] {display_name}:")
        else:
            lines.append(f"[{ts_start} - {ts_end}]")
        lines.append(combined_text)
        lines.append("")  # blank line separator

    return "\n".join(lines).rstrip()
