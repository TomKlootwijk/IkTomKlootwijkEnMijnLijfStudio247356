# WhatsApp media supplied on 13 October 2026

This bundle preserves the exact bytes of the three supplied MP4 files. Its four
parts are consecutive bytes of one ordinary ZIP; download every part together
with `checksums.json`. The first three parts are 80 MiB each and the last part is
38,206,923 bytes. The parts are storage chunks and must be restored before the
videos can be played.

| Original filename | Original bytes |
| --- | ---: |
| WhatsApp Video 2026-10-13 at 20.51.20.mp4 | 151,474,623 |
| WhatsApp Video 2026-10-13 at 21.55.28.mp4 | 35,387,488 |
| WhatsApp Video 2026-10-13 at 22.10.28.mp4 | 104,337,403 |

The manifest records SHA-256 hashes for every original video, ZIP part, and the
complete ZIP. The complete ZIP is 289,865,163 bytes, with SHA-256
`c6509a959b22ac11cae467ed4c7d3a7af75733e7ef66e677b1a51216373739f7`.
Dates in filenames and ZIP file metadata are not verified recording or chat
message timestamps. No relationship to a specific chat message is inferred.
The separately supplied JPEG remains a directly tracked repository file.

From the repository root, run:

```powershell
python scripts/restore_archive.py archive-bundles/whatsapp-media-2026-10-13/checksums.json --output output/archives/whatsapp-media-2026-10-13.zip --extract tmp/whatsapp-media-2026-10-13-restored
```

Python's standard library is sufficient. The extraction directory must be absent
or empty. The command verifies each part, the whole ZIP, and the extracted source
files. The videos appear inside the extraction directory with their original
filenames. The full local ZIP, test extraction, and original working copies are
kept outside the committed bundle.

The bundle passed a complete reconstruction and extraction check, explicit ZIP
CRC validation, and an independent SHA-256 comparison between all three original
and extracted files. Every stored part is at most 80 MiB.

To regenerate the bundle from the original working copies:

```powershell
python scripts/package_media.py --source-root . --bundle-id whatsapp-media-2026-10-13 --output output/archives/whatsapp-media-2026-10-13.zip --parts-dir archive-bundles/whatsapp-media-2026-10-13 "WhatsApp Video 2026-10-13 at 20.51.20.mp4" "WhatsApp Video 2026-10-13 at 21.55.28.mp4" "WhatsApp Video 2026-10-13 at 22.10.28.mp4"
```
