"""Fuzzy search utilities for partial matching of names, emails, and domains.

Covers common failure cases when searching for contacts:
- Typos / misspellings (e.g. "Santora" vs "Santoro")
- Partial names (e.g. "Sol" matching "Sol Santoro")
- Company name variations (e.g. "Intuitive Machines" vs "Intuition Machines")
- Domain guessing from company names (e.g. "Intuition Machines" -> intuitionmachines.com)
- Name<->email mapping (e.g. first.last@domain.com)
"""
import re
from typing import List, Optional, Tuple


def _normalize(text: str) -> str:
    """Lowercase, strip, collapse whitespace."""
    return re.sub(r"\s+", " ", text.strip().lower())


# ---------------------------------------------------------------------------
# Name / string similarity (pure Python fallback when rapidfuzz unavailable)
# ---------------------------------------------------------------------------

try:
    from rapidfuzz import fuzz as _fuzz, process as _process

    def ratio(a: str, b: str) -> float:
        """0-100 similarity score between two strings."""
        return _fuzz.ratio(_normalize(a), _normalize(b))

    def partial_ratio(a: str, b: str) -> float:
        """0-100 partial match score (best substring alignment)."""
        return _fuzz.partial_ratio(_normalize(a), _normalize(b))

    def token_sort_ratio(a: str, b: str) -> float:
        """0-100 token-sorted similarity (word order doesn't matter)."""
        return _fuzz.token_sort_ratio(_normalize(a), _normalize(b))

    def best_match(query: str, choices: List[str], threshold: float = 60.0) -> List[Tuple[str, float]]:
        """Return choices scoring above threshold, sorted best first."""
        results = _process.extract(
            _normalize(query),
            [_normalize(c) for c in choices],
            scorer=_fuzz.WRatio,
            limit=10,
        )
        norm_to_orig = {}
        for c in choices:
            norm_to_orig.setdefault(_normalize(c), c)
        return [
            (norm_to_orig.get(match, match), score)
            for match, score, _ in results
            if score >= threshold
        ]

except ImportError:
    import difflib

    def ratio(a: str, b: str) -> float:
        return difflib.SequenceMatcher(None, _normalize(a), _normalize(b)).ratio() * 100

    def partial_ratio(a: str, b: str) -> float:
        na, nb = _normalize(a), _normalize(b)
        if len(na) > len(nb):
            na, nb = nb, na
        best = 0.0
        for i in range(len(nb) - len(na) + 1):
            s = difflib.SequenceMatcher(None, na, nb[i:i + len(na)]).ratio() * 100
            if s > best:
                best = s
        return best if best > 0 else difflib.SequenceMatcher(None, na, nb).ratio() * 100

    def token_sort_ratio(a: str, b: str) -> float:
        sa = " ".join(sorted(_normalize(a).split()))
        sb = " ".join(sorted(_normalize(b).split()))
        return difflib.SequenceMatcher(None, sa, sb).ratio() * 100

    def best_match(query: str, choices: List[str], threshold: float = 60.0) -> List[Tuple[str, float]]:
        results = []
        nq = _normalize(query)
        for c in choices:
            score = max(ratio(nq, c), partial_ratio(nq, c), token_sort_ratio(nq, c))
            if score >= threshold:
                results.append((c, score))
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:10]


# ---------------------------------------------------------------------------
# Name parsing & variations
# ---------------------------------------------------------------------------

def generate_name_variations(name: str) -> List[str]:
    """Generate common variations of a person or company name for search."""
    name = name.strip()
    if not name:
        return []

    variations = [name]
    parts = name.split()

    for part in parts:
        if part.lower() not in variations and len(part) >= 2:
            variations.append(part)

    if len(parts) >= 2:
        variations.append(f"{parts[0].lower()}.{parts[-1].lower()}")
        variations.append(f"{parts[0][0].lower()}{parts[-1].lower()}")
        variations.append(f"{parts[0].lower()}{parts[-1][0].lower()}")

    no_spaces = name.replace(" ", "").lower()
    if no_spaces not in [v.lower() for v in variations]:
        variations.append(no_spaces)

    return variations


