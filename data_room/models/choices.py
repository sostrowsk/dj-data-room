from django.db import models
from django.utils.translation import gettext_lazy as _

# Frozen copy of users.User.TYPE_CHOICES — the package must not import the
# host user model for field choices (drift guard:
# data_room/tests/test_conf.py::test_frozen_choices_match_host_user_model).
USER_TYPE_CHOICES = [
    ("client", _("Client")),
    ("broker", _("Broker")),
    ("partner", _("Leasing partner")),
    ("admin", _("Admin")),
]


class StatementType(models.TextChoices):
    """Statement type: single entity vs consolidated group."""

    SINGLE = "single", _("Single Entity")
    CONSOLIDATED = "consolidated", _("Consolidated")


class ClientDocumentType(models.TextChoices):
    """Document type choices for client documents."""

    COMMERCIAL_REGISTER = "commercial_register", _("Commercial Register")
    RATING = "rating", _("Rating")
    ANNUAL_REPORT = "annual_report", _("Annual Report")
    BANKING_FACILITIES = "banking_facilities", _("Banking Facilities")
    BUSINESS_PLAN = "business_plan", _("Business Plan")
    INTERIM_FIGURES = "interim_figures", _("Interim Figures")
    STRUCTURE_CHART = "structure_chart", _("Structure Chart")
    COMPANY_PRESENTATION = "company_presentation", _("Company Presentation")
    COMPANY_DESCRIPTION = "company_description", _("Company Description")
    CHANGE_CONCEPT = "change_concept", _("Change Concept")
    OTHER = "other", _("Other")


class ProjectDocumentType(models.TextChoices):
    """Document type choices for project documents."""

    ASSET_SPECIFICATION = "asset_specification", _("Asset Specification")
    OFFER = "offer", _("Offer")
    ORDER_CONFIRMATION = "order_confirmation", _("Order Confirmation")
    INVOICE = "invoice", _("Invoice")
    OVERTAKING = "overtaking", _("Acceptance Confirmation")
    LEASE_CONTRACT = "lease_contract", _("Lease Contract")
    INFO_MEMO = "info_memo", _("Info Memo")
    OTHER = "other", _("Other")
