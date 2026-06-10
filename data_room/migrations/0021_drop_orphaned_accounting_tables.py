"""Drop orphaned accounting_* tables.

The `accounting` Django app was removed in commit 55bd19c25 (2026-04-11)
but the database objects (11 tables, 45 constraints, 10 django_migrations
rows) were left behind. Three of those constraints are FKs into
`data_room_protectedclientdocument`, which silently blocks every
ProtectedClientDocument delete with an IntegrityError.

Forward-only: the schema is no longer in code, so reversal is a noop.
"""

from django.db import migrations


SQL_FORWARD = """
DROP TABLE IF EXISTS accounting_export CASCADE;
DROP TABLE IF EXISTS accounting_entry CASCADE;
DROP TABLE IF EXISTS accounting_batch CASCADE;
DROP TABLE IF EXISTS accounting_periodbalance CASCADE;
DROP TABLE IF EXISTS accounting_guvrawposition CASCADE;
DROP TABLE IF EXISTS accounting_bilanzrawposition CASCADE;
DROP TABLE IF EXISTS accounting_annualaccounts CASCADE;
DROP TABLE IF EXISTS accounting_accountmapping CASCADE;
DROP TABLE IF EXISTS accounting_account CASCADE;
DROP TABLE IF EXISTS accounting_company CASCADE;
DROP TABLE IF EXISTS accounting_auditlog CASCADE;
DELETE FROM django_migrations WHERE app = 'accounting';
"""


class Migration(migrations.Migration):

    dependencies = [
        ("data_room", "0020_alter_protectedclientdocument_document_type_and_more"),
    ]

    operations = [
        migrations.RunSQL(sql=SQL_FORWARD, reverse_sql=migrations.RunSQL.noop),
    ]
