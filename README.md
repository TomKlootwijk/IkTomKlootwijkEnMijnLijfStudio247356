# YouTube playlist audio and transcript archive

The repository also contains a [complete WhatsApp text archive](archive/whatsapp/+31-6-55954068/README.md), with 1,599 messages, exact exported date/time, full links, available metadata, byte-identical source exports, and transcriptions of separately supplied media.

Source playlist: [Chris the Hacker and Tom the Fag](https://www.youtube.com/playlist?list=PLyIdTztuBW5ieppWuuOPSBXmxX1mN_w5v).

The frozen playlist inventory contains **102 entries**. This archive contains **all 102 recordings**, about **18 hours 40 minutes**, in the original playlist order, with their original titles and timestamped machine transcripts.

**89 recordings** were downloaded from YouTube, and **one private recording** was downloaded through the owner's authenticated YouTube Studio download control. **Nine** were recovered from the [Internet Archive channel mirror](https://archive.org/details/slightbloodiermargie), including all four age-restricted recordings and five removed recordings. **Three** more were recovered from exact-video-ID Wayback Machine media captures. Saved metadata identifies each source and its verification details. The frozen inventory preserves the original availability observations; every listed recording was subsequently recovered.

All original titles are preserved. The title of `qOymgEa74C8`, "Chris the hacker gives an update part 1", was verified against an exact-ID archived YouTube watch page. Windows-invalid filename characters are replaced, and long filenames are shortened; the full titles remain in metadata, audio tags, transcripts, and the PDF.

## Contents

- `archive/PLyIdTztuBW5ieppWuuOPSBXmxX1mN_w5v/audio/`: MP3 audio at 96 kbps, with title, album, artist, and playlist-position tags.
- `archive/PLyIdTztuBW5ieppWuuOPSBXmxX1mN_w5v/playlist.m3u8`: playable playlist in source order.
- `archive/PLyIdTztuBW5ieppWuuOPSBXmxX1mN_w5v/metadata/`: source descriptions, dates, IDs, original titles, and archive provenance.
- `archive/PLyIdTztuBW5ieppWuuOPSBXmxX1mN_w5v/transcripts/`: timestamped UTF-8 text and structured JSON for every archived recording.
- `output/pdf/playlist-transcripts.pdf`: one searchable document, with linked contents, bookmarks, source links, and all transcripts.
- `output/archives/PLyIdTztuBW5ieppWuuOPSBXmxX1mN_w5v.zip`: one compressed archive of the audio, metadata, transcripts, and combined PDF.
- `archive-bundles/PLyIdTztuBW5ieppWuuOPSBXmxX1mN_w5v/`: consecutive parts of that same ZIP, each no larger than 80 MiB, plus SHA-256 checksums.

The Git repository stores the complete ZIP as parts to stay below GitHub's individual-file limit. It also stores the PDF, metadata, and transcripts directly. Raw local audio and the duplicate full ZIP are ignored by Git; their bytes are preserved in the committed ZIP parts.

## Restore the complete archive from Git

No third-party Python packages are needed to reconstruct and verify the ZIP:

```powershell
python scripts/restore_archive.py archive-bundles/PLyIdTztuBW5ieppWuuOPSBXmxX1mN_w5v/checksums.json --output restored.zip --extract restored
```

The extraction directory must be new or empty. The restore script checks every part, the full ZIP, ZIP integrity, and source-file hashes. A full ZIP downloaded from cloud storage can also be extracted with a normal ZIP application.

## Transcription method

Transcripts are generated locally with faster-whisper and the large-v3-turbo model, using GPU inference with `int8_float16`, beam size 5, and contiguous 30-second chunks over the full audio. Quiet speech is included rather than discarded by voice activity detection. Repetitive or unreliable decoding is recorded as timed uncertainty markers; suppressed raw candidates remain in JSON for inspection. These are machine transcripts and have not been reviewed word for word by a person. Names, overlapping speakers, and poor recordings can contain recognition errors.

`audio-validation.json` records full audio decoding, duration, tag, metadata, byte-count, and SHA-256 checks. `transcription-status.json` and each transcript record the actual model revision, settings, and quality information. The original frozen inventory is preserved separately from recovered metadata.

## Reproduce or resume

Install Python 3.12+, FFmpeg/ffprobe, and a supported JavaScript runtime for yt-dlp. Create a virtual environment and install `requirements.txt`. The optional Windows CUDA packages used for this run are pinned in `requirements-gpu.txt`.

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m pip install -r requirements-gpu.txt
.venv/Scripts/python.exe scripts/download_playlist.py --inventory archive/PLyIdTztuBW5ieppWuuOPSBXmxX1mN_w5v/playlist-inventory.json --destination archive/PLyIdTztuBW5ieppWuuOPSBXmxX1mN_w5v --node C:/path/to/node.exe
.venv/Scripts/python.exe scripts/transcribe.py archive/PLyIdTztuBW5ieppWuuOPSBXmxX1mN_w5v/manifest.json --model turbo --device auto --compute-type int8_float16 --threads 12 --batch-size 2
.venv/Scripts/python.exe scripts/build_transcript_pdf.py --manifest archive/PLyIdTztuBW5ieppWuuOPSBXmxX1mN_w5v/manifest.json --output output/pdf/playlist-transcripts.pdf
```

Mirror recovery uses the verified URLs and exact original IDs saved in `mirror-sources.json`; it never substitutes a recording based only on a similar title. Packaging refuses unfinished downloads or missing transcripts for available audio.

## Storage destinations

Google Drive destination: [playlist archive folder](https://drive.google.com/drive/folders/1I3rEUiNBOuqKnkQg4CNnLecEGZZe_Pt-).

Google Drive stores the same ZIP in consecutive parts, alongside `checksums.json`, `restore_archive.py`, and `GOOGLE-DRIVE-RESTORE.txt`. Download those files together and follow the included instructions to reconstruct and verify the complete archive. The PDF is also available separately.

OneDrive destination: `C:/Users/ietsm/OneDrive/Chris the Hacker and Tom the Fag/`.

The delivery receipt records completed uploads and OneDrive Cloud Files synchronization checks. Authentication cookies and tokens are not included in the repository or archives.

## WhatsApp chat archive

Source: [`+31 6 55954068.zip`](+31%206%2055954068.zip). The [archive guide](archive/whatsapp/+31-6-55954068/README.md) links to HTML, Markdown, and plain text transcripts, structured JSONL messages, a full URL index, media references, source reconciliation, validation, and SHA-256 checksums.

The updated ZIP contains 1,599 messages across September 23–October 22, 2026, through 05:56:42, and four quoted replies preserved from its Markdown export. The link index contains 829 occurrences (821 unique URLs): 828 in message bodies and one in a quoted reply. This export adds 30 messages and retains all 1,569 previous messages, their existing IDs, duplicates, multiline text, and ordering. Prior exports are preserved in the archive's export history.

The text ZIP inventories 448 omitted-media references and 46 album markers. Three separately supplied videos and one image have [transcriptions and metadata](archive/whatsapp/+31-6-55954068/supplied-media.json), linked from their matching chat entries. The video audio was fully processed; one video has no reliably recognized speech and retains uncertainty information. The image transcription marks obscured text. Source timestamps have no specified timezone; filename-based media associations are recorded as candidates.

The three original videos are preserved in [four verified ZIP chunks](archive-bundles/whatsapp-media-2026-10-13/README.md), each at most 80 MiB. Their original working copies and the complete local ZIP are ignored by Git. Restore and verify them with:

```powershell
python scripts/restore_archive.py archive-bundles/whatsapp-media-2026-10-13/checksums.json --output output/archives/whatsapp-media-2026-10-13.zip --extract tmp/whatsapp-media-2026-10-13-restored
```

Rebuild from the repository root using Python's standard library:

```powershell
python scripts/archive_whatsapp.py "+31 6 55954068.zip" --output "archive/whatsapp/+31-6-55954068" --previous-archive "archive/whatsapp/+31-6-55954068/export-history/04c715415e02e98406b2051ca45236331d5337f1959b387fece0be0906533036" --media-sources "archive/whatsapp/+31-6-55954068/media-sources.json"
```
