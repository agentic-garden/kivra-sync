"""Filesystem path helpers for keeping mailbox archives separate."""

from utils.helpers import clean_filename


def mailbox_folder_name(value):
    """Return one safe Windows path component while preserving spaces."""
    name = clean_filename(str(value or ''), replace='').strip(' .')[:100].strip(' .')
    reserved = ({'CON', 'PRN', 'AUX', 'NUL'} |
                {f'COM{i}' for i in range(1, 10)} |
                {f'LPT{i}' for i in range(1, 10)})
    if not name or name.upper() in reserved:
        raise ValueError('A valid mailbox/company name is required')
    return name
