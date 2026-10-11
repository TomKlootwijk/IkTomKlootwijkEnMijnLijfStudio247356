"""Recover exact video-ID matches from a public Internet Archive channel mirror."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import subprocess
import threading
import time

from mutagen.easyid3 import EasyID3
import requests

from download_playlist import filename, now, write_json


def digest(path, algorithm):
    result = hashlib.new(algorithm)
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def download(url, path, size=None, sha1=None):
    if path.exists() and (size is None or path.stat().st_size == size) and (sha1 is None or digest(path, 'sha1') == sha1):
        return
    error = None
    for attempt in range(3):
        partial = path.with_suffix(path.suffix + '.part')
        try:
            with requests.get(url, stream=True, timeout=(20, 90)) as response:
                response.raise_for_status()
                with partial.open('wb') as stream:
                    for block in response.iter_content(1024 * 1024):
                        stream.write(block)
            if size is not None and partial.stat().st_size != size:
                raise ValueError('Mirror file size does not match its official metadata')
            if sha1 is not None and digest(partial, 'sha1') != sha1:
                raise ValueError('Mirror SHA-1 does not match its official metadata')
            partial.replace(path)
            return
        except Exception as caught:
            error = caught
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f'Failed to retrieve {path.name}: {error}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--matches', required=True, type=Path)
    parser.add_argument('--cache', default='tmp/mirror-recovery', type=Path)
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    matches = json.loads(args.matches.read_text(encoding='utf-8'))
    manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
    if not manifest.get('download_complete'):
        raise RuntimeError('Wait for the main downloader to finish before recovering mirror files')
    root = args.manifest.resolve().parent
    manifest['download_complete'] = False
    manifest['mirror_recovery_started_at'] = now()
    write_json(args.manifest, manifest)
    by_id = {entry['id']: entry for entry in manifest['entries']}
    lock = threading.Lock()
    failures = []

    def recover(match):
        entry = by_id[match['id']]
        if entry['download_status'] == 'downloaded':
            return
        print(f"RECOVER {entry['position']:03d} {entry['id']}", flush=True)
        source = args.cache / (entry['id'] + '.mp3')
        info_path = args.cache / (entry['id'] + '.info.json')
        if match.get('info_url'):
            download(match['info_url'], info_path)
            info = json.loads(info_path.read_text(encoding='utf-8'))
        else:
            info = match['original_metadata']
        if info.get('id') != entry['id']:
            raise ValueError('Original mirror metadata does not identify this exact video')
        download(match['media_url'], source, match['size'], match.get('media_sha1'))
        # Preserve current YouTube title where known; restore missing original titles from saved metadata.
        title = entry['title'] or info['title']
        stem = filename(entry['position'], title, entry['id'])
        target = root / 'audio' / (stem + '.mp3')
        temporary = args.cache / (entry['id'] + '.converted.mp3')
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source),
                        '-vn', '-codec:a', 'libmp3lame', '-b:a', '96k', '-threads', '2', str(temporary)], check=True)
        tags = EasyID3(str(temporary))
        tags['title'] = title or f"Title unavailable [{entry['id']}]"
        tags['album'] = manifest['title']
        tags['artist'] = info.get('uploader') or manifest.get('channel') or 'Unknown'
        tags['tracknumber'] = str(entry['position'])
        if info.get('upload_date'):
            tags['date'] = info['upload_date'][:4]
        tags.save(str(temporary))
        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration:stream=codec_name',
                                '-of', 'json', str(temporary)], check=True, capture_output=True, text=True)
        data = json.loads(probe.stdout)
        actual_duration = float(data['format']['duration'])
        if not any(stream.get('codec_name') == 'mp3' for stream in data['streams']):
            raise ValueError('Recovered file has no MP3 audio stream')
        expected_duration = entry.get('duration') or info.get('duration')
        if expected_duration and abs(actual_duration - expected_duration) > 3:
            raise ValueError(f'Mirror duration differs from original: {actual_duration} vs {expected_duration}')
        fields = ['id', 'title', 'description', 'webpage_url', 'uploader', 'uploader_id', 'channel',
                  'channel_id', 'channel_url', 'upload_date', 'timestamp', 'duration', 'tags',
                  'categories', 'license', 'age_limit', 'availability', 'language', 'view_count', 'like_count']
        metadata = {field: info.get(field) for field in fields}
        metadata['title'] = title
        metadata.update(playlist_id=manifest['id'], playlist_title=manifest['title'],
                        playlist_position=entry['position'], archived_at=now(),
                        archive_mirror={**match, 'archive_identifier': matches['archive_identifier'],
                                        'original_saved_title': info['title'],
                                        'sha1_verified': bool(match.get('media_sha1')),
                                        'download_sha256': digest(source, 'sha256')})
        write_json(root / 'metadata' / (stem + '.json'), metadata)
        temporary.replace(target)
        with lock:
            entry.update(title=title,
                         title_source='YouTube' if entry['title'] else ('Original metadata in Internet Archive' if title else 'Title not recoverable'),
                         duration=info.get('duration') or actual_duration, audio_duration=actual_duration,
                         audio=f'audio/{stem}.mp3', metadata=f'metadata/{stem}.json',
                         transcript_json=f'transcripts/{stem}.json', transcript_text=f'transcripts/{stem}.txt',
                         audio_bytes=target.stat().st_size, download_source=matches['archive_identifier'],
                         mirror_url=match['media_url'], previous_download_status=entry['download_status'],
                         youtube_error=entry.get('error'), error=None, download_status='downloaded', downloaded_at=now())
            write_json(args.manifest, manifest)
        print(f"RECOVERED {entry['position']:03d} {entry['id']} {actual_duration:.2f}s", flush=True)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {executor.submit(recover, match): match for match in matches['matches']}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as error:
                video_id = futures[future]['id']
                failures.append({'id': video_id, 'error': str(error)})
                print(f'RECOVERY FAILED {video_id}: {error}', flush=True)
    manifest['download_complete'] = True
    manifest['mirror_recovery_finished_at'] = now()
    manifest['mirror_recovery_failures'] = failures
    manifest['download_summary'] = {state: sum(entry['download_status'] == state for entry in manifest['entries'])
                                    for state in ['downloaded', 'unavailable', 'failed', 'pending']}
    manifest['all_listed_audio_archived'] = manifest['download_summary']['downloaded'] == len(manifest['entries'])
    write_json(args.manifest, manifest)
    tracks = ['#EXTM3U']
    for entry in manifest['entries']:
        if entry['download_status'] == 'downloaded':
            display_title = entry['title'] or f"Title unavailable [{entry['id']}]"
            tracks.extend([f"#EXTINF:{entry.get('duration') or -1},{display_title}", entry['audio']])
    (root / 'playlist.m3u8').write_text('\n'.join(tracks) + '\n', encoding='utf-8')
    source_log_path = root / 'mirror-sources.json'
    source_log = json.loads(source_log_path.read_text(encoding='utf-8')) if source_log_path.exists() else {'sources': []}
    if 'matches' in source_log:
        source_log = {'sources': [source_log]}
    source_log['sources'].append(matches)
    write_json(source_log_path, source_log)
    print(json.dumps(manifest['download_summary']), flush=True)
    return bool(failures)


if __name__ == '__main__':
    raise SystemExit(main())
