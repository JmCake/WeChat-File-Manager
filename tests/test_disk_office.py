import os
import time
from pathlib import Path

import pytest

from app.disk_usage import DiskScanCancelled
from app.disk_office_scan import (
    scan_office_matches, scan_similar_folders, recognizable_name,
)
from app.disk_office_cleanup import DiskOfficeCleanup
from app.core import Store, canonical
from tests.test_deletion import FakeRecycle


def test_office_scan_all_subfolders_and_versioned_names(tmp_path):
    d = tmp_path / 'D'
    office = d / 'xwechat_files' / 'wxid_one' / 'msg' / 'file' / '2026-11'
    other = d / '毕业材料' / '多层' / '目录'
    office.mkdir(parents=True)
    other.mkdir(parents=True)
    for p in (
        office / '毕业论文.docx',
        office / '毕业论文（2）.docx',
        other / '毕业论文-定稿.doc',
        office / '项目计划.xlsx',
        other / '项目计划-副本.xls',
        other / '项目计划.pdf',
        other / '0a4b9ecdf0a4b9ecdf0a4b9ecdf0a4b9ecdf.xlsx',
        other / '未知类型.dat',
        other / '~$项目计划.xlsx',
    ):
        p.write_bytes(b'demo')
    groups, stats = scan_office_matches(d)
    assert stats['errors'] == 0
    assert stats['folders'] >= 6
    assert len(groups) == 2
    assert {r['name'] for group in groups for r in group} == {
        '毕业论文.docx', '毕业论文（2）.docx', '毕业论文-定稿.doc',
        '项目计划.xlsx', '项目计划-副本.xls',
    }
    assert all(row['file_type'] in {'Word','Excel','PPT'} for group in groups for row in group)
    assert any('xwechat_files' in row['path'] for group in groups for row in group)
    pdf_groups, _ = scan_office_matches(d,include_pdf=True)
    project = next(g for g in pdf_groups if any('项目计划' in r['name'] for r in g))
    assert any(r['file_type'] == 'PDF' for r in project)
    assert stats['office_files'] == 5


def test_office_scan_excludes_reparse_or_symlink(tmp_path):
    root = tmp_path / 'drive'
    linked = tmp_path / 'outside'
    root.mkdir()
    linked.mkdir()
    (linked / '报告.docx').write_bytes(b'out')
    (root / '报告-副本.docx').write_bytes(b'in')
    try:
        (root / 'linked').symlink_to(linked,target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip('当前环境无符号链接权限')
    groups, stats = scan_office_matches(root)
    assert groups == []
    assert stats['skipped_links'] == 1


def test_office_scan_cancel_and_unreadable_folders(tmp_path, monkeypatch):
    from app import disk_office_scan
    root = tmp_path / 'disk'
    child = root / 'secret'
    child.mkdir(parents=True)
    (child/'项目.docx').write_bytes(b'demo')
    with pytest.raises(DiskScanCancelled):
        scan_office_matches(root,cancelled=lambda: True)
    scan = os.scandir
    def denied(path):
        if Path(path) == child:
            raise PermissionError('denied')
        return scan(path)
    monkeypatch.setattr(disk_office_scan.os,'scandir',denied)
    _, stats = scan_office_matches(root)
    assert stats['errors'] == 1


def test_similar_folders_include_deep_subfolders_without_deletion(tmp_path):
    d = tmp_path / 'D'
    a = d / 'WX' / '资料'
    b = d / 'other' / 'nested' / '资料-副本'
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    (a / 'report.docx').write_bytes(b'12345')
    (b / 'other.docx').write_bytes(b'12')
    groups, info = scan_similar_folders(d)
    assert len(groups) == 1
    assert {r['name'] for r in groups[0]} == {'资料','资料-副本'}
    assert {r['size'] for r in groups[0]} == {2,5}
    assert all(r['source'] == 'folder' for r in groups[0])


def prepared(tmp_path, copy_everything=False):
    root = tmp_path / 'D'
    folder = root / 'xwechat_files' / 'wxid_demo' / 'msg' / 'file'
    folder.mkdir(parents=True)
    paths = [folder / '期末报告.docx', folder / '期末报告-副本.docx']
    for path in paths:
        path.write_bytes(path.name.encode('utf-8'))
        os.utime(path, (time.time()-12,time.time()-12))
    groups, _ = scan_office_matches(root)
    store = Store(tmp_path / 'index.sqlite')
    backend = FakeRecycle(tmp_path/'recycle')
    cleaner = DiskOfficeCleanup(store,backend,settle_seconds=0)
    selected = groups[0] if copy_everything else groups[0][:1]
    return paths, groups, cleaner, cleaner.prepare(selected,groups)


def test_cleanup_requires_one_untouched_copy_per_group(tmp_path):
    paths,groups,cleaner,plan = prepared(tmp_path,copy_everything=True)
    assert not any(item.allowed for item in plan.items)
    assert cleaner.execute(plan,confirmed=True,original_ack=True)['success'] == 0
    assert all(p.exists() for p in paths)


def test_cleanup_only_selected_file_and_once_only(tmp_path):
    paths,groups,cleaner,plan = prepared(tmp_path)
    assert plan.items[0].allowed
    result = cleaner.execute(plan,confirmed=True,original_ack=True)
    assert result['success'] == 1
    assert sum(p.exists() for p in paths) == 1
    with pytest.raises(ValueError,match='已使用'):
        cleaner.execute(plan,confirmed=True,original_ack=True)


def test_cleanup_requires_snapshot_and_rejects_links(tmp_path):
    paths,groups,cleaner,plan = prepared(tmp_path)
    selected = Path(plan.items[0].path)
    selected.write_bytes(b'changed after scan')
    result = cleaner.execute(plan,confirmed=True,original_ack=True)
    assert result['success'] == 0
    assert selected.exists()
    assert sum(p.exists() for p in paths) == 2
