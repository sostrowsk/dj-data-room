from data_room.conf import get_client_company_model
from data_room.services.company_names import company_name_similarity

ClientCompany = get_client_company_model()


def find_similar_client(name, threshold=0.85):
    """Return (client, score) if a similar-but-not-exact ClientCompany exists."""
    best_match = None
    best_score = 0.0

    for client in ClientCompany.objects.filter(is_active=True):
        score = company_name_similarity(name, client.company)
        if score >= threshold and score > best_score:
            best_match = client
            best_score = score

    if best_match:
        return best_match, best_score
    return None
