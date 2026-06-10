"""Merge linked_client into client field and remove linked_client column."""

from django.db import migrations, models


def copy_linked_client_to_client(apps, schema_editor):
    """For docs where linked_client differs from client, update client to linked_client."""
    ProtectedClientDocument = apps.get_model("data_room", "ProtectedClientDocument")
    updated = (
        ProtectedClientDocument.objects.filter(
            linked_client__isnull=False,
        )
        .exclude(
            linked_client=models.F("client"),
        )
        .update(client=models.F("linked_client"))
    )
    if updated:
        print(f"  Updated {updated} documents: copied linked_client -> client")


class Migration(migrations.Migration):
    dependencies = [
        ("data_room", "0014_remove_protectedprojectdocument_tag_and_more"),
    ]

    operations = [
        migrations.RunPython(copy_linked_client_to_client, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="protectedclientdocument",
            name="linked_client",
        ),
    ]
