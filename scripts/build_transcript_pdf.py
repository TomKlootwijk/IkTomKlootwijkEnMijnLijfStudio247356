#!/usr/bin/env python3
"""Build a searchable, ordered transcript archive from a playlist manifest.

Run with the bundled Python runtime (ReportLab and pypdf are required):
    python scripts/build_transcript_pdf.py --manifest archive/PLAYLIST/manifest.json \
        --output output/pdf/playlist-transcripts.pdf

The manifest is read only. Transcript paths are relative to its directory.
No transcript content is invented when a source or transcript is unavailable.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from xml.sax.saxutils import escape, quoteattr

from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
)
from reportlab.platypus.tableofcontents import TableOfContents


NAVY = colors.HexColor("#17394A")
TEAL = colors.HexColor("#166A70")
INK = colors.HexColor("#202D35")
MUTED = colors.HexColor("#5C6C75")
RULE = colors.HexColor("#D6E0E5")
DASHES = str.maketrans({c: "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"})


def clean(value: Any) -> str:
    """Keep readable Unicode while removing XML-invalid control characters."""
    text = str(value if value is not None else "").translate(DASHES)
    text = text.replace("\u00a0", " ").replace("\ufeff", "")
    return "".join(c for c in text if c in "\t\n\r" or ord(c) >= 32)


def markup(value: Any) -> str:
    return escape(clean(value)).replace("\n", "<br/>")


def seconds(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (TypeError, ValueError):
        return None


def timestamp(value: Any) -> str:
    duration = seconds(value)
    if duration is None:
        return "time not supplied"
    total = int(duration)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def duration_label(value: Any) -> str:
    duration = seconds(value)
    return timestamp(duration) if duration is not None else "Unknown"


def source_url(entry: dict[str, Any]) -> str:
    return str(entry.get("url") or f"https://www.youtube.com/watch?v={entry.get('id', '')}")


def timed_url(url: str, start: Any) -> str:
    at = seconds(start)
    if at is None:
        return url
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query) if key != "t"]
    query.append(("t", str(int(at))))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def link(url: str, label: Any) -> str:
    return f'<link href={quoteattr(clean(url))} color="#166A70">{markup(label)}</link>'


def local_timestamp() -> str:
    try:
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo("Europe/Berlin"))
        return now.strftime("%d %B %Y, %H:%M %Z")
    except (ImportError, KeyError):
        # A Windows Python installation may not ship the IANA time-zone database.
        # Compute the EU's last-Sunday-of-March/October rule without host-local time.
        now = datetime.now(timezone.utc)
        march = datetime(now.year, 3, 31, 1, tzinfo=timezone.utc)
        october = datetime(now.year, 10, 31, 1, tzinfo=timezone.utc)
        dst_start = march - timedelta(days=(march.weekday() + 1) % 7)
        dst_end = october - timedelta(days=(october.weekday() + 1) % 7)
        is_dst = dst_start <= now < dst_end
        local = now.astimezone(timezone(timedelta(hours=2 if is_dst else 1)))
        return local.strftime("%d %B %Y, %H:%M ") + ("CEST" if is_dst else "CET")


def register_fonts() -> None:
    import reportlab

    windows = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    bundled = Path(reportlab.__file__).parent / "fonts"
    candidates = [
        (windows, ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf")),
        (
            Path("/usr/share/fonts/truetype/dejavu"),
            ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf", "DejaVuSans-BoldOblique.ttf"),
        ),
        (bundled, ("Vera.ttf", "VeraBd.ttf", "VeraIt.ttf", "VeraBI.ttf")),
    ]
    for directory, filenames in candidates:
        paths = [directory / filename for filename in filenames]
        if all(path.is_file() for path in paths):
            names = ("ArchiveSans", "ArchiveSans-Bold", "ArchiveSans-Italic", "ArchiveSans-BoldItalic")
            for name, path in zip(names, paths):
                pdfmetrics.registerFont(TTFont(name, str(path)))
            pdfmetrics.registerFontFamily(
                "ArchiveSans", normal=names[0], bold=names[1], italic=names[2], boldItalic=names[3]
            )
            return
    raise RuntimeError("No suitable embeddable font family was found.")


@dataclass
class Transcript:
    status: str
    data: dict[str, Any] | None = None
    text: str | None = None
    problem: str | None = None

    @property
    def available(self) -> bool:
        return self.status in {"segments", "text"}


def resolve_path(base: Path, value: Any) -> Path | None:
    if not value:
        return None
    path = Path(str(value))
    return path if path.is_absolute() else base / path


def read_transcript(base: Path, entry: dict[str, Any]) -> Transcript:
    json_path = resolve_path(base, entry.get("transcript_json"))
    text_path = resolve_path(base, entry.get("transcript_text"))
    json_problem = None
    if json_path and json_path.is_file():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("The JSON root is not an object.")
            segments = data.get("segments", [])
            if not isinstance(segments, list):
                raise ValueError("The transcript segments field is not a list.")
            usable = [segment for segment in segments if isinstance(segment, dict) and clean(segment.get("text")).strip()]
            qa = data.get("qa") or {}
            if usable or data.get("complete") or qa.get("full_audio_processed"):
                # Preserve the source sequence; do not reorder or merge speech.
                return Transcript("segments", data={**data, "segments": usable})
            if clean(data.get("text")).strip():
                return Transcript("text", data=data, text=clean(data["text"]))
            json_problem = "The transcript file contains no recognized speech."
        except (OSError, UnicodeError, ValueError) as error:
            json_problem = f"The transcript JSON could not be read: {error}"
    if text_path and text_path.is_file():
        try:
            text = text_path.read_text(encoding="utf-8-sig").strip()
            if text:
                return Transcript("text", text=text, problem=json_problem)
        except (OSError, UnicodeError) as error:
            return Transcript("missing", problem=f"The transcript text could not be read: {error}")
    reason = entry.get("transcript_error") or entry.get("error") or json_problem
    status = str(entry.get("transcription_status") or entry.get("transcript_status") or "").lower()
    if not reason:
        if status in {"failed", "unavailable", "skipped", "pending"}:
            reason = f"Transcription status: {status}. No transcript text is available in this archive."
        elif str(entry.get("download_status", "")).lower() in {"unavailable", "failed"}:
            reason = "The source audio was unavailable or could not be downloaded; no transcript is available."
        else:
            reason = "No readable transcript file is available in this archive."
    return Transcript("missing", problem=clean(reason))


class ChapterHeading(Paragraph):
    def __init__(self, text: str, style: ParagraphStyle, key: str, toc_text: str):
        super().__init__(text, style)
        self.bookmark_key = key
        self.toc_text = toc_text


class ArchiveDocument(BaseDocTemplate):
    def __init__(self, filename: str, playlist_title: str, **kwargs: Any):
        super().__init__(filename, **kwargs)
        self.playlist_title = playlist_title
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id="body", leftPadding=0, bottomPadding=0, rightPadding=0, topPadding=0)
        self.addPageTemplates(PageTemplate(id="archive", frames=[frame], onPage=self.draw_page))

    def draw_page(self, canvas: Any, document: Any) -> None:
        canvas.saveState()
        page_width, page_height = A4
        canvas.setTitle(self.playlist_title + " - Playlist transcripts")
        canvas.setAuthor("Playlist transcript archive")
        canvas.setSubject("Ordered, timestamped transcripts with original source links")
        if document.page > 1:
            canvas.setStrokeColor(RULE)
            canvas.setLineWidth(0.5)
            canvas.line(self.leftMargin, page_height - 17 * mm, page_width - self.rightMargin, page_height - 17 * mm)
            canvas.setFillColor(MUTED)
            canvas.setFont("ArchiveSans", 8)
            canvas.drawString(self.leftMargin, page_height - 14 * mm, self.truncated_title(self.width - 42 * mm))
            canvas.drawRightString(page_width - self.rightMargin, page_height - 14 * mm, "TRANSCRIPT ARCHIVE")
        canvas.setStrokeColor(RULE)
        canvas.line(self.leftMargin, 17 * mm, page_width - self.rightMargin, 17 * mm)
        canvas.setFont("ArchiveSans", 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(self.leftMargin, 12 * mm, "Machine-generated transcript archive")
        canvas.drawRightString(page_width - self.rightMargin, 12 * mm, str(document.page))
        canvas.restoreState()

    def truncated_title(self, width: float) -> str:
        title = clean(self.playlist_title).replace("\n", " ")
        if pdfmetrics.stringWidth(title, "ArchiveSans", 8) <= width:
            return title
        while title and pdfmetrics.stringWidth(title + "...", "ArchiveSans", 8) > width:
            title = title[:-1]
        return title.rstrip() + "..."

    def afterFlowable(self, flowable: Any) -> None:
        if isinstance(flowable, ChapterHeading):
            self.canv.bookmarkPage(flowable.bookmark_key)
            self.canv.addOutlineEntry(clean(flowable.toc_text), flowable.bookmark_key, level=0, closed=False)
            self.notify("TOCEntry", (0, markup(flowable.toc_text), self.page, flowable.bookmark_key))


def make_styles() -> dict[str, ParagraphStyle]:
    common = dict(fontName="ArchiveSans", textColor=INK, alignment=TA_LEFT, splitLongWords=True, allowWidows=0, allowOrphans=0)
    return {
        "eyebrow": ParagraphStyle("ArchiveEyebrow", **{**common, "textColor": TEAL}, fontSize=9, leading=13, textTransform="uppercase", spaceAfter=13),
        "cover": ParagraphStyle("ArchiveCover", **{**common, "fontName": "ArchiveSans-Bold", "textColor": NAVY}, fontSize=29, leading=35, spaceAfter=17),
        "subtitle": ParagraphStyle("ArchiveSubtitle", **common, fontSize=15, leading=21, spaceAfter=18),
        "h1": ParagraphStyle("ArchiveChapter", **{**common, "fontName": "ArchiveSans-Bold", "textColor": NAVY}, fontSize=18, leading=23, spaceAfter=11, keepWithNext=True),
        "h2": ParagraphStyle("ArchiveSubheading", **{**common, "fontName": "ArchiveSans-Bold", "textColor": NAVY}, fontSize=11, leading=15, spaceBefore=14, spaceAfter=7, keepWithNext=True),
        "body": ParagraphStyle("ArchiveBody", **common, fontSize=10.2, leading=14.2, spaceAfter=8),
        "segment": ParagraphStyle("ArchiveSegment", **common, fontSize=10, leading=14, spaceAfter=6),
        "meta": ParagraphStyle("ArchiveMetadata", **{**common, "textColor": MUTED}, fontSize=8.8, leading=12.5, spaceAfter=6),
        "note": ParagraphStyle("ArchiveNote", **{**common, "textColor": MUTED}, fontSize=9.2, leading=13, spaceAfter=8),
        "toc": ParagraphStyle("ArchiveTOC", **common, fontSize=10, leading=14, leftIndent=0, firstLineIndent=0, spaceBefore=7, rightIndent=22),
    }


def transcript_provenance(transcript: Transcript) -> str:
    data = transcript.data or {}
    details = []
    if data.get("language"):
        details.append("Automatic language detection: " + clean(data["language"]))
    model = data.get("model") or data.get("transcription_model")
    if model:
        details.append("Model: " + clean(model))
    if transcript.status == "segments":
        details.append(f"{len(data['segments']):,} timestamped segments")
    elif transcript.status == "text":
        details.append("Text transcript; use the supplied time labels where present")
    return " | ".join(details)


def transcript_events(data: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Insert omission markers without exposing suppressed decoder candidates."""
    intervals = [
        interval for interval in (data.get("qa") or {}).get("unreliable_intervals", [])
        if isinstance(interval, dict)
    ]
    intervals.sort(key=lambda interval: seconds(interval.get("start")) if seconds(interval.get("start")) is not None else float("inf"))
    result: list[tuple[str, dict[str, Any]]] = []
    next_interval = 0
    for segment in data.get("segments", []):
        segment_start = seconds(segment.get("start"))
        while next_interval < len(intervals):
            interval = intervals[next_interval]
            interval_start = seconds(interval.get("start"))
            if interval_start is None or segment_start is None or interval_start > segment_start:
                break
            result.append(("unreliable", interval))
            next_interval += 1
        result.append(("speech", segment))
    result.extend(("unreliable", interval) for interval in intervals[next_interval:])
    return result


