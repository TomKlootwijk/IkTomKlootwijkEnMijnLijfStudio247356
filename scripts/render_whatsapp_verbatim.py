"""Render frozen UTF-8 briefs locally with Piper; never summarize or rewrite text.

Requires the separately installed piper-tts package and an already downloaded
official voice model. This renderer performs no downloads or cloud requests.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import socket
import sys
import time
import wave

import numpy as np
import onnxruntime
from piper import PiperVoice, SynthesisConfig


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def verify_receipt(receipt):
    for entry in receipt["files"]:
        data = Path(entry["path"]).read_bytes()
        if len(data) != entry["bytes"] or sha(data) != entry["sha256"]:
            raise ValueError("Frozen source receipt no longer matches source bytes")


def audio_info(path):
    with wave.open(str(path), "rb") as wav:
        params = wav.getparams()
        pcm = wav.readframes(params.nframes)
    if params.nchannels != 1 or params.sampwidth != 2 or params.comptype != "NONE":
        raise ValueError("Unexpected Piper output format")
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.int32)
    if len(samples) != params.nframes or not len(samples):
        raise ValueError("Incomplete/empty output audio")
    peak = int(np.max(np.abs(samples)))
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    if peak == 0 or rms == 0:
        raise ValueError("Output audio is silent")
    return {"container": "WAV", "codec": "PCM signed 16-bit little-endian", "channels": params.nchannels,
            "sample_rate": params.framerate, "sample_width_bytes": params.sampwidth,
            "frames": params.nframes, "duration_seconds": params.nframes / params.framerate,
            "peak_absolute_sample": peak, "rms_dbfs": 20 * math.log10(rms / 32768),
            "full_scale_sample_count": int(np.count_nonzero(np.abs(samples) >= 32767)),
            "pcm_sha256": sha(pcm), "file_bytes": path.stat().st_size, "file_sha256": sha(path.read_bytes())}


def denied_network(*args, **kwargs):
    raise RuntimeError("Network access is disabled inside the offline TTS renderer")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brief-receipt", type=Path, required=True)
    parser.add_argument("--original-upload-receipt", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--model-provenance", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    brief = json.loads(args.brief_receipt.read_text(encoding="utf-8"))
    originals = json.loads(args.original_upload_receipt.read_text(encoding="utf-8"))
    verify_receipt(brief)
    verify_receipt(originals)
    download = json.loads(args.model_provenance.read_text(encoding="utf-8"))
    for entry in download["files"]:
        path = args.model_provenance.parent / entry["file"]
        data = path.read_bytes()
        if len(data) != entry["bytes"] or sha(data) != entry["sha256"]:
            raise ValueError("Cached model file does not match download provenance")
    config_path = Path(str(args.model) + ".json")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config["espeak"]["voice"] != "nl" or config["num_speakers"] != 1:
        raise ValueError("Expected one-speaker Dutch voice")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    candidates = [args.output_dir / f"verbatim-episode-{entry['episode']:02d}.wav" for entry in brief["files"]]
    if any(path.exists() for path in candidates):
        raise ValueError("A candidate already exists; preserve or explicitly relocate it before rerendering")
    onnxruntime.disable_telemetry_events()
    # Downloads are complete before this process starts. Python networking is
    # deliberately unavailable for every inference operation below.
    socket.socket = denied_network
    socket.create_connection = denied_network
    voice = PiperVoice.load(args.model, config_path=config_path, use_cuda=False)
    synthesis = SynthesisConfig(speaker_id=0)  # All other values use natural voice defaults.
    package = importlib.metadata.metadata("piper-tts")
    provenance = {"schema_version": 1, "status": "rendering_candidates_not_final", "started_at_utc": datetime.now(timezone.utc).isoformat(),
                  "engine": "Piper local neural text-to-speech", "piper_package_version": importlib.metadata.version("piper-tts"),
                  "piper_package_license": package.get("License"), "python": sys.version, "python_executable": sys.executable,
                  "installed_packages": sorted(f"{d.metadata['Name']}=={d.version}" for d in importlib.metadata.distributions()),
                  "processor_script": str(Path(__file__).resolve()), "processor_script_sha256": sha(Path(__file__).read_bytes()),
                  "command_argv": [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]],
                  "official_documentation": ["https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/API_PYTHON.md", "https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/CLI.md"],
                  "model": {"path": str(args.model.resolve()), "sha256": sha(args.model.read_bytes()),
                            "configuration_path": str(config_path.resolve()), "configuration_sha256": sha(config_path.read_bytes()),
                            "download": download, "model_card_dataset_license": "CC0", "repository_license_metadata": download["repository_license_metadata"],
                            "language": "nl_NL", "espeak_voice": "nl", "speakers": 1, "voice_name": "pim", "voice_gender": "Not specified in official model card"},
                  "inference": {"device": "CPU", "speaker_id": 0, "voice_defaults": config["inference"], "normalize_audio": True, "volume": 1.0,
                                "pitch_or_speed_adjustment": "None; voice defaults", "onnx_telemetry_disabled": True, "python_network_calls_disabled": True},
                  "privacy": "No cloud TTS or HTTP service used. Private brief bytes are read only by this local synthesis process. Model/package downloads occurred separately without source text.",
                  "text_policy": "Each entire frozen UTF-8 file is decoded exactly once and passed unchanged to PiperVoice.synthesize_wav. No stripping, rewriting, summarization, speaker reassignment, title removal, or phonetic substitutions.",
                  "brief_receipt_path": str(args.brief_receipt.resolve()), "brief_receipt_sha256": sha(args.brief_receipt.read_bytes()),
                  "outputs": []}
    provenance_path = args.output_dir / "verbatim-tts-provenance.json"
    write_json(provenance_path, provenance)
    for entry, target in zip(brief["files"], candidates, strict=True):
        source = Path(entry["path"])
        data = source.read_bytes()
        if sha(data) != entry["sha256"]:
            raise ValueError("Brief changed before its synthesis")
        text = data.decode("utf-8")
        temporary = target.with_suffix(".wav.tmp")
        start = time.monotonic()
        with wave.open(str(temporary), "wb") as wav:
            voice.synthesize_wav(text, wav, syn_config=synthesis)
        info = audio_info(temporary)
        if source.read_bytes() != data:
            raise ValueError("Brief changed during synthesis")
        temporary.rename(target)
        provenance["outputs"].append({"episode": entry["episode"], "source_path": str(source.resolve()),
                                      "source_utf8_bytes": len(data), "source_utf8_sha256": sha(data), "source_text_codepoints": len(text),
                                      "source_words": len(text.split()), "text_transformations": [], "path": str(target.resolve()),
                                      "synthesis_elapsed_seconds": time.monotonic() - start, "audio": info,
                                      "qa_status": "Candidate awaiting listening/content QA; not a promoted final episode"})
        write_json(provenance_path, provenance)
        print(json.dumps({"episode": entry["episode"], "path": str(target.resolve()), "duration_seconds": info["duration_seconds"], "sha256": info["file_sha256"]}), flush=True)
    verify_receipt(brief)
    verify_receipt(originals)
    provenance["status"] = "three_candidates_rendered_awaiting_QA"
    provenance["frozen_briefs_and_original_uploads_unchanged"] = True
    provenance["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    write_json(provenance_path, provenance)


if __name__ == "__main__":
    main()
