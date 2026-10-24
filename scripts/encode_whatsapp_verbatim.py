"""Encode verified local Piper WAV candidates to smaller AAC/M4A candidates."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    args = parser.parse_args()
    folder = args.source_dir.resolve()
    synthesis_path = folder / "verbatim-tts-provenance.json"
    synthesis = json.loads(synthesis_path.read_text(encoding="utf-8"))
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("Existing ffmpeg and ffprobe are required; no install attempted")
    outputs = synthesis["outputs"]
    for entry in outputs:
        source = Path(entry["path"])
        if digest(source) != entry["audio"]["file_sha256"]:
            raise ValueError("WAV master differs from synthesis provenance")
        if source.with_suffix(".m4a").exists():
            raise ValueError("M4A candidate already exists; refusing to overwrite it")

    def convert(entry):
        source = Path(entry["path"])
        target = source.with_suffix(".m4a")
        command = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-n", "-i", str(source),
                   "-map", "0:a:0", "-map_metadata", "-1", "-c:a", "aac", "-b:a", "96k", "-ac", "1", "-ar", "22050",
                   "-movflags", "+faststart", str(target)]
        subprocess.run(command, check=True, capture_output=True)
        probe_command = [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(target)]
        info = json.loads(subprocess.check_output(probe_command))
        stream = info["streams"][0]
        if len(info["streams"]) != 1 or stream["codec_name"] != "aac" or stream["channels"] != 1 or int(stream["sample_rate"]) != 22050:
            raise ValueError("Encoded output has an unexpected audio format")
        duration = float(info["format"]["duration"])
        if abs(duration - entry["audio"]["duration_seconds"]) > 0.15:
            raise ValueError("Encoded duration does not match WAV master")
        subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-i", str(target), "-map", "0:a:0", "-f", "null", "-"], check=True, capture_output=True)
        if digest(source) != entry["audio"]["file_sha256"]:
            raise ValueError("WAV master changed during conversion")
        return {"episode": entry["episode"], "input_path": str(source), "input_sha256": digest(source),
                "input_duration_seconds": entry["audio"]["duration_seconds"], "output_path": str(target), "output_sha256": digest(target),
                "output_bytes": target.stat().st_size, "output_duration_seconds": duration, "requested_audio_bitrate": "96k",
                "audio_codec": "AAC", "channels": 1, "sample_rate": 22050, "command_argv": command,
                "ffprobe_command_argv": probe_command, "ffprobe": info, "full_decode_check": "passed",
                "qa_status": "Candidate awaiting listening/content QA; canonical final names unchanged"}

    with ThreadPoolExecutor(max_workers=3) as pool:
        encoded = list(pool.map(convert, outputs))
    provenance = {"schema_version": 1, "status": "three_m4a_candidates_encoded_awaiting_QA", "created_at_utc": datetime.now(timezone.utc).isoformat(),
                  "converter_script": str(Path(__file__).resolve()), "converter_script_sha256": digest(Path(__file__)),
                  "command_argv": [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]],
                  "ffmpeg_executable": ffmpeg, "ffprobe_executable": ffprobe,
                  "ffmpeg_version": subprocess.check_output([ffmpeg, "-version"]).decode("utf-8", errors="replace"),
                  "synthesis_provenance_path": str(synthesis_path), "synthesis_provenance_sha256": digest(synthesis_path),
                  "privacy": "Existing local FFmpeg only; no network, TTS rerendering, or text processing.", "outputs": encoded}
    (folder / "verbatim-m4a-provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    for entry in encoded:
        print(json.dumps({k: entry[k] for k in ("episode", "output_path", "output_bytes", "output_duration_seconds", "output_sha256")}))


if __name__ == "__main__":
    main()
