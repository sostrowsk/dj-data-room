"""Schema move for dj-data-room extraction (Plan Phase 4A, step A1).

``ExtractedClientEntity`` and ``ClientMatchResult`` live in
``data_room.schemas``; ``ai_agents.schemas.client`` re-exports them so all
legacy import paths stay valid. ``ClientExtractionResult`` stays in
ai_agents (it depends on BaseExtractionResult + the frameworks registry).
"""


def test_extracted_client_entity_reexported_identically_from_ai_agents():
    from ai_agents.schemas import client as legacy
    from data_room import schemas

    assert legacy.ExtractedClientEntity is schemas.ExtractedClientEntity


def test_client_match_result_reexported_identically_from_ai_agents():
    from ai_agents.schemas import client as legacy
    from data_room import schemas

    assert legacy.ClientMatchResult is schemas.ClientMatchResult


def test_client_extraction_result_stays_in_ai_agents_and_uses_moved_entity():
    from ai_agents.schemas.client import ClientExtractionResult
    from data_room.schemas import ExtractedClientEntity

    result = ClientExtractionResult(entities=[{"name": "Acme GmbH"}])

    assert result.item_count == 1
    assert isinstance(result.entities[0], ExtractedClientEntity)


def test_client_match_result_round_trip_with_moved_entity():
    from data_room.schemas import ClientMatchResult, ExtractedClientEntity

    match = ClientMatchResult(
        extracted_entity=ExtractedClientEntity(name="Acme GmbH", registration_number="HRB 12345"),
        existing_client_id=7,
        match_type="exact_hrb",
        match_confidence=1.0,
    )

    assert match.extracted_entity.name == "Acme GmbH"
    assert match.existing_client_id == 7
