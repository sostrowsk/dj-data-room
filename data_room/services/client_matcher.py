"""
Client Matcher Service.

Matches extracted client entities against existing database clients
using registration number (HRB) matching and LLM-based comparison.
"""

import logging
from typing import List, Optional

from ai_router.client import get_llm_client
from django.conf import settings
from django.db.models import Q
from pydantic import BaseModel, Field

from data_room.conf import get_client_company_model
from data_room.schemas import ClientMatchResult, ExtractedClientEntity

ClientCompany = get_client_company_model()

logger = logging.getLogger(__name__)


class ClientMatchLLMResponse(BaseModel):
    """Structured LLM response for client matching."""

    match_id: Optional[int] = Field(
        None,
        description="The database ID of the matching client, or None if no match found",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score between 0.0 and 1.0",
    )
    reason: str = Field(
        ...,
        description="Explanation for the matching decision",
    )


class ClientMatcherService:
    """Service for matching extracted entities to existing database clients."""

    def __init__(self, client_company=None, use_llm: bool = True):
        """
        Initialize the matcher service.

        Args:
            client_company: Optional ClientCompany to scope client search
            use_llm: Whether to use LLM for fuzzy matching (default True)
        """
        self.client_company = client_company
        self.use_llm = use_llm

    def find_matches(self, extracted_entities: List[ExtractedClientEntity]) -> List[ClientMatchResult]:
        """
        Find potential matches for all extracted entities.

        Args:
            extracted_entities: List of extracted client entities

        Returns:
            List of match results for each entity
        """
        results = []
        for entity in extracted_entities:
            result = self._match_single_entity(entity)
            results.append(result)
        return results

    def _match_single_entity(self, entity: ExtractedClientEntity) -> ClientMatchResult:
        """
        Match a single extracted entity against the database.

        Args:
            entity: The extracted entity to match

        Returns:
            ClientMatchResult with match information
        """
        # First, try exact HRB match
        if entity.registration_number:
            hrb_match = self._find_by_registration_number(entity.registration_number)
            if hrb_match:
                return ClientMatchResult(
                    extracted_entity=entity,
                    existing_client_id=hrb_match.id,
                    existing_client_name=hrb_match.company,
                    match_type="exact_hrb",
                    match_confidence=1.0,
                    llm_reasoning=f"Exact HRB match: {entity.registration_number}",
                )

        # Then, find similar candidates by name
        candidates = self._find_name_candidates(entity.name)

        if not candidates:
            return ClientMatchResult(
                extracted_entity=entity,
                match_type="none",
                match_confidence=0.0,
            )

        # Check for exact name match first
        for candidate in candidates:
            if candidate.company.lower() == entity.name.lower():
                return ClientMatchResult(
                    extracted_entity=entity,
                    existing_client_id=candidate.id,
                    existing_client_name=candidate.company,
                    match_type="exact_name",
                    match_confidence=1.0,
                    llm_reasoning=f"Exact name match: {entity.name}",
                )

        # If only one candidate and name is very similar, suggest it
        if len(candidates) == 1:
            candidate = candidates[0]
            if self._names_are_similar(entity.name, candidate.company):
                if self.use_llm:
                    llm_result = self._compare_with_llm(entity, [candidate])
                    if llm_result:
                        return llm_result
                return ClientMatchResult(
                    extracted_entity=entity,
                    existing_client_id=candidate.id,
                    existing_client_name=candidate.company,
                    match_type="suggested",
                    match_confidence=0.7,
                )

        # Multiple candidates: use LLM to determine best match
        if self.use_llm and candidates:
            llm_result = self._compare_with_llm(entity, candidates)
            if llm_result:
                return llm_result

        # No confident match found
        return ClientMatchResult(
            extracted_entity=entity,
            match_type="none",
            match_confidence=0.0,
        )

    def _find_by_registration_number(self, registration_number: str) -> Optional[ClientCompany]:
        """Find client by exact registration number match."""
        # Normalize the registration number
        normalized = self._normalize_hrb(registration_number)

        try:
            qs = ClientCompany.objects.filter(is_active=True)
            if self.client_company:
                # Include the scoped company (regardless of is_active) plus its active holding peers
                if self.client_company.holding:
                    qs = ClientCompany.objects.filter(
                        Q(id=self.client_company.id) | Q(holding=self.client_company.holding, is_active=True)
                    )
                else:
                    # No holding — include scoped client + all active companies
                    qs = ClientCompany.objects.filter(Q(id=self.client_company.id) | Q(is_active=True))
            return qs.filter(
                Q(register_number__iexact=registration_number) | Q(register_number__iexact=normalized)
            ).first()
        except Exception as e:
            logger.error(f"Error finding client by HRB: {e}")
            return None

    @staticmethod
    def _normalize_hrb(hrb: str) -> str:
        """Normalize HRB format: 'HRB12345' -> 'HRB 12345'."""
        if not hrb:
            return ""
        hrb = hrb.strip().upper()
        # Handle "HRB12345" -> "HRB 12345"
        if hrb.startswith("HRB") and len(hrb) > 3 and hrb[3] != " ":
            hrb = "HRB " + hrb[3:]
        if hrb.startswith("HRA") and len(hrb) > 3 and hrb[3] != " ":
            hrb = "HRA " + hrb[3:]
        return hrb

    def _find_name_candidates(self, name: str, limit: int = 10) -> List[ClientCompany]:
        """Find clients with similar names."""
        if not name:
            return []

        # Extract core name words (remove legal form suffixes)
        core_name = self._extract_core_name(name)
        name_words = core_name.lower().split()

        if not name_words:
            return []

        # Build query for partial matches
        query = Q()
        for word in name_words[:3]:  # Use first 3 significant words
            if len(word) >= 3:  # Skip short words
                query |= Q(company__icontains=word)

        # Guard: if no search terms were added, return empty list
        if not query:
            logger.debug(f"No valid search terms found in name '{name}' (all words too short)")
            return []

        try:
            qs = ClientCompany.objects.filter(is_active=True)
            if self.client_company:
                # Include the scoped company (regardless of is_active) plus its active holding peers
                if self.client_company.holding:
                    qs = ClientCompany.objects.filter(
                        Q(id=self.client_company.id) | Q(holding=self.client_company.holding, is_active=True)
                    )
                else:
                    # No holding — include scoped client + all active companies
                    qs = ClientCompany.objects.filter(Q(id=self.client_company.id) | Q(is_active=True))
            return list(qs.filter(query)[:limit])
        except Exception as e:
            logger.error(f"Error finding name candidates: {e}")
            return []

    @staticmethod
    def _extract_core_name(name: str) -> str:
        """Remove legal form suffixes from company name."""
        # Sorted by length descending to match longer suffixes first
        # e.g., " GmbH & Co. KG" must be checked before " KG"
        legal_forms = [
            " UG (haftungsbeschränkt)",
            " GmbH & Co. OHG",
            " GmbH & Co. KG",
            " Ltd.",
            " Inc.",
            " GmbH",
            " s.r.o.",
            " e.K.",
            " a.s.",
            " B.V.",
            " S.A.",
            " OHG",
            " SE",
            " AG",
            " UG",
            " KG",
            " eG",
        ]
        result = name
        for form in legal_forms:
            if result.endswith(form):
                result = result[: -len(form)]
                break  # Only remove one suffix
        return result.strip()

    @staticmethod
    def _normalize_name(name: str) -> str:
        """Normalize company name for comparison: lowercase, hyphens→spaces, strip."""
        return name.lower().replace("-", " ").strip()

    def _names_are_similar(self, name1: str, name2: str) -> bool:
        """Check if two names are similar enough to suggest."""
        core1 = self._normalize_name(self._extract_core_name(name1))
        core2 = self._normalize_name(self._extract_core_name(name2))

        # Simple containment check
        if core1 in core2 or core2 in core1:
            return True

        # Word overlap check
        words1 = set(core1.split())
        words2 = set(core2.split())
        overlap = len(words1 & words2)
        total = len(words1 | words2)

        return total > 0 and (overlap / total) > 0.5

    def _compare_with_llm(
        self, entity: ExtractedClientEntity, candidates: List[ClientCompany]
    ) -> Optional[ClientMatchResult]:
        """Use LLM to compare extracted entity with candidates."""
        if not candidates:
            return None

        try:
            client = get_llm_client(model=settings.DEFAULT_MODEL_DATA_ROOM)

            candidates_text = "\n".join(
                [
                    f"- ID {c.id}: {c.company} (HRB: {c.register_number or 'unbekannt'}, "
                    f"Rechtsform: {c.legal_form or 'unbekannt'})"
                    for c in candidates
                ]
            )

            system_prompt = "Du bist ein Experte für Unternehmensidentifikation und -vergleich."
            user_prompt = f"""Vergleiche das extrahierte Unternehmen mit den Kandidaten aus der Datenbank.

Extrahiertes Unternehmen:
- Name: {entity.name}
- Handelsregister: {entity.registration_number or 'unbekannt'}
- Rechtsform: {entity.legal_form or 'unbekannt'}

Kandidaten in der Datenbank:
{candidates_text}

Frage: Ist das extrahierte Unternehmen identisch mit einem der Kandidaten?

Beachte:
- Gleicher HRB = gleiches Unternehmen
- Leichte Namensunterschiede (Schreibweise, Abkürzungen) können das gleiche Unternehmen sein
- ABER: Unterschiedliche Rechtsformen (z.B. "X GmbH" vs "X SE") können unterschiedliche Unternehmen sein
- Holding/Group vs Tochtergesellschaft sind UNTERSCHIEDLICHE Unternehmen

Antworte im JSON-Format mit:
- match_id: Die ID des passenden Kandidaten, oder null wenn kein Match
- confidence: Deine Konfidenz als Zahl zwischen 0.0 und 1.0
- reason: Deine Begründung für die Entscheidung"""

            from ai_router.logging import llm_log

            with llm_log("client_matcher", settings.DEFAULT_MODEL_DATA_ROOM, user_prompt=user_prompt) as log:
                result, parsed = client.invoke(system_prompt, user_prompt, output_schema=ClientMatchLLMResponse)
                if parsed:
                    log.output = str(parsed.model_dump())
                    response = parsed
                else:
                    log.output = result.content
                    return None

            if response.match_id and response.confidence >= 0.7:
                matched_client = next((c for c in candidates if c.id == response.match_id), None)
                if matched_client:
                    return ClientMatchResult(
                        extracted_entity=entity,
                        existing_client_id=matched_client.id,
                        existing_client_name=matched_client.company,
                        match_type="llm_confirmed",
                        match_confidence=response.confidence,
                        llm_reasoning=response.reason,
                    )

            return None

        except Exception as e:
            logger.error(f"LLM comparison failed: {e}")
            return None
