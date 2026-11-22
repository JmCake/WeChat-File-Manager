"""Cleanup preview needs one final confirmation, not another round of checks."""
import os
import time

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox

from app.core import Store
from app.delete_service import DeleteItem, DeletePlan
from app.ui.delete_dialog import DeleteDialog
from app.ui.window import Window


def sample_plan(valid=2, invalid=1):
    items = []
    for i in range(valid):
        items.append(DeleteItem(
            f'D:/xwechat_files/wxid_demo/msg/file/{i}.docx',
            'D:/xwechat_files/wxid_demo', '测试微信账号',
            1024 * (i + 1), (1, 2, 3, 4, i + 1)))
    for i in range(invalid):
        items.append(DeleteItem(
            f'D:/xwechat_files/wxid_demo/msg/file/missing-{i}.docx',
            'D:/xwechat_files/wxid_demo', '测试微信账号',
            1024, (1, 2, 3, 4, i + 3), allowed=False,
            reason='文件已不存在或不在当前索引中。'))
    return DeletePlan('test-preview', time.time(), tuple(items))


def test_preview_auto_checks_all_eligible_items_and_never_checks_missing():
    app = QApplication.instance() or QApplication([])
    plan = sample_plan()
    dialog = DeleteDialog(plan)
    model = dialog.preview_model
    assert model.count == 2
    assert model.size == 3072
    assert dialog.confirm.isEnabled()
    assert model.data(model.index(0, 0), Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
    assert model.data(model.index(1, 0), Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
    assert model.data(model.index(2, 0), Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Unchecked
    assert not model.flags(model.index(2, 0)) & Qt.ItemFlag.ItemIsUserCheckable
    text = ' '.join(label.text() for label in dialog.findChildren(QLabel))
    assert '上一步已选择 3 项' in text
    assert '无需再次勾选' in text
    assert '跳过 1 项' in text

    dialog.deselect_all_button.click()
    assert model.count == 0 and not dialog.confirm.isEnabled()
    assert dialog.excluded_paths() == frozenset(item.path for item in plan.items[:2])
    dialog.select_all_button.click()
    assert model.count == 2 and dialog.confirm.isEnabled()
    assert not dialog.excluded_paths()
    assert not model.setData(model.index(2, 0), Qt.CheckState.Checked, Qt.ItemDataRole.CheckStateRole)
    assert model.count == 2
    assert '尚未执行' in dialog.paths.toPlainText()
    dialog.close()


def test_all_missing_skips_cleanup_preview_and_never_attempts_recycle(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'index.sqlite')
    window = Window(store, auto=False)
    plan = sample_plan(valid=0, invalid=15)
    window.delete_service._plans[plan.token] = plan
    monkeypatch.setattr(QMessageBox, 'exec', lambda self: 0)
    messages = []
    original_message = QMessageBox.setText
    def remember(self, text):
        messages.append(text)
        return original_message(self, text)
    monkeypatch.setattr(QMessageBox, 'setText', remember)

    # When no file is eligible, no 15-row checkbox preview should appear.
    monkeypatch.setattr('app.ui.window.DeleteDialog', lambda *args, **kwargs:
                        (_ for _ in ()).throw(AssertionError('Invalid preview should not open')))
    window.delete_prepared({'result': plan})
    assert any('15' in message and '没有执行任何删除' in message for message in messages)
    assert plan.token not in window.delete_service._plans
    assert not window.delete_service.history()
    assert '未执行删除' in window.progress_label.text()
    window.close()


def test_cancelled_preview_invalidates_plan_without_adding_delete_history(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'index.sqlite')
    window = Window(store, auto=False)
    plan = sample_plan(valid=2, invalid=0)
    window.delete_service._plans[plan.token] = plan
    monkeypatch.setattr(DeleteDialog, 'exec', lambda self: QDialog.DialogCode.Rejected)
    window.delete_prepared({'result': plan})
    assert plan.token not in window.delete_service._plans
    assert not window.delete_service.history()
    assert '没有删除' in window.progress_label.text()
    window.close()