def guess_email_domain(company_name: str) -> List[str]:
    """Guess possible email domains from a company name."""
    name = re.sub(r"[&+]", "and", company_name.strip())
    name = re.sub(r"[^a-zA-Z0-9\s-]", "", name)
    parts = name.lower().split()

    if not parts:
        return []

    domains = []
    domains.append(f"{''.join(parts)}.com")
    if len(parts) > 1:
        domains.append(f"{'-'.join(parts)}.com")
    domains.append(f"{parts[0]}.com")
    for suffix in [".io", ".ai", ".co"]:
        domains.append(f"{''.join(parts)}{suffix}")

    return domains


# ---------------------------------------------------------------------------
# Gmail query builder with fuzzy strategies
# ---------------------------------------------------------------------------

def build_email_search_queries(
    person_name: Optional[str] = None,
    company_name: Optional[str] = None,
    email_address: Optional[str] = None,
) -> List[str]:
    """Generate multiple Gmail search queries to find emails from/about a person.

    Returns a list of queries ordered from most specific to broadest.
    The caller should try each query until results are found.
    """
    queries = []

    if email_address:
        queries.append(f"from:{email_address} OR to:{email_address}")

    if person_name:
        name_vars = generate_name_variations(person_name)
        queries.append(f'"{person_name}"')
        for var in name_vars[:5]:
            queries.append(f"from:{var}")
        for part in person_name.split():
            if len(part) >= 3:
                queries.append(part)

    if company_name:
        domains = guess_email_domain(company_name)
        for domain in domains[:3]:
            queries.append(f"from:@{domain}")
        for part in company_name.split():
            if len(part) >= 3:
                queries.append(part)

    if person_name and company_name:
        parts = person_name.lower().split()
        domains = guess_email_domain(company_name)
        for domain in domains[:2]:
            if len(parts) >= 2:
                queries.append(f"from:{parts[0]}.{parts[-1]}@{domain}")
                queries.append(f"from:{parts[0][0]}{parts[-1]}@{domain}")

    seen = set()
    unique = []
    for q in queries:
        if q.lower() not in seen:
            seen.add(q.lower())
            unique.append(q)

    return unique


# ---------------------------------------------------------------------------
# Contact matching
# ---------------------------------------------------------------------------

def match_contact(
    query: str,
    contacts: List[dict],
    threshold: float = 55.0,
) -> List[Tuple[dict, float]]:
    """Match a fuzzy query against a list of contact dicts."""
    query_norm = _normalize(query)
    results = []

    for contact in contacts:
        best_score = 0.0
        contact_name = contact.get("name", "")
        contact_email = contact.get("email", "")
        contact_desc = contact.get("description", "")
        aliases = contact.get("aliases", [])
        if isinstance(aliases, str):
            try:
                import json
                aliases = json.loads(aliases)
            except Exception:
                aliases = []

        if contact_name:
            name_score = max(
                ratio(query, contact_name),
                partial_ratio(query, contact_name),
                token_sort_ratio(query, contact_name),
            )
            best_score = max(best_score, name_score)
            for part in contact_name.lower().split():
                if query_norm in part or part in query_norm:
                    best_score = max(best_score, 85.0)

        if contact_email:
            prefix = contact_email.split("@")[0].replace(".", " ").replace("-", " ")
            email_score = max(partial_ratio(query, prefix), token_sort_ratio(query, prefix))
            best_score = max(best_score, email_score * 0.9)
            if query_norm == contact_email.lower():
                best_score = 100.0

        if contact_desc:
            desc_score = partial_ratio(query, contact_desc)
            best_score = max(best_score, desc_score * 0.8)

        for alias in aliases:
            if isinstance(alias, str):
                alias_score = max(ratio(query, alias), partial_ratio(query, alias))
                best_score = max(best_score, alias_score)

        if best_score >= threshold:
            results.append((contact, best_score))

    results.sort(key=lambda x: x[1], reverse=True)
    return results


def is_likely_same_entity(name_a: str, name_b: str, threshold: float = 75.0) -> bool:
    """Check if two names likely refer to the same entity."""
    score = max(
        ratio(name_a, name_b),
        token_sort_ratio(name_a, name_b),
        partial_ratio(name_a, name_b) * 0.95,
    )
    return score >= threshold
