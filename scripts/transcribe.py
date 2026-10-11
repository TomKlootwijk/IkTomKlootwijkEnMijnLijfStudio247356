"""Resume local ASR for completed audio files in a playlist archive.

The downloader owns manifest.json. This worker only writes transcripts and
transcription-status.json, refreshing the manifest between videos.
"""

from __future__ import annotations

import argparse
from collections import Counter
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import re
import sys
import time
import zlib
from datetime import datetime, timezone


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def timestamp(seconds):
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def configure_local_nvidia():
    """Expose venv DLLs to this process without changing system configuration."""
    if os.name != "nt":
        return [], []
    nvidia = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    directories = sorted({dll.parent for dll in nvidia.rglob("*.dll")}) if nvidia.exists() else []
    handles = [os.add_dll_directory(str(directory)) for directory in directories]
    if directories:
        os.environ["PATH"] = os.pathsep.join(map(str, directories)) + os.pathsep + os.environ.get("PATH", "")
    return handles, [str(directory) for directory in directories]


def resolve_model(requested):
    explicit = Path(requested).expanduser()
    if explicit.is_dir():
        return str(explicit.resolve()), str(explicit.resolve())
    repositories = {"small": "Systran/faster-whisper-small",
                    "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
                    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo"}
    if requested in repositories:
        repository = repositories[requested]
        cache = Path.home() / ".cache" / "huggingface" / "hub" / ("models--" + repository.replace("/", "--"))
        ref = cache / "refs" / "main"
        if ref.is_file():
            revision = ref.read_text().strip()
            snapshot = cache / "snapshots" / revision
            if all((snapshot / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json")):
                return str(snapshot), repository + "@" + revision
    return requested, requested


def package_versions():
    result = {}
    for package in ("faster-whisper", "ctranslate2", "av", "onnxruntime", "nvidia-cublas-cu12", "nvidia-cudnn-cu12", "nvidia-cuda-runtime-cu12"):
        try:
            result[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            pass
    return result


class Transcriber:
    def __init__(self, arguments):
        self.arguments = arguments
        self.model = None
        self.pipeline = None
        self.device = None
        self.compute_type = None
        self.model_path, self.model_source = resolve_model(arguments.model)
        self.dll_handles, self.dll_directories = configure_local_nvidia()
        self.versions = package_versions()
        self.gpu_fallback_reason = None

    def load(self, force_cpu=False):
        from faster_whisper import WhisperModel, BatchedInferencePipeline
        import ctranslate2

        device = "cpu" if force_cpu else self.arguments.device
        if device == "auto":
            try:
                device = "cuda" if ctranslate2.get_cuda_device_count() else "cpu"
            except Exception as error:
                self.gpu_fallback_reason = str(error)
                device = "cpu"
        compute_type = getattr(self.arguments, "compute_type", "int8_float16") if device == "cuda" else "int8"
        print(f"Loading {self.model_source} on {device} ({compute_type})", flush=True)
        try:
            self.model = WhisperModel(self.model_path, device=device, compute_type=compute_type,
                                      cpu_threads=self.arguments.threads, num_workers=1)
        except Exception as error:
            if device != "cuda" or self.arguments.device != "auto":
                raise
            self.gpu_fallback_reason = str(error)
            print(f"GPU initialization failed; falling back to CPU: {error}", flush=True)
            self.load(force_cpu=True)
            return
        self.device = device
        self.compute_type = compute_type
        self.pipeline = BatchedInferencePipeline(model=self.model)

    def runtime(self):
        return {"engine": "faster-whisper", "device": self.device,
                "transcription_profile": "full-audio-30s-repetition-v3",
                "compute_type": self.compute_type, "cpu_threads": self.arguments.threads,
                "batch_size": self.arguments.batch_size if self.device == "cuda" else min(4, self.arguments.batch_size),
                "beam_size": 5, "vad_filter": False, "full_audio": True,
                "chunk_seconds": 30, "without_timestamps": False,
                "silence_guard": "skip no_speech_prob > 0.6 and avg_logprob < -1.0",
                "repetition_guard": "suppress chunks with compression_ratio > 3 or >=4 repeated phrases dominating a chunk",
                "acoustic_qa": "VAD measured for QA only; long sparse output without acoustic support suppressed; low-confidence output outside acoustic speech suppressed for <1%-speech recordings",
                "condition_on_previous_text": False,
                "python": platform.python_version(), "packages": self.versions,
                "gpu_fallback_reason": self.gpu_fallback_reason}

    def transcribe(self, audio, progress):
        if self.model is None:
            self.load()
        try:
            return self._transcribe(audio, progress)
        except Exception as error:
            if self.device == "cuda" and self.arguments.device == "auto" and isinstance(error, RuntimeError):
                self.gpu_fallback_reason = str(error)
                print(f"GPU inference failed; retrying this video on CPU: {error}", flush=True)
                self.pipeline = None
                self.model = None
                self.load(force_cpu=True)
                return self._transcribe(audio, progress)
            raise

    def _transcribe(self, audio, progress):
        from faster_whisper.audio import decode_audio
        from faster_whisper.vad import get_speech_timestamps, VadOptions
        samples = decode_audio(str(audio), sampling_rate=16000)
        duration = len(samples) / 16000
        acoustic_speech = get_speech_timestamps(samples, VadOptions())
        acoustic_seconds = sum(item["end"] - item["start"] for item in acoustic_speech) / 16000
        clips = [{"start": start, "end": min(start + 30, duration)}
                 for start in range(0, math.ceil(duration), 30)]
        segments, info = self.pipeline.transcribe(
            samples, beam_size=5, vad_filter=False, clip_timestamps=clips,
            without_timestamps=False,
            condition_on_previous_text=False,
            batch_size=self.arguments.batch_size if self.device == "cuda" else min(4, self.arguments.batch_size),
        )
        output = []
        suppressed = []
        unreliable = []
        last_update = 0
        for segment in segments:
            if not (math.isfinite(segment.start) and math.isfinite(segment.end)):
                raise ValueError("ASR produced an invalid timestamp")
            item = {"start": round(max(0, min(segment.start, duration)), 3),
                           "end": round(max(0, min(segment.end, duration)), 3),
                           "text": segment.text.strip(), "avg_logprob": segment.avg_logprob,
                           "no_speech_prob": segment.no_speech_prob,
                           "compression_ratio": segment.compression_ratio}
            if item["end"] <= item["start"] or not item["text"]:
                continue
            speech_overlap = sum(max(0, min(item["end"], interval["end"] / 16000)
                                     - max(item["start"], interval["start"] / 16000)) for interval in acoustic_speech)
            item["acoustic_speech_overlap_seconds"] = round(speech_overlap, 3)
            reason = None
            if segment.no_speech_prob > 0.6 and segment.avg_logprob < -1.0:
                reason = "High no-speech probability and low recognition confidence."
            elif item["end"] - item["start"] > 10 and len(item["text"].split()) <= 4 and speech_overlap < 0.2:
                reason = "Sparse decoder output over a long interval without acoustic speech support."
            elif acoustic_seconds / max(duration, 1) < 0.01 and speech_overlap < 0.2 and segment.avg_logprob < -0.6:
                reason = "Low-confidence decoding without acoustic speech support in an almost non-speech recording."
            if reason:
                suppressed.append({**item, "reason": reason})
                unreliable.append({"start": item["start"], "end": item["end"], "reason": reason})
            else:
                flags = []
                if segment.avg_logprob < -0.8:
                    flags.append("low_recognition_confidence")
                if segment.no_speech_prob > 0.6:
                    flags.append("high_no_speech_probability")
                if segment.compression_ratio > 2.4:
                    flags.append("possible_repetition")
                if speech_overlap < 0.2:
                    flags.append("little_acoustic_speech_support")
                if flags:
                    item["qa_flags"] = flags
                output.append(item)
            if time.monotonic() - last_update >= 15:
                progress(segment.end, len(output))
                last_update = time.monotonic()
        # Decoder loops in typing/music/silence can have deceptively high scores.
        # Preserve the raw candidates as suppressed evidence instead of publishing
        # their repeated words as recognized speech.
        grouped = {}
        for item in output:
            grouped.setdefault(int(item["start"] // 30), []).append(item)
        rejected = set()
        for chunk, items in grouped.items():
            combined = " ".join(item["text"] for item in items)
            encoded = combined.encode("utf-8")
            ratio = len(encoded) / len(zlib.compress(encoded)) if encoded else 0
            normalized = [re.sub(r"[^\w]+", " ", item["text"].casefold()).strip() for item in items]
            common = Counter(normalized).most_common(1)
            repeated = bool(common and common[0][1] >= 4 and common[0][1] >= len(items) / 2)
            if ratio > 3 or repeated:
                rejected.add(chunk)
                unreliable.append({"start": chunk * 30, "end": min((chunk + 1) * 30, duration),
                                   "reason": "Repetitive decoder output; speech could not be reliably recognized.",
                                   "compression_ratio": round(ratio, 3)})
                suppressed.extend({**item, "reason": "Probable decoder repetition loop."} for item in items)
        output = [item for item in output if int(item["start"] // 30) not in rejected]
        unreliable.sort(key=lambda item: (item["start"], item["end"]))
        return output, info, suppressed, unreliable, acoustic_seconds


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--model", default="turbo")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--threads", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--compute-type", choices=("float16", "int8_float16"), default="int8_float16")
    parser.add_argument("--poll", type=float, default=5)
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    args.manifest = args.manifest.resolve()
    archive = args.manifest.parent
    status_path = archive / "transcription-status.json"
    status = {"schema_version": 1, "started_at": utc_now(), "updated_at": utc_now(),
              "complete": False, "current": None, "entries": {}}
    if status_path.exists():
        old_status = read_json(status_path)
        status["entries"] = old_status.get("entries", {})
        if args.retry_failed:
            status["entries"] = {key: value for key, value in status["entries"].items() if value.get("status") != "failed"}
        for item in status["entries"].values():
            if item.get("status") in ("completed", "processing"):
                item["status"] = "pending"
    transcriber = Transcriber(args)

    def write_status():
        status["updated_at"] = utc_now()
        status["model"] = transcriber.model_source
        status["runtime"] = transcriber.runtime()
        counts = {}
        for item in status["entries"].values():
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        status["counts"] = counts
        atomic_json(status_path, status)

    while True:
        try:
            manifest = read_json(args.manifest)
        except (FileNotFoundError, json.JSONDecodeError, PermissionError):
            time.sleep(args.poll)
            continue
        entries = manifest["entries"]
        status["playlist_id"] = manifest["id"]
        status["listed_entries"] = len(entries)
        worked = False
        for entry in entries:
            video_id = entry["id"]
            existing = status["entries"].get(video_id, {})
            transcript_json = archive / entry["transcript_json"]
            transcript_text = archive / entry["transcript_text"]
            if transcript_json.is_file() and transcript_text.is_file():
                try:
                    saved = read_json(transcript_json)
                    if (saved.get("complete") is True and saved.get("id") == video_id
                            and saved.get("title") == entry["title"]
                            and saved.get("runtime", {}).get("full_audio") is True
                            and saved.get("runtime", {}).get("transcription_profile") == "full-audio-30s-repetition-v3"
                            and saved.get("model") == transcriber.model_source):
                        status["entries"][video_id] = {"status": "completed", "title": entry["title"],
                            "segments": len(saved["segments"]), "transcript_json": entry["transcript_json"],
                            "transcript_text": entry["transcript_text"], "completed_at": saved["completed_at"]}
                        continue
                except (OSError, ValueError, KeyError):
                    pass
            if existing.get("status") == "failed":
                continue
            download_status = entry.get("download_status", "pending")
            if download_status in ("unavailable", "failed"):
                status["entries"][video_id] = {"status": "unavailable" if download_status == "unavailable" else "download_failed",
                    "title": entry["title"], "reason": entry.get("error", "Audio could not be downloaded.")}
                continue
            audio = archive / entry["audio"]
            if download_status != "downloaded" or not audio.is_file() or audio.stat().st_size == 0:
                if manifest.get("download_complete"):
                    status["entries"][video_id] = {"status": "failed", "title": entry["title"],
                        "error": "Download completed without a usable audio file."}
                else:
                    status["entries"][video_id] = {"status": "pending", "title": entry["title"]}
                continue
            if audio.suffix == ".part" or Path(str(audio) + ".part").exists():
                continue
            started = time.monotonic()
            started_at = utc_now()
            status["current"] = {"id": video_id, "title": entry["title"], "position": entry["position"],
                                 "started_at": started_at, "processed_audio_seconds": 0, "segments": 0}
            status["entries"][video_id] = {"status": "processing", "title": entry["title"]}
            write_status()
            print(f"Transcribing #{entry['position']}: {entry['title']}", flush=True)

            def progress(seconds, count):
                status["current"]["processed_audio_seconds"] = round(seconds, 3)
                status["current"]["segments"] = count
                status["current"]["elapsed_seconds"] = round(time.monotonic() - started, 1)
                write_status()

            try:
                segments, info, suppressed, unreliable, acoustic_seconds = transcriber.transcribe(audio, progress)
                flagged = sum(bool(segment.get("qa_flags")) for segment in segments)
                result = {"schema_version": 1, "complete": True, "id": video_id, "title": entry["title"],
                          "position": entry["position"], "url": entry["url"], "audio": entry["audio"],
                          "language": info.language, "language_probability": info.language_probability,
                          "duration": info.duration, "duration_after_vad": info.duration_after_vad,
                          "model": transcriber.model_source, "runtime": transcriber.runtime(),
                          "started_at": started_at, "completed_at": utc_now(),
                          "processing_seconds": round(time.monotonic() - started, 3), "segments": segments,
                          "qa": {"full_audio_processed": True, "processed_audio_seconds": info.duration,
                                 "flagged_segments": flagged, "suppressed_segments": len(suppressed),
                                 "acoustic_speech_seconds": round(acoustic_seconds, 3),
                                 "suppressed_probable_silence_segments": sum(item["reason"].startswith("High no-speech") for item in suppressed),
                                 "unreliable_intervals": unreliable,
                                 "human_reviewed": False,
                                 "flags": ["Contains low confidence or possible non-speech recognition."] if flagged else []},
                          "suppressed_segments": suppressed,
                          "notice": "Machine transcription of downloaded audio; may contain recognition errors."}
                transcript_text.parent.mkdir(parents=True, exist_ok=True)
                lines = [entry["title"] or f"[Original title unavailable] {video_id}", entry["url"],
                         f"Language: {info.language} | Model: {transcriber.model_source}",
                         "Machine transcription; may contain recognition errors.", ""]
                lines.extend(f"[{timestamp(segment['start'])} --> {timestamp(segment['end'])}] {segment['text']}"
                             + (" [ASR uncertain: " + ", ".join(segment["qa_flags"]) + "]" if segment.get("qa_flags") else "")
                             for segment in segments)
                if not segments:
                    lines.append("No speech was reliably recognized. See JSON for processing and confidence details.")
                if unreliable:
                    lines.append("")
                    lines.append("Intervals requiring review (unreliable ASR output suppressed):")
                    lines.extend(f"[{timestamp(item['start'])} --> {timestamp(item['end'])}] {item['reason']}" for item in unreliable)
                temporary = transcript_text.with_name(transcript_text.name + ".tmp")
                temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
                temporary.replace(transcript_text)
                atomic_json(transcript_json, result)
                status["entries"][video_id] = {"status": "completed", "title": entry["title"], "segments": len(segments),
                    "transcript_json": entry["transcript_json"], "transcript_text": entry["transcript_text"],
                    "completed_at": result["completed_at"], "processing_seconds": result["processing_seconds"]}
                print(f"Completed #{entry['position']}: {len(segments)} segments in {result['processing_seconds']:.1f}s", flush=True)
            except Exception as error:
                status["entries"][video_id] = {"status": "failed", "title": entry["title"],
                    "error": f"{type(error).__name__}: {error}", "failed_at": utc_now()}
                print(f"FAILED #{entry['position']}: {type(error).__name__}: {error}", flush=True)
            status["current"] = None
            write_status()
            worked = True
            break  # Refresh download state before selecting the next video.
        if worked:
            continue
        terminal = {"completed", "unavailable", "download_failed", "failed"}
        if manifest.get("download_complete") and all(status["entries"].get(entry["id"], {}).get("status") in terminal for entry in entries):
            status["complete"] = True
            status["completed_at"] = utc_now()
            write_status()
            print("Transcription worker complete: " + json.dumps(status["counts"]), flush=True)
            return 1 if status["counts"].get("failed", 0) else 0
        write_status()
        time.sleep(args.poll)


if __name__ == "__main__":
    raise SystemExit(main())