def build_story(manifest: dict[str, Any], transcripts: list[Transcript], styles: dict[str, ParagraphStyle]) -> list[Any]:
    entries = manifest["entries"]
    title = clean(manifest.get("title") or manifest.get("id") or "YouTube playlist")
    playlist_url = str(manifest.get("url") or f"https://www.youtube.com/playlist?list={manifest.get('id', '')}")
    total_duration = sum(seconds(entry.get("duration")) or 0 for entry in entries)
    duration_known = sum(seconds(entry.get("duration")) is not None for entry in entries)
    available = sum(transcript.available for transcript in transcripts)
    downloaded = sum(entry.get("download_status") == "downloaded" for entry in entries)
    story: list[Any] = [Spacer(1, 21 * mm)]
    story.append(Paragraph("AUDIO COLLECTION / TRANSCRIPTS", styles["eyebrow"]))
    story.append(Paragraph(markup(title), styles["cover"]))
    story.append(Paragraph("Playlist transcript archive", styles["subtitle"]))
    story.append(HRFlowable(width="100%", thickness=1.5, color=TEAL, spaceAfter=17))
    story.append(Paragraph(f"<b>{len(entries):,}</b> playlist entries  |  <b>{available:,}</b> transcripts available  |  <b>{downloaded:,}</b> audio files downloaded", styles["body"]))
    duration_note = duration_label(total_duration) if duration_known else "Unknown"
    if duration_known != len(entries):
        duration_note += f" across {duration_known:,} entries with known duration"
    story.append(Paragraph("Total source duration: " + markup(duration_note), styles["body"]))
    story.append(Paragraph("Source playlist: " + link(playlist_url, playlist_url), styles["meta"]))
    story.append(Paragraph("Prepared: " + markup(local_timestamp()), styles["meta"]))
    hidden = manifest.get("youtube_hidden_unavailable_count", 0)
    if hidden:
        story.append(Paragraph(
            markup(f"YouTube's initial listing reported {hidden} unavailable playlist items. The current totals above include recordings subsequently recovered from public archives. This historical warning is not an additional entry count; chapters marked unavailable identify the remaining gaps."),
            styles["note"],
        ))
    recovered = sum(bool(entry.get("mirror_url")) and entry.get("download_status") == "downloaded" for entry in entries)
    if recovered:
        story.append(Paragraph(
            markup(f"{recovered} audio recordings were retrieved from public archive mirrors. Their chapters link to the original YouTube source and the available archived audio."),
            styles["note"],
        ))
    story.append(Spacer(1, 12 * mm))
    story.append(Paragraph("Reading this archive", styles["h2"]))
    for paragraph in (
        "Recordings appear in playlist order and retain their source titles. The contents and PDF bookmarks link to each recording. Timestamp labels link to the corresponding position in the original YouTube source.",
        "Machine transcription; not human reviewed. Recognition errors and omissions may occur, especially for quiet or overlapping speech. Time labels are approximate. Consult the source recording when an exact quotation matters.",
        "An 'Uncertain recognition' label marks speech flagged by the recognition system. Timed 'Unreliable audio transcription' markers identify intervals where speech could not be reliably recognized. Suppressed decoder candidates are omitted, rather than presented as spoken words.",
        "Every listed entry has a chapter. Missing, failed, pending, or unavailable material is identified explicitly; no missing speech is reconstructed. Playlist metadata and transcripts describe the archive at the time this document was built.",
    ):
        story.append(Paragraph(paragraph, styles["note"]))
    story.append(PageBreak())
    story.append(Paragraph("Contents", styles["h1"]))
    story.append(Paragraph("Select a title or page number to open a recording. Use the PDF bookmarks for the same ordered index.", styles["note"]))
    toc = TableOfContents()
    toc.levelStyles = [styles["toc"]]
    toc.dotsMinLevel = 0
    story.extend([toc, PageBreak()])

    for sequence, (entry, transcript) in enumerate(zip(entries, transcripts), start=1):
        position = entry.get("position", sequence)
        recording_title = clean(entry.get("title") or f"Title unavailable [{entry.get('id', sequence)}]")
        chapter_title = f"{position}. {recording_title}"
        key = f"recording-{sequence:04d}-{entry.get('id', sequence)}"
        heading = ChapterHeading(markup(chapter_title), styles["h1"], key, chapter_title)
        audio_status = clean(entry.get("download_status") or "not recorded")
        transcript_status = "Available" if transcript.available else "Unavailable"
        metadata = f"Duration: {duration_label(entry.get('duration'))} | Audio: {audio_status} | Transcript: {transcript_status}"
        source = source_url(entry)
        opening = [heading, Paragraph(markup(metadata), styles["meta"]), Paragraph("Source: " + link(source, source), styles["meta"])]
        if entry.get("mirror_url"):
            mirror_label = "Archived audio: " + clean(entry.get("download_source") or "Public archive")
            opening.append(Paragraph(link(str(entry["mirror_url"]), mirror_label), styles["meta"]))
        if entry.get("title_source"):
            opening.append(Paragraph("Title source: " + markup(entry["title_source"]), styles["meta"]))
        provenance = transcript_provenance(transcript)
        if provenance:
            opening.append(Paragraph(markup(provenance), styles["meta"]))
        if transcript.available:
            qa = (transcript.data or {}).get("qa") or {}
            review_notice = "Machine transcription; not human reviewed. Recognition errors and omissions may occur, especially for quiet or overlapping speech."
            if qa.get("human_reviewed") is True:
                review_notice = "Machine transcription; marked as human reviewed in the transcript metadata."
            opening.append(Paragraph(review_notice, styles["note"]))
            quality = []
            if qa.get("full_audio_processed") is True:
                quality.append("Full audio processed")
            if qa.get("flagged_segments"):
                quality.append(f"{qa['flagged_segments']} flagged recognition segments")
            suppressed = qa.get("suppressed_segments", qa.get("suppressed_probable_silence_segments", 0))
            if suppressed:
                quality.append(f"{suppressed} unreliable or probable non-speech candidates omitted")
            if quality:
                opening.append(Paragraph(markup(" | ".join(quality)), styles["meta"]))
        opening.append(HRFlowable(width="100%", thickness=0.5, color=RULE, spaceBefore=3, spaceAfter=11))
        # A normal-length title and its metadata stay together. A pathological title
        # can still split as a Paragraph instead of causing an oversized table row.
        story.extend(opening)
        if audio_status in {"failed", "unavailable", "pending"}:
            reason = entry.get("download_error") or entry.get("error")
            message = f"Audio status: {audio_status}."
            if reason:
                message += " " + clean(reason)
            story.append(Paragraph(markup(message), styles["note"]))
        if transcript.problem and transcript.available:
            story.append(Paragraph(markup(transcript.problem), styles["note"]))
        if transcript.status == "segments":
            events = transcript_events(transcript.data)
            if not events:
                story.append(Paragraph("No speech was reliably recognized; see uncertainty intervals when supplied.", styles["note"]))
            for kind, segment in events:
                at = timestamp(segment.get("start"))
                end = timestamp(segment.get("end")) if seconds(segment.get("end")) is not None else None
                label = f"[{at}{' - ' + end if end else ''}]"
                stamp = f'<font size="8" color="#166A70">{link(timed_url(source, segment.get("start")), label)}</font>'
                if kind == "unreliable":
                    reason = clean(segment.get("reason") or "Speech could not be reliably recognized.").strip()
                    text = "<i>" + markup("[Unreliable audio transcription: " + reason + "]") + "</i>"
                else:
                    text = markup(clean(segment.get("text")).strip())
                    if segment.get("qa_flags"):
                        text = '<font size="8" color="#5C6C75">[Uncertain recognition]</font> ' + text
                story.append(Paragraph(stamp + "  " + text, styles["segment"]))
        elif transcript.status == "text":
            for paragraph in re.split(r"\n\s*\n", clean(transcript.text).strip()):
                if paragraph.strip():
                    story.append(Paragraph(markup(paragraph), styles["segment"]))
        else:
            story.append(Paragraph("Transcript unavailable", styles["h2"]))
            story.append(Paragraph(markup(transcript.problem), styles["body"]))
        if sequence < len(entries):
            story.append(PageBreak())
    return story


