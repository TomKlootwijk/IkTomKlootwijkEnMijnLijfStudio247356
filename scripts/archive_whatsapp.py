"""Losslessly archive the supplied paired WhatsApp TXT/Markdown ZIP export.

Uses only the Python standard library. Source timestamps remain timezone-naive.
Run from the repository root; output paths in metadata are repository-relative.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import html
import json
from pathlib import Path
import re
from urllib.parse import quote, urlsplit
from zipfile import ZipFile


TXT_HEADER = re.compile(
    r"^\[(?P<date>\d{1,2}/\d{1,2}/\d{2}), "
    r"(?P<time>\d{1,2}:\d{2}:\d{2} [AP]M)\] "
    r"(?P<sender>[^\n]+?): (?P<body>[^\n]*)$", re.MULTILINE
)
MD_HEADER = re.compile(r"^\[(\d{1,2}:\d{2} [AP]M)\] \*\*(.+?):\*\*(?: (.*))?$")
URL = re.compile(r'https?://[^\s<>"`]+')
MARKER = re.compile(r"<(image omitted|audio omitted|document omitted|video omitted|album message)>")
MARKDOWN_MARKERS = {
    "<image omitted>": "[Image]", "<audio omitted>": "[Audio]",
    "<document omitted>": "[Document]", "<video omitted>": "[Video]",
    "<album message>": "[album message]",
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def write_jsonl(path, values):
    path.write_text("".join(json.dumps(v, ensure_ascii=False) + "\n" for v in values), encoding="utf-8", newline="\n")


def parse_txt(data):
    text = data.decode("utf-8")
    matches = list(TXT_HEADER.finditer(text))
    if not matches:
        raise ValueError("No supported TXT message headers found")
    # Catch a changed or unsupported export format rather than silently absorbing it.
    for line in text.splitlines():
        if re.match(r"^\[\d{1,2}/\d{1,2}/\d{2}, ", line) and not TXT_HEADER.fullmatch(line):
            raise ValueError(f"Unparsed message header: {line[:100]!r}")
    records = []
    byte_offsets = [0]
    for character in text:
        byte_offsets.append(byte_offsets[-1] + len(character.encode("utf-8")))
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        raw = text[match.start():end]
        body = text[match.start("body"):end]
        if body.endswith("\n"):
            body = body[:-1]  # Only the record terminator; internal blank lines survive.
        stamp = datetime.strptime(f'{match["date"]}, {match["time"]}', "%m/%d/%y, %I:%M:%S %p")
        record_id = f"m{i + 1:06d}"
        records.append({
            "id": record_id, "sequence": i + 1,
            "timestamp_local": stamp.isoformat(), "date": stamp.date().isoformat(),
            "time": stamp.time().isoformat(), "timezone": None,
            "timestamp_raw": f'[{match["date"]}, {match["time"]}]',
            "sender": match["sender"], "body": body,
            "source": {
                "file": "source/chat.txt", "line_start": text.count("\n", 0, match.start()) + 1,
                "line_end": text.count("\n", 0, end) + (0 if raw.endswith("\n") else 1),
                "byte_start": byte_offsets[match.start()], "byte_end_exclusive": byte_offsets[end],
                "raw_record": raw, "sha256": digest(raw.encode("utf-8")),
            },
            "reply_context": [], "links": [], "media_references": [],
            "transcript_anchor": f"transcript.html#{record_id}",
        })
    return text[:matches[0].start()], records


def parse_markdown(data):
    lines = data.decode("utf-8").splitlines(keepends=True)
    date = None
    records = []
    info = {}
    for i, raw_line in enumerate(lines):
        line = raw_line.rstrip("\n")
        if line.startswith("# WhatsApp Chat Export: "):
            info["chat_label"] = line.split(": ", 1)[1]
        if line.startswith("Export date: "):
            info["export_date_raw"] = line.removeprefix("Export date: ")
            info["export_timestamp_local"] = datetime.strptime(info["export_date_raw"], "%B %d, %Y at %I:%M %p").isoformat()
        if line.startswith("## "):
            date = datetime.strptime(line[3:], "%B %d, %Y").date().isoformat()
        match = MD_HEADER.fullmatch(line)
        if match:
            if date is None:
                raise ValueError("Markdown message has no date heading")
            clock = datetime.strptime(match[1], "%I:%M %p").strftime("%H:%M")
            records.append({"date": date, "time_minute": clock, "sender": match[2],
                            "line_start": i + 1, "header": line, "body_lines": [match[3]] if match[3] is not None else []})
        elif records and not line.startswith("## "):
            records[-1]["body_lines"].append(line)
    for record in records:
        body_lines = record.pop("body_lines")
        while body_lines and body_lines[-1] in ("", "---"):
            body_lines.pop()
        record["body"] = "\n".join(body_lines)
    return info, records


def reconcile(records, markdown_records):
    if len(records) != len(markdown_records):
        raise ValueError("TXT and Markdown message counts differ")
    supplemental = []
    for txt, md in zip(records, markdown_records, strict=True):
        if (txt["date"], txt["time"][:5], txt["sender"]) != (md["date"], md["time_minute"], md["sender"]):
            raise ValueError(f'TXT/Markdown identity mismatch at {txt["id"]}')
        body = md["body"]
        contexts = []
        while body.startswith("> _"):
            first, separator, rest = body.partition("\n")
            if not separator or not first.endswith("_"):
                raise ValueError(f'Unrecognized reply quote at {txt["id"]}')
            quote_text = first[3:-1]
            sender, sep, quoted_body = quote_text.partition(": ")
            context = {"sender": sender if sep else None, "quoted_text": quoted_body if sep else quote_text,
                       "raw_markdown": first, "source_file": "source/chat.md",
                       "source_line": md["line_start"] + 1 + len(contexts),
                       "replied_to_message_id": None}
            contexts.append(context)
            body = rest
        expected = txt["body"]
        for original, replacement in MARKDOWN_MARKERS.items():
            expected = expected.replace(original, replacement)
        if body != expected:
            raise ValueError(f'TXT/Markdown body mismatch at {txt["id"]}')
        txt["reply_context"] = contexts
        txt["markdown_source"] = {"file": "source/chat.md", "line_start": md["line_start"],
                                  "header_raw": md["header"], "body_raw": md["body"]}
        if contexts:
            supplemental.append({"message_id": txt["id"], "reply_context": contexts})
    return {"status": "passed", "records_compared": len(records),
            "identity_check": "Sequence, calendar date, minute timestamp, and sender match for every record.",
            "body_check": "Every body matches after the documented media marker substitutions and removal of supplemental reply quotes.",
            "marker_substitutions": MARKDOWN_MARKERS,
            "supplemental_reply_context_count": sum(len(x["reply_context"]) for x in supplemental),
            "supplemental_records": supplemental, "unreconciled_records": 0}


def enrich(records):
    links, media = [], []
    for record in records:
        context = {"message_id": record["id"], "timestamp_local": record["timestamp_local"],
                   "timezone": None, "sender": record["sender"], "transcript_anchor": record["transcript_anchor"]}
        for occurrence in URL.finditer(record["body"]):
            url = occurrence.group()
            parsed = urlsplit(url)
            link = {"id": f"l{len(links) + 1:06d}", **context, "url": url,
                    "scheme": parsed.scheme, "host": parsed.hostname,
                    "body_character_start": occurrence.start(), "body_character_end_exclusive": occurrence.end()}
            links.append(link)
            record["links"].append(link["id"])
        for occurrence in MARKER.finditer(record["body"]):
            marker = occurrence.group()
            kind = occurrence[1].split()[0]
            reference = {"id": f"a{len(media) + 1:06d}", **context, "kind": kind,
                         "marker": marker, "content_in_archive": False,
                         "status": "album_marker_only" if kind == "album" else "omitted_in_export",
                         "exported_description": record["body"][occurrence.end():].strip(),
                         "body_character_start": occurrence.start(), "body_character_end_exclusive": occurrence.end()}
            media.append(reference)
            record["media_references"].append(reference["id"])
    return links, media


def markdown_text(text):
    parts, position = [], 0
    for match in URL.finditer(text):
        parts.append(re.sub(r"([\\`*_{}\[\]<>()#+.!|~>-])", r"\\\1", text[position:match.start()]))
        parts.append(f"<{match.group()}>")
        position = match.end()
    parts.append(re.sub(r"([\\`*_{}\[\]<>()#+.!|~>-])", r"\\\1", text[position:]))
    return "".join(parts)


def html_text(text):
    parts, position = [], 0
    for match in URL.finditer(text):
        parts.append(html.escape(text[position:match.start()]))
        safe_url = html.escape(match.group(), quote=True)
        parts.append(f'<a href="{safe_url}" rel="noreferrer">{safe_url}</a>')
        position = match.end()
    parts.append(html.escape(text[position:]))
    return "".join(parts)


def render(destination, records, metadata, preamble):
    title = f'WhatsApp archive: {metadata["chat_label"]}'
    note = (f'{len(records):,} messages; {metadata["date_range"]["first"]} through {metadata["date_range"]["last"]}. '
            'All timestamps are the exported local time; the source does not specify a timezone. '
            'All source sender labels are preserved. Media marked omitted is absent from the supplied ZIP.')
    md = [f"# {title}\n\n{note}\n\n[Archive metadata](metadata.json) · [All links](links.jsonl) · "
          "[Media references](media-references.jsonl) · [Source comparison](source-comparison.json)\n\n"
          f"Source notice (undated):\n\n> {markdown_text(preamble.rstrip(chr(10)))}\n\n"]
    plain = [f"{title}\n{note}\n\nSOURCE NOTICE (undated)\n{preamble}\n"]
    doc = ['<!doctype html><html lang="en"><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">',
           f'<title>{html.escape(title)}</title>',
           '<style>body{max-width:72rem;margin:2rem auto;padding:0 1rem;font:16px/1.6 system-ui,sans-serif;color:#17212b;background:#fff}'
           'a{color:#075daf;overflow-wrap:anywhere}nav{display:flex;gap:.8rem;flex-wrap:wrap}'
           'article{border-top:1px solid #dce2e6;padding:1rem 0;scroll-margin-top:1rem}'
           'h2{margin-top:2.5rem}h3{font-size:1rem;margin:0}.body,.reply{white-space:pre-wrap;overflow-wrap:anywhere}'
           '.reply{border-left:3px solid #73808c;padding-left:1rem;color:#485663}.meta{font-size:.85rem;color:#53616d}'
           '@media print{body{max-width:none}article{break-inside:avoid}nav{display:none}}</style>',
           f'<h1>{html.escape(title)}</h1><p>{html.escape(note)}</p>',
           '<p><a href="README.md">Archive guide</a> · <a href="metadata.json">Metadata</a> · '
           '<a href="links.jsonl">Link index</a> · <a href="media-references.jsonl">Media references</a></p>',
           '<nav aria-label="Chat dates">']
    for day, count in metadata["messages_by_date"].items():
        doc.append(f'<a href="#d{day}">{day} ({count})</a>')
    doc.extend(['</nav>', f'<p class="meta">Undated source notice: {html.escape(preamble.rstrip(chr(10)))}</p>'])
    day = None
    for record in records:
        if record["date"] != day:
            day = record["date"]
            md.append(f"## {day}\n\n")
            doc.append(f'<h2 id="d{day}">{day}</h2>')
        label = f'{record["id"]} · {record["date"]} {record["time"]} · {record["sender"]}'
        md.append(f'### {markdown_text(label)}\n\n')
        plain.append(f'[{record["date"]} {record["time"]}] {record["sender"]} ({record["id"]})\n')
        doc.append(f'<article id="{record["id"]}"><h3><a href="#{record["id"]}">{html.escape(label)}</a></h3>')
        for reply in record["reply_context"]:
            content = f'{reply["sender"]}: {reply["quoted_text"]}'
            md.append(f'> Reply context from chat.md: {markdown_text(content)}\n\n')
            plain.append(f'REPLY CONTEXT (chat.md): {content}\n')
            doc.append(f'<blockquote class="reply">Reply context from chat.md: {html_text(content)}</blockquote>')
        md.append("\n".join("> " + line if line else ">" for line in markdown_text(record["body"]).split("\n")) + "\n\n")
        md.append(f'[Open message](transcript.html#{record["id"]}) · Source: chat.txt lines '
                  f'{record["source"]["line_start"]}–{record["source"]["line_end"]}\n\n')
        plain.append(record["body"] + "\n\n")
        doc.append(f'<div class="body">{html_text(record["body"])}</div>')
        doc.append(f'<p class="meta">Source: chat.txt lines {record["source"]["line_start"]}–'
                   f'{record["source"]["line_end"]}; record {record["sequence"]}</p></article>')
    doc.append('</html>')
    for name, parts in [("transcript.md", md), ("transcript.txt", plain), ("transcript.html", doc)]:
        content = "\n".join(parts) if name.endswith(".html") else "".join(parts)
        (destination / name).write_text(content.rstrip("\n") + "\n", encoding="utf-8", newline="\n")


def build(source, destination):
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "source").mkdir(exist_ok=True)
    with ZipFile(source) as zipped:
        if sorted(zipped.namelist()) != ["chat.md", "chat.txt"]:
            raise ValueError("Expected exactly chat.txt and chat.md; inspect other members before extending this parser")
        if zipped.testzip() is not None:
            raise ValueError("ZIP integrity check failed")
        members = []
        for entry in zipped.infolist():
            data = zipped.read(entry)
            (destination / "source" / entry.filename).write_bytes(data)
            members.append({"name": entry.filename, "bytes": len(data), "sha256": digest(data),
                            "crc32": f"{entry.CRC:08x}", "compressed_bytes": entry.compress_size,
                            "compression_method": entry.compress_type, "zip_timestamp_local": datetime(*entry.date_time).isoformat(),
                            "zip_timestamp_timezone": None, "zip_flag_bits": entry.flag_bits,
                            "zip_create_system": entry.create_system, "zip_external_attributes": entry.external_attr,
                            "zip_extra_hex": entry.extra.hex(), "zip_comment_hex": entry.comment.hex()})
        zip_comment = zipped.comment.hex()
    txt = (destination / "source/chat.txt").read_bytes()
    md = (destination / "source/chat.md").read_bytes()
    preamble, records = parse_txt(txt)
    export_info, md_records = parse_markdown(md)
    comparison = reconcile(records, md_records)
    links, media = enrich(records)
    dates = Counter(record["date"] for record in records)
    stamps = Counter(record["timestamp_local"] for record in records)
    duplicates = Counter((r["timestamp_local"], r["sender"], r["body"]) for r in records)
    source_path = source.relative_to(Path.cwd()).as_posix()
    metadata = {
        "schema_version": 1, "archive_type": "WhatsApp paired text export",
        **export_info, "export_timestamp_timezone": None,
        "source_archive": {"path": source_path, "bytes": source.stat().st_size,
                           "sha256": digest(source.read_bytes()), "zip_comment_hex": zip_comment, "members": members},
        "canonical_message_source": "source/chat.txt", "supplemental_metadata_source": "source/chat.md",
        "archive_builder": {"path": "scripts/archive_whatsapp.py", "sha256": digest(Path(__file__).read_bytes())},
        "source_notice": {"text": preamble, "timestamp": None, "source_line_start": 1},
        "message_count": len(records), "sender_label_counts": dict(Counter(r["sender"] for r in records)),
        "date_range": {"first": records[0]["timestamp_local"], "last": records[-1]["timestamp_local"]},
        "messages_by_date": dict(dates), "timezone": None,
        "timestamp_interpretation": "M/D/YY and 12-hour clock parsed from chat.txt as 20YY; no timezone conversion or offset assigned.",
        "record_order": "Original export order; equal timestamps and exact duplicates retained.",
        "same_timestamp_groups": sum(count > 1 for count in stamps.values()),
        "exact_duplicate_groups": sum(count > 1 for count in duplicates.values()),
        "multiline_message_count": sum("\n" in r["body"] for r in records),
        "link_occurrence_count": len(links), "unique_link_count": len({v["url"] for v in links}),
        "links_by_host": dict(Counter(v["host"] for v in links).most_common()),
        "link_extraction": "Unmodified, whitespace-delimited HTTP(S) URL tokens; no resolution, fetching, query rewriting, or deduplication of occurrences.",
        "media_reference_count": len(media), "media_markers_by_kind": dict(Counter(v["kind"] for v in media)),
        "omitted_media_reference_count": sum(v["status"] == "omitted_in_export" for v in media),
        "album_marker_count": sum(v["kind"] == "album" for v in media),
        "media_files_in_archive": 0, "supplemental_reply_context_count": comparison["supplemental_reply_context_count"],
        "limitations": [
            "Completeness is relative to the two files in the supplied ZIP; messages absent from this export cannot be recovered from it.",
            "Every exported sender label is You; no other participant identity is inferred from the chat label.",
            "Omitted image, audio, document, and video bytes are absent, so no voice transcription, image OCR, or document text can be recovered from this ZIP.",
            "Album markers have no included album contents or item counts.",
            "Source timestamps and ZIP timestamps carry no timezone offset.",
            "Two reply quotes are preserved from chat.md; their target message IDs are not specified by the export.",
            "No delivery/read receipts, WhatsApp internal IDs, edit history, device identifiers, or attachment EXIF are supplied.",
        ],
        "byte_offset_convention": "Zero-based, end-exclusive UTF-8 byte offsets in source/chat.txt; line numbers are one-based.",
        "character_offset_convention": "Zero-based, end-exclusive Unicode code-point offsets in each message body.",
        "reproduce": f'python scripts/archive_whatsapp.py "{source_path}" --output "{destination.relative_to(Path.cwd()).as_posix()}"',
    }
    if preamble.encode("utf-8") + b"".join(r["source"]["raw_record"].encode("utf-8") for r in records) != txt:
        raise ValueError("Lossless reconstruction failed")
    if any(a["timestamp_local"] > b["timestamp_local"] for a, b in zip(records, records[1:])):
        raise ValueError("Non-chronological source records; inspect before archiving")
    for record in records:
        span = record["source"]
        if txt[span["byte_start"]:span["byte_end_exclusive"]] != span["raw_record"].encode("utf-8"):
            raise ValueError("Source byte span validation failed")
    write_jsonl(destination / "messages.jsonl", records)
    write_jsonl(destination / "links.jsonl", links)
    write_jsonl(destination / "media-references.jsonl", media)
    write_json(destination / "metadata.json", metadata)
    write_json(destination / "source-comparison.json", comparison)
    render(destination, records, metadata, preamble)
    archive_link = quote("../../../" + source_path, safe="/.")
    guide = f'''# WhatsApp chat archive: {metadata["chat_label"]}

This is the complete text transcription of the supplied [{source.name}]({archive_link}), containing **{len(records):,} messages** from **{records[0]["date"]} {records[0]["time"]}** through **{records[-1]["date"]} {records[-1]["time"]}** across **{len(dates)} dates**. Original sender labels, spelling, Unicode, multiline bodies, repeated timestamps, duplicate records, full URLs, and media placeholders are preserved.

Open the [searchable HTML transcript](transcript.html) locally in a browser for clickable links, date navigation, and a permanent anchor for every message. A [Markdown transcript](transcript.md) and [plain text transcript](transcript.txt) are also included. The HTML is self-contained, runs no scripts, and loads no remote assets.

- [messages.jsonl](messages.jsonl): one record per message, with exact local date/time to the second, sender, body, source line/byte spans, raw source record, SHA-256, link/media IDs, and reply context.
- [links.jsonl](links.jsonl): **{len(links)} URL occurrences**, **{metadata["unique_link_count"]} unique URLs**, with exact full URLs, sender/time, body offsets, and message anchors. Repeated occurrences remain separate. Links were preserved without fetching them.
- [media-references.jsonl](media-references.jsonl): **{len(media)} placeholders** with original markers and descriptions: **{metadata["omitted_media_reference_count"]} omitted-media references** and **{metadata["album_marker_count"]} album markers**.
- [metadata.json](metadata.json): source/export metadata, ZIP member information and hashes, daily counts, participants as labeled, date range, media/link counts, parsing conventions, and available-data limits.
- [source-comparison.json](source-comparison.json): reconciliation of all messages in both exports, including the **{comparison["supplemental_reply_context_count"]} reply quotes** available only in Markdown.
- [source/chat.txt](source/chat.txt) and [source/chat.md](source/chat.md): byte-identical originals extracted from the ZIP.
- [validation.json](validation.json) and [checksums.json](checksums.json): completeness checks and SHA-256 checksums for every delivered archive file except the checksum manifest itself.

The TXT export is the canonical message body/time source because it retains seconds. The Markdown export supplies the chat label, export time ({export_info["export_date_raw"]}), and two reply quotes. All {len(records):,} message identities and bodies reconcile after the documented placeholder substitutions and separate preservation of those quotes. The undated encryption notice is preserved separately without assigning it a fabricated timestamp.

The ZIP contains only these two text files. It includes **no actual media**: image ({metadata["media_markers_by_kind"].get("image", 0)}), audio ({metadata["media_markers_by_kind"].get("audio", 0)}), document ({metadata["media_markers_by_kind"].get("document", 0)}), and video ({metadata["media_markers_by_kind"].get("video", 0)}) references say omitted; {metadata["album_marker_count"]} album markers have no item contents. The absent voice recordings cannot be transcribed from this export. Source timestamps have **no specified timezone**; no offset is assumed. Exported sender labels are all `You`; the phone-number chat label is not used to invent another sender. No WhatsApp internal message IDs or read/delivery receipts are available; `m000001` and similar IDs are local sequence identifiers.

Rebuild using Python 3.12+ and the standard library, from the repository root:

```powershell
{metadata["reproduce"]}
```

The build fails if the paired exports do not reconcile, any record bytes are lost, source byte spans differ, or the ZIP fails its integrity check. Outputs are deterministic. `metadata.json.source_notice.text` followed by every `messages.jsonl` record's `source.raw_record` reproduces `source/chat.txt` exactly as UTF-8.
'''
    (destination / "README.md").write_text(guide, encoding="utf-8", newline="\n")
    html_doc = (destination / "transcript.html").read_text(encoding="utf-8")
    validation = {"status": "passed", "source_zip_crc_check": "passed",
                  "source_reconstruction_sha256": digest(txt), "source_reconstruction_matches": True,
                  "source_byte_spans_match": True, "source_record_order_preserved": True,
                  "paired_export_comparison": comparison["status"], "message_count": len(records),
                  "link_occurrence_count": len(links), "unique_link_count": metadata["unique_link_count"],
                  "media_reference_count": len(media), "reply_context_count": comparison["supplemental_reply_context_count"],
                  "html_message_anchor_count": len(re.findall(r'<article id="m\d{6}">', html_doc)),
                  "scope": "Every text record and available supplemental metadata in the supplied ZIP; absent media is explicitly inventoried."}
    if validation["html_message_anchor_count"] != len(records):
        raise ValueError("Missing HTML message anchors")
    write_json(destination / "validation.json", validation)
    paths = sorted(p for p in destination.rglob("*") if p.is_file() and p.name != "checksums.json")
    checksums = {"algorithm": "SHA-256", "source_archive": metadata["source_archive"],
                 "files": [{"path": p.relative_to(destination).as_posix(), "bytes": p.stat().st_size,
                            "sha256": digest(p.read_bytes())} for p in paths]}
    write_json(destination / "checksums.json", checksums)
    print(json.dumps(validation, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build(args.source.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
