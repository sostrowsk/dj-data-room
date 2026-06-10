"""Regression test for the pipeline-catch reset loop (Codex P1).

Bug staging: ProtectedClientDocument#105 reached indexing_attempts=7336
because _process_single_document_task's outer catch block reset
indexing_status to 'pending' on every failure. Even after MAX_ATTEMPTS
(when the inner task had already correctly set the doc to terminal
'failed'), this catch OVERWROTE the terminal state back to 'pending'.
The scheduled enqueuer then picked it up again — infinite loop bypassing
all the terminal-state work in update_document_status and
_emergency_status_update.

Fix: only reset to pending when the doc still has retry budget
(indexing_attempts < MAX_ATTEMPTS) AND isn't already on a terminal
status ('failed' / 'indexed'). After MAX_ATTEMPTS the terminal 'failed'
state must survive so the scheduled enqueuer stops picking it up.
"""

from unittest.mock import MagicMock, patch

import pytest

from data_room.helpers.update_document_status import MAX_ATTEMPTS
from data_room.tasks.index_document import _process_single_document_task
from data_room.tests.factories import ProtectedDocumentFactory


def _patch_extract_to_set_state_then_raise(model_class, doc_id, *, status, attempts):
    """Patch extract_document_data_task.apply so it (a) updates the doc to
    the given (status, attempts) — mimicking what update_document_status
    does INSIDE the inner task — and then (b) raises, which forces the
    outer pipeline catch to run.

    This is the realistic flow: by the time the outer catch fires, the
    inner task has already terminal-marked the doc; the bug is that the
    catch unconditionally clobbers that terminal state."""

    def _stub_apply(*args, **kwargs):
        model_class.objects.filter(id=doc_id).update(indexing_status=status, indexing_attempts=attempts)
        raise RuntimeError("simulated inner-task failure")

    return patch(
        "ai_agents.tasks.extract_document_data.extract_document_data_task.apply",
        side_effect=_stub_apply,
    )


@pytest.mark.django_db
class TestPipelineCatchPreservesTerminalFailed:
    def test_pipeline_failure_under_max_attempts_resets_to_pending(self):
        """Sanity: doc with retry budget left should be re-queued."""
        doc = ProtectedDocumentFactory.create(indexing_status="pending", indexing_attempts=0)

        with _patch_extract_to_set_state_then_raise(type(doc), doc.id, status="failed", attempts=1), patch(
            "data_room.tasks.index_document.ProgressTrackerService"
        ) as pts:
            pts.create_task.return_value = MagicMock(id=1)
            _process_single_document_task(doc.id, user_id=doc.user.id, model_name=type(doc).__name__)

        doc.refresh_from_db()
        assert doc.indexing_status == "pending"  # reset for retry

    def test_pipeline_failure_at_max_attempts_does_not_revive_terminal_failed(self):
        """The actual staging bug: doc that the inner task already marked
        as terminal 'failed' (attempts >= MAX) must NOT be reset to
        'pending' by the outer pipeline catch. Otherwise the scheduled
        enqueuer revives the loop forever."""
        doc = ProtectedDocumentFactory.create(indexing_status="pending", indexing_attempts=MAX_ATTEMPTS - 1)

        with _patch_extract_to_set_state_then_raise(type(doc), doc.id, status="failed", attempts=MAX_ATTEMPTS), patch(
            "data_room.tasks.index_document.ProgressTrackerService"
        ) as pts:
            pts.create_task.return_value = MagicMock(id=1)
            _process_single_document_task(doc.id, user_id=doc.user.id, model_name=type(doc).__name__)

        doc.refresh_from_db()
        # Must STAY failed — terminal state preserved.
        assert doc.indexing_status == "failed"
        assert doc.indexing_attempts == MAX_ATTEMPTS

    def test_pipeline_failure_does_not_reset_already_indexed_doc(self):
        """An indexed doc shouldn't be reset to pending by the catch either."""
        doc = ProtectedDocumentFactory.create(indexing_status="pending", indexing_attempts=0)

        with _patch_extract_to_set_state_then_raise(type(doc), doc.id, status="indexed", attempts=1), patch(
            "data_room.tasks.index_document.ProgressTrackerService"
        ) as pts:
            pts.create_task.return_value = MagicMock(id=1)
            _process_single_document_task(doc.id, user_id=doc.user.id, model_name=type(doc).__name__)

        doc.refresh_from_db()
        assert doc.indexing_status == "indexed"
