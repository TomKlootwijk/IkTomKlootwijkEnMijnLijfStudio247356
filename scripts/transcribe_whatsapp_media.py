"""Transcribe supplied WhatsApp media locally with source and ASR provenance.

Uses the existing playlist Transcriber, including its uncertainty guards. No
speech, recording time, speaker identity, or attachment identity is inferred
from a filename. Filename/export timestamp matches are retained as candidates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

from transcribe import Transcriber, read_json, timestamp, utc_now


FILENAME_TIME = re.compile(
    r"WhatsApp (?:Video|Audio) (\d{4}-\d{2}-\d{2}) at (\d{2})\.(\d{2})\.(\d{2})",
    re.IGNORECASE,
)


def atomic_json(path, value):
    """Keep delivered bytes stable under the repository's LF Git attributes."""
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8", newline="\n")
    temporary.replace(path)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative_path(path, root):
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def probe_media(path, executable):
    command = [executable, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=True)
    probe = json.loads(completed.stdout)
    if not any(stream.get("codec_type") == "audio" for stream in probe.get("streams", [])):
        raise ValueError(f"No audio stream in {path.name}")
    version = subprocess.run([executable, "-version"], capture_output=True, text=True,
                             encoding="utf-8", check=True).stdout.splitlines()[0]
    return probe, {"executable": executable, "version": version, "arguments": command[1:]}


def filename_timestamp(path):
    match = FILENAME_TIME.fullmatch(path.stem)
    if not match:
        return None
    date, hour, minute, second = match.groups()
    return f"{date}T{hour}:{minute}:{second}"


def associations(path, messages_path, root):
    local_time = filename_timestamp(path)
    evidence = {"filename_timestamp_local": local_time, "timezone": None,
                "timestamp_meaning": "Label encoded in the supplied filename; not verified recording or message time.",
                "messages_source": relative_path(messages_path, root) if messages_path else None,
                "messages_sha256": None, "candidates": [], "attachment_identity_verified": False}
    if not messages_path or not messages_path.is_file():
        evidence["notice"] = "No exported message index was available for matching."
        return evidence
    evidence["messages_sha256"] = sha256(messages_path)
    for line in messages_path.read_text(encoding="utf-8-sig").splitlines():
        message = json.loads(line)
        body = message.get("body", "")
        exact_name = path.name in body
        matching_time = local_time and message.get("timestamp_local") == local_time
        if not exact_name and not matching_time:
            continue
        evidence["candidates"].append({
            "message_id": message["id"], "timestamp_local": message.get("timestamp_local"),
            "timezone": message.get("timezone"), "sender": message.get("sender"),
            "media_reference_ids": message.get("media_references", []),
            "transcript_anchor": message.get("transcript_anchor"),
            "basis": "Exact supplied filename occurs in exported body." if exact_name else
                     "Exact local date and second in supplied filename match exported message timestamp.",
            "media_omission_in_body": "<video omitted>" in body.casefold(),
            "identity_verified": False,
        })
    evidence["notice"] = ("Filename and time matching do not prove that the supplied bytes are the exported attachment. "
                          "No absolute speech timestamps or speaker identities are assigned.")
    return evidence


