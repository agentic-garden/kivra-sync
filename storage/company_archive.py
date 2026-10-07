"""Sender-scoped, lossless archive for company letters."""

import calendar
import hashlib
import json
import mimetypes
import re
from datetime import datetime
from pathlib import Path

from utils.helpers import clean_filename


def safe_name(value, fallback='unknown', limit=70):
    name = clean_filename(str(value or fallback)).strip(' ._')[:limit].strip(' ._')
    if not name or name.upper() in ({'CON', 'PRN', 'AUX', 'NUL'} |
                                    {f'COM{i}' for i in range(1, 10)} |
                                    {f'LPT{i}' for i in range(1, 10)}):
        return fallback
    return name


class CompanyArchive:
    def __init__(self, base_dir, sender_key, dry_run=False):
        self.base_dir = Path(base_dir).resolve()
        self.sender_key = sender_key
        self.dry_run = dry_run
        self.sender_dir = None

    def archive_letter(self, listing, api_client):
        sender = listing.get('sender') or {}
        if sender.get('key') != self.sender_key:
            raise RuntimeError('Letter sender does not match selected sender')
        received = listing.get('receivedAt')
        try:
            date = datetime.fromisoformat(received.replace('Z', '+00:00')).date()
        except (AttributeError, ValueError) as exc:
            raise RuntimeError('Letter has no valid receivedAt date') from exc
        sender_name = safe_name(sender.get('name'))
        root = (self.base_dir / sender_name).resolve()
        if root.parent != self.base_dir:
            raise RuntimeError('Invalid sender archive path')
        if self.sender_dir is not None and root != self.sender_dir:
            raise RuntimeError('Sender name changed within selected sender')
        self.sender_dir = root
        folder = root / str(date.year) / calendar.month_name[date.month]
        key = listing.get('key')
        if not key:
            raise RuntimeError('Letter has no content key')
        suffix = hashlib.sha256(str(key).encode()).hexdigest()[:12]
        base = f"{safe_name(listing.get('subject'), 'letter')}_{suffix}_{date.isoformat()}"
        manifest_path = folder / f'{base}.json'
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            if manifest.get('content_key') == key and all(
                (root / part['path']).is_file() for part in manifest.get('parts', [])
            ):
                return False
        detail = api_client.get_content_details(key)
        parts = detail.get('parts') or []
        if not parts:
            raise RuntimeError('Content detail contains no parts')
        saved = []
        if not self.dry_run:
            folder.mkdir(parents=True, exist_ok=True)
        for index, part in enumerate(parts):
            mime = (part.get('content_type') or 'application/octet-stream').split(';')[0]
            file_key = part.get('key')
            if file_key:
                content = api_client.get_content_file(key, file_key)
            elif mime in ('text/plain', 'text/html') and part.get('body') is not None:
                content = part['body'].encode('utf-8')
            else:
                raise RuntimeError(f'Part {index} has neither a file key nor a text body')
            original = part.get('filename') or part.get('file_name') or part.get('name')
            extension = Path(str(original)).suffix if original else ''
            if (not extension or len(extension) > 12 or
                    not re.fullmatch(r'\.[A-Za-z0-9]{1,11}', extension)):
                extension = {'application/pdf': '.pdf', 'text/plain': '.txt',
                             'text/html': '.html'}.get(mime) or mimetypes.guess_extension(mime) or '.bin'
            stem = safe_name(Path(str(original)).stem, 'part') if original else 'part'
            path = folder / f'{base}_part{index:02d}_{stem}{extension}'
            if not self.dry_run:
                path.write_bytes(content)
            saved.append({'index': index, 'key': file_key, 'content_type': mime,
                          'original_filename': original, 'path': path.relative_to(root).as_posix()})
        manifest = {'sender_name': sender.get('name'), 'sender_key': self.sender_key,
                    'content_key': key, 'received_at': received, 'subject': listing.get('subject'),
                    'listing': listing, 'detail': detail, 'parts': saved}
        if not self.dry_run:
            temporary = manifest_path.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(manifest_path)
        return True
