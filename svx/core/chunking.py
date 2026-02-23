"""
Audio chunking utilities for SuperVoxtral.

Provides splitting of long WAV files into overlapping chunks and merging
of transcription results back into a single coherent output.

Dependencies:
- soundfile (for reading/writing WAV data)
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

import soundfile as sf

from svx.providers.base import TranscriptionSegment

__all__ = [
    "ChunkInfo",
    "split_wav",
    "merge_segments",
    "merge_texts",
]


@dataclass
class ChunkInfo:
    """Metadata for a single audio chunk."""

    index: int
    path: Path
    start_seconds: float
    end_seconds: float


def split_wav(
    wav_path: Path,
    chunk_duration: int = 300,
    overlap: int = 30,
    output_dir: Path | None = None,
) -> list[ChunkInfo]:
    """
    Split a WAV file into overlapping chunks.

    Args:
        wav_path: Path to the source WAV file.
        chunk_duration: Duration of each chunk in seconds.
        overlap: Overlap between consecutive chunks in seconds.
        output_dir: Directory for chunk files. Uses a temp dir if None.

    Returns:
        List of ChunkInfo with paths to the chunk WAV files.
    """
    info = sf.info(str(wav_path))
    samplerate = info.samplerate
    total_frames = info.frames
    total_duration = total_frames / samplerate

    if total_duration <= chunk_duration:
        return [ChunkInfo(index=0, path=wav_path, start_seconds=0.0, end_seconds=total_duration)]

    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="svx_chunks_"))
    else:
        output_dir.mkdir(parents=True, exist_ok=True)

    step = chunk_duration - overlap
    chunks: list[ChunkInfo] = []
    chunk_idx = 0
    start = 0.0

    while start < total_duration:
        end = min(start + chunk_duration, total_duration)
        start_frame = int(start * samplerate)
        end_frame = int(end * samplerate)
        num_frames = end_frame - start_frame

        # Read the chunk data
        data, _ = sf.read(str(wav_path), start=start_frame, frames=num_frames, dtype="int16")

        chunk_path = output_dir / f"chunk_{chunk_idx:03d}.wav"
        sf.write(
            str(chunk_path),
            data,
            samplerate,
            subtype="PCM_16",
        )

        chunks.append(
            ChunkInfo(
                index=chunk_idx,
                path=chunk_path,
                start_seconds=start,
                end_seconds=end,
            )
        )
        logging.debug("Chunk %d: %.1fs - %.1fs -> %s", chunk_idx, start, end, chunk_path)

        chunk_idx += 1
        start += step
        if end >= total_duration:
            break

    logging.info(
        "Split %s (%.1fs) into %d chunks of %ds with %ds overlap",
        wav_path.name,
        total_duration,
        len(chunks),
        chunk_duration,
        overlap,
    )
    return chunks


def merge_segments(
    chunks: list[ChunkInfo],
    chunk_results: list[list[TranscriptionSegment]],
) -> list[TranscriptionSegment]:
    """
    Merge transcription segments from overlapping chunks into a single list.

    For overlap zones, uses a crossfade strategy: keeps segments from chunk_i
    for the first half of the overlap and segments from chunk_{i+1} for the second half.

    Args:
        chunks: List of ChunkInfo describing each chunk's time boundaries.
        chunk_results: List of segment lists, one per chunk.

    Returns:
        Merged and deduplicated list of TranscriptionSegment sorted by start time.
    """
    if len(chunks) != len(chunk_results):
        raise ValueError("chunks and chunk_results must have the same length")

    if len(chunks) == 0:
        return []

    if len(chunks) == 1:
        # Adjust timestamps for single chunk
        return _adjust_timestamps(chunks[0], chunk_results[0])

    merged: list[TranscriptionSegment] = []

    for i, (chunk, segments) in enumerate(zip(chunks, chunk_results)):
        adjusted = _adjust_timestamps(chunk, segments)

        if i == 0:
            # First chunk: keep everything up to the midpoint of the overlap with next chunk
            overlap_mid = chunk.end_seconds - (chunk.end_seconds - chunks[i + 1].start_seconds) / 2
            merged.extend(seg for seg in adjusted if seg["start"] < overlap_mid)
        elif i == len(chunks) - 1:
            # Last chunk: keep everything from the midpoint of the overlap with previous chunk
            overlap_mid = (
                chunks[i - 1].end_seconds - (chunks[i - 1].end_seconds - chunk.start_seconds) / 2
            )
            merged.extend(seg for seg in adjusted if seg["start"] >= overlap_mid)
        else:
            # Middle chunk: bounded by both overlap midpoints
            prev_overlap_mid = (
                chunks[i - 1].end_seconds - (chunks[i - 1].end_seconds - chunk.start_seconds) / 2
            )
            next_overlap_mid = (
                chunk.end_seconds - (chunk.end_seconds - chunks[i + 1].start_seconds) / 2
            )
            merged.extend(
                seg for seg in adjusted if prev_overlap_mid <= seg["start"] < next_overlap_mid
            )

    merged.sort(key=lambda seg: seg["start"])
    logging.info("Merged %d segments from %d chunks", len(merged), len(chunks))
    return merged


def _adjust_timestamps(
    chunk: ChunkInfo, segments: list[TranscriptionSegment]
) -> list[TranscriptionSegment]:
    """Adjust segment timestamps to absolute positions based on chunk offset."""
    offset = chunk.start_seconds
    if offset == 0.0:
        return segments

    adjusted: list[TranscriptionSegment] = []
    for seg in segments:
        adjusted.append(
            TranscriptionSegment(
                text=seg["text"],
                start=seg["start"] + offset,
                end=seg["end"] + offset,
                speaker_id=seg["speaker_id"],
                score=seg["score"],
            )
        )
    return adjusted


def merge_texts(chunks: list[ChunkInfo], texts: list[str], overlap: int) -> str:
    """
    Merge transcription texts from overlapping chunks via simple concatenation.

    This is a fallback when segment-level data is not available.
    The prompt transformation step (step 2) can clean up any duplicated text
    at chunk boundaries.

    Args:
        chunks: List of ChunkInfo (unused beyond validation, kept for API consistency).
        texts: List of transcription texts, one per chunk.
        overlap: Overlap duration in seconds (informational).

    Returns:
        Concatenated text with double-newline separators.
    """
    if len(chunks) != len(texts):
        raise ValueError("chunks and texts must have the same length")

    return "\n\n".join(t.strip() for t in texts if t.strip())
