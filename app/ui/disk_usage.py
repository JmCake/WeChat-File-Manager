"""Drive-wide folder usage view; never moves, edits or deletes files."""
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, QUrl, Signal, QSignalBlocker
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QComboBox, QWidget, QHBoxLayout, QLabel, QMessageBox, QProgressBar,
    QPushButton, QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator, QVBoxLayout,
)

from app.disk_usage import DiskScanCancelled, fixed_drives, scan_disk
from app.paths import human


PATH_ROLE = Qt.ItemDataRole.UserRole
LOADED_ROLE = Qt.ItemDataRole.UserRole + 1
PAGE_SIZE = 200


class DiskScanWorker(QThread):
    progress = Signal(str)
    done = Signal(object)

    def __init__(self, root, parent=None):
        super().__init__(parent)
        self.root = root

    def run(self):
        try:
            report = scan_disk(
                self.root, progress=self.progress.emit,
                cancelled=self.isInterruptionRequested,
            )
            self.done.emit({'report': report})
        except DiskScanCancelled:
            self.done.emit({'cancelled': True})
        except Exception as error:
            self.done.emit({'error': str(error)})


class DiskUsagePage(QWidget):
    def __init__(self, parent=None, autostart=True, mode='tree'):
        super().__init__(parent)
        if mode not in {'tree','largest'}:
            raise ValueError('未知的磁盘查看方式')
        self.mode = mode
        self.worker = None
        self.report = None
        self.checked_paths = set()

        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        layout.addLayout(controls)
        self.drives = QComboBox()
        self.drives.setMinimumWidth(100)
        self.drives.addItems(fixed_drives())
        controls.addWidget(self.drives)
        self.scan_button = QPushButton('扫描磁盘')
        self.scan_button.setProperty('kind', 'primary')
        self.scan_button.clicked.connect(self.start_scan)
        controls.addWidget(self.scan_button)
        self.cancel_button = QPushButton('停止扫描')
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.stop_scan)
        controls.addWidget(self.cancel_button)
        controls.addStretch()
        self.open_button = QPushButton('打开选中文件夹')
        self.open_button.clicked.connect(self.open_folder)
        controls.addWidget(self.open_button)

        self.space = QLabel('请选择磁盘进行分析')
        self.space.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.space)
        self.status = QLabel('')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.hide()
        layout.addWidget(self.busy)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['文件夹', '逻辑大小',
                                   '占扫描总量' if mode == 'largest' else '占当前目录',
                                   '文件数', '所在位置' if mode == 'largest' else ''])
        self.tree.setColumnHidden(4, mode != 'largest')
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.setColumnWidth(0, 470)
        self.tree.setColumnWidth(1, 130)
        self.tree.setColumnWidth(2, 160)
        if mode == 'largest':
            self.tree.setColumnWidth(0, 240)
            self.tree.setColumnWidth(4, 310)
        self.tree.itemChanged.connect(self.check_changed)
        self.tree.itemExpanded.connect(self.expand_folder)
        self.tree.itemDoubleClicked.connect(self.activate_item)
        self.tree.itemActivated.connect(self.activate_item)
        layout.addWidget(self.tree, 1)
        selection = QHBoxLayout()
        layout.addLayout(selection)
        self.select_marked_button = QPushButton('勾选高亮文件夹')
        self.select_marked_button.clicked.connect(self.select_marked)
        selection.addWidget(self.select_marked_button)
        self.select_loaded_button = QPushButton('全选已加载文件夹')
        self.select_loaded_button.clicked.connect(self.select_loaded)
        selection.addWidget(self.select_loaded_button)
        self.clear_checked_button = QPushButton('清空勾选')
        self.clear_checked_button.clicked.connect(self.clear_checked)
        selection.addWidget(self.clear_checked_button)
        self.selection_count = QLabel('已勾选 0 个文件夹')
        selection.addWidget(self.selection_count, 1)
        self.copy_selected_button = QPushButton('复制勾选路径')
        self.copy_selected_button.clicked.connect(self.copy_checked_paths)
        selection.addWidget(self.copy_selected_button)
        self.update_selection()

        self.note = QLabel(
            '文件夹按逻辑大小统计；硬链接、压缩文件、稀疏文件及无权限目录会影响与磁盘实际占用的对应关系。'
        )
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        if autostart and self.drives.count():
            QTimer.singleShot(0, self.start_scan)

    def start_scan(self):
        if self.worker and self.worker.isRunning():
            return
        if not self.drives.count():
            self.status.setText('未找到可分析的本地磁盘。')
            return
        root = self.drives.currentText()
        self.report = None
        self.clear_checked()
        self.tree.clear()
        self.space.setText('')
        self.status.setText('准备扫描 ' + root)
        self.drives.setEnabled(False)
        self.scan_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.busy.show()
        self.worker = DiskScanWorker(root, self)
        self.worker.progress.connect(self.status.setText)
        self.worker.done.connect(self.scan_done)
        self.worker.finished.connect(self.scan_finished)
        self.worker.start()

    def stop_scan(self):
        if self.worker and self.worker.isRunning():
            self.worker.requestInterruption()
            self.cancel_button.setEnabled(False)
            self.status.setText('正在停止扫描…')

    def scan_done(self, result):
        if 'report' in result:
            self.render_report(result['report'])
        elif result.get('cancelled'):
            self.status.setText('已取消扫描，未修改任何文件。')
        else:
            self.status.setText('扫描失败：' + result.get('error', '未知错误'))

    def scan_finished(self):
        self.drives.setEnabled(True)
        self.scan_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.busy.hide()

    def render_report(self, report):
        self.report = report
        self.clear_checked()
        self.tree.clear()
        self.space.setText(
            f'磁盘总容量 {human(report.disk_total)}  ·  '
            f'已使用 {human(report.disk_used)}  ·  '
            f'可用 {human(report.disk_free)}'
        )
        self.status.setText(
            f'扫描到 {report.folder_count:,} 个文件夹、{report.file_count:,} 个文件  ·  '
            f'文件逻辑大小 {human(report.nodes[report.root].total_bytes)}'
            + (f'  ·  读取异常 {report.errors:,} 项' if report.errors else '')
            + (f'  ·  跳过链接 {report.skipped_links:,} 项' if report.skipped_links else '')
            + (f'  ·  重复硬链接 {report.repeated_hardlinks:,} 项' if report.repeated_hardlinks else '')
        )
        root = report.nodes[report.root]
        if self.mode == 'largest':
            self._append_largest(0)
        else:
            item = self._make_item(self.tree, root.path, root.name, root.total_bytes,
                                   root.total_bytes, root.total_files, True)
            item.setExpanded(True)

    def _make_item(self, parent, path, name, size, parent_size, files, expandable):
        item = QTreeWidgetItem(parent, [name, human(size), '', f'{files:,}'])
        item.setData(0, PATH_ROLE, path)
        item.setToolTip(0, path or name)
        if path is not None and path != self.report.root:
            item.setCheckState(0, Qt.CheckState.Unchecked)
        if expandable:
            item.setChildIndicatorPolicy(
                QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator
            )
        percentage = 100.0 * size / parent_size if parent_size else 0.0
        bar = QProgressBar()
        bar.setRange(0, 1000)
        bar.setValue(round(percentage * 10))
        bar.setFormat(f'{percentage:.1f}%')
        self.tree.setItemWidget(item, 2, bar)
        return item

    def _append_largest(self, start):
        if not self.report:
            return
        if not hasattr(self, '_ranked_paths') or start == 0:
            self._ranked_paths = sorted(
                (path for path in self.report.nodes if path != self.report.root),
                key=lambda path: (-self.report.nodes[path].total_bytes, path.casefold()),
            )
        root_size = self.report.nodes[self.report.root].total_bytes
        for path in self._ranked_paths[start:start + PAGE_SIZE]:
            node = self.report.nodes[path]
            item = self._make_item(self.tree, node.path, node.name,
                                   node.total_bytes, root_size, node.total_files, False)
            item.setText(4, str(Path(path).parent))
        count = len(self._ranked_paths) - start - PAGE_SIZE
        if count > 0:
            more = QTreeWidgetItem(self.tree, [f'双击加载更多（剩余 {count:,} 个文件夹）'])
            more.setData(0, PATH_ROLE, ('rank_more', start + PAGE_SIZE))

    def expand_folder(self, item):
        if not self.report or item.data(0, LOADED_ROLE):
            return
        path = item.data(0, PATH_ROLE)
        if path in self.report.nodes:
            item.setData(0, LOADED_ROLE, True)
            self._append_children(item, path, 0)

    def _append_children(self, parent, path, start):
        node = self.report.nodes[path]
        entries = [
            (self.report.nodes[child_path].total_bytes, child_path)
            for child_path in node.children
        ]
        if node.own_files:
            entries.append((node.own_bytes, None))
        entries.sort(key=lambda entry: (-entry[0], entry[1] or ''))
        for size, child_path in entries[start:start + PAGE_SIZE]:
            if child_path is None:
                self._make_item(parent, None, '（当前目录文件）',
                                size, node.total_bytes, node.own_files, False)
            else:
                child = self.report.nodes[child_path]
                self._make_item(
                    parent, child.path, child.name, child.total_bytes,
                    node.total_bytes, child.total_files,
                    bool(child.children or child.own_files),
                )
        remaining = len(entries) - start - PAGE_SIZE
        if remaining > 0:
            more = QTreeWidgetItem(parent, [f'双击加载更多（剩余 {remaining:,} 项）'])
            more.setData(0, PATH_ROLE, ('more', path, start + PAGE_SIZE))

    def activate_item(self, item, column=0):
        data = item.data(0, PATH_ROLE)
        if isinstance(data, tuple) and len(data) == 2 and data[0] == 'rank_more':
            index = self.tree.indexOfTopLevelItem(item)
            self.tree.takeTopLevelItem(index)
            self._append_largest(data[1])
            return
        if isinstance(data, tuple) and len(data) == 3 and data[0] == 'more':
            parent = item.parent()
            if parent is not None:
                parent.removeChild(item)
                self._append_children(parent, data[1], data[2])

    def check_changed(self, item, column):
        if column != 0:
            return
        path = item.data(0, PATH_ROLE)
        if not isinstance(path, str) or not self.report or path == self.report.root:
            return
        if item.checkState(0) == Qt.CheckState.Checked:
            self.checked_paths.add(path)
        else:
            self.checked_paths.discard(path)
        self.update_selection()

    def _mark_paths(self, paths):
        paths = set(paths)
        with QSignalBlocker(self.tree):
            for path in paths:
                if self.report and path in self.report.nodes and path != self.report.root:
                    self.checked_paths.add(path)
            it = QTreeWidgetItemIterator(self.tree)
            while it.value():
                item = it.value()
                path = item.data(0, PATH_ROLE)
                if isinstance(path, str) and path in paths and path != self.report.root:
                    item.setCheckState(0, Qt.CheckState.Checked)
                it += 1
        self.update_selection()

    def select_marked(self):
        self._mark_paths(item.data(0, PATH_ROLE)
                         for item in self.tree.selectedItems())

    def select_loaded(self):
        paths = []
        it = QTreeWidgetItemIterator(self.tree)
        while it.value():
            path = it.value().data(0, PATH_ROLE)
            if isinstance(path, str) and self.report and path != self.report.root:
                paths.append(path)
            it += 1
        self._mark_paths(paths)

    def clear_checked(self):
        self.checked_paths.clear()
        with QSignalBlocker(self.tree):
            it = QTreeWidgetItemIterator(self.tree)
            while it.value():
                item = it.value()
                if isinstance(item.data(0, PATH_ROLE), str) and item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                    item.setCheckState(0, Qt.CheckState.Unchecked)
                it += 1
        self.update_selection()

    def update_selection(self):
        count = len(self.checked_paths)
        self.selection_count.setText(f'已勾选 {count:,} 个文件夹')
        self.copy_selected_button.setEnabled(bool(count))
        self.clear_checked_button.setEnabled(bool(count))

    def copy_checked_paths(self):
        if self.checked_paths:
            QApplication.clipboard().setText('\n'.join(sorted(self.checked_paths)))
            self.status.setText(f'已复制 {len(self.checked_paths):,} 个文件夹路径（未删除任何数据）')

    def open_folder(self):
        item = self.tree.currentItem()
        if item is None:
            return
        path = item.data(0, PATH_ROLE)
        if not isinstance(path, str) or not Path(path).is_dir():
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            QMessageBox.warning(self, '打开失败', '无法打开所选文件夹。')

