"""Losslessly archive the supplied paired WhatsApp TXT/Markdown ZIP export.

Uses only the Python standard library. Source timestamps remain timezone-naive.
Run from the repository root; output paths in metadata are repository-relative.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict, deque
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


def preserve_identities(records, previous_archive):
    if previous_archive is None:
        return {}, None
    previous = [json.loads(line) for line in (previous_archive / "messages.jsonl").read_text(encoding="utf-8").splitlines()]
    previous_meta = json.loads((previous_archive / "metadata.json").read_text(encoding="utf-8"))
    rebuilt = previous_meta["source_notice"]["text"].encode("utf-8") + b"".join(r["source"]["raw_record"].encode("utf-8") for r in previous)
    if rebuilt != (previous_archive / "source/chat.txt").read_bytes():
        raise ValueError("Previous export snapshot does not reconstruct")
    for member in previous_meta["source_archive"]["members"]:
        original = (previous_archive / "source" / member["name"]).read_bytes()
        if len(original) != member["bytes"] or digest(original) != member["sha256"]:
            raise ValueError("Previous export snapshot source hash mismatch")
    waiting = defaultdict(deque)
    key = lambda r: (r["timestamp_local"], r["sender"], r["body"])
    for record in previous:
        waiting[key(record)].append(record)
    next_id = max(int(r["id"][1:]) for r in previous) + 1
    added, reordered = [], []
    for record in records:
        matches = waiting[key(record)]
        if matches:
            old = matches.popleft()
            record["id"] = old["id"]
            if old["sequence"] != record["sequence"]:
                reordered.append({"message_id": old["id"], "previous_sequence": old["sequence"], "current_sequence": record["sequence"]})
        else:
            record["id"] = f"m{next_id:06d}"
            next_id += 1
            added.append(record["id"])
        record["transcript_anchor"] = f'transcript.html#{record["id"]}'
    if any(waiting.values()):
        raise ValueError("Updated export omits or changes earlier messages; inspect before replacing the archive")
    update = {"status": "passed", "previous_archive": previous_archive.relative_to(Path.cwd()).as_posix(),
              "previous_source_archive_sha256": previous_meta["source_archive"]["sha256"],
              "previous_message_count": len(previous), "current_message_count": len(records),
              "retained_message_count": len(previous), "added_message_count": len(added),
              "added_message_ids": added, "removed_or_changed_previous_messages": 0,
              "reordered_message_count": len(reordered), "reordered_messages": reordered,
              "identity_method": "Match timestamp, sender, exact body, and occurrence order among duplicates; retain prior message/link/media IDs. Sequence follows the current source export."}
    return {r["id"]: r for r in previous}, update


def enrich(records, previous=None):
    previous = previous or {}
    next_link = max((int(v[1:]) for r in previous.values() for v in r["links"]), default=0) + 1
    next_media = max((int(v[1:]) for r in previous.values() for v in r["media_references"]), default=0) + 1
    links, media = [], []
    for record in records:
        old = previous.get(record["id"], {})
        old_links = old.get("links", [])
        old_media = old.get("media_references", [])
        context = {"message_id": record["id"], "timestamp_local": record["timestamp_local"],
                   "timezone": None, "sender": record["sender"], "transcript_anchor": record["transcript_anchor"]}
        for i, occurrence in enumerate(URL.finditer(record["body"])):
            url = occurrence.group()
            parsed = urlsplit(url)
            link_id = old_links[i] if i < len(old_links) else f"l{next_link:06d}"
            if i >= len(old_links):
                next_link += 1
            link = {"id": link_id, **context, "url": url,
                    "scheme": parsed.scheme, "host": parsed.hostname,
                    "body_character_start": occurrence.start(), "body_character_end_exclusive": occurrence.end()}
            links.append(link)
            record["links"].append(link["id"])
        for i, occurrence in enumerate(MARKER.finditer(record["body"])):
            marker = occurrence.group()
            kind = occurrence[1].split()[0]
            media_id = old_media[i] if i < len(old_media) else f"a{next_media:06d}"
            if i >= len(old_media):
                next_media += 1
            reference = {"id": media_id, **context, "kind": kind,
                         "marker": marker, "content_in_archive": False,
                         "status": "album_marker_only" if kind == "album" else "omitted_in_export",
                         "exported_description": record["body"][occurrence.end():].strip(),
                         "body_character_start": occurrence.start(), "body_character_end_exclusive": occurrence.end()}
            media.append(reference)
            record["media_references"].append(reference["id"])
    return links, media


def associate_media(records, references, media_sources, destination):
    for record in records:
        record["media_supplements"] = []
    if media_sources is None:
        return []
    registry = json.loads(media_sources.read_text(encoding="utf-8"))
    by_id = {r["id"]: r for r in records}
    supplied = []
    for item in registry["files"]:
        item = dict(item)
        for field in ("transcript_text", "transcript_json"):
            artifact = (destination / item[field]).resolve()
            if not artifact.is_relative_to(destination) or not artifact.is_file():
                raise ValueError(f"Missing or unsafe media transcript: {item[field]}")
        transcript = json.loads((destination / item["transcript_json"]).read_text(encoding="utf-8"))
        if transcript["source"]["sha256"] != item["sha256"] or transcript["source"]["size_bytes"] != item["bytes"]:
            raise ValueError(f"Media transcript source hash mismatch: {item['original_file']}")
        matched = [r for r in references if r["kind"] == item["kind"] and r["timestamp_local"] == item["filename_timestamp_local"]]
        if len(matched) != 1:
            raise ValueError(f"Expected one timestamp/kind media candidate for {item['original_file']}")
        item["message_id"] = matched[0]["message_id"]
        item["media_reference_id"] = matched[0]["id"]
        item["association_basis"] = "Filename local timestamp and media kind match one exported placeholder; the export supplies no attachment hash or internal ID to independently prove identity."
        matched[0]["supplied_media"] = item
        by_id[item["message_id"]]["media_supplements"].append(item)
        supplied.append(item)
    return supplied


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
        for supplied in record.get("media_supplements", []):
            kind = supplied["kind"]
            text_path = supplied["transcript_text"]
            json_path = supplied["transcript_json"]
            md.append(f'Separately supplied {kind}: [text transcription]({text_path}) · [metadata and QA]({json_path}) · [original media]({supplied["storage_guide"]})\n\n')
            plain.append(f'SEPARATELY SUPPLIED {kind.upper()}: {text_path} (metadata: {json_path})\n\n')
            doc.append(f'<p>Separately supplied {html.escape(kind)}: <a href="{html.escape(text_path, quote=True)}">text transcription</a> · '
                       f'<a href="{html.escape(json_path, quote=True)}">metadata and QA</a> · '
                       f'<a href="{html.escape(supplied["storage_guide"], quote=True)}">original media</a></p>')
        doc.append(f'<p class="meta">Source: chat.txt lines {record["source"]["line_start"]}–'
                   f'{record["source"]["line_end"]}; record {record["sequence"]}</p></article>')
    doc.append('</html>')
    for name, parts in [("transcript.md", md), ("transcript.txt", plain), ("transcript.html", doc)]:
        content = "\n".join(parts) if name.endswith(".html") else "".join(parts)
        (destination / name).write_text(content.rstrip("\n") + "\n", encoding="utf-8", newline="\n")


def build(source, destination, previous_archive=None, media_sources=None):
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
    previous, update = preserve_identities(records, previous_archive)
    export_info, md_records = parse_markdown(md)
    comparison = reconcile(records, md_records)
    links, media = enrich(records, previous)
    supplied = associate_media(records, media, media_sources, destination)
    dates = Counter(record["date"] for record in records)
    stamps = Counter(record["timestamp_local"] for record in records)
    duplicates = Counter((r["timestamp_local"], r["sender"], r["body"]) for r in records)
    source_path = source.relative_to(Path.cwd()).as_posix()
    metadata = {
        "schema_version": 2, "archive_type": "WhatsApp paired text export",
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
        "message_id_convention": "Local persistent IDs; reused from the previous export by exact content/occurrence matching. Sequence is the current export position; IDs need not be numerically ordered.",
        "update_comparison": update,
        "separately_supplied_media_count": len(supplied),
        "separately_supplied_media_by_kind": dict(Counter(item["kind"] for item in supplied)),
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
            "Omitted media bytes are absent from the text ZIP. Separately supplied media and their transcription limits are recorded in supplied-media.json.",
            "Album markers have no included album contents or item counts.",
            "Source timestamps and ZIP timestamps carry no timezone offset.",
            "Two reply quotes are preserved from chat.md; their target message IDs are not specified by the export.",
            "No delivery/read receipts, WhatsApp internal IDs, edit history, device identifiers, or attachment EXIF are supplied.",
        ],
        "byte_offset_convention": "Zero-based, end-exclusive UTF-8 byte offsets in source/chat.txt; line numbers are one-based.",
        "character_offset_convention": "Zero-based, end-exclusive Unicode code-point offsets in each message body.",
        "reproduce": f'python scripts/archive_whatsapp.py "{source_path}" --output "{destination.relative_to(Path.cwd()).as_posix()}"',
    }
    if previous_archive:
        metadata["reproduce"] += f' --previous-archive "{previous_archive.relative_to(Path.cwd()).as_posix()}"'
    if media_sources:
        metadata["reproduce"] += f' --media-sources "{media_sources.relative_to(Path.cwd()).as_posix()}"'
        metadata["media_source_registry"] = {"path": media_sources.relative_to(Path.cwd()).as_posix(), "sha256": digest(media_sources.read_bytes())}
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
    write_json(destination / "supplied-media.json", {"files": supplied})
    if update:
        write_json(destination / "export-update.json", update)
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

The ZIP contains only these two text files. It includes **no actual media**: image ({metadata["media_markers_by_kind"].get("image", 0)}), audio ({metadata["media_markers_by_kind"].get("audio", 0)}), document ({metadata["media_markers_by_kind"].get("document", 0)}), and video ({metadata["media_markers_by_kind"].get("video", 0)}) references say omitted; {metadata["album_marker_count"]} album markers have no item contents. The absent voice recordings cannot be transcribed from this export. Separately supplied files are described below. Source timestamps have **no specified timezone**; no offset is assumed. Exported sender labels are all `You`; the phone-number chat label is not used to invent another sender. No WhatsApp internal message IDs or read/delivery receipts are available; `m000001` and similar IDs are local persistent identifiers.

Rebuild using Python 3.12+ and the standard library, from the repository root:

```powershell
{metadata["reproduce"]}
```

The build fails if the paired exports do not reconcile, any record bytes are lost, source byte spans differ, or the ZIP fails its integrity check. Outputs are deterministic. `metadata.json.source_notice.text` followed by every `messages.jsonl` record's `source.raw_record` reproduces `source/chat.txt` exactly as UTF-8.
'''
    if update:
        guide += f'\nThis update adds **{update["added_message_count"]} messages** and retains all **{update["retained_message_count"]} previous messages**. The source reorders **{update["reordered_message_count"]} earlier document entries** within identical timestamps; their existing message, link, and media IDs remain attached to the same content. See [export-update.json](export-update.json). The [previous export snapshot]({previous_archive.relative_to(destination).as_posix()}/metadata.json) retains its original records and source bytes.\n'
    if supplied:
        guide += f'\n**{len(supplied)} separately supplied media files** have [text transcriptions and metadata](supplied-media.json), linked from their matching chat entries. Filename timestamps and media kinds establish candidate associations; the export contains no attachment hashes to prove those associations. Video transcripts retain machine confidence flags and suppressed candidates; the image transcription marks obscured text. [Restore the three original videos from verified chunks](../../../archive-bundles/whatsapp-media-2026-10-13/README.md).\n'
        guide += "\n" + "".join(f'- {item["original_file"]}: [transcription]({item["transcript_text"]}) · [metadata and QA]({item["transcript_json"]}) · [chat entry](transcript.html#{item["message_id"]})\n' for item in supplied)
    (destination / "README.md").write_text(guide, encoding="utf-8", newline="\n")
    html_doc = (destination / "transcript.html").read_text(encoding="utf-8")
    validation = {"status": "passed", "source_zip_crc_check": "passed",
                  "source_reconstruction_sha256": digest(txt), "source_reconstruction_matches": True,
                  "source_byte_spans_match": True, "source_record_order_preserved": True,
                  "paired_export_comparison": comparison["status"], "message_count": len(records),
                  "link_occurrence_count": len(links), "unique_link_count": metadata["unique_link_count"],
                  "media_reference_count": len(media), "reply_context_count": comparison["supplemental_reply_context_count"],
                  "previous_message_ids_preserved": len(previous), "separately_supplied_media_count": len(supplied),
                  "supplied_media_transcript_source_hashes_match": True if supplied else None,
                  "html_message_anchor_count": len(re.findall(r'<article id="m\d{6}">', html_doc)),
                  "scope": "Every text record and available metadata in the supplied ZIP, plus separately supplied media transcription links; absent media is explicitly inventoried."}
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
    parser.add_argument("--previous-archive", type=Path, help="Saved previous export snapshot, to preserve persistent message IDs")
    parser.add_argument("--media-sources", type=Path, help="Registry of separately supplied media and their transcript artifacts")
    args = parser.parse_args()
    build(args.source.resolve(), args.output.resolve(),
          args.previous_archive.resolve() if args.previous_archive else None,
          args.media_sources.resolve() if args.media_sources else None)


if __name__ == "__main__":
    main()
