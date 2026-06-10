"""Regression tests for max-attempts state setter.

Bug staging: ProtectedClientDocument#105 in indexing_status='pending'
with indexing_attempts=7336. The "Marking as indexed" warning fires
hundreds of times — but the status never sticks.

Two distinct setters in update_document_status set the post-MAX status
to 'indexed' instead of 'failed':
- ORM path :120: `document.indexing_status = "indexed"`
- SQL fallback :145: `(status if attempts < MAX else "indexed")`

Both must set 'failed' so the doc reaches a terminal state and Celery
retries stop. The state machine already has 'failed': ['pending'] for
manual re-queue.
"""

import pytest

from data_room.helpers.update_document_status import MAX_ATTEMPTS, update_document_status
from data_room.tests.factories import ProtectedDocumentFactory


@pytest.mark.django_db
class TestMaxAttemptsOrmPath:
    def test_max_attempts_marks_document_as_failed_not_indexed(self):
        """ORM path (line 120): after MAX_ATTEMPTS-1 prior failures, the
        next failed transition must set 'failed' (terminal), not 'indexed'."""
        doc = ProtectedDocumentFactory.create(indexing_status="indexing", indexing_attempts=MAX_ATTEMPTS - 1)

        ok = update_document_status(doc, "failed")

        assert ok is True
        doc.refresh_from_db()
        assert doc.indexing_status == "failed"
        assert doc.indexing_attempts == MAX_ATTEMPTS

    def test_below_max_attempts_marks_document_as_failed(self):
        """Sanity check: under MAX_ATTEMPTS, regular failed transition still works."""
        doc = ProtectedDocumentFactory.create(indexing_status="indexing", indexing_attempts=0)

        ok = update_document_status(doc, "failed")

        assert ok is True
        doc.refresh_from_db()
        assert doc.indexing_status == "failed"
        assert doc.indexing_attempts == 1


@pytest.mark.django_db
class TestMaxAttemptsSqlFallbackPath:
    def test_sql_fallback_marks_document_as_failed_not_indexed(self):
        """SQL fallback path (line 145, exception handler): when the ORM
        save fails, the emergency UPDATE must also set 'failed', not
        'indexed', once attempts reach MAX_ATTEMPTS."""
        doc = ProtectedDocumentFactory.create(indexing_status="indexing", indexing_attempts=MAX_ATTEMPTS)

        # Force the ORM path to raise so the except branch (SQL fallback) runs.
        # Patch document.save to throw a generic Exception (not IntegrityError
        # — that branch returns early without trying the SQL fallback).
        from unittest.mock import patch

        with patch.object(type(doc), "save", side_effect=RuntimeError("simulated ORM failure")):
            ok = update_document_status(doc, "failed")

        assert ok is True
        doc.refresh_from_db()
        # SQL fallback must have set 'failed', NOT 'indexed'.
        assert doc.indexing_status == "failed"

    def test_sql_fallback_keeps_status_under_max_attempts(self):
        """SQL fallback path: under MAX_ATTEMPTS the requested status is set
        verbatim (no auto-promotion to terminal)."""
        from unittest.mock import patch

        doc = ProtectedDocumentFactory.create(indexing_status="indexing", indexing_attempts=0)

        with patch.object(type(doc), "save", side_effect=RuntimeError("simulated ORM failure")):
            update_document_status(doc, "failed")

        doc.refresh_from_db()
        assert doc.indexing_status == "failed"
