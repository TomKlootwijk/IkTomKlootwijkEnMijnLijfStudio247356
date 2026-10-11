"""Audit every downloaded MP3 without changing the audio or playlist manifest.

Checks original title and playlist track tags, matching IDs and metadata,
recorded byte counts and durations, SHA-256, and a complete ffmpeg decode.
Requires mutagen and ffmpeg. Concurrency is bounded to two decoding workers.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile

from mutagen.mp3 import MP3


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def source_path(root, relative):
    if not isinstance(relative, str) or "\\" in relative:
        raise ValueError(f"Invalid relative source path: {relative!r}")
    pure = PurePosixPath(relative)
    if not pure.parts or pure.is_absolute() or ".." in pure.parts or any(":" in part for part in pure.parts):
        raise ValueError(f"Unsafe relative source path: {relative!r}")
    path = root.joinpath(*pure.parts)
    if not path.resolve().is_relative_to(root) or path.is_symlink():
        raise ValueError(f"Unsafe source path: {relative!r}")
    return path


def finite_number(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def audit_entry(root, entry, ffmpeg, tolerance):
    check_names = ("file_exists", "filename_id", "byte_count", "metadata_id_title",
                   "id3_title", "id3_track_position", "duration", "full_decode", "stable_source")
    result = {"position": entry.get("position"), "id": entry.get("id"), "title": entry.get("title"),
              "audio": entry.get("audio"), "passed": False,
              "checks": {name: False for name in check_names}, "errors": []}

    def check(name, passed, error):
        result["checks"][name] = bool(passed)
        if not passed:
            result["errors"].append(error)

    try:
        audio = source_path(root, entry.get("audio"))
        if not audio.is_file() or not audio.stat().st_size:
            raise ValueError("Downloaded audio is missing or empty")
        before = audio.stat()
        result["checks"]["file_exists"] = True
        result["bytes"] = before.st_size
        check("filename_id", audio.suffix.lower() == ".mp3" and f"[{entry['id']}]" in audio.name,
              "MP3 filename does not contain the exact bracketed video ID")
        check("byte_count", before.st_size == entry.get("audio_bytes"),
              f"File bytes {before.st_size} differ from manifest audio_bytes {entry.get('audio_bytes')!r}")

        metadata = read_json(source_path(root, entry.get("metadata")))
        check("metadata_id_title", metadata.get("id") == entry["id"] and metadata.get("title") == entry.get("title"),
              "Metadata ID/title differ from the manifest")

        parsed = MP3(str(audio))
        expected_title = entry.get("title")
        if expected_title is None:
            expected_title = f"Title unavailable [{entry['id']}]"
        title_frames = parsed.tags.getall("TIT2") if parsed.tags is not None else []
        title_values = [str(text) for frame in title_frames for text in frame.text]
        track_frames = parsed.tags.getall("TRCK") if parsed.tags is not None else []
        track_values = [str(text) for frame in track_frames for text in frame.text]
        result["id3_title"] = title_values
        result["id3_track_position"] = track_values
        check("id3_title", title_values == [expected_title],
              f"Embedded title differs from expected original/display title: {title_values!r}")
        check("id3_track_position", track_values == [str(entry["position"])],
              f"Embedded track position differs from manifest: {track_values!r}")

        actual_duration = parsed.info.length
        result["duration_seconds"] = round(actual_duration, 6)
        comparisons = {}
        for name in ("audio_duration", "duration"):
            expected = entry.get(name)
            if finite_number(expected):
                comparisons[name] = {"expected_seconds": expected, "delta_seconds": round(actual_duration - expected, 6)}
        result["duration_comparisons"] = comparisons
        duration_ok = (finite_number(actual_duration) and actual_duration > 0
                       and "audio_duration" in comparisons
                       and all(abs(item["delta_seconds"]) <= tolerance for item in comparisons.values()))
        check("duration", duration_ok, f"Audio duration differs from recorded duration by more than {tolerance}s: {comparisons!r}")

        digest = hashlib.sha256()
        with audio.open("rb") as reader:
            while block := reader.read(256 * 1024):
                digest.update(block)
        result["sha256"] = digest.hexdigest()

        command = [ffmpeg, "-v", "error", "-nostdin", "-threads", "1", "-i", str(audio),
                   "-map", "0:a:0", "-vn", "-sn", "-dn", "-threads", "1", "-f", "null", "-"]
        # Keep potentially verbose decoder errors on disk, with a bounded excerpt
        # in the report. No decoded PCM is buffered in Python memory.
        with tempfile.TemporaryFile() as errors:
            decoded = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=errors, check=False)
            error_bytes = errors.tell()
            errors.seek(0)
            excerpt = errors.read(4096).decode("utf-8", errors="replace").strip()
        result["ffmpeg_exit_code"] = decoded.returncode
        result["ffmpeg_error_bytes"] = error_bytes
        if excerpt:
            result["ffmpeg_error_excerpt"] = excerpt
        check("full_decode", decoded.returncode == 0 and error_bytes == 0,
              f"Full MP3 decode reported errors (exit {decoded.returncode}): {excerpt}")
        after = audio.stat()
        check("stable_source", (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns),
              "Audio changed during audit")
    except Exception as error:
        result["errors"].append(f"{type(error).__name__}: {error}")
    result["passed"] = all(result["checks"].values())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path, help="Stable playlist manifest.json")
    parser.add_argument("--output", type=Path, help="Defaults to audio-validation.json beside the manifest")
    parser.add_argument("--workers", type=int, choices=(1, 2), default=2)
    parser.add_argument("--duration-tolerance", type=float, default=3.0)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args()
    manifest_path = args.manifest.resolve()
    root = manifest_path.parent
    output = args.output.resolve() if args.output else root / "audio-validation.json"
    if output == manifest_path or output.parent != root or output.name != "audio-validation.json":
        parser.error("Audit output must be audio-validation.json beside the manifest")
    if not 0 <= args.duration_tolerance <= 3:
        parser.error("Duration tolerance must be between 0 and 3 seconds")
    ffmpeg = shutil.which(args.ffmpeg)
    if ffmpeg is None:
        parser.error("ffmpeg could not be found")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes.decode("utf-8-sig"))
    entries = manifest["entries"]
    downloaded = [entry for entry in entries if entry.get("download_status") == "downloaded"]
    report = {"schema_version": 1, "playlist_id": manifest["id"], "started_at": utc_now(),
              "complete": False, "passed": False, "workers": args.workers,
              "duration_tolerance_seconds": args.duration_tolerance,
              "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
              "ffmpeg": ffmpeg, "checks": {}, "errors": [], "entries": [],
              "unavailable": [{"position": entry["position"], "id": entry["id"], "title": entry.get("title"),
                               "reason": entry.get("error")} for entry in entries if entry.get("download_status") == "unavailable"]}
    if manifest.get("download_complete") is not True:
        report["errors"].append("Manifest download_complete is not true")
    unresolved = [entry["id"] for entry in entries if entry.get("download_status") not in ("downloaded", "unavailable")]
    if unresolved:
        report["errors"].append(f"Unresolved download entries: {unresolved!r}")
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(audit_entry, root, entry, ffmpeg, args.duration_tolerance) for entry in downloaded]
        for future in as_completed(futures):
            result = future.result()
            report["entries"].append(result)
            count = len(report["entries"])
            if not result["passed"]:
                print(f"FAILED #{result['position']} {result['id']}: " + "; ".join(result["errors"]), flush=True)
            elif count % 10 == 0 or count == len(downloaded):
                print(f"Audited {count}/{len(downloaded)} downloaded MP3s", flush=True)
    report["entries"].sort(key=lambda entry: entry["position"])
    report["checks"] = {name: {"passed": sum(entry["checks"][name] for entry in report["entries"]),
                               "failed": sum(not entry["checks"][name] for entry in report["entries"])}
                        for name in report["entries"][0]["checks"]} if report["entries"] else {}
    if manifest_path.read_bytes() != manifest_bytes:
        report["errors"].append("Manifest changed during the audit")
    report["summary"] = {"listed_entries": len(entries), "downloaded_audio": len(downloaded),
                         "audited_audio": len(report["entries"]),
                         "passed_audio": sum(entry["passed"] for entry in report["entries"]),
                         "failed_audio": sum(not entry["passed"] for entry in report["entries"]),
                         "unavailable_entries": len(report["unavailable"]),
                         "audio_bytes": sum(entry.get("bytes", 0) for entry in report["entries"]),
                         "audio_duration_seconds": round(sum(entry.get("duration_seconds", 0) for entry in report["entries"]), 3)}
    report["complete"] = True
    report["passed"] = bool(downloaded) and not report["errors"] and report["summary"]["failed_audio"] == 0
    report["finished_at"] = utc_now()
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    print(json.dumps({"output": str(output), "passed": report["passed"], **report["summary"]}, ensure_ascii=False), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