def validate_pdf(path: Path, entries: list[dict[str, Any]], transcript_count: int) -> dict[str, Any]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = len(reader.pages)
    if pages < 2:
        raise RuntimeError("Generated PDF is unexpectedly short.")
    cover_text = reader.pages[0].extract_text() or ""
    if "Playlist transcript archive" not in cover_text:
        raise RuntimeError("The PDF cover is not searchable.")
    outline_count = sum(isinstance(item, dict) for item in reader.outline)
    if outline_count != len(entries):
        raise RuntimeError(f"Expected {len(entries)} recording bookmarks; found {outline_count}.")
    empty_pages = [number + 1 for number, page in enumerate(reader.pages) if not (page.extract_text() or "").strip()]
    if empty_pages:
        raise RuntimeError(f"Unexpected empty PDF pages: {empty_pages}")
    return {"output": str(path.resolve()), "pages": pages, "entries": len(entries), "transcripts_available": transcript_count, "bookmarks": outline_count, "searchable": True, "bytes": path.stat().st_size}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", default=Path("output/pdf/playlist-transcripts.pdf"), type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8-sig"))
    entries = manifest.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("The manifest must contain a nonempty entries array.")
    if any(not isinstance(entry, dict) for entry in entries):
        raise ValueError("Every playlist entry must be an object.")
    transcripts = [read_transcript(args.manifest.parent, entry) for entry in entries]
    register_fonts()
    rl_config.warnOnMissingFontGlyphs = 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    document = ArchiveDocument(
        str(args.output),
        clean(manifest.get("title") or manifest.get("id") or "YouTube playlist"),
        pagesize=A4,
        leftMargin=21 * mm,
        rightMargin=21 * mm,
        topMargin=23 * mm,
        bottomMargin=23 * mm,
        pageCompression=1,
        allowSplitting=1,
    )
    document.multiBuild(build_story(manifest, transcripts, make_styles()), maxPasses=6)
    result = validate_pdf(args.output, entries, sum(transcript.available for transcript in transcripts))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"PDF build failed: {exc}", file=sys.stderr)
        raise
