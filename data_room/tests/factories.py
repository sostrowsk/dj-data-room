import factory
from factory.django import DjangoModelFactory

from data_room.models import ProtectedClientDocument, ProtectedProjectDocument
from project.tests.project_utils import create_project
from users.tests.factories import ClientCompanyFactory, ClientFactory


class ProtectedDocumentFactory(DjangoModelFactory):
    class Meta:
        model = ProtectedProjectDocument
        skip_postgeneration_save = True

    project = factory.LazyFunction(
        lambda: create_project(
            name="Test Project",
            asset_name="Test Asset",
            client_company=ClientCompanyFactory(),
        )
    )
    name = factory.Sequence(lambda n: f"Test Document {n}")
    file = factory.django.FileField(filename="test.pdf", data=b"PDF content")
    user = factory.SubFactory(ClientFactory)
    user_type = "client"
    user_company = factory.LazyAttribute(
        lambda obj: (
            obj.user.client_company.company if obj.user and hasattr(obj.user, "client_company") else "Test Company"
        )
    )
    indexing_status = "indexed"
    reviewed = True
    disabled = False
    size = 1000
    indexed_chunks = 10

    @factory.post_generation
    def skip_save_hooks(obj, create, extracted, **kwargs):
        pass


class ProtectedClientDocumentFactory(DjangoModelFactory):
    class Meta:
        model = ProtectedClientDocument
        skip_postgeneration_save = True

    client = factory.SubFactory(ClientCompanyFactory)
    name = factory.Sequence(lambda n: f"Test Client Document {n}")
    file = factory.django.FileField(filename="client_doc.pdf", data=b"PDF content")
    user = factory.SubFactory(ClientFactory)
    user_type = "client"
    user_company = factory.LazyAttribute(lambda obj: obj.client.company if obj.client else "Test Company")
    reviewed = True
    disabled = False
    size = 1000
