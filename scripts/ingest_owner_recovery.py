"""Ingest the owner's exact-ID Studio download and verified archived title.

Accepts only local media and saved public title evidence. Authentication cookies,
signed download URLs, and browser session data are never read or stored.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess

from mutagen.easyid3 import EasyID3

from download_playlist import filename, now, write_json
from validate_archive_audio import source_path


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as reader:
        while block := reader.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def probe(path):
    return json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)
    ]))


def paths(root, entry, title):
    stem = filename(entry["position"], title, entry["id"])
    relatives = {"audio": f"audio/{stem}.mp3", "metadata": f"metadata/{stem}.json",
                 "transcript_json": f"transcripts/{stem}.json", "transcript_text": f"transcripts/{stem}.txt"}
    # Validate every old and new resolved path before any rename or write.
    old = {key: source_path(root, entry[key]) for key in relatives}
    new = {key: source_path(root, relative) for key, relative in relatives.items()}
    for key in old:
        if old[key] != new[key] and new[key].exists():
            raise FileExistsError(f"Refusing to replace a different archive file: {new[key]}")
    return relatives, old, new


def tag_audio(path, title, manifest, position, artist, year=None):
    tags = EasyID3(str(path))
    tags["title"] = title
    tags["album"] = manifest["title"]
    tags["artist"] = artist
    tags["tracknumber"] = str(position)
    if year:
        tags["date"] = str(year)
    tags.save(str(path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--owner-video", required=True, type=Path)
    parser.add_argument("--title-evidence", required=True, type=Path)
    args = parser.parse_args()
    root = args.manifest.resolve().parent
    manifest = json.loads(args.manifest.read_text("utf-8-sig"))
    frozen = root / "playlist-inventory.json"
    frozen_sha256 = sha256(frozen)
    entries = {entry["id"]: entry for entry in manifest["entries"]}
    public = entries["qOymgEa74C8"]
    private = entries["MYuB75Nb2gw"]
    evidence = json.loads(args.title_evidence.read_text("utf-8-sig"))
    title = "Chris the hacker gives an update part 1"
    if evidence.get("exact_video_id") != public["id"] or evidence.get("title") != title:
        raise ValueError("Saved title evidence does not identify the expected exact video and title")
    if evidence.get("duration_seconds") != 901 or evidence.get("uploader") != "wnxeagle2":
        raise ValueError("Saved title evidence has unexpected duration or uploader")
    public_rel, public_old, public_new = paths(root, public, title)
    private_title = "Chris the hacker acting like a voicemail machine"
    private_rel, private_old, private_new = paths(root, private, private_title)
    source = args.owner_video.resolve(strict=True)
    source_probe = probe(source)
    source_duration = float(source_probe["format"]["duration"])
    if abs(source_duration - 16.579046) > 0.2:
        raise ValueError("Owner download duration differs from the verified Studio recording")
    if not any(item.get("codec_type") == "audio" for item in source_probe["streams"]):
        raise ValueError("Owner download has no audio stream")
    metadata_evidence = {key: value for key, value in evidence.items() if key != "saved_html_path"}
    public_metadata = json.loads(public_old["metadata"].read_text("utf-8-sig"))
    transcript = json.loads(public_old["transcript_json"].read_text("utf-8-sig"))
    transcript_before = {key: value for key, value in transcript.items() if key not in {"title", "audio", "metadata_corrections"}}
    text_before = public_old["transcript_text"].read_text("utf-8")
    _, separator, text_body = text_before.partition("\n")
    if not separator:
        raise ValueError("Existing transcript has no title heading")

    for key in public_old:
        if public_old[key] != public_new[key]:
            public_old[key].rename(public_new[key])
    tag_audio(public_new["audio"], title, manifest, public["position"], evidence["uploader"], "2012")
    correction_time = now()
    public_metadata.update(title=title, uploader=evidence["uploader"], uploader_id=evidence["uploader"],
                           channel_id=evidence["channel_id"], channel_url=evidence["uploader_url"],
                           upload_date=evidence["upload_date"].replace("-", ""), duration=evidence["duration_seconds"],
                           title_source="Original YouTube player metadata preserved by Wayback Machine",
                           title_recovery=metadata_evidence, metadata_corrected_at=correction_time)
    write_json(public_new["metadata"], public_metadata)
    public.update(**public_rel, title=title, title_source=public_metadata["title_source"],
                  title_evidence_url=evidence["source_url"], duration=evidence["duration_seconds"],
                  audio_bytes=public_new["audio"].stat().st_size, metadata_corrected_at=correction_time)
    transcript.update(title=title, audio=public_rel["audio"])
    transcript.setdefault("metadata_corrections", []).append({
        "corrected_at": correction_time, "fields": ["title", "audio"],
        "source_url": evidence["source_url"], "asr_text_and_settings_unchanged": True})
    if {key: value for key, value in transcript.items() if key not in {"title", "audio", "metadata_corrections"}} != transcript_before:
        raise AssertionError("Title correction changed existing ASR content or settings")
    write_json(public_new["transcript_json"], transcript)
    public_new["transcript_text"].write_text(title + "\n" + text_body, encoding="utf-8")
    if public_new["transcript_text"].read_text("utf-8").partition("\n")[2] != text_body:
        raise AssertionError("Title correction changed timestamped transcript text")

    target = private_new["audio"]
    temporary = source_path(root, private_rel["audio"] + ".ingest.tmp.mp3")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
                    "-map", "0:a:0", "-vn", "-codec:a", "libmp3lame", "-b:a", "96k",
                    "-threads", "2", str(temporary)], check=True)
    tag_audio(temporary, private_title, manifest, private["position"], "wnxeagle2")
    audio_probe = probe(temporary)
    audio_duration = float(audio_probe["format"]["duration"])
    if abs(audio_duration - source_duration) > 0.2:
        raise ValueError("Converted audio duration differs from the owner download")
    temporary.replace(target)
    owner_provenance = {
        "method": "Authenticated owner used YouTube Studio's official Download action",
        "exact_video_id": private["id"], "watch_url": private["url"],
        "studio_edit_url": f"https://studio.youtube.com/video/{private['id']}/edit",
        "owner_display_name": "TomKlootwijk", "owner_handle": "@wnxeagle2",
        "visibility": "Private", "studio_display_duration": "0:17",
        "studio_reported_duration_seconds": 16.579046,
        "original_upload_filename": "chris acting like a voicemail.wmv",
        "local_download_filename": source.name, "source_bytes": source.stat().st_size,
        "source_sha256": sha256(source), "source_ffprobe": {
            "format": {key: value for key, value in source_probe["format"].items() if key != "filename"},
            "streams": source_probe["streams"]},
        "verification": "Exact video ID, original title, description, owner handle and private visibility were confirmed in authenticated YouTube watch/Studio UI.",
        "signed_download_url_stored": False, "ingested_at": now()}
    metadata = {
        "id": private["id"], "title": private_title,
        "description": "Chris tries to trick Tom into thinking he is a voicemail machine.",
        "webpage_url": private["url"], "uploader": "wnxeagle2", "uploader_id": "wnxeagle2",
        "channel": "TomKlootwijk", "channel_url": "https://www.youtube.com/@wnxeagle2",
        "duration": 16.579046, "availability": "private", "visibility": "Private",
        "original_filename": "chris acting like a voicemail.wmv", "playlist_id": manifest["id"],
        "playlist_title": manifest["title"], "playlist_position": private["position"],
        "archived_at": now(), "authenticated_owner_studio": owner_provenance}
    write_json(private_new["metadata"], metadata)
    previous_status = private.get("download_status")
    previous_error = private.get("error")
    private.update(**private_rel, title=private_title,
                   title_source="Authenticated owner YouTube Studio metadata",
                   duration=16.579046, audio_duration=audio_duration, audio_bytes=target.stat().st_size,
                   download_status="downloaded", download_source="YouTube Studio authenticated owner download",
                   availability="private", visibility="Private", error=None, downloaded_at=now())
    private.setdefault("previous_download_status", previous_status)
    private.setdefault("youtube_error", previous_error)
    manifest.update(download_complete=True, updated_at=now(), owner_recovery_finished_at=now(),
                    download_summary={key: sum(entry["download_status"] == key for entry in manifest["entries"])
                                      for key in ["downloaded", "unavailable", "failed", "pending"]},
                    all_listed_audio_archived=True, all_listed_titles_recovered=True)
    write_json(args.manifest, manifest)
    lines = ["#EXTM3U"]
    for entry in sorted(manifest["entries"], key=lambda item: item["position"]):
        if entry["download_status"] == "downloaded":
            lines.extend([f"#EXTINF:{entry.get('duration') or -1},{entry['title']}", entry["audio"]])
    (root / "playlist.m3u8").write_text("\n".join(lines) + "\n", encoding="utf-8")
    status_path = root / "transcription-status.json"
    status = json.loads(status_path.read_text("utf-8-sig"))
    status["entries"][public["id"]].update(title=title, transcript_json=public_rel["transcript_json"],
                                          transcript_text=public_rel["transcript_text"])
    status["entries"][private["id"]] = {"status": "pending", "title": private_title}
    status.update(complete=False, current=None, updated_at=now(),
                  counts=dict(Counter(item["status"] for item in status["entries"].values())))
    status.pop("completed_at", None)
    write_json(status_path, status)
    if sha256(frozen) != frozen_sha256:
        raise AssertionError("Frozen original playlist inventory changed")
    print(json.dumps({"download_summary": manifest["download_summary"],
                      "all_titles_present": all(entry.get("title") for entry in manifest["entries"]),
                      "frozen_inventory_sha256": frozen_sha256,
                      "existing_asr_content_preserved": True,
                      "owner_source_sha256": owner_provenance["source_sha256"],
                      "owner_source_duration_seconds": source_duration,
                      "owner_audio_duration_seconds": audio_duration}, indent=2))


if __name__ == "__main__":
    main()
