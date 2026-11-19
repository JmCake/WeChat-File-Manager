"""Read-only folder-size analysis for a local disk.

The reported folder sizes are logical file lengths. They may not equal physical
disk usage because of hard links, compression, sparse files or inaccessible data.
"""
import os
import shutil
import stat
import time
from dataclasses import dataclass, field


class DiskScanCancelled(Exception):
    """Raised when the user stops a disk scan."""


@dataclass
class FolderUsage:
    path: str
    name: str
    own_bytes: int = 0
    own_files: int = 0
    total_bytes: int = 0
    total_files: int = 0
    total_folders: int = 0
    errors: int = 0
    children: list[str] = field(default_factory=list)


@dataclass
class DiskUsageReport:
    root: str
    nodes: dict[str, FolderUsage]
    disk_total: int
    disk_used: int
    disk_free: int
    file_count: int
    folder_count: int
    errors: int
    skipped_links: int
    repeated_hardlinks: int
    repeated_hardlink_bytes: int


def _is_link_or_reparse(info):
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, 'st_file_attributes', 0) & 0x400
    )


def fixed_drives():
    """Return readable local drive roots, preferring D: on Windows."""
    if os.name != 'nt':
        return [os.path.abspath(os.sep)]
    import ctypes
    kernel = ctypes.windll.kernel32
    kernel.GetLogicalDrives.restype = ctypes.c_ulong
    kernel.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    kernel.GetDriveTypeW.restype = ctypes.c_uint
    mask = kernel.GetLogicalDrives()
    drives = [
        f'{chr(65 + letter)}:\\' for letter in range(26)
        if mask & (1 << letter) and kernel.GetDriveTypeW(
            f'{chr(65 + letter)}:\\') == 3
    ]
    return sorted(drives, key=lambda path: (path.upper() != 'D:\\', path))


def scan_disk(root, progress=lambda _: None, cancelled=lambda: False):
    """Scan a disk without following junctions, symlinks or mount points.

    Each directory is indexed once. Folder totals are computed in a separate
    reverse pass, so a deep directory tree cannot exhaust Python's call stack.
    """
    root = os.path.abspath(os.fspath(root))
    if not os.path.isdir(root):
        raise ValueError('所选磁盘或目录不存在。')
    root_stat = os.lstat(root)
    if _is_link_or_reparse(root_stat):
        raise ValueError('不能扫描符号链接或目录联接点。')
    usage = shutil.disk_usage(root)
    nodes = {root: FolderUsage(root, root)}
    visited = [root]
    pending = [root]
    files = 0
    folders = 0
    errors = 0
    skipped_links = 0
    repeated_hardlinks = 0
    repeated_hardlink_bytes = 0
    seen_hardlinks = set()
    last_progress = 0.0

    while pending:
        if cancelled():
            raise DiskScanCancelled()
        current = pending.pop()
        node = nodes[current]
        try:
            iterator = os.scandir(current)
        except OSError:
            if current == root:
                raise
            node.errors += 1
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
                            # Do not cross to another filesystem when scanning
                            # a single drive; Windows junctions are excluded above.
                            # Windows may report a different st_dev for
                            # directory entries than for the drive root.
                            # Volume mount points are reparse points and have
                            # already been skipped above.
                            if os.name != 'nt' and info.st_dev != root_stat.st_dev:
                                skipped_links += 1
                                continue
                            child_path = entry.path
                            nodes[child_path] = FolderUsage(child_path, entry.name)
                            node.children.append(child_path)
                            visited.append(child_path)
                            pending.append(child_path)
                            folders += 1
                        elif stat.S_ISREG(info.st_mode):
                            size = max(0, info.st_size)
                            node.own_bytes += size
                            node.own_files += 1
                            files += 1
                            # On Windows DirEntry.stat() always reports
                            # zero for st_ino/st_dev/st_nlink; a separate
                            # os.stat() call is needed for real file identity.
                            link_info = (os.stat(entry.path, follow_symlinks=False)
                                         if os.name == 'nt' else info)
                            if link_info.st_nlink > 1 and link_info.st_ino:
                                identity = (link_info.st_dev, link_info.st_ino)
                                if identity in seen_hardlinks:
                                    repeated_hardlinks += 1
                                    repeated_hardlink_bytes += size
                                else:
                                    seen_hardlinks.add(identity)
                    except OSError:
                        node.errors += 1
                        errors += 1
                    now = time.monotonic()
                    if now - last_progress >= .2:
                        progress(f'扫描中：{folders:,} 个文件夹，{files:,} 个文件')
                        last_progress = now
            except OSError:
                node.errors += 1
                errors += 1

    for path in reversed(visited):
        node = nodes[path]
        node.total_bytes += node.own_bytes
        node.total_files += node.own_files
        for child_path in node.children:
            child = nodes[child_path]
            node.total_bytes += child.total_bytes
            node.total_files += child.total_files
            node.total_folders += child.total_folders + 1
            node.errors += child.errors

    progress(f'完成：{folders:,} 个文件夹，{files:,} 个文件')
    return DiskUsageReport(
        root=root, nodes=nodes, disk_total=usage.total,
        disk_used=usage.used, disk_free=usage.free,
        file_count=files, folder_count=folders, errors=errors,
        skipped_links=skipped_links, repeated_hardlinks=repeated_hardlinks,
        repeated_hardlink_bytes=repeated_hardlink_bytes,
    )
