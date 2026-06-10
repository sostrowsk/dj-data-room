"""Regression tests for codex review round 3 findings.

P1: Pipeline must NOT run index_document_task when extraction returned `skipped`
    (non-PDF case) — otherwise SCRIBE.process_pdf rejects and doc loops on pending.
P2: extract_document_data_task retry guard must check BOTH client_extraction_status
    AND financial_extraction_status, so partial states get completed.
P2: process_document_pipeline_task dispatcher must only enqueue workers for docs
    that actually transitioned pending → queued (avoid duplicate dispatch).
"""

from unittest.mock import MagicMock, patch

import pytest
from django.core.files.base import ContentFile


@pytest.fixture
def user(db):
    from users.factories import create_broker

    return create_broker()


@pytest.fixture
def client_doc(db, user):
    from data_room.models import ProtectedClientDocument
    from users.models import ClientCompany

    client = ClientCompany.objects.create(company="TestCo", register_number="HRB 1", is_active=True)
    doc = ProtectedClientDocument.objects.create(
        client=client,
        name="doc",
        user=user,
        user_type="broker",
        user_company="B",
        indexing_status="pending",
    )
    doc.file.save("x.pdf", ContentFile(b"%PDF-1.4 fake"))
    return doc


@pytest.mark.django_db
class TestPipelineSkipsIndexingOnExtractionSkip:
    """P1: When extraction returns skipped (e.g. non-PDF), don't run indexing."""

    def test_image_upload_does_not_reach_indexing(self, user):
        from data_room.models import ProtectedClientDocument
        from data_room.tasks.index_document import _process_single_document_task
        from users.models import ClientCompany

        cc = ClientCompany.objects.create(company="TestCo2", register_number="HRB 2", is_active=True)
        doc = ProtectedClientDocument.objects.create(
            client=cc, name="img", user=user, user_type="broker", user_company="B", indexing_status="pending"
        )
        doc.file.save("pic.jpg", ContentFile(b"\xff\xd8\xff fake-jpeg"))

        with patch("data_room.tasks.index_document.index_document_task.apply") as mock_index:
            _process_single_document_task(doc.id, user.id, "ProtectedClientDocument")
            mock_index.assert_not_called()


@pytest.mark.django_db
class TestFinancialRetryOnPartialCompletion:
    """P2: Partial doc (client=completed, financial=pending) must retry extraction."""

    def test_partial_state_retries_extraction(self, client_doc, user):
        """If client=completed but financial=pending, re-running must retry."""
        from ai_agents.tasks.extract_document_data import extract_document_data_task
        from data_room.models import ProtectedClientDocument

        ProtectedClientDocument.objects.filter(id=client_doc.id).update(
            client_extraction_status="completed",
            financial_extraction_status="pending",
        )

        with patch("ai_agents.tasks.extract_document_data._run_extraction_call") as mock_call:
            mock_result = MagicMock(
                input_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=1, content="md"
            )
            mock_call.return_value = (None, mock_result)

            result = extract_document_data_task.apply(
                args=[client_doc.id],
                kwargs={"user_id": user.id, "document_model": "ProtectedClientDocument"},
            )
            result_dict = result.result if hasattr(result, "result") else result

            # Must NOT be skipped — partial state should retry
            assert result_dict.get("status") != "skipped", (
                f"Partial state (financial=pending) should retry, got status={result_dict.get('status')}, "
                f"output={result_dict.get('output')}"
            )


@pytest.mark.django_db
class TestDispatcherSchedulesOnlyTransitioned:
    """P2: Dispatcher must only enqueue workers for docs that transitioned to queued."""

    def test_already_queued_docs_not_redispatched(self, client_doc, user):
        """If doc is already in 'queued' state, dispatcher must not enqueue another worker."""
        from data_room.models import ProtectedClientDocument
        from data_room.tasks.index_document import process_document_pipeline_task

        # Simulate doc already queued by a prior dispatch
        ProtectedClientDocument.objects.filter(id=client_doc.id).update(indexing_status="queued")

        with patch("data_room.tasks.index_document._process_single_document_task.delay") as mock_dispatch:
            process_document_pipeline_task([client_doc.id], user.id, "ProtectedClientDocument")
            mock_dispatch.assert_not_called()
