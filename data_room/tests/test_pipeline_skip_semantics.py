"""Regression tests for codex review round 4 findings.

P1: Pipeline must differentiate between "skipped because already done"
    (indexing should still run) and "skipped because can't extract"
    (indexing must also skip).
P1: Image uploads (jpg/png/etc.) must fall back to OCR-based markdown
    extraction so they can still be indexed in Milvus.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.core.files.base import ContentFile


@pytest.fixture
def user(db):
    from users.factories import create_broker

    return create_broker()


@pytest.fixture
def client_doc_with_markdown(db, user):
    """Client doc where extraction is already complete and markdown exists."""
    from data_room.models import ProtectedClientDocument
    from users.models import ClientCompany

    cc = ClientCompany.objects.create(company="TestCo3", register_number="HRB 3", is_active=True)
    doc = ProtectedClientDocument.objects.create(
        client=cc,
        name="doc",
        user=user,
        user_type="broker",
        user_company="B",
        indexing_status="pending",  # only indexing needs to re-run
        client_extraction_status="completed",
        financial_extraction_status="completed",
        markdown="# existing markdown content",
        tokens=100,
    )
    doc.file.save("x.pdf", ContentFile(b"%PDF-1.4 fake"))
    return doc


@pytest.mark.django_db
class TestSkippedExtractionButIndexingRuns:
    """P1: restart_indexing must re-index docs with existing markdown."""

    def test_extraction_already_complete_still_runs_indexing(self, client_doc_with_markdown, user):
        """When extraction returns skipped due to already-done status, indexing must still run."""
        from data_room.tasks.index_document import _process_single_document_task

        with patch("data_room.tasks.index_document.index_document_task.apply") as mock_index:
            mock_index.return_value = MagicMock(result={"status": "success"})

            _process_single_document_task(client_doc_with_markdown.id, user.id, "ProtectedClientDocument")

            # Indexing must run since markdown exists
            mock_index.assert_called_once()


@pytest.mark.django_db
class TestImageUploadsFallbackToOcr:
    """P1: Image uploads must get OCR-based markdown to be indexable."""

    def test_jpg_client_doc_gets_ocr_markdown(self, user):
        from data_room.models import ProtectedClientDocument
        from data_room.tasks.index_document import _process_single_document_task
        from users.models import ClientCompany

        cc = ClientCompany.objects.create(company="TestCo4", register_number="HRB 4", is_active=True)
        doc = ProtectedClientDocument.objects.create(
            client=cc,
            name="img",
            user=user,
            user_type="broker",
            user_company="B",
            indexing_status="pending",
        )
        doc.file.save("pic.jpg", ContentFile(b"\xff\xd8\xff fake-jpeg"))

        def ocr_side_effect(*args, **kwargs):
            # Simulate successful OCR by populating markdown on the document
            ProtectedClientDocument.objects.filter(id=doc.id).update(markdown="# OCR markdown")
            return MagicMock(result={"status": "success"})

        with patch(
            "data_room.tasks.index_document.extract_markdown_task.apply", side_effect=ocr_side_effect
        ) as mock_ocr, patch("data_room.tasks.index_document.index_document_task.apply") as mock_index:
            mock_index.return_value = MagicMock(result={"status": "success"})

            _process_single_document_task(doc.id, user.id, "ProtectedClientDocument")

            # OCR fallback must run for non-PDF files
            mock_ocr.assert_called_once()
            # Indexing must also run after OCR
            mock_index.assert_called_once()
