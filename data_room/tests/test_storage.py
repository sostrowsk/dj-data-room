"""Tests for plan step W14-A8: package-local protected storage.

data_room must not import ``pages.storage`` anymore. The storage backend and
file field move into ``data_room/storage.py``; the field deconstruction path
(``data_room.storage.ProtectedFileField``) anchors the in-place rewrite of the
historic migrations (0001, 0007, 0011-0014).
"""

import os

import pytest
from django.conf import settings
from django.test import SimpleTestCase

from data_room.models import ProjectZip, ProtectedClientDocument, ProtectedProjectDocument
from data_room.storage import ProtectedFileField, ProtectedFileSystemStorage
from data_room.tests.factories import ProtectedDocumentFactory


class TestProtectedStorage(SimpleTestCase):
    def test_storage_reads_protected_media_settings(self):
        storage = ProtectedFileSystemStorage()
        self.assertEqual(storage.location, settings.PROTECTED_MEDIA_ROOT)
        self.assertEqual(storage.base_url, settings.PROTECTED_MEDIA_URL)

    def test_field_uses_protected_storage(self):
        field = ProtectedFileField(upload_to="document")
        self.assertIsInstance(field.storage, ProtectedFileSystemStorage)

    def test_model_fields_are_data_room_storage_fields(self):
        for model, field_name in [
            (ProtectedProjectDocument, "file"),
            (ProtectedProjectDocument, "original"),
            (ProtectedClientDocument, "file"),
            (ProtectedClientDocument, "original"),
            (ProjectZip, "zip_file"),
        ]:
            field = model._meta.get_field(field_name)
            self.assertIs(type(field), ProtectedFileField, f"{model.__name__}.{field_name}")
            self.assertIsInstance(field.storage, ProtectedFileSystemStorage)

    def test_field_deconstructs_to_data_room_path(self):
        """Anchor for migration byte-stability: deconstruction must reference
        data_room.storage, matching the rewritten migration files."""
        field = ProtectedProjectDocument._meta.get_field("file")
        _, path, _, kwargs = field.deconstruct()
        self.assertEqual(path, "data_room.storage.ProtectedFileField")
        storage_path, _, _ = kwargs["storage"].deconstruct()
        self.assertEqual(storage_path, "data_room.storage.ProtectedFileSystemStorage")


@pytest.mark.django_db
def test_upload_roundtrip_lands_under_protected_media_root():
    doc = ProtectedDocumentFactory(file__data=b"A8 roundtrip content")
    try:
        file_path = doc.file.path
        protected_root = os.path.realpath(settings.PROTECTED_MEDIA_ROOT)
        assert os.path.realpath(file_path).startswith(protected_root + os.sep)
        with doc.file.open("rb") as handle:
            assert handle.read() == b"A8 roundtrip content"
    finally:
        doc.file.delete(save=False)
