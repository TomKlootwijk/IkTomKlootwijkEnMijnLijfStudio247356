"""Join ZIP parts, validate SHA-256 hashes, and optionally extract safely.

Usage: python scripts/restore_archive.py archive-bundles/PLAYLIST/checksums.json
           --output output/archives/PLAYLIST.zip --extract restored

Only Python's standard library is required. Part order comes from checksums.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
import zipfile


BLOCK_BYTES = 1024 * 1024
MAX_PART_BYTES = 80 * 1024 * 1024


def valid_record(record, label, allow_empty=False):
    if not isinstance(record, dict):
        raise ValueError(f"Invalid {label} record")
    size, digest = record.get("bytes"), record.get("sha256")
    if not isinstance(size, int) or isinstance(size, bool) or size < (0 if allow_empty else 1):
        raise ValueError(f"Invalid byte count for {label}")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError(f"Invalid SHA-256 for {label}")


def part_path(directory, name):
    if not isinstance(name, str) or not name or Path(name).name != name or "/" in name or "\\" in name or ":" in name:
        raise ValueError(f"Unsafe part filename: {name!r}")
    path = directory / name
    if path.is_symlink() or not path.resolve().is_relative_to(directory) or not path.is_file():
        raise ValueError(f"Missing or unsafe part: {name}")
    return path


def safe_members(zipped):
    seen = set()
    reserved = {"CON", "PRN", "AUX", "NUL"} | {f"{prefix}{i}" for prefix in ("COM", "LPT") for i in range(1, 10)}
    result = []
    for member in zipped.infolist():
        name = member.filename
        pure = PurePosixPath(name)
        if (not name or "\\" in name or "\x00" in name or pure.is_absolute()
                or ".." in pure.parts or not pure.parts):
            raise ValueError(f"Unsafe ZIP member path: {name!r}")
        for part in pure.parts:
            if (":" in part or part.endswith((" ", "."))
                    or part.split(".")[0].upper() in reserved):
                raise ValueError(f"Unsafe ZIP member path: {name!r}")
        mode = member.external_attr >> 16
        if stat.S_ISLNK(mode) or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
            raise ValueError(f"Unsupported ZIP member type: {name!r}")
        normalized = pure.as_posix().casefold()
        if normalized in seen:
            raise ValueError(f"Duplicate ZIP member path: {name!r}")
        seen.add(normalized)
        result.append((member, pure))
    return result


def extract_archive(zipped, members, target, source_records):
    target = Path(target).resolve()
    if target.exists() and (not target.is_dir() or next(target.iterdir(), None) is not None):
        raise ValueError("Extraction destination must be absent or an empty directory")
    source_map = {item["path"]: item for item in source_records}
    file_members = {member.filename for member, _ in members if not member.is_dir()}
    if source_map and set(source_map) != file_members:
        raise ValueError("ZIP contents differ from the source file inventory")
    # Check every destination before extracting any bytes.
    destinations = []
    for member, pure in members:
        destination = target.joinpath(*pure.parts)
        if not destination.resolve().is_relative_to(target):
            raise ValueError(f"ZIP member escapes extraction destination: {member.filename}")
        destinations.append((member, destination))
    target.mkdir(parents=True, exist_ok=True)
    for member, destination in destinations:
        if member.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        with zipped.open(member) as reader, destination.open("xb") as writer:
            while block := reader.read(BLOCK_BYTES):
                writer.write(block)
                digest.update(block)
                size += len(block)
        expected = source_map.get(member.filename)
        if expected is not None and (size != expected["bytes"] or digest.hexdigest() != expected["sha256"]):
            raise ValueError(f"Extracted file failed source SHA-256 validation: {member.filename}")


def restore_archive(checksums_path, output, extract=None):
    checksums_path, output = Path(checksums_path).resolve(), Path(output).resolve()
    checksums = json.loads(checksums_path.read_text(encoding="utf-8-sig"))
    if checksums.get("schema_version") != 1:
        raise ValueError("Unsupported checksums schema version")
    zip_record = checksums.get("zip")
    valid_record(zip_record, "ZIP")
    parts = checksums.get("parts")
    if not isinstance(parts, list) or not parts:
        raise ValueError("Checksums must contain an ordered, nonempty list of parts")
    part_files = []
    seen = set()
    for index, record in enumerate(parts, 1):
        valid_record(record, f"part {index}")
        if record["bytes"] > MAX_PART_BYTES:
            raise ValueError(f"Part {index} exceeds 80 MiB")
        name = record.get("file")
        if name != f"{zip_record.get('file')}.part{index:03d}" or name in seen:
            raise ValueError(f"Part {index} is missing, duplicated, or out of order")
        seen.add(name)
        part_files.append((part_path(checksums_path.parent, name), record))
    if sum(record["bytes"] for _, record in part_files) != zip_record["bytes"]:
        raise ValueError("Part byte counts do not equal the full ZIP byte count")
    if output == checksums_path or any(output == path.resolve() for path, _ in part_files):
        raise ValueError("Output cannot replace the checksums file or a ZIP part")
    source_records = checksums.get("source_files", [])
    if not isinstance(source_records, list):
        raise ValueError("Invalid source file inventory")
    for record in source_records:
        valid_record(record, "source file", allow_empty=True)
        if not isinstance(record.get("path"), str):
            raise ValueError("Invalid source file path")
    if len({item["path"] for item in source_records}) != len(source_records):
        raise ValueError("Duplicate source file inventory path")

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".playlist-restore-", dir=output.parent) as stage:
        staged_zip = Path(stage) / "restored.zip"
        full_hash = hashlib.sha256()
        total = 0
        with staged_zip.open("wb") as writer:
            for path, record in part_files:
                digest = hashlib.sha256()
                size = 0
                with path.open("rb") as reader:
                    while block := reader.read(BLOCK_BYTES):
                        writer.write(block)
                        digest.update(block)
                        full_hash.update(block)
                        size += len(block)
                if size != record["bytes"] or digest.hexdigest() != record["sha256"]:
                    raise ValueError(f"Part failed SHA-256 or byte-count validation: {path.name}")
                total += size
        if total != zip_record["bytes"] or full_hash.hexdigest() != zip_record["sha256"]:
            raise ValueError("Reconstructed ZIP failed SHA-256 or byte-count validation")
        with zipfile.ZipFile(staged_zip) as zipped:
            members = safe_members(zipped)
            if extract is not None:
                extract_archive(zipped, members, extract, source_records)
            elif zipped.testzip() is not None:
                raise ValueError("Reconstructed ZIP failed CRC validation")
        staged_zip.replace(output)
    return {"zip": str(output), "bytes": total, "sha256": full_hash.hexdigest(),
            "parts": len(parts), "extracted_to": str(Path(extract).resolve()) if extract is not None else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checksums", type=Path, help="Path to generated checksums.json beside ZIP parts")
    parser.add_argument("--output", required=True, type=Path, help="Reconstructed full ZIP path")
    parser.add_argument("--extract", type=Path, help="Optionally extract to a new or empty directory")
    args = parser.parse_args()
    try:
        result = restore_archive(args.checksums, args.output, args.extract)
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
        print(f"Restoration refused: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
