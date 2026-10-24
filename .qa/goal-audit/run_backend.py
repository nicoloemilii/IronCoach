"""Run isolated tests with workspace temp directories inheriting sandbox ACLs.

On Windows, Python mkdir(mode=0700) replaces the inherited ACL with the owner
only. The tool sandbox is a separate restricted identity, so pytest cannot
reopen its own directories. QA directories may inherit the workspace ACL.
The application code and test assertions are otherwise unchanged.
"""
import os
from pathlib import Path
import sys
import tempfile
from uuid import uuid4

PROJECT = Path(__file__).resolve().parents[2]
QA = Path(__file__).resolve().parent
os.chdir(PROJECT)
sys.path.insert(0, str(PROJECT))
os.environ['IRON_DATA_DIR'] = str(QA / 'runtime-isolated')
os.environ['IRON_AUTO_SYNC'] = '0'
temporary_root = QA / 'temporary-files'
temporary_root.mkdir(exist_ok=True)
os.environ['TEMP'] = os.environ['TMP'] = str(temporary_root)
tempfile.tempdir = str(temporary_root)
original_mkdir = os.mkdir


def inherited_workspace_mkdir(path, mode=0o777, *, dir_fd=None):
    target = Path(path).resolve()
    if target.is_relative_to(QA) and mode == 0o700:
        mode = 0o777
    if dir_fd is None:
        return original_mkdir(path, mode)
    return original_mkdir(path, mode, dir_fd=dir_fd)


if __name__ == '__main__':
    import pytest
    os.mkdir = inherited_workspace_mkdir
    try:
        args = sys.argv[1:] or ['tests']
        result = pytest.main([*args, '-q', '-p', 'no:cacheprovider', '--basetemp', str(QA / ('tmp-' + uuid4().hex))])
    finally:
        os.mkdir = original_mkdir
    raise SystemExit(result)
