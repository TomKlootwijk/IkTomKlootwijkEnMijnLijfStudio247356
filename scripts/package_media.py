"""Package selected original media files as verified ZIP parts of at most 80 MiB.

Files retain their paths relative to --source-root and their original bytes.
The bundle uses the same checksums schema and restore_archive.py as playlist
bundles. Only Python's standard library is required.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
import tempfile
import zipfile

from package_archive import MAX_PART_BYTES, add_file, split_zip
from restore_archive import safe_members, verify_source_contents


def package_media(files, source_root, output, parts_dir, bundle_id, part_size=MAX_PART_BYTES):
    source_root, output, parts_dir = (Path(path).resolve() for path in (source_root, output, parts_dir))
    if not source_root.is_dir():
        raise ValueError("Source root must be an existing directory")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", bundle_id):
        raise ValueError("Bundle ID may contain only letters, digits, underscores, and hyphens")
    if not 0 < part_size <= MAX_PART_BYTES:
        raise ValueError("Part size must be positive and no greater than 80 MiB")
    if output.suffix.lower() != ".zip":
        raise ValueError("--output must name a .zip file")
    if output.is_relative_to(parts_dir):
        raise ValueError("The full ZIP must be outside the directory containing Git-sized parts")

    sources = []
    seen = set()
    for filename in files:
        supplied = Path(filename)
        if not supplied.is_absolute():
            supplied = source_root / supplied
        source = supplied.resolve()
        if supplied.is_symlink() or not source.is_relative_to(source_root) or not source.is_file():
            raise ValueError(f"Source must be a regular file inside the source root: {filename}")
        if source.stat().st_size == 0:
            raise ValueError(f"Source is empty: {filename}")
        if source == output or source.is_relative_to(parts_dir):
            raise ValueError(f"A package destination cannot be a source: {filename}")
        member = source.relative_to(source_root).as_posix()
        if member.casefold() in seen:
            raise ValueError(f"Duplicate source file: {filename}")
        seen.add(member.casefold())
        sources.append((source, member))
    if not sources:
        raise ValueError("At least one source file is required")
    fingerprints = {source: (source.stat().st_size, source.stat().st_mtime_ns) for source, _ in sources}

    output.parent.mkdir(parents=True, exist_ok=True)
    parts_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".media-package-", dir=output.parent) as zip_stage:
        staged_zip = Path(zip_stage) / output.name
        with zipfile.ZipFile(staged_zip, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=6, allowZip64=True) as zipped:
            source_files = [add_file(zipped, source, member) for source, member in sources]
        for source, expected in fingerprints.items():
            if (source.stat().st_size, source.stat().st_mtime_ns) != expected:
                raise ValueError(f"Source changed while packaging: {source}")
        with zipfile.ZipFile(staged_zip) as zipped:
            members = safe_members(zipped)
            verify_source_contents(zipped, members, source_files)
        with tempfile.TemporaryDirectory(prefix=".media-parts-", dir=parts_dir.parent) as parts_stage:
            staged_parts = Path(parts_stage)
            zip_record, parts = split_zip(staged_zip, staged_parts, output.name, part_size)
            checksums = {
                "schema_version": 1,
                "bundle_id": bundle_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "description": "Original supplied media; ZIP members preserve repo-relative filenames and exact bytes.",
                "timestamp_note": "Dates in source filenames and ZIP file metadata are not verified recording or chat message timestamps.",
                "zip": zip_record,
                "part_size_limit_bytes": part_size,
                "parts": parts,
                "source_total_bytes": sum(item["bytes"] for item in source_files),
                "source_files": source_files,
                "restore": f"python scripts/restore_archive.py {parts_dir.relative_to(source_root).as_posix() if parts_dir.is_relative_to(source_root) else parts_dir.as_posix()}/checksums.json --output output/archives/{output.name} --extract tmp/{bundle_id}-restored",
            }
            (staged_parts / "checksums.json").write_text(
                json.dumps(checksums, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            parts_dir.mkdir(parents=True, exist_ok=True)
            expected_names = {item["file"] for item in parts}
            pattern = re.compile(re.escape(output.name) + r"\.part\d{3,}$")
            for existing in parts_dir.iterdir():
                if pattern.fullmatch(existing.name) and existing.name not in expected_names:
                    raise ValueError(f"Destination contains a stale part; move it aside before repackaging: {existing}")
            for name in [*expected_names, "checksums.json"]:
                destination = parts_dir / name
                if destination.exists() and (not destination.is_file() or destination.is_symlink()):
                    raise ValueError(f"Unexpected destination object: {destination}")
            for part in parts:
                (staged_parts / part["file"]).replace(parts_dir / part["file"])
            (staged_parts / "checksums.json").replace(parts_dir / "checksums.json")
            staged_zip.replace(output)
    return checksums


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path, help="Files to preserve, relative to --source-root or absolute")
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    parser.add_argument("--output", required=True, type=Path, help="Ignored local full ZIP path")
    parser.add_argument("--parts-dir", required=True, type=Path, help="Directory for ZIP parts and checksums.json")
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument("--part-size-mib", type=int, default=80, choices=range(1, 81), metavar="1..80")
    args = parser.parse_args()
    try:
        result = package_media(args.files, args.source_root, args.output, args.parts_dir,
                               args.bundle_id, args.part_size_mib * 1024 * 1024)
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
        print(f"Packaging refused: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"zip": str(args.output.resolve()),
                      "checksums": str((args.parts_dir / "checksums.json").resolve()),
                      "zip_bytes": result["zip"]["bytes"], "sha256": result["zip"]["sha256"],
                      "parts": len(result["parts"]), "source_files": len(result["source_files"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
