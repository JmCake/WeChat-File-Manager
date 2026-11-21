import os
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.core import Store
from app.disk_office_cleanup import DiskOfficeCleanup
from app.main import Window
from app.ui.delete_dialog import DeleteDialog
from tests.test_deletion import FakeRecycle


def wait_for(app, predicate):
    until = time.monotonic() + 12
    while not predicate() and time.monotonic() < until:
        app.processEvents()
        time.sleep(.01)
    assert predicate()


def test_main_navigation_and_diskwide_office_cleanup(tmp_path, monkeypatch):
    from app.ui import duplicates, disk_usage

    drive = tmp_path / 'D'
    deep = drive / 'xwechat_files' / 'account' / 'msg' / 'file' / '2026-11'
    deep.mkdir(parents=True)
    first = deep / '实验报告.docx'
    second = deep / '实验报告-副本.docx'
    first.write_bytes(b'first version')
    second.write_bytes(b'second version')
    monkeypatch.setattr(duplicates, 'fixed_drives', lambda: [str(drive)])
    monkeypatch.setattr(disk_usage, 'fixed_drives', lambda: [str(drive)])

    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path/'index.sqlite')
    window = Window(store, auto=False)
    window.disk_cleanup_service = DiskOfficeCleanup(
        store, FakeRecycle(tmp_path/'mock-recycle'), settle_seconds=0)
    window.show()
    assert 'large_folders' in window.nav_buttons
    assert window.switch_page('large_folders')
    large = window.large_folders_page
    wait_for(app, lambda: large.report is not None and not large.worker.isRunning())
    assert large.tree.topLevelItem(0).text(1)

    assert window.switch_page('duplicates')
    page = window.duplicate_dialog
    assert page.current_source() == 'disk'
    assert page.drives.currentText() == str(drive)
    assert page.format_filter.currentData() is False
    page.detect_button.click()
    wait_for(app, lambda: page.matches and not window.worker.isRunning())
    assert len(page.matches) == 1
    group = page.groups.topLevelItem(0)
    assert not bool(group.flags() & Qt.ItemFlag.ItemIsUserCheckable)
    group.setExpanded(True)
    app.processEvents()
    assert group.childCount() == 2
    assert {group.child(i).text(1) for i in range(2)} == {'Word'}
    selected = group.child(0)
    selected.setCheckState(0, Qt.CheckState.Checked)
    assert len(page.checked) == 1
    assert '回收站' in page.recycle_button.text()

    def accept(dialog):
        assert dialog.disk_scope
        assert len(dialog.plan.items) == 1
        assert dialog.plan.items[0].allowed
        assert dialog.plan.items[0].keeper is not None
        dialog.confirm.click()
        return dialog.result()
    monkeypatch.setattr(DeleteDialog, 'exec', accept)
    results = []
    window.disk_cleanup_done = results.append
    page.recycle_button.click()
    wait_for(app, lambda: bool(results) and not window.worker.isRunning())
    assert results[0]['result']['success'] == 1
    assert sum(path.exists() for path in (first, second)) == 1
    window.close()
