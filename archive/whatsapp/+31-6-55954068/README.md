# WhatsApp chat archive: +31 6 55954068

This is the complete text transcription of the supplied [+31 6 55954068.zip](../../../%2B31%206%2055954068.zip), containing **1,200 messages** from **2026-09-23 06:40:13** through **2026-10-13 22:23:00** across **21 dates**. Original sender labels, spelling, Unicode, multiline bodies, repeated timestamps, duplicate records, full URLs, and media placeholders are preserved.

Open the [searchable HTML transcript](transcript.html) locally in a browser for clickable links, date navigation, and a permanent anchor for every message. A [Markdown transcript](transcript.md) and [plain text transcript](transcript.txt) are also included. The HTML is self-contained, runs no scripts, and loads no remote assets.

- [messages.jsonl](messages.jsonl): one record per message, with exact local date/time to the second, sender, body, source line/byte spans, raw source record, SHA-256, link/media IDs, and reply context.
- [links.jsonl](links.jsonl): **606 URL occurrences**, **600 unique URLs**, with exact full URLs, sender/time, body offsets, and message anchors. Repeated occurrences remain separate. Links were preserved without fetching them.
- [media-references.jsonl](media-references.jsonl): **375 placeholders** with original markers and descriptions: **345 omitted-media references** and **30 album markers**.
- [metadata.json](metadata.json): source/export metadata, ZIP member information and hashes, daily counts, participants as labeled, date range, media/link counts, parsing conventions, and available-data limits.
- [source-comparison.json](source-comparison.json): reconciliation of all messages in both exports, including the **2 reply quotes** available only in Markdown.
- [source/chat.txt](source/chat.txt) and [source/chat.md](source/chat.md): byte-identical originals extracted from the ZIP.
- [validation.json](validation.json) and [checksums.json](checksums.json): completeness checks and SHA-256 checksums for every delivered archive file except the checksum manifest itself.

The TXT export is the canonical message body/time source because it retains seconds. The Markdown export supplies the chat label, export time (October 13, 2026 at 10:35 PM), and two reply quotes. All 1,200 message identities and bodies reconcile after the documented placeholder substitutions and separate preservation of those quotes. The undated encryption notice is preserved separately without assigning it a fabricated timestamp.

The ZIP contains only these two text files. It includes **no actual media**: image (224), audio (95), document (20), and video (6) references say omitted; 30 album markers have no item contents. The absent voice recordings cannot be transcribed from this export. Separately supplied files are described below. Source timestamps have **no specified timezone**; no offset is assumed. Exported sender labels are all `You`; the phone-number chat label is not used to invent another sender. No WhatsApp internal message IDs or read/delivery receipts are available; `m000001` and similar IDs are local persistent identifiers.

Rebuild using Python 3.12+ and the standard library, from the repository root:

```powershell
python scripts/archive_whatsapp.py "+31 6 55954068.zip" --output "archive/whatsapp/+31-6-55954068" --previous-archive "archive/whatsapp/+31-6-55954068/export-history/d1276687bdfeb0e916c226e4cd9b8de210d5fa5ebb8bc10c9350f991b2a3d356" --media-sources "archive/whatsapp/+31-6-55954068/media-sources.json"
```

The build fails if the paired exports do not reconcile, any record bytes are lost, source byte spans differ, or the ZIP fails its integrity check. Outputs are deterministic. `metadata.json.source_notice.text` followed by every `messages.jsonl` record's `source.raw_record` reproduces `source/chat.txt` exactly as UTF-8.

This update adds **42 messages** and retains all **1158 previous messages**. The source reorders **7 earlier document entries** within identical timestamps; their existing message, link, and media IDs remain attached to the same content. See [export-update.json](export-update.json). The [previous export snapshot](export-history/d1276687bdfeb0e916c226e4cd9b8de210d5fa5ebb8bc10c9350f991b2a3d356/metadata.json) retains its original records and source bytes.

**4 separately supplied media files** have [text transcriptions and metadata](supplied-media.json), linked from their matching chat entries. Filename timestamps and media kinds establish candidate associations; the export contains no attachment hashes to prove those associations. Video transcripts retain machine confidence flags and suppressed candidates; the image transcription marks obscured text. [Restore the three original videos from verified chunks](../../../archive-bundles/whatsapp-media-2026-10-13/README.md).

- WhatsApp Video 2026-10-13 at 20.51.20.mp4: [transcription](media-transcripts/WhatsApp-Video-2026-10-13-at-20.51.20.txt) · [metadata and QA](media-transcripts/WhatsApp-Video-2026-10-13-at-20.51.20.json) · [chat entry](transcript.html#m001191)
- WhatsApp Video 2026-10-13 at 21.55.28.mp4: [transcription](media-transcripts/WhatsApp-Video-2026-10-13-at-21.55.28.txt) · [metadata and QA](media-transcripts/WhatsApp-Video-2026-10-13-at-21.55.28.json) · [chat entry](transcript.html#m001197)
- WhatsApp Video 2026-10-13 at 22.10.28.mp4: [transcription](media-transcripts/WhatsApp-Video-2026-10-13-at-22.10.28.txt) · [metadata and QA](media-transcripts/WhatsApp-Video-2026-10-13-at-22.10.28.json) · [chat entry](transcript.html#m001198)
- WhatsApp Image 2026-10-13 at 22.23.00.jpeg: [transcription](media-transcripts/image-2026-10-13-22-23-00.txt) · [metadata and QA](media-transcripts/image-2026-10-13-22-23-00.json) · [chat entry](transcript.html#m001200)
