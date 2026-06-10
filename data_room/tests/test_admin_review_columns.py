"""Self-Review-Loop columns must be visible in the
ProtectedClientDocumentAdmin changelist so the operator can spot
queued/processing/failed docs and their runs+score at a glance —
without opening each doc.

Required columns (User-Direktive):
- review_status  (existing CharField)
- review_runs    (audit-trail length, computed via list_display method)
- review_score   (existing FloatField)

Plus review_status in list_filter (existing CharField with choices).
"""

import pytest
from django.contrib.admin.sites import AdminSite


@pytest.mark.django_db
class TestProtectedClientDocumentAdminReviewColumns:
    def test_list_display_contains_review_status(self):
        from data_room.admin import ProtectedClientDocumentAdmin
        from data_room.models import ProtectedClientDocument

        admin = ProtectedClientDocumentAdmin(ProtectedClientDocument, AdminSite())
        assert "review_status" in admin.list_display, (
            "review_status muss als Spalte in list_display stehen, " f"aktuell: {admin.list_display}"
        )

    def test_list_display_contains_review_score(self):
        from data_room.admin import ProtectedClientDocumentAdmin
        from data_room.models import ProtectedClientDocument

        admin = ProtectedClientDocumentAdmin(ProtectedClientDocument, AdminSite())
        assert "review_score" in admin.list_display, (
            "review_score muss als Spalte in list_display stehen, " f"aktuell: {admin.list_display}"
        )

    def test_list_display_exposes_runs_count(self):
        """JSONField review_runs als List → Anzahl Iterationen ueber
        Method/Property in list_display. Method-Name muss in
        list_display + auf der Admin-Klasse existieren."""
        from data_room.admin import ProtectedClientDocumentAdmin
        from data_room.models import ProtectedClientDocument

        admin = ProtectedClientDocumentAdmin(ProtectedClientDocument, AdminSite())
        candidates = [c for c in admin.list_display if "run" in c.lower()]
        assert candidates, (
            "list_display muss mindestens eine Spalte fuer review_runs-Count "
            f"enthalten, aktuell: {admin.list_display}"
        )
        for col in candidates:
            assert hasattr(admin, col) or hasattr(
                ProtectedClientDocument, col
            ), f"Spalte {col!r} hat weder Admin-Method noch Model-Field/-Property."

    def test_runs_count_returns_len_of_review_runs(self):
        """Die runs-Spalten-Method/Property muss len(review_runs or [])
        liefern — nicht das ganze JSON, nicht None."""
        from django.core.files.base import ContentFile

        from data_room.admin import ProtectedClientDocumentAdmin
        from data_room.models import ProtectedClientDocument
        from users.factories import create_broker
        from users.models import ClientCompany

        broker = create_broker()
        client = ClientCompany.objects.create(company="ReviewColCo", is_active=True)
        doc = ProtectedClientDocument.objects.create(
            client=client,
            name="d-runs",
            user=broker,
            user_type="broker",
            user_company="x",
            review_runs=[{"i": 0}, {"i": 1}, {"i": 2}],
        )
        doc.file.save("d.pdf", ContentFile(b"%PDF-1.4"))
        admin = ProtectedClientDocumentAdmin(ProtectedClientDocument, AdminSite())

        runs_col = next(c for c in admin.list_display if "run" in c.lower())
        getter = getattr(admin, runs_col, None) or getattr(doc, runs_col, None)
        assert getter is not None, f"Kein Getter fuer {runs_col!r} gefunden."
        value = getter(doc) if callable(getter) else getter
        assert value == 3, f"runs-Spalte erwartet 3 (len(review_runs)), bekam {value!r}"

    def test_runs_count_handles_none_review_runs(self):
        """review_runs ist default=[] aber legacy/edge JSON kann None
        sein. Method darf nicht crashen, muss 0 liefern."""
        from django.core.files.base import ContentFile

        from data_room.admin import ProtectedClientDocumentAdmin
        from data_room.models import ProtectedClientDocument
        from users.factories import create_broker
        from users.models import ClientCompany

        broker = create_broker()
        client = ClientCompany.objects.create(company="ReviewColNoneCo", is_active=True)
        doc = ProtectedClientDocument.objects.create(
            client=client,
            name="d-none",
            user=broker,
            user_type="broker",
            user_company="x",
        )
        doc.file.save("d.pdf", ContentFile(b"%PDF-1.4"))
        # Forcieren: keine review_runs persistiert
        doc.review_runs = None
        doc.save(update_fields=["review_runs"])
        admin = ProtectedClientDocumentAdmin(ProtectedClientDocument, AdminSite())

        runs_col = next(c for c in admin.list_display if "run" in c.lower())
        getter = getattr(admin, runs_col, None) or getattr(doc, runs_col, None)
        value = getter(doc) if callable(getter) else getter
        assert value == 0, f"runs-Spalte mit None-runs erwartet 0, bekam {value!r}"

    def test_list_filter_contains_review_status(self):
        """review_status mit Choices → typischer Admin-Filter, sonst
        kein einfaches Drilldown auf 'queued' Docs."""
        from data_room.admin import ProtectedClientDocumentAdmin
        from data_room.models import ProtectedClientDocument

        admin = ProtectedClientDocumentAdmin(ProtectedClientDocument, AdminSite())
        assert "review_status" in admin.list_filter, (
            "review_status muss als Filter in list_filter stehen, " f"aktuell: {admin.list_filter}"
        )
