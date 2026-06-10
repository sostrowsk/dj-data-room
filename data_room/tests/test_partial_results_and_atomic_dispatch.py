"""Regression tests for codex review round 7 findings.

P1: When ONE parallel extraction fails but others (esp. markdown) succeeded,
    partial results must still be persisted — otherwise markdown is lost and
    indexing can't run.
P2: Two callers dispatching the same pending doc must not both enqueue a
    worker (race between upload path and periodic beat).
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

    cc = ClientCompany.objects.create(company="TC", register_number="HRB 1", is_active=True)
    doc = ProtectedClientDocument.objects.create(
        client=cc, name="d", user=user, user_type="broker", user_company="B", indexing_status="pending"
    )
    doc.file.save("f.pdf", ContentFile(b"%PDF-1.4 fake"))
    return doc


@pytest.mark.django_db
class TestPartialResultsPersisted:
    """P1: Markdown/successful results must persist even if auxiliary fails."""

    def test_markdown_persisted_when_guv_call_fails(self, client_doc, user):
        from ai_agents.tasks.extract_document_data import extract_document_data_task

        # Mock: client + markdown succeed, guv raises.
        # **kwargs absorbs the Phase-5 `pdf_bytes` keyword argument.
        def fake_call(client, pdf_path, system_prompt, extraction_prompt, output_schema, label, **kwargs):
            stats = MagicMock(input_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=1)
            if label == "client":
                # Return a minimal ClientExtractionResult
                from ai_agents.schemas.client import ClientExtractionResult

                return ClientExtractionResult(entities=[], document_type="annual_report"), stats
            if label == "markdown":
                stats.content = "# extracted markdown"
                return None, stats
            if label == "guv":
                raise RuntimeError("GuV API timed out")
            return None, stats

        with patch("ai_agents.tasks.extract_document_data._run_extraction_call", side_effect=fake_call), patch(
            "ai_agents.tasks.extract_document_data._get_cached_client_and_model",
            return_value=(MagicMock(), "test-model"),
        ):
            extract_document_data_task.apply(
                args=[client_doc.id],
                kwargs={"user_id": user.id, "document_model": "ProtectedClientDocument"},
            )

        client_doc.refresh_from_db()
        # Markdown should be persisted despite the auxiliary failure
        assert (
            client_doc.markdown == "# extracted markdown"
        ), f"Expected markdown to be persisted despite GuV failure, got {client_doc.markdown!r}"


@pytest.mark.django_db
class TestDispatchAtomicity:
    """P2: Concurrent dispatchers must not both enqueue workers for same doc."""

    def test_dispatcher_uses_atomic_transition(self, client_doc, user):
        """
        Verify the dispatcher atomically reads+updates.
        Simulate: prior dispatcher already moved doc to 'queued'.
        A second dispatcher must enqueue NO worker for this doc.
        """
        from data_room.models import ProtectedClientDocument
        from data_room.tasks.index_document import process_document_pipeline_task

        # First dispatch already moved doc to 'queued'
        ProtectedClientDocument.objects.filter(id=client_doc.id).update(indexing_status="queued")

        with patch("data_room.tasks.index_document._process_single_document_task.delay") as mock_dispatch:
            process_document_pipeline_task([client_doc.id], user.id, "ProtectedClientDocument")

            mock_dispatch.assert_not_called()
