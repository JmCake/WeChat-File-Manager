import os
from pathlib import Path

import pytest

from app.disk_usage import DiskScanCancelled, scan_disk


def test_folder_totals_include_nested_folders_and_direct_files(tmp_path):
    first = tmp_path / '课程资料'
    nested = first / '文档'
    nested.mkdir(parents=True)
    second = tmp_path / '视频'
    second.mkdir()
    (tmp_path / 'root.txt').write_bytes(b'xyz')
    (first / 'notes.txt').write_bytes(b'12345')
    (nested / 'paper.pdf').write_bytes(b'1234567')
    (second / 'movie.mp4').write_bytes(b'0' * 11)

    report = scan_disk(tmp_path)
    root = report.nodes[report.root]
    assert report.file_count == 4
    assert report.folder_count == 3
    assert root.own_bytes == 3
    assert root.total_bytes == 26
    assert root.total_files == 4
    assert root.total_folders == 3
    assert report.nodes[str(first)].total_bytes == 12
    assert report.nodes[str(first)].total_folders == 1
    assert report.nodes[str(second)].total_bytes == 11
    assert report.errors == 0
    assert report.disk_total >= report.disk_used >= 0


def test_hardlinks_are_reported_without_understating_folder_size(tmp_path):
    first = tmp_path / 'A'
    second = tmp_path / 'B'
    first.mkdir()
    second.mkdir()
    original = first / 'original.dat'
    original.write_bytes(b'hard-link-test')
    try:
        os.link(original, second / 'copy.dat')
    except OSError as error:
        pytest.skip(f'文件系统不支持硬链接：{error}')
    report = scan_disk(tmp_path)
    assert report.nodes[report.root].total_bytes == 2 * len(b'hard-link-test')
    assert report.repeated_hardlinks == 1
    assert report.repeated_hardlink_bytes == len(b'hard-link-test')


def test_symlink_is_not_followed(tmp_path):
    (tmp_path / 'real').mkdir()
    (tmp_path / 'real' / 'data.txt').write_bytes(b'hello')
    try:
        (tmp_path / 'linked').symlink_to(tmp_path / 'real', target_is_directory=True)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f'无法建立符号链接：{error}')
    report = scan_disk(tmp_path)
    assert report.file_count == 1
    assert report.nodes[report.root].total_bytes == 5
    assert report.skipped_links == 1


def test_cancel_does_not_return_incomplete_result(tmp_path):
    (tmp_path / 'one.txt').write_text('data')
    with pytest.raises(DiskScanCancelled):
        scan_disk(tmp_path, cancelled=lambda: True)


def test_missing_drive_is_rejected(tmp_path):
    with pytest.raises(ValueError, match='不存在'):
        scan_disk(tmp_path / 'does-not-exist')


def test_inaccessible_subdirectory_is_explicitly_reported(tmp_path, monkeypatch):
    from app import disk_usage

    blocked = tmp_path / 'private'
    blocked.mkdir()
    (blocked / 'hidden.txt').write_bytes(b'secret')
    original_scandir = os.scandir

    def deny_one(path):
        if Path(path) == blocked:
            raise PermissionError(str(path))
        return original_scandir(path)

    with monkeypatch.context() as patch:
        patch.setattr(disk_usage.os, 'scandir', deny_one)
        report = scan_disk(tmp_path)

    assert report.errors == 1
    assert report.file_count == 0
    assert report.nodes[str(blocked)].errors == 1


def test_tree_lazily_loads_more_than_200_folders(tmp_path):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from app.ui.disk_usage import DiskUsagePage

    for i in range(205):
        (tmp_path / f'folder-{i:03d}').mkdir()
    report = scan_disk(tmp_path)
    app = QApplication.instance() or QApplication([])
    dialog = DiskUsagePage(autostart=False)
    dialog.render_report(report)
    root = dialog.tree.topLevelItem(0)
    dialog.expand_folder(root)
    assert root.childCount() == 201
    more = root.child(200)
    assert '加载更多' in more.text(0)
    dialog.activate_item(more)
    assert root.childCount() == 205
    dialog.close()

def test_disk_usage_is_a_primary_embedded_page(tmp_path, monkeypatch):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    import time

    from PySide6.QtWidgets import QApplication
    from app.core import Store
    from app.ui import disk_usage
    from app.ui.window import Window

    app = QApplication.instance() or QApplication([])
    source = tmp_path / 'wxid_demo' / 'msg' / 'file'
    source.mkdir(parents=True)
    (source / '微信附件.txt').write_bytes(b'wechat')
    store = Store(tmp_path / 'index.sqlite')
    store.add(source.parents[1])
    store.scan()
    drive = tmp_path / 'disk'
    folder = drive / '学习资料'
    folder.mkdir(parents=True)
    (folder / '笔记.txt').write_bytes(b'123456')
    monkeypatch.setattr(disk_usage, 'fixed_drives', lambda: [str(drive)])

    window = Window(store, auto=False)
    assert 'disk' in window.nav_buttons
    assert window.nav_buttons['disk'].text() == '磁盘空间'
    assert window.switch_page('disk')
    page = window.disk_usage_page
    assert page is not None
    assert window.pages.currentWidget() is page
    assert page.parentWidget() is window.pages
    assert window.nav_buttons['disk'].isChecked()
    assert not window.query.isVisibleTo(window)
    assert not window.scan_button.isVisibleTo(window)

    deadline = time.monotonic() + 8
    while (page.report is None or (page.worker and page.worker.isRunning())) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    app.processEvents()
    assert page.report is not None
    assert page.report.nodes[page.report.root].total_bytes == 6
    assert page.tree.topLevelItem(0) is not None
    assert store.search(query='微信附件')

    assert window.switch_page('search')
    assert window.pages.currentWidget() is not page
    assert window.switch_page('disk')
    assert window.disk_usage_page is page
    assert page.report is not None
    window.close()


def test_large_folders_rank_all_nested_directories(tmp_path):
    import os
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from app.ui.disk_usage import DiskUsagePage

    (tmp_path/'微信资料'/'2026').mkdir(parents=True)
    (tmp_path/'普通文档').mkdir()
    (tmp_path/'微信资料'/'2026'/'报告.docx').write_bytes(b'123456')
    (tmp_path/'普通文档'/'合同.docx').write_bytes(b'123')
    report = scan_disk(tmp_path)
    app = QApplication.instance() or QApplication([])
    page = DiskUsagePage(autostart=False, mode='largest')
    page.render_report(report)
    assert page.tree.topLevelItemCount() == report.folder_count
    assert page.tree.topLevelItem(0).text(0) == '微信资料'
    assert page.tree.topLevelItem(0).text(1)
    assert page.tree.topLevelItem(0).text(4) == str(tmp_path)
    assert {page.tree.topLevelItem(i).text(0)
            for i in range(page.tree.topLevelItemCount())} == {
                '微信资料', '2026', '普通文档'
            }
    page.close()
