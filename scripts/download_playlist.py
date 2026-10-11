"""Download a frozen playlist inventory to titled, tagged 96 kbps MP3 files."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import threading
import time

import yt_dlp


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(path)


def filename(position, title, video_id):
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title or 'Unavailable video')
    safe = re.sub(r'\s+', ' ', safe).strip(' .')[:120].rstrip(' .')
    return f'{position:03d} - {safe} [{video_id}]'


class Logger:
    def __init__(self):
        self.errors = []

    def debug(self, message):
        pass

    def warning(self, message):
        if 'unavailable' in message.lower():
            self.errors.append(message)

    def error(self, message):
        self.errors.append(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', required=True)
    parser.add_argument('--destination', required=True)
    parser.add_argument('--node', required=True)
    parser.add_argument('--workers', type=int, default=2)
    parser.add_argument('--browser', choices=['chrome', 'edge', 'firefox'],
                        help='Use this browser\'s existing signed-in YouTube session; cookies stay in memory.')
    args = parser.parse_args()
    root = Path(args.destination).resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / 'manifest.json'
    inventory = json.loads(Path(args.inventory).read_text(encoding='utf-8'))
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest['id'] != inventory['id']:
            raise ValueError('Destination belongs to a different playlist')
    else:
        entries = []
        for position, entry in enumerate(inventory['entries'], 1):
            stem = filename(position, entry.get('title'), entry['id'])
            entries.append({
                'position': position, 'id': entry['id'], 'title': entry.get('title'),
                'duration': entry.get('duration'), 'url': f"https://www.youtube.com/watch?v={entry['id']}",
                'audio': f'audio/{stem}.mp3', 'metadata': f'metadata/{stem}.json',
                'transcript_json': f'transcripts/{stem}.json',
                'transcript_text': f'transcripts/{stem}.txt', 'download_status': 'pending',
            })
        manifest = {
            'id': inventory['id'], 'title': inventory['title'],
            'url': f"https://www.youtube.com/playlist?list={inventory['id']}",
            'channel': inventory.get('channel'), 'created_at': now(),
            'listed_entries': len(entries),
            'youtube_hidden_unavailable_count': sum(not entry.get('title') for entry in inventory['entries']),
            'audio_format': 'MP3, 96 kbps, source channel layout and sample rate',
            'filename_note': 'Windows-invalid characters replaced; full original titles retained in metadata and MP3 tags.',
            'download_complete': False, 'entries': entries,
        }
    manifest['download_complete'] = False
    write_json(manifest_path, manifest)
    write_json(root / 'playlist-inventory.json', inventory)
    lock = threading.Lock()

    def save(entry, **updates):
        with lock:
            entry.update(updates)
            manifest['updated_at'] = now()
            write_json(manifest_path, manifest)

    def process(entry):
        target = root / entry['audio']
        if entry['download_status'] == 'downloaded' and target.exists() and target.stat().st_size:
            print(f"RESUME {entry['position']:03d} {entry['id']}", flush=True)
            return
        logger = Logger()
        stem = target.stem
        options = {
            'format': 'bestaudio/best', 'noplaylist': True,
            'outtmpl': str(root / 'audio' / (stem + '.%(ext)s')),
            'windowsfilenames': True, 'overwrites': False,
            'quiet': True, 'no_warnings': False, 'logger': logger,
            'socket_timeout': 30, 'retries': 3, 'fragment_retries': 3,
            'concurrent_fragment_downloads': 2,
            'js_runtimes': {'node': {'path': args.node}},
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '96'}],
            'postprocessor_args': {'ExtractAudio+ffmpeg_o': ['-threads', '2']},
        }
        if args.browser:
            options['cookiesfrombrowser'] = (args.browser,)
        try:
            print(f"START {entry['position']:03d} {entry['id']}", flush=True)
            with yt_dlp.YoutubeDL(options) as downloader:
                info = downloader.extract_info(entry['url'], download=True)
            if not target.exists() or not target.stat().st_size:
                raise RuntimeError('Audio postprocessing did not produce the expected MP3')
            fields = ['id', 'title', 'description', 'webpage_url', 'original_url', 'uploader',
                      'uploader_id', 'channel', 'channel_id', 'channel_url', 'upload_date',
                      'timestamp', 'duration', 'tags', 'categories', 'license',
                      'age_limit', 'availability', 'language', 'view_count', 'like_count']
            metadata = {field: info.get(field) for field in fields}
            metadata.update(playlist_id=manifest['id'], playlist_title=manifest['title'],
                            playlist_position=entry['position'], archived_at=now())
            write_json(root / entry['metadata'], metadata)
            from mutagen.easyid3 import EasyID3
            from mutagen.id3 import ID3NoHeaderError
            try:
                tags = EasyID3(str(target))
            except ID3NoHeaderError:
                tags = EasyID3()
            tags['title'] = info['title']
            tags['album'] = manifest['title']
            tags['tracknumber'] = str(entry['position'])
            tags['artist'] = info.get('uploader') or manifest.get('channel') or 'Unknown'
            if info.get('upload_date'):
                tags['date'] = info['upload_date'][:4]
            tags.save(str(target))
            probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries',
                                    'format=duration:stream=codec_name', '-of', 'json', str(target)],
                                   check=True, capture_output=True, text=True)
            probe_data = json.loads(probe.stdout)
            if not any(s.get('codec_name') == 'mp3' for s in probe_data.get('streams', [])):
                raise RuntimeError('Audio validation found no MP3 stream')
            save(entry, title=info['title'], duration=info.get('duration'),
                 audio_duration=float(probe_data['format']['duration']),
                 audio_bytes=target.stat().st_size, download_status='downloaded',
                 error=None, downloaded_at=now())
            print(f"DONE {entry['position']:03d} {entry['id']} {target.stat().st_size} bytes", flush=True)
        except Exception as error:
            message = str(error)
            unavailable = any(word in message.lower() for word in
                              ['private video', 'video unavailable', 'removed', 'not available', 'deleted'])
            save(entry, download_status='unavailable' if unavailable else 'failed', error=message)
            print(f"FAILED {entry['position']:03d} {entry['id']}: {message}", flush=True)
        time.sleep(0.5)

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(process, entry) for entry in manifest['entries']]
        for future in as_completed(futures):
            future.result()
    manifest['download_complete'] = True
    manifest['download_finished_at'] = now()
    manifest['download_summary'] = {
        status: sum(entry['download_status'] == status for entry in manifest['entries'])
        for status in ['downloaded', 'unavailable', 'failed', 'pending']
    }
    write_json(manifest_path, manifest)
    tracks = ['#EXTM3U']
    for entry in manifest['entries']:
        if entry['download_status'] == 'downloaded':
            tracks.extend([f"#EXTINF:{entry.get('duration') or -1},{entry['title']}", entry['audio']])
    (root / 'playlist.m3u8').write_text('\n'.join(tracks) + '\n', encoding='utf-8')
    print(json.dumps(manifest['download_summary']), flush=True)


if __name__ == '__main__':
    main()
