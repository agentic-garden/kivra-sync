"""Rebuild a company sender index from completed message manifests."""

import csv
import json
import re
import subprocess
from pathlib import Path


FIELDS = ['sender_name', 'sender_key', 'received_date', 'subject', 'content_key',
          'attachments', 'bodies', 'pdf_text_paths', 'extraction_status',
          'references', 'emails', 'phone_numbers']


def rebuild_index(sender_dir):
    root = Path(sender_dir).resolve()
    rows = []
    for path in sorted(root.glob('*/*/*.json')):
        data = json.loads(path.read_text(encoding='utf-8'))
        if not {'content_key', 'sender_key', 'parts'} <= data.keys():
            continue
        attachments, bodies, text_paths, statuses = [], [], [], []
        texts = []
        for part in data['parts']:
            relative = part['path']
            part_path = (root / relative).resolve()
            if not part_path.is_relative_to(root) or not part_path.is_file():
                raise RuntimeError(f'Manifest part is missing or outside sender archive: {path.name}')
            mime = part['content_type']
            if mime in ('text/plain', 'text/html'):
                bodies.append(relative)
                texts.append(part_path.read_text(encoding='utf-8', errors='replace'))
            else:
                attachments.append(relative)
            if mime == 'application/pdf':
                text_path = part_path.with_suffix(part_path.suffix + '.txt')
                try:
                    result = subprocess.run(['pdftotext', '-layout', str(part_path), str(text_path)],
                                            capture_output=True, check=False)
                except FileNotFoundError:
                    statuses.append('pdftotext unavailable')
                    continue
                if result.returncode:
                    statuses.append('PDF extraction failed')
                    continue
                extracted = text_path.read_text(encoding='utf-8', errors='replace')
                if extracted.strip():
                    text_paths.append(text_path.relative_to(root).as_posix())
                    texts.append(extracted)
                    statuses.append('text extracted')
                else:
                    statuses.append('no extractable PDF text; OCR needed')
        searchable = '\n'.join(texts)
        references = sorted(set(re.findall(r'(?i)\b(?:ärendenummer|diarienummer|referens(?:nummer)?|fakturanummer)\s*[:#]?\s*([A-Z0-9][A-Z0-9/-]{3,})', searchable)))
        emails = sorted(set(re.findall(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b', searchable)))
        phones = sorted(set(re.findall(r'(?<!\d)(?:\+46|0)[\d() -]{7,16}\d', searchable)))
        rows.append({'sender_name': data.get('sender_name'), 'sender_key': data['sender_key'],
                     'received_date': (data.get('received_at') or '')[:10],
                     'subject': data.get('subject'), 'content_key': data['content_key'],
                     'attachments': ';'.join(attachments), 'bodies': ';'.join(bodies),
                     'pdf_text_paths': ';'.join(text_paths),
                     'extraction_status': ';'.join(statuses) or 'no PDF',
                     'references': ';'.join(references), 'emails': ';'.join(emails),
                     'phone_numbers': ';'.join(phones)})
    rows.sort(key=lambda row: (row['received_date'], row['content_key']))
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'sender-index.csv').open('w', newline='', encoding='utf-8-sig') as output:
        writer = csv.DictWriter(output, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Rebuild a sender index from company archive manifests')
    parser.add_argument('sender_dir')
    args = parser.parse_args()
    print(f'Indexed {rebuild_index(args.sender_dir)} messages')
