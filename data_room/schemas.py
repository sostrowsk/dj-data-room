"""
Pydantic schemas for client/company entities extracted from documents.

``ExtractedClientEntity`` describes a single company mentioned in a
financial document, ``ClientMatchResult`` the result of matching such an
entity against existing database clients (see
``data_room.services.client_matcher``). Moved here from
``ai_agents.schemas.client`` (which re-exports them) for the dj-data-room
extraction; ``ClientExtractionResult`` stays in ai_agents.
"""

from typing import Dict, Optional

from pydantic import BaseModel, Field


class ExtractedClientEntity(BaseModel):
    """A single company/client entity extracted from a document."""

    name: str = Field(description="Full company name exactly as it appears in the document")
    canonical_name: Optional[str] = Field(
        default=None,
        description="Standardized/canonical company name without legal form suffix",
    )
    registration_number: Optional[str] = Field(
        default=None,
        description="Commercial register number (Handelsregisternummer), e.g., 'HRB 12345' or 'HRB 12345 B'",
    )
    legal_form: Optional[str] = Field(
        default=None,
        description="Legal form of the company, e.g., 'GmbH', 'SE', 'AG', 'KG', 'OHG', 'GmbH & Co. KG'",
    )
    role: Optional[str] = Field(
        default=None,
        description="Role of this entity in the document: 'subject' (main company), 'parent' (holding/group), 'subsidiary', 'auditor', 'other'",
    )
    additional_identifiers: Optional[Dict[str, str]] = Field(
        default=None,
        description="Other identifying information: tax_number, vat_id, lei, address, etc.",
    )
    confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Confidence score for this extraction (0.0 to 1.0)",
    )


class ClientMatchResult(BaseModel):
    """Result of matching an extracted entity to existing database clients."""

    extracted_entity: ExtractedClientEntity = Field(description="The extracted entity being matched")
    existing_client_id: Optional[int] = Field(
        default=None,
        description="ID of matched existing client, if any",
    )
    existing_client_name: Optional[str] = Field(
        default=None,
        description="Name of matched existing client, if any",
    )
    match_type: str = Field(
        default="none",
        description="Type of match: 'exact_hrb' (HRB match), 'exact_name' (exact name), 'llm_confirmed' (LLM determined same), 'suggested' (similar name), 'none' (no match)",
    )
    match_confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Confidence of the match (0.0 to 1.0)",
    )
    llm_reasoning: Optional[str] = Field(
        default=None,
        description="LLM's reasoning for the match decision",
    )
