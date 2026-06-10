# data_room/helpers/get_document.py
from django.db import transaction

from ..models import ProtectedProjectDocument


def get_document(protected_document_id, model_name: str = "ProtectedDocument"):
    from ..models import ProtectedClientDocument

    model_class = ProtectedClientDocument if model_name == "ProtectedClientDocument" else ProtectedProjectDocument

    with transaction.atomic():
        return model_class.objects.select_for_update().get(id=protected_document_id)
