# WhatsApp chat archive: +31 6 55954068

This is the complete text transcription of the supplied [+31 6 55954068.zip](../../../%2B31%206%2055954068.zip), containing **1,538 messages** from **2026-09-23 06:40:13** through **2026-10-20 16:46:14** across **28 dates**. Original sender labels, spelling, Unicode, multiline bodies, repeated timestamps, duplicate records, full URLs, and media placeholders are preserved.

Open the [searchable HTML transcript](transcript.html) locally in a browser for clickable links, date navigation, and a permanent anchor for every message. A [Markdown transcript](transcript.md) and [plain text transcript](transcript.txt) are also included. The HTML is self-contained, runs no scripts, and loads no remote assets.

- [messages.jsonl](messages.jsonl): one record per message, with exact local date/time to the second, sender, body, source line/byte spans, raw source record, SHA-256, link/media IDs, and reply context.
- [links.jsonl](links.jsonl): **801 URL occurrences**, **793 unique URLs** (800 in message bodies; 1 in supplemental quoted replies), with exact full URLs, sender/time, body or quote offsets, and message anchors. Repeated occurrences remain separate. Links were preserved without fetching them.
- [media-references.jsonl](media-references.jsonl): **472 placeholders** with original markers and descriptions: **428 omitted-media references** and **44 album markers**.
- [metadata.json](metadata.json): source/export metadata, ZIP member information and hashes, daily counts, participants as labeled, date range, media/link counts, parsing conventions, and available-data limits.
- [source-comparison.json](source-comparison.json): reconciliation of all messages in both exports, including the **4 reply quotes** available only in Markdown.
- [source/chat.txt](source/chat.txt) and [source/chat.md](source/chat.md): byte-identical originals extracted from the ZIP.
- [validation.json](validation.json) and [checksums.json](checksums.json): completeness checks and SHA-256 checksums for every delivered archive file except the checksum manifest itself.

The TXT export is the canonical message body/time source because it retains seconds. The Markdown export supplies the chat label, export time (October 20, 2026 at 5:17 PM), and 4 reply quotes. All 1,538 message identities and bodies reconcile after the documented placeholder substitutions and separate preservation of those quotes. The undated encryption notice is preserved separately without assigning it a fabricated timestamp.

The ZIP contains only these two text files. It includes **no actual media**: image (288), audio (96), document (37), and video (7) references say omitted; 44 album markers have no item contents. The absent voice recordings cannot be transcribed from this export. Separately supplied files are described below. Source timestamps have **no specified timezone**; no offset is assumed. Exported sender labels are all `You`; the phone-number chat label is not used to invent another sender. No WhatsApp internal message IDs or read/delivery receipts are available; `m000001` and similar IDs are local persistent identifiers.

Rebuild using Python 3.12+ and the standard library, from the repository root:

```powershell
python scripts/archive_whatsapp.py "+31 6 55954068.zip" --output "archive/whatsapp/+31-6-55954068" --previous-archive "archive/whatsapp/+31-6-55954068/export-history/7f5ecbc6f1bd91b4f716ae8e35918f4d5428adb5fb6108d70dec458a0f09c46a" --media-sources "archive/whatsapp/+31-6-55954068/media-sources.json"
```

The build fails if the paired exports do not reconcile, any record bytes are lost, source byte spans differ, or the ZIP fails its integrity check. Outputs are deterministic. `metadata.json.source_notice.text` followed by every `messages.jsonl` record's `source.raw_record` reproduces `source/chat.txt` exactly as UTF-8.

This update adds **23 messages** and retains all **1515 previous messages**. Existing message, link, and media IDs remain attached to the same content. See [export-update.json](export-update.json). The [previous export snapshot](export-history/7f5ecbc6f1bd91b4f716ae8e35918f4d5428adb5fb6108d70dec458a0f09c46a/metadata.json) retains its original records and source bytes.

**4 separately supplied media files** have [text transcriptions and metadata](supplied-media.json), linked from their matching chat entries. Filename timestamps and media kinds establish candidate associations; the export contains no attachment hashes to prove those associations. Video transcripts retain machine confidence flags and suppressed candidates; the image transcription marks obscured text. [Restore the three original videos from verified chunks](../../../archive-bundles/whatsapp-media-2026-10-13/README.md).

- WhatsApp Video 2026-10-13 at 20.51.20.mp4: [transcription](media-transcripts/WhatsApp-Video-2026-10-13-at-20.51.20.txt) · [metadata and QA](media-transcripts/WhatsApp-Video-2026-10-13-at-20.51.20.json) · [chat entry](transcript.html#m001191)
- WhatsApp Video 2026-10-13 at 21.55.28.mp4: [transcription](media-transcripts/WhatsApp-Video-2026-10-13-at-21.55.28.txt) · [metadata and QA](media-transcripts/WhatsApp-Video-2026-10-13-at-21.55.28.json) · [chat entry](transcript.html#m001197)
- WhatsApp Video 2026-10-13 at 22.10.28.mp4: [transcription](media-transcripts/WhatsApp-Video-2026-10-13-at-22.10.28.txt) · [metadata and QA](media-transcripts/WhatsApp-Video-2026-10-13-at-22.10.28.json) · [chat entry](transcript.html#m001198)
- WhatsApp Image 2026-10-13 at 22.23.00.jpeg: [transcription](media-transcripts/image-2026-10-13-22-23-00.txt) · [metadata and QA](media-transcripts/image-2026-10-13-22-23-00.json) · [chat entry](transcript.html#m001200)
