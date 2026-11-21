"""Navigation regression tests: primary pages, shortcuts, and quick file views."""
import os

os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.core import Store
from app.main import Window


def demo_window(tmp_path):
    app = QApplication.instance() or QApplication([])
    root = tmp_path / 'wxid_example'
    folder = root / 'msg' / 'file'
    folder.mkdir(parents=True)
    doc = folder / '资料.txt'
    image = folder / '封面.png'
    doc.write_bytes(b'document-data' * 3)
    image.write_bytes(b'image')
    store = Store(tmp_path / 'index.sqlite')
    store.add(root)
    store.scan()
    indexed_path = store.search(query='资料')[0]['path']
    store.annotate(indexed_path, '查阅资料', '课业', True)
    assert len(store.search(favorite=True)) == 1
    window = Window(store, auto=False)
    window.show()
    app.processEvents()
    return app, window


def test_sidebar_has_direct_navigation_and_visible_categories(tmp_path):
    app, window = demo_window(tmp_path)
    keys = ['directory', 'disk', 'duplicates', 'search',
            'favorite', 'recent', 'large', 'large_folders', 'missing']
    assert list(window.nav_buttons) == keys
    assert all(window.nav_buttons[key].isVisibleTo(window) for key in keys)
    assert all(window.nav_buttons[key].isCheckable() for key in keys)
    assert window.nav_buttons['directory'].isChecked()
    assert window.sidebar_settings.isVisibleTo(window)
    assert len(window.shortcuts) == 5

    categories = []
    for i in range(window.tree.topLevelItemCount()):
        node = window.tree.topLevelItem(i)
        data = node.data(0, Qt.ItemDataRole.UserRole) or {}
        if data.get('category'):
            categories.append(node)
            assert not node.isHidden()
    assert any((node.data(0, Qt.ItemDataRole.UserRole) or {}).get('category') == '文档'
               for node in categories)
    assert window.raw_directory_root == window.tree.topLevelItem(1)
    assert not window.raw_directory_root.isHidden()
    window.close()


def test_quick_access_filters_are_independent_and_restore_directory(tmp_path):
    app, window = demo_window(tmp_path)
    doc_category = next(
        window.tree.topLevelItem(i)
        for i in range(window.tree.topLevelItemCount())
        if (window.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) or {}).get('category') == '文档'
    )
    window.tree.setCurrentItem(doc_category)
    app.processEvents()
    assert window.page == 'directory'
    assert window.model.total == 1
    assert window.model.rows[0]['name'] == '资料.txt'

    assert window.switch_page('favorite')
    assert window.nav_buttons['favorite'].isChecked()
    assert window.page_title.text() == '收藏文件'
    assert window.model.total == 1
    assert window.model.rows[0]['name'] == '资料.txt'
    assert not window.directory_panel.isVisibleTo(window)

    assert window.switch_page('recent')
    assert window.model.total == 2
    assert window.switch_page('large')
    assert window.sort_order == 'size'
    assert window.model.rows[0]['name'] == '资料.txt'
    assert window.switch_page('missing')
    assert window.model.total == 0
    assert '失效' in window.empty_label.text()

    assert window.switch_page('directory')
    assert window.nav_buttons['directory'].isChecked()
    assert window.tree.currentItem() is doc_category
    assert window.model.total == 1
    assert window.nav.get('category') == '文档'
    window.close()


def test_global_search_focus_and_page_switches(tmp_path):
    app, window = demo_window(tmp_path)
    window.focus_search()
    assert window.page == 'search'
    assert window.query.hasFocus()
    window.query.setText('资料')
    window.reload()
    assert window.model.total == 1

    assert window.switch_page('favorite')
    assert window.model.total == 1
    assert window.switch_page('search')
    assert window.query.text() == '资料'
    assert window.model.total == 1
    window.close()

def test_folder_double_click_does_not_open_rename_dialog(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    app, window = demo_window(tmp_path)
    item = window.raw_directory_root.child(0)
    monkeypatch.setattr(QInputDialog, 'getText', lambda *args, **kwargs:
                        (_ for _ in ()).throw(AssertionError('Unexpected rename dialog')))
    window.activate_directory_item(item, 0)
    assert window.tree.expandsOnDoubleClick()
    window.close()


def test_unsaved_edit_blocks_sidebar_navigation(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    app, window = demo_window(tmp_path)
    window.table.selectRow(0)
    app.processEvents()
    assert window.active is not None
    original = window.note.toPlainText()
    window.note.setPlainText('未保存的内容')
    assert window.is_dirty()
    monkeypatch.setattr(QMessageBox, 'warning',
                        lambda *args, **kwargs: QMessageBox.StandardButton.Cancel)
    assert not window.switch_page('favorite')
    assert window.page == 'directory'
    assert window.nav_buttons['directory'].isChecked()
    assert not window.nav_buttons['favorite'].isChecked()
    window.note.setPlainText(original)
    window.close()


def test_reload_clears_selection_and_reactivates_file_details(tmp_path):
    app, window = demo_window(tmp_path)
    assert window.switch_page('search')
    window.query.setText('资料')
    window.reload()
    window.table.selectRow(0)
    app.processEvents()
    assert window.active and window.active['name'] == '资料.txt'

    window.reload()
    assert window.active is None
    assert not window.table.selectionModel().hasSelection()

    window.table.selectRow(0)
    app.processEvents()
    assert window.active and window.active['name'] == '资料.txt'
    window.note.setPlainText('回归测试备注')
    assert window.save_note()
    assert window.store.search(query='回归测试备注')
    window.close()
