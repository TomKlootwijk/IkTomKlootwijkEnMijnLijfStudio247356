"""Validate a completed playlist archive, ZIP it, and make Git-sized binary parts.

The parts are consecutive bytes of one ordinary ZIP, not independent ZIP files.
Use restore_archive.py and the generated checksums.json to reconstruct it.
Only Python's standard library is required.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
import zipfile


MAX_PART_BYTES = 80 * 1024 * 1024
BLOCK_BYTES = 1024 * 1024


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def archive_file(root, relative, label):
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError(f"Invalid {label} path: {relative!r}")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or any(":" in part for part in pure.parts):
        raise ValueError(f"Unsafe {label} path: {relative!r}")
    path = root.joinpath(*pure.parts)
    if not path.resolve().is_relative_to(root) or path.is_symlink():
        raise ValueError(f"{label} is outside the archive or is a symbolic link: {relative}")
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"Missing or empty {label}: {relative}")
    return path


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_transcript(path, entry):
    transcript = read_json(path)
    if transcript.get("complete") is not True or transcript.get("id") != entry["id"]:
        raise ValueError(f"Incomplete or mismatched transcript: {path.name}")
    if transcript.get("title") != entry.get("title"):
        raise ValueError(f"Transcript title does not match the manifest: {path.name}")
    segments = transcript.get("segments")
    if not isinstance(segments, list):
        raise ValueError(f"Transcript segments must be a list: {path.name}")
    previous_start = -1
    duration = transcript.get("duration")
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise ValueError(f"Malformed segment {index} in {path.name}")
        start, end, text = (segment.get(key) for key in ("start", "end", "text"))
        if (not number(start) or not number(end) or start < 0 or end < start
                or start < previous_start or not isinstance(text, str)):
            raise ValueError(f"Malformed segment {index} in {path.name}")
        if number(duration) and end > duration + 5:
            raise ValueError(f"Segment {index} exceeds audio duration in {path.name}")
        previous_start = start
    return len(segments)


def validate_archive(root):
    manifest_path = archive_file(root, "manifest.json", "manifest")
    manifest = read_json(manifest_path)
    if manifest.get("download_complete") is not True:
        raise ValueError("Downloads are not complete; refusing to package a partial archive")
    playlist_id = manifest.get("id")
    if not isinstance(playlist_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", playlist_id):
        raise ValueError("Manifest has an invalid playlist ID")
    inventory = read_json(archive_file(root, "playlist-inventory.json", "playlist inventory"))
    entries = manifest.get("entries")
    inventory_entries = inventory.get("entries")
    if (not isinstance(entries, list) or not entries or not isinstance(inventory_entries, list)
            or inventory.get("id") != playlist_id or len(entries) != len(inventory_entries)
            or manifest.get("listed_entries") != len(entries)):
        raise ValueError("Manifest and frozen playlist inventory do not describe the same playlist")

    status_path = root / "transcription-status.json"
    status = read_json(status_path) if status_path.is_file() else None
    if status is not None and (status.get("complete") is not True or status.get("current") is not None):
        raise ValueError("Transcription worker is not complete; refusing to package changing files")

    counts = {"listed_entries": len(entries), "downloaded": 0, "unavailable": 0, "transcript_segments": 0,
              "youtube_hidden_unavailable_count": manifest.get("youtube_hidden_unavailable_count", 0)}
    for position, (entry, original) in enumerate(zip(entries, inventory_entries), 1):
        if (not isinstance(entry, dict) or not isinstance(original, dict)
                or entry.get("position") != position or not entry.get("id")
                or original.get("id") != entry.get("id")):
            raise ValueError(f"Inventory record missing or mismatched at position {position}")
        download_status = entry.get("download_status")
        if download_status == "unavailable":
            if not isinstance(entry.get("error"), str) or not entry["error"].strip():
                raise ValueError(f"Unavailable entry {position} needs an explicit recorded reason")
            if original.get("title") is None and entry.get("title") is not None:
                raise ValueError(f"Unavailable entry {position} has a fabricated source title")
            counts["unavailable"] += 1
            continue
        if download_status != "downloaded":
            raise ValueError(f"Entry {position} is {download_status!r}; only downloaded or unavailable entries can be packaged")
        audio = archive_file(root, entry.get("audio"), f"audio for entry {position}")
        if entry.get("audio_bytes") is not None and audio.stat().st_size != entry["audio_bytes"]:
            raise ValueError(f"Audio byte count changed for entry {position}")
        metadata = read_json(archive_file(root, entry.get("metadata"), f"metadata for entry {position}"))
        if metadata.get("id") != entry["id"] or metadata.get("title") != entry.get("title"):
            raise ValueError(f"Metadata does not match entry {position}")
        transcript_json = archive_file(root, entry.get("transcript_json"), f"JSON transcript for entry {position}")
        transcript_text = archive_file(root, entry.get("transcript_text"), f"text transcript for entry {position}")
        if not transcript_text.read_text(encoding="utf-8-sig").strip():
            raise ValueError(f"Empty text transcript for entry {position}")
        counts["transcript_segments"] += validate_transcript(transcript_json, entry)
        if status is not None and status.get("entries", {}).get(entry["id"], {}).get("status") != "completed":
            raise ValueError(f"Transcription status is not completed for entry {position}")
        counts["downloaded"] += 1
    return manifest, counts


def add_file(zipped, source, member):
    before = source.stat()
    digest = hashlib.sha256()
    size = 0
    info = zipfile.ZipInfo.from_file(source, member)
    info.compress_type = zipfile.ZIP_DEFLATED
    info._compresslevel = 6
    with source.open("rb") as reader, zipped.open(info, "w", force_zip64=True) as writer:
        while block := reader.read(BLOCK_BYTES):
            digest.update(block)
            size += len(block)
            writer.write(block)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or size != before.st_size:
        raise ValueError(f"Source changed while packaging: {source}")
    return {"path": member, "bytes": size, "sha256": digest.hexdigest()}


def split_zip(source, destination, zip_name, part_size):
    parts = []
    complete_hash = hashlib.sha256()
    total = 0
    with source.open("rb") as reader:
        while first_block := reader.read(min(BLOCK_BYTES, part_size)):
            name = f"{zip_name}.part{len(parts) + 1:03d}"
            part_hash = hashlib.sha256()
            written = 0
            with (destination / name).open("wb") as writer:
                block = first_block
                while block:
                    writer.write(block)
                    part_hash.update(block)
                    complete_hash.update(block)
                    written += len(block)
                    if written == part_size:
                        break
                    block = reader.read(min(BLOCK_BYTES, part_size - written))
            total += written
            parts.append({"file": name, "bytes": written, "sha256": part_hash.hexdigest()})
    return {"file": zip_name, "bytes": total, "sha256": complete_hash.hexdigest()}, parts


def package_archive(archive, pdf, output, parts_dir, part_size=MAX_PART_BYTES, readme=None):
    archive, pdf, output, parts_dir = (Path(path).resolve() for path in (archive, pdf, output, parts_dir))
    if not 0 < part_size <= MAX_PART_BYTES:
        raise ValueError("Part size must be positive and no greater than 80 MiB")
    manifest, coverage = validate_archive(archive)
    if not pdf.is_file() or pdf.stat().st_size == 0:
        raise ValueError("The combined transcript PDF is missing or is not a PDF")
    with pdf.open("rb") as reader:
        if reader.read(5) != b"%PDF-":
            raise ValueError("The combined transcript PDF is missing or is not a PDF")
    if output.suffix.lower() != ".zip":
        raise ValueError("--output must name a .zip file")
    if output.is_relative_to(archive) or parts_dir.is_relative_to(archive):
        raise ValueError("ZIP and parts destinations must be outside the source archive")

    sources = []
    for source in sorted(archive.rglob("*")):
        if source.is_symlink() or not source.resolve().is_relative_to(archive):
            raise ValueError(f"Symbolic links are not supported in the archive: {source}")
        if source.is_file():
            if source.suffix.lower() in {".tmp", ".part", ".ytdl"}:
                raise ValueError(f"Unfinished temporary file in archive: {source.name}")
            sources.append((source, f"archive/{manifest['id']}/{source.relative_to(archive).as_posix()}"))
    sources.append((pdf, f"output/pdf/{pdf.name}"))
    if readme is None:
        readme = Path(__file__).resolve().parents[1] / "README.md"
    readme = Path(readme).resolve()
    if readme.is_file():
        sources.append((readme, "README.md"))
    for source, _ in sources:
        if source == output:
            raise ValueError("Output ZIP cannot replace a source file")
    fingerprints = {source: (source.stat().st_size, source.stat().st_mtime_ns) for source, _ in sources}

    output.parent.mkdir(parents=True, exist_ok=True)
    parts_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".playlist-package-", dir=output.parent) as zip_stage:
        staged_zip = Path(zip_stage) / output.name
        with zipfile.ZipFile(staged_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as zipped:
            source_files = [add_file(zipped, source, member) for source, member in sources]
        for source, expected in fingerprints.items():
            if (source.stat().st_size, source.stat().st_mtime_ns) != expected:
                raise ValueError(f"Source changed while packaging: {source}")
        with zipfile.ZipFile(staged_zip) as zipped:
            bad_file = zipped.testzip()
            if bad_file is not None:
                raise ValueError(f"ZIP integrity validation failed: {bad_file}")
        with tempfile.TemporaryDirectory(prefix=".playlist-parts-", dir=parts_dir.parent) as parts_stage:
            staged_parts = Path(parts_stage)
            zip_record, parts = split_zip(staged_zip, staged_parts, output.name, part_size)
            checksums = {"schema_version": 1, "playlist_id": manifest["id"], "title": manifest.get("title"),
                         "created_at": datetime.now(timezone.utc).isoformat(), "coverage": coverage,
                         "zip": zip_record, "part_size_limit_bytes": part_size, "parts": parts,
                         "source_total_bytes": sum(item["bytes"] for item in source_files),
                         "source_files": source_files,
                         "restore": "python scripts/restore_archive.py PATH/TO/checksums.json --output restored.zip --extract restored"}
            (staged_parts / "checksums.json").write_text(json.dumps(checksums, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            parts_dir.mkdir(parents=True, exist_ok=True)
            # Remove only stale parts with this exact generated archive filename.
            expected = {item["file"] for item in parts}
            pattern = re.compile(re.escape(output.name) + r"\.part\d{3,}$")
            for existing in parts_dir.iterdir():
                if pattern.fullmatch(existing.name) and existing.name not in expected:
                    if not existing.is_file() or existing.is_symlink():
                        raise ValueError(f"Unexpected object at a stale part path: {existing}")
                    existing.unlink()
            for part in parts:
                (staged_parts / part["file"]).replace(parts_dir / part["file"])
            (staged_parts / "checksums.json").replace(parts_dir / "checksums.json")
            staged_zip.replace(output)
    return checksums


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path, help="Completed playlist archive directory")
    parser.add_argument("--pdf", required=True, type=Path, help="Combined transcript PDF")
    parser.add_argument("--output", required=True, type=Path, help="Full local ZIP path")
    parser.add_argument("--parts-dir", required=True, type=Path, help="Directory for ZIP parts and checksums.json")
    parser.add_argument("--part-size-mib", type=int, default=80, choices=range(1, 81), metavar="1..80")
    parser.add_argument("--readme", type=Path, help="README to include if present; defaults to the repository README")
    args = parser.parse_args()
    try:
        result = package_archive(args.archive, args.pdf, args.output, args.parts_dir,
                                 args.part_size_mib * 1024 * 1024, args.readme)
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
        print(f"Packaging refused: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"zip": str(args.output.resolve()), "checksums": str((args.parts_dir / 'checksums.json').resolve()),
                      "zip_bytes": result["zip"]["bytes"], "sha256": result["zip"]["sha256"],
                      "parts": len(result["parts"]), "coverage": result["coverage"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
