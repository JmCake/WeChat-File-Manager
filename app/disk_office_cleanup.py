"""Explicit, guarded recycle-bin cleanup for *selected* drive-wide Office files.

Filename resemblance is not content equality. Every selected document is
reviewed separately and each group must retain a nonselected file.
"""
import os
import stat
import time
import uuid
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path

from app.delete_service import DeleteItem, DeletePlan
from app.disk_office_scan import OFFICE_TYPES, PDF_TYPES, group_key
from app.directory_mapper import PROTECTED_NAMES
from app.paths import canonical
from app.windows_recycle import WindowsRecycle


DISALLOWED_ANCESTORS = PROTECTED_NAMES | {
    '$recycle.bin', 'system volume information', 'windows',
    'program files', 'program files (x86)', 'programdata',
    '.git', '.venv', 'node_modules',
}


class DiskOfficeCleanup:
    def __init__(self, store, backend=None, settle_seconds=1.):
        self.store = store
        self.backend = backend if backend is not None else WindowsRecycle()
        self.settle_seconds = settle_seconds
        self._plans = {}

    def _validate(self, item):
        path = Path(canonical(item.path))
        root = Path(canonical(item.account))
        if path == root or not path.is_relative_to(root) or not root.is_dir():
            raise ValueError('文件不属于确认的本地磁盘目录。')
        if path.suffix.casefold() not in OFFICE_TYPES | PDF_TYPES:
            raise ValueError('只允许已识别的 Word、Excel、PPT 或 PDF 文件。')
        relative = path.relative_to(root).parts
        if any(part.casefold() in DISALLOWED_ANCESTORS for part in relative[:-1]):
            raise ValueError('内部数据或系统目录不允许清理。')
        # Validate all ancestors, not only the displayed leaf path.
        for part in (path, *path.parents):
            info = os.lstat(part)
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('目录联接点、重解析点和符号链接不允许清理。')
        info = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('只允许普通文件。')
        if getattr(info, 'st_file_attributes', 0) & (0x2 | 0x4 | 0x4000):
            raise ValueError('隐藏、系统或加密文件不允许清理。')
        present = (info.st_size, info.st_mtime_ns, info.st_ctime_ns,
                   info.st_dev, info.st_ino)
        if not info.st_ino or present != item.snapshot:
            raise ValueError('文件大小、时间或身份与扫描时不同，请重新扫描。')
        if info.st_nlink > 1:
            raise ValueError('硬链接文件不参与清理。')
        if time.time() - max(info.st_mtime, info.st_ctime) < self.settle_seconds:
            raise ValueError('文件最近有变化，请稍后重新扫描。')

    def prepare(self, rows, groups, cancelled=lambda: False, progress=lambda _: None):
        selected = {}
        for row in rows:
            path = canonical(row['path'])
            selected[path] = row
        if not selected:
            raise ValueError('请先逐项勾选需要清理的文件。')
        lookup = {}
        group_members = {}
        for group in groups:
            for row in group:
                path = canonical(row['path'])
                lookup[path] = row
                group_members[path] = group
        if any(path not in lookup for path in selected):
            raise ValueError('查找结果已失效，请重新扫描。')
        items = []
        for path, row in selected.items():
            if cancelled():
                raise ValueError('准备已取消，未执行清理。')
            member_rows = group_members[path]
            remaining = [other for other in member_rows
                         if canonical(other['path']) not in selected]
            keeper = remaining[0] if remaining else None
            snapshot = tuple(row['snapshot'])
            item = DeleteItem(path, canonical(row['account']),
                              str(Path(path).parent), row['size'], snapshot)
            try:
                if keeper is None:
                    raise ValueError('同名候选组必须至少保留一份；请取消勾选其中一个文件。')
                keep = DeleteItem(canonical(keeper['path']), canonical(keeper['account']),
                                  str(Path(keeper['path']).parent), keeper['size'],
                                  tuple(keeper['snapshot']))
                item = replace(item, keeper=keep)
                self._validate(item)
                self._validate(keep)
                self.backend.supports(path)
            except Exception as error:
                item = replace(item, allowed=False, reason=str(error))
            items.append(item)
            if len(items) % 25 == 0:
                progress(f'预检查 {len(items)} 个选中文档；尚未删除')
        plan = DeletePlan(uuid.uuid4().hex, time.time(), tuple(items))
        self._plans = {plan.token: plan}
        return plan

    def _log(self, plan, item, result, reason, log_id=None):
        with self.store.connect() as db:
            if log_id is not None:
                db.execute('UPDATE deletion_log SET time=?,result=?,reason=? WHERE id=?',
                           (time.time(), result, reason, log_id))
                return log_id
            cursor = db.execute(
                'INSERT INTO deletion_log(operation_id,time,path,size,account,sha256,result,reason) '
                'VALUES (?,?,?,?,?,?,?,?)',
                (plan.token, time.time(), item.path, item.size, item.account,
                 '', result, reason))
            return cursor.lastrowid

    def execute(self, plan, confirmed=False, original_ack=False,
                cancelled=lambda: False, progress=lambda _: None, excluded=()):
        if self._plans.pop(plan.token, None) is not plan:
            raise ValueError('清理计划无效或已使用。')
        if time.time() - plan.created > 300:
            raise ValueError('清理计划超过 5 分钟，请重新确认。')
        results = []
        excluded = set(excluded)
        for index, item in enumerate(plan.items, 1):
            status = 'skipped'
            reason = ''
            log_id = None
            attempted = False
            if item.path in excluded:
                reason = '用户取消勾选。'
            elif not confirmed or cancelled():
                status, reason = 'cancelled', '已取消。'
            elif not original_ack:
                reason = '未确认原始文件可能仍被应用程序引用。'
            elif not item.allowed:
                reason = item.reason
            else:
                try:
                    log_id = self._log(plan, item, 'pending', '执行中；中断需核对回收站。')
                    with self.store._index_lock:
                        self._validate(item)
                        self._validate(item.keeper)
                        self.backend.supports(item.path)
                        with ExitStack() as pins:
                            retain = getattr(self.backend, 'pin_keeper', None)
                            pins.enter_context(
                                retain(item.keeper.path, item.keeper.account)
                                if retain else self.backend.pin(item.keeper.path, item.keeper.account))
                            pins.enter_context(self.backend.pin(item.path, item.account))
                            self._validate(item)
                            self._validate(item.keeper)
                            if cancelled():
                                raise ValueError('用户取消。')
                            attempted = True
                            def guard():
                                self._validate(item)
                                self._validate(item.keeper)
                            self.backend.recycle(item.path, guard)
                        if os.path.lexists(item.path):
                            raise OSError('回收站操作后原路径仍存在，不能确认处理成功。')
                        # The source may also be indexed in the WeChat file list.
                        # Non-indexed documents are ignored by update_paths().
                        self.store.update_paths([item.path])
                        status, reason = 'success', '已确认移入系统回收站，不代表已释放磁盘空间。'
                except ValueError as error:
                    status, reason = 'skipped', str(error)
                except FileNotFoundError:
                    if attempted and not os.path.lexists(item.path):
                        status, reason = 'uncertain', '回收站结果不明确，请核对原路径和回收站。'
                    else:
                        status, reason = 'skipped', '文件已不存在。'
                except Exception as error:
                    status = 'failed' if os.path.lexists(item.path) else 'uncertain'
                    reason = str(error)
                    if status == 'uncertain':
                        reason += ' 原路径已不存在，请人工核对回收站。'
            try:
                self._log(plan, item, status, reason, log_id)
            except Exception as error:
                reason += f'；记录失败：{error}'
            results.append(dict(path=item.path, size=item.size, result=status,
                                reason=reason, excluded=item.path in excluded))
            if index % 20 == 0 or index == len(plan.items):
                progress(f'已处理 {index}/{len(plan.items)} 个选中文档')
        return {
            'items': results,
            'success': sum(row['result'] == 'success' for row in results),
            'failed': sum(row['result'] == 'failed' for row in results),
            'skipped': sum(row['result'] == 'skipped' for row in results),
            'cancelled': sum(row['result'] == 'cancelled' for row in results),
            'uncertain': sum(row['result'] == 'uncertain' for row in results),
            'recycled_bytes': sum(row['size'] for row in results
                                  if row['result'] == 'success'),
        }
