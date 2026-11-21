"""Read-only, recursive document discovery on a chosen local drive.

This is deliberately separate from the WeChat account index. A similar name
is a review candidate, never proof that two files have identical contents.
"""
import os
import re
import stat
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

from app.disk_usage import DiskScanCancelled, _is_link_or_reparse
from app.name_matches import VERSION_SUFFIX

OFFICE_TYPES = {
    '.doc': 'Word', '.docx': 'Word', '.docm': 'Word',
    '.xls': 'Excel', '.xlsx': 'Excel', '.xlsm': 'Excel',
    '.ppt': 'PPT', '.pptx': 'PPT', '.pptm': 'PPT',
}
PDF_TYPES = {'.pdf': 'PDF'}
EXCLUDED_FOLDERS = {'$recycle.bin', 'system volume information', '$windows.~bt'}
# Numeric/hex IDs and Office temporary files are not meaningful documents.
NOISE_NAME = re.compile(r'^(?:[0-9a-f]{24,}|[0-9a-f]{8}-[0-9a-f-]{27,}|[0-9_-]{8,})$', re.I)


def recognizable_name(name):
    stem = unicodedata.normalize('NFKC', Path(name).stem).strip()
    if not stem or stem.startswith(('~$', '._', '.')):
        return False
    reduced = ''.join(char for char in stem if char.isalnum())
    if len(reduced) < 2 or NOISE_NAME.fullmatch(reduced):
        return False
    return True


def group_key(name):
    # Match identical base names and explicit version/copy suffixes, while
    # avoiding the broad prefix rule that lumps unrelated reports together.
    stem = unicodedata.normalize('NFKC', Path(name).stem).casefold().strip()
    stem = re.sub(r'[\uff08\uff09]', lambda m: '(' if m.group() == '\uff08' else ')', stem)
    base = VERSION_SUFFIX.sub('', stem).strip()
    return ''.join(char for char in base if char.isalnum())


def scan_office_matches(root, include_pdf=False, cancelled=lambda: False,
                        progress=lambda _: None):
    """Walk all descendants without following symlinks, junctions or mounts.

    Return (groups, statistics). No file content is opened or hashed.
    """
    root = os.path.abspath(os.fspath(root))
    if not os.path.isdir(root):
        raise ValueError('磁盘不存在，无法开始扫描。')
    root_stat = os.lstat(root)
    if _is_link_or_reparse(root_stat):
        raise ValueError('不扫描链接或重解析点目录。')
    types = dict(OFFICE_TYPES)
    if include_pdf:
        types.update(PDF_TYPES)
    buckets = defaultdict(list)
    stack = [root]
    files = folders = errors = skipped_links = candidates = 0
    last_progress = 0.0
    while stack:
        if cancelled():
            raise DiskScanCancelled()
        current = stack.pop()
        try:
            iterator = os.scandir(current)
        except OSError:
            errors += 1
            continue
        with iterator:
            try:
                for entry in iterator:
                    if cancelled():
                        raise DiskScanCancelled()
                    try:
                        info = entry.stat(follow_symlinks=False)
                        if _is_link_or_reparse(info):
                            skipped_links += 1
                            continue
                        if stat.S_ISDIR(info.st_mode):
                            if entry.name.casefold() in EXCLUDED_FOLDERS:
                                continue
                            if os.name != 'nt' and info.st_dev != root_stat.st_dev:
                                skipped_links += 1
                                continue
                            stack.append(entry.path)
                            folders += 1
                        elif stat.S_ISREG(info.st_mode):
                            files += 1
                            extension = Path(entry.name).suffix.casefold()
                            if extension not in types or not recognizable_name(entry.name):
                                continue
                            if getattr(info, 'st_file_attributes', 0) & (0x2 | 0x4 | 0x4000):
                                continue
                            key = group_key(entry.name)
                            if len(key) < 2:
                                continue
                            # Use real file identity on Windows. DirEntry.stat()
                            # may return st_ino=0 and cannot protect cleanup.
                            file_info = (os.stat(entry.path, follow_symlinks=False)
                                         if os.name == 'nt' else info)
                            if _is_link_or_reparse(file_info):
                                skipped_links += 1
                                continue
                            path = os.path.normcase(os.path.abspath(entry.path))
                            row = {
                                'path': path, 'name': entry.name,
                                'size': max(0, file_info.st_size),
                                'category': '文档', 'file_type': types[extension],
                                'extension': extension,
                                'account': root, 'account_name': str(Path(entry.path).parent),
                                'source': 'disk',
                                'snapshot': (file_info.st_size, file_info.st_mtime_ns,
                                             file_info.st_ctime_ns, file_info.st_dev,
                                             file_info.st_ino),
                            }
                            buckets[key].append(row)
                            candidates += 1
                    except OSError:
                        errors += 1
                    now = time.monotonic()
                    if now - last_progress > .2:
                        progress(f'扫描 {folders:,} 个目录、{files:,} 个文件；办公文档 {candidates:,} 个')
                        last_progress = now
            except OSError:
                errors += 1

    groups = []
    for rows in buckets.values():
        if cancelled():
            raise DiskScanCancelled()
        if len(rows) > 1:
            groups.append(sorted(rows, key=lambda row: (row['name'], row['path'])))
    # WeChat results are shown first; within these prioritize larger groups.
    def score(rows):
        wechat = any('wechat' in row['path'].casefold() or
                     'weixin' in row['path'].casefold() or
                     'xwechat_files' in row['path'].casefold()
                     for row in rows)
        return (-int(wechat), -len(rows), -sum(row['size'] for row in rows),
                rows[0]['name'].casefold())
    groups.sort(key=score)
    return groups, {
        'folders': folders, 'files': files, 'office_files': candidates,
        'groups': len(groups), 'errors': errors, 'skipped_links': skipped_links,
        'root': root,
    }
