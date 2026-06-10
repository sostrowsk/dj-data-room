"""Regression guard for removal of the dead ``download_pdf_task``.

``data_room/tasks/download_pdf.py::download_pdf_task`` was unreachable dead
code: it had no URL route, no view caller, and no Celery registration. It also
called ``process_image`` with a stale 3-argument signature while the function
requires 5 positional arguments
(``process_image(filename, watermark1, watermark2, empty, user_type)``), so any
invocation would have raised ``TypeError``.

These tests assert the symbol and its module are gone so the broken task is not
silently re-exported again.
"""

import importlib

import pytest


def test_download_pdf_task_not_exported_from_tasks_package():
    tasks_pkg = importlib.import_module("data_room.tasks")
    assert not hasattr(tasks_pkg, "download_pdf_task")


def test_download_pdf_module_is_removed():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("data_room.tasks.download_pdf")
