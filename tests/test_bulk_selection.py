"""Bulk selection stays explicit and non-destructive until final confirmation."""
import os
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.disk_usage import scan_disk
from app.ui.disk_usage import DiskUsagePage
from app.ui.duplicates import NameMatchesPage
from tests.test_sidebar_ui import demo_window


def app_instance():
    return QApplication.instance() or QApplication([])


def disk_candidate(i, member):
    name = f'学习总结-{i:03d}' + ('-副本.docx' if member else '.docx')
    return dict(
        path=f'D:/documents/group-{i:03d}/{name}',
        name=name, category='文档', file_type='Word', source='disk',
        account='D:/', account_name=f'D:/documents/group-{i:03d}',
        size=5 + member, snapshot=(5 + member, 100 + member, 80, 1, i * 2 + member + 1)
    )


def test_all_office_groups_can_be_bulk_selected_beyond_ui_pagination():
    app_instance()
    page = NameMatchesPage(lambda: None)
    groups = [[disk_candidate(i, 0), disk_candidate(i, 1)] for i in range(157)]
    page.set_results(groups, 'test disk', disk_mode=True)
    assert page.groups.topLevelItemCount() == 150
    group = page.groups.topLevelItem(0)
    assert group.flags() & Qt.ItemFlag.ItemIsUserCheckable

    page.select_all_button.click()
    assert len(page.checked) == 157
    for rows in groups:
        assert sum(row['path'] in page.checked for row in rows) == 1
        assert rows[1]['path'] not in page.checked  # newest candidate retained

    page.load_groups()
    assert page.groups.topLevelItemCount() == 157
    assert page.groups.topLevelItem(156).checkState(0) == Qt.CheckState.PartiallyChecked
    captured = []
    page.recycleRequested.connect(captured.append)
    page.recycle_button.click()
    assert captured and len(captured[0]) == 157
    page.clear_button.click()
    assert not page.checked
    assert not page.recycle_button.isEnabled()
    page.close()


def test_group_checkbox_bulk_selection_and_keeper_cannot_be_rechecked():
    app_instance()
    page = NameMatchesPage(lambda: None)
    members = [disk_candidate(3, 0), disk_candidate(3, 1)]
    page.set_results([members], 'disk', disk_mode=True)
    group = page.groups.topLevelItem(0)
    group.setCheckState(0, Qt.CheckState.Checked)
    assert len(page.checked) == 1
    assert group.checkState(0) == Qt.CheckState.PartiallyChecked
    group.setExpanded(True)
    other = next(group.child(i) for i in range(group.childCount())
                 if group.child(i).data(0, page.role)['path'] not in page.checked)
    other.setCheckState(0, Qt.CheckState.Checked)
    assert len(page.checked) == 1
    assert other.checkState(0) == Qt.CheckState.Unchecked
    group.setCheckState(0, Qt.CheckState.Unchecked)
    assert not page.checked
    page.close()


def test_similar_folder_bulk_selection_copies_paths_not_recycle():
    app = app_instance()
    page = NameMatchesPage(lambda: None)
    folders = [
        dict(path=f'D:/xwechat_files/资料-{i}', name=f'资料-{i}',
             category='文件夹', file_type='文件夹', source='folder',
             account='D:/', account_name='D:/xwechat_files', size=i + 1)
        for i in range(3)
    ]
    page.set_results([folders], 'folder', folder_mode=True)
    assert page.groups.topLevelItem(0).flags() & Qt.ItemFlag.ItemIsUserCheckable
    sent_to_recycle = []
    page.recycleRequested.connect(sent_to_recycle.append)
    page.select_all_button.click()
    assert len(page.checked) == 3
    assert '复制' in page.recycle_button.text()
    page.recycle_button.click()
    assert app.clipboard().text().splitlines() == sorted(row['path'] for row in folders)
    assert not sent_to_recycle
    page.clear_selection()
    assert not page.checked
    page.close()


def test_large_folders_and_disk_tree_allow_multi_check_and_copy(tmp_path):
    app = app_instance()
    (tmp_path/'课程'/'Word').mkdir(parents=True)
    (tmp_path/'视频').mkdir()
    (tmp_path/'课程'/'Word'/'笔记.docx').write_bytes(b'abc')
    report = scan_disk(tmp_path)
    for mode in ('largest','tree'):
        page = DiskUsagePage(autostart=False, mode=mode)
        page.render_report(report)
        if mode == 'tree':
            root = page.tree.topLevelItem(0)
            page.expand_folder(root)
            assert root.checkState(0) == Qt.CheckState.Unchecked
        page.select_loaded_button.click()
        assert page.checked_paths
        assert str(tmp_path) not in page.checked_paths
        page.copy_selected_button.click()
        assert app.clipboard().text().splitlines() == sorted(page.checked_paths)
        page.clear_checked_button.click()
        assert not page.checked_paths
        page.render_report(report)
        assert not page.checked_paths
        page.close()


def test_all_indexed_file_views_have_batch_select_buttons(tmp_path):
    app, window = demo_window(tmp_path)
    assert window.switch_page('large')
    window.edit_selection_button.setChecked(True)
    window.edit_file_selection(True)
    window.select_all_loaded_button.click()
    assert len(window.model.checked_rows()) == 2
    window.clear_checks_button.click()
    assert not window.model.checked_paths
    window.table.selectRow(0)
    window.select_highlighted_button.click()
    assert len(window.model.checked_rows()) == 1
    assert window.recycle_checked_button.isEnabled()
    window.edit_selection_button.setChecked(False)
    window.edit_file_selection(False)
    assert window.switch_page('favorite')
    window.edit_selection_button.setChecked(True)
    window.edit_file_selection(True)
    window.select_all_loaded_button.click()
    assert len(window.model.checked_rows()) == 1
    window.edit_selection_button.setChecked(False)
    window.edit_file_selection(False)
    window.close()
