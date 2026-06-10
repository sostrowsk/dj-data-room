"""Company-name parsing move for dj-data-room (Plan Phase 4A, step A2).

``parse_company_name`` / ``company_names_match`` / ``company_name_similarity``
(plus their constants) live in ``data_room.services.company_names``;
``ai_agents.services.financial_formulas`` re-exports them so all legacy
import paths and patch targets stay valid.
"""


def test_company_name_functions_reexported_identically_from_financial_formulas():
    from ai_agents.services import financial_formulas as legacy
    from data_room.services import company_names

    assert legacy.parse_company_name is company_names.parse_company_name
    assert legacy.company_names_match is company_names.company_names_match
    assert legacy.company_name_similarity is company_names.company_name_similarity


def test_company_name_constants_reexported_identically_from_financial_formulas():
    from ai_agents.services import financial_formulas as legacy
    from data_room.services import company_names

    assert legacy.LEGAL_FORMS is company_names.LEGAL_FORMS
    assert legacy.CONSOLIDATION_MARKERS is company_names.CONSOLIDATION_MARKERS
    assert legacy.CONSOLIDATION_PARENTHETICAL is company_names.CONSOLIDATION_PARENTHETICAL
    assert legacy.OTHER_PARENTHETICAL is company_names.OTHER_PARENTHETICAL


def test_parse_company_name_extracts_legal_form_and_consolidation():
    from data_room.services.company_names import parse_company_name

    parsed = parse_company_name("Acme Holding GmbH & Co. KG (Konzern)")

    assert parsed == {
        "normalized_name": "acme holding",
        "legal_form": "GmbH & Co. KG",
        "is_consolidated": True,
    }


def test_company_name_similarity_scores_identical_and_mismatched_names():
    from data_room.services.company_names import company_name_similarity

    assert company_name_similarity("Acme GmbH", "ACME GmbH") == 1.0
    # Different legal forms are a hard mismatch.
    assert company_name_similarity("Acme GmbH", "Acme AG") == 0.0