def write_text(path, result):
    lines = [result["source"]["filename"],
             f"Source: {result['source']['path']}",
             f"SHA-256: {result['source']['sha256']}",
             f"Filename local timestamp: {result['message_association']['filename_timestamp_local'] or 'unavailable'} (timezone unknown; unverified)",
             f"Audio duration: {result['duration_seconds']:.3f} seconds",
             f"Language: {result['language']} (model probability {result['language_probability']:.4f})",
             f"Model: {result['model']}",
             "Machine transcription; may contain recognition errors. Not human reviewed.",
             "Timestamps below are relative to the beginning of this media's decoded audio.",
             "Silence and non-speech sounds are not transcribed; suppressed raw candidates are preserved in JSON.", ""]
    for item in result["segments"]:
        flags = " [ASR uncertain: " + ", ".join(item["qa_flags"]) + "]" if item.get("qa_flags") else ""
        lines.append(f"[{timestamp(item['start'])} --> {timestamp(item['end'])}] {item['text']}{flags}")
    if not result["segments"]:
        lines.append("No speech was reliably recognized. See JSON for raw candidates and confidence details.")
    if result["qa"]["unreliable_intervals"]:
        lines.extend(["", "Intervals requiring review (unreliable ASR output suppressed):"])
        for item in result["qa"]["unreliable_intervals"]:
            lines.append(f"[{timestamp(item['start'])} --> {timestamp(item['end'])}] {item['reason']}")
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    temporary.replace(path)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--messages", type=Path, help="Optional archive messages.jsonl for candidate associations.")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--model", default="turbo")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--threads", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--compute-type", choices=("float16", "int8_float16"), default="int8_float16")
    parser.add_argument("--force", action="store_true", help="Run ASR again even when matching complete output exists.")
    args = parser.parse_args()
    root = args.repo_root.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    messages = args.messages.resolve() if args.messages else None
    transcriber = Transcriber(args)
    script = Path(__file__).resolve()
    engine_script = script.with_name("transcribe.py")
    failures = []
    for supplied_path in args.sources:
        source = supplied_path.resolve(strict=True)
        if not source.is_file():
            raise ValueError(f"Not a source file: {source}")
        started_at, started = utc_now(), time.monotonic()
        source_digest = sha256(source)
        basename = re.sub(r"[^A-Za-z0-9._-]+", "-", source.stem).strip("-")
        json_path, text_path = output / (basename + ".json"), output / (basename + ".txt")
        try:
            if json_path.is_file() and not args.force:
                existing = read_json(json_path)
                if (existing.get("complete") and existing.get("source", {}).get("sha256") == source_digest
                        and existing.get("model") == transcriber.model_source
                        and existing.get("runtime", {}).get("transcription_profile") == "full-audio-30s-repetition-v3"):
                    existing["message_association"] = associations(source, messages, root)
                    existing["provenance"]["latest_output_writer"] = {
                        "script": relative_path(script, root), "sha256": sha256(script),
                        "operation": "Reused unchanged ASR inference; refreshed candidate associations and serialized outputs with LF newlines."}
                    atomic_json(json_path, existing)
                    write_text(text_path, existing)
                    print(f"Reused complete ASR and refreshed associations: {source.name}", flush=True)
                    continue
            probe, probe_runtime = probe_media(source, args.ffprobe)
            print(f"Transcribing: {source.name}", flush=True)

            def progress(seconds, count):
                print(f"{source.name}: {seconds:.1f}s, {count} accepted segments, elapsed {time.monotonic() - started:.1f}s", flush=True)

            segments, info, suppressed, unreliable, acoustic_seconds = transcriber.transcribe(source, progress)
            duration = float(info.duration)
            if any(not (0 <= item["start"] < item["end"] <= duration + 0.001) for item in segments + suppressed):
                raise ValueError("ASR segment outside decoded audio duration")
            flagged = sum(bool(item.get("qa_flags")) for item in segments)
            result = {
                "schema_version": 1, "complete": True,
                "source": {"path": relative_path(source, root), "absolute_path_at_processing": str(source),
                           "filename": source.name, "sha256": source_digest, "size_bytes": source.stat().st_size,
                           "ffprobe": probe},
                "message_association": associations(source, messages, root),
                "timestamp_basis": "Relative seconds from the beginning of the media's decoded audio; no absolute speech time assigned.",
                "language": info.language, "language_probability": info.language_probability,
                "duration_seconds": duration, "duration_after_vad_seconds": info.duration_after_vad,
                "model": transcriber.model_source, "runtime": transcriber.runtime(),
                "provenance": {"driver_script": relative_path(script, root), "driver_script_sha256": sha256(script),
                               "engine_script": relative_path(engine_script, root), "engine_script_sha256": sha256(engine_script),
                               "probe_runtime": probe_runtime,
                               "audio_decode": "faster-whisper decode_audio via PyAV, mono 16000 Hz; original full media supplied directly"},
                "started_at": started_at, "completed_at": utc_now(),
                "processing_seconds": round(time.monotonic() - started, 3),
                "segments": segments, "suppressed_segments": suppressed,
                "qa": {"full_audio_processed": True, "processed_audio_seconds": duration,
                       "accepted_segments": len(segments), "flagged_segments": flagged,
                       "suppressed_segments": len(suppressed),
                       "acoustic_speech_seconds": round(acoustic_seconds, 3),
                       "unreliable_intervals": unreliable, "human_reviewed": False,
                       "speaker_diarization": False,
                       "notice": "Full decoded audio was processed. This does not certify that every spoken word was recognized; silence and non-speech are omitted."},
                "notice": "Machine transcription of supplied WhatsApp video audio; may contain recognition errors. Filename labels and candidate message associations are not verified attachment identity.",
            }
            atomic_json(json_path, result)
            write_text(text_path, result)
            print(f"Completed: {source.name}; {len(segments)} accepted, {flagged} flagged, {len(suppressed)} suppressed; {result['processing_seconds']:.1f}s", flush=True)
        except Exception as error:
            failures.append({"source": relative_path(source, root), "error": f"{type(error).__name__}: {error}"})
            print(f"FAILED {source.name}: {type(error).__name__}: {error}", flush=True)
    if failures:
        print(json.dumps({"failures": failures}, ensure_ascii=False), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
