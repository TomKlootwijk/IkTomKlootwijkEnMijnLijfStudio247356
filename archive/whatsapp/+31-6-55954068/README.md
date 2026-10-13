# WhatsApp chat archive: +31 6 55954068

This is the complete text transcription of the supplied [+31 6 55954068.zip](../../../%2B31%206%2055954068.zip), containing **1,158 messages** from **2026-09-23 06:40:13** through **2026-10-13 10:32:28** across **21 dates**. Original sender labels, spelling, Unicode, multiline bodies, repeated timestamps, duplicate records, full URLs, and media placeholders are preserved.

Open the [searchable HTML transcript](transcript.html) locally in a browser for clickable links, date navigation, and a permanent anchor for every message. A [Markdown transcript](transcript.md) and [plain text transcript](transcript.txt) are also included. The HTML is self-contained, runs no scripts, and loads no remote assets.

- [messages.jsonl](messages.jsonl): one record per message, with exact local date/time to the second, sender, body, source line/byte spans, raw source record, SHA-256, link/media IDs, and reply context.
- [links.jsonl](links.jsonl): **604 URL occurrences**, **598 unique URLs**, with exact full URLs, sender/time, body offsets, and message anchors. Repeated occurrences remain separate. Links were preserved without fetching them.
- [media-references.jsonl](media-references.jsonl): **340 placeholders** with original markers and descriptions: **316 omitted-media references** and **24 album markers**.
- [metadata.json](metadata.json): source/export metadata, ZIP member information and hashes, daily counts, participants as labeled, date range, media/link counts, parsing conventions, and available-data limits.
- [source-comparison.json](source-comparison.json): reconciliation of all messages in both exports, including the **2 reply quotes** available only in Markdown.
- [source/chat.txt](source/chat.txt) and [source/chat.md](source/chat.md): byte-identical originals extracted from the ZIP.
- [validation.json](validation.json) and [checksums.json](checksums.json): completeness checks and SHA-256 checksums for every delivered archive file except the checksum manifest itself.

The TXT export is the canonical message body/time source because it retains seconds. The Markdown export supplies the chat label, export time (October 13, 2026 at 11:21 AM), and two reply quotes. All 1,158 message identities and bodies reconcile after the documented placeholder substitutions and separate preservation of those quotes. The undated encryption notice is preserved separately without assigning it a fabricated timestamp.

The ZIP contains only these two text files. It includes **no actual media**: image (198), audio (95), document (20), and video (3) references say omitted; 24 album markers have no item contents. The absent voice recordings cannot be transcribed from this export. Source timestamps have **no specified timezone**; no offset is assumed. Exported sender labels are all `You`; the phone-number chat label is not used to invent another sender. No WhatsApp internal message IDs or read/delivery receipts are available; `m000001` and similar IDs are local sequence identifiers.

Rebuild using Python 3.12+ and the standard library, from the repository root:

```powershell
python scripts/archive_whatsapp.py "+31 6 55954068.zip" --output "archive/whatsapp/+31-6-55954068"
```

The build fails if the paired exports do not reconcile, any record bytes are lost, source byte spans differ, or the ZIP fails its integrity check. Outputs are deterministic. `metadata.json.source_notice.text` followed by every `messages.jsonl` record's `source.raw_record` reproduces `source/chat.txt` exactly as UTF-8.
