"""Tests for fuzzy search utilities."""
import pytest
from src.utils.fuzzy_search import (
    ratio, partial_ratio, token_sort_ratio, best_match,
    generate_name_variations, guess_email_domain,
    build_email_search_queries, match_contact, is_likely_same_entity,
)

class TestStringSimilarity:
    def test_exact_match(self):
        assert ratio("Sol Santoro", "Sol Santoro") == 100.0

    def test_close_typo(self):
        score = ratio("Santora", "Santoro")
        assert score >= 80.0

    def test_partial_match_first_name(self):
        score = partial_ratio("Sol", "Sol Santoro")
        assert score >= 90.0

    def test_partial_match_last_name(self):
        score = partial_ratio("Santoro", "Sol Santoro")
        assert score >= 90.0

    def test_token_sort_handles_reordering(self):
        score = token_sort_ratio("Santoro Sol", "Sol Santoro")
        assert score >= 95.0

    def test_case_insensitive(self):
        assert ratio("sol santoro", "Sol Santoro") >= 95.0

    def test_company_name_variation(self):
        score = ratio("Intuitive Machines", "Intuition Machines")
        assert score >= 70.0

    def test_completely_different(self):
        score = ratio("Alice Johnson", "Bob Williams")
        assert score < 40.0

class TestBestMatch:
    def test_finds_closest(self):
        choices = ["Sol Santoro", "John Smith", "Sarah Connor", "Solomon King"]
        results = best_match("Sol", choices, threshold=50.0)
        assert len(results) >= 1
        assert results[0][0].lower() in ["sol santoro", "solomon king"]

    def test_respects_threshold(self):
        choices = ["completely different", "nothing related"]
        results = best_match("Sol Santoro", choices, threshold=80.0)
        assert len(results) == 0

class TestGenerateNameVariations:
    def test_person_name(self):
        vars = generate_name_variations("Sol Santoro")
        assert "Sol Santoro" in vars
        assert "Sol" in vars
        assert "Santoro" in vars
        assert "sol.santoro" in vars
        assert "ssantoro" in vars

    def test_single_name(self):
        vars = generate_name_variations("Sol")
        assert "Sol" in vars
        assert len(vars) >= 1

    def test_company_name(self):
        vars = generate_name_variations("Intuition Machines")
        assert "Intuition Machines" in vars
        assert "Intuition" in vars
        assert "intuitionmachines" in vars

    def test_empty(self):
        assert generate_name_variations("") == []
        assert generate_name_variations("  ") == []

class TestGuessEmailDomain:
    def test_two_word_company(self):
        domains = guess_email_domain("Intuition Machines")
        assert "intuitionmachines.com" in domains
        assert "intuition-machines.com" in domains
        assert "intuition.com" in domains

    def test_single_word_company(self):
        domains = guess_email_domain("Google")
        assert "google.com" in domains

    def test_includes_tech_suffixes(self):
        domains = guess_email_domain("Intuition Machines")
        has_io = any(d.endswith(".io") for d in domains)
        has_ai = any(d.endswith(".ai") for d in domains)
        assert has_io or has_ai

    def test_special_chars(self):
        domains = guess_email_domain("McKinsey & Company")
        assert any("mckinsey" in d for d in domains)

class TestBuildEmailSearchQueries:
    def test_person_name_only(self):
        queries = build_email_search_queries(person_name="Sol Santoro")
        assert any('"Sol Santoro"' in q for q in queries)
        assert any("from:Sol" in q for q in queries)
        assert any("from:Santoro" in q for q in queries)
        assert any("from:sol.santoro" in q for q in queries)

    def test_company_only(self):
        queries = build_email_search_queries(company_name="Intuition Machines")
        assert any("@intuitionmachines.com" in q for q in queries)
        assert any("Intuition" in q for q in queries)

    def test_person_and_company(self):
        queries = build_email_search_queries(
            person_name="Sol Santoro", company_name="Intuition Machines",
        )
        assert any("sol.santoro@intuitionmachines.com" in q for q in queries)

    def test_email_address(self):
        queries = build_email_search_queries(email_address="sol@example.com")
        assert queries[0] == "from:sol@example.com OR to:sol@example.com"

    def test_no_duplicates(self):
        queries = build_email_search_queries(person_name="Sol")
        assert len(queries) == len(set(q.lower() for q in queries))

    def test_empty(self):
        queries = build_email_search_queries()
        assert queries == []

class TestMatchContact:
    @pytest.fixture
    def contacts(self):
        return [
            {"name": "Sol Santoro", "email": "sol.santoro@intuitionmachines.com",
             "description": "Email contact: sol.santoro@intuitionmachines.com", "aliases": []},
            {"name": "John Smith", "email": "john@example.com",
             "description": "Email contact: john@example.com", "aliases": ["Johnny"]},
            {"name": "Intuition Machines", "email": "",
             "description": "Organization from email domain: intuitionmachines.com",
             "aliases": ["Intuitive Machines"]},
        ]

    def test_exact_name_match(self, contacts):
        results = match_contact("Sol Santoro", contacts)
        assert len(results) >= 1
        assert results[0][0]["name"] == "Sol Santoro"
        assert results[0][1] >= 90.0

    def test_partial_first_name(self, contacts):
        results = match_contact("Sol", contacts)
        assert len(results) >= 1
        assert results[0][0]["name"] == "Sol Santoro"

    def test_partial_last_name(self, contacts):
        results = match_contact("Santoro", contacts)
        assert len(results) >= 1
        assert results[0][0]["name"] == "Sol Santoro"

    def test_misspelled_name(self, contacts):
        results = match_contact("Santora", contacts, threshold=50.0)
        assert len(results) >= 1
        assert results[0][0]["name"] == "Sol Santoro"

    def test_email_match(self, contacts):
        results = match_contact("sol.santoro@intuitionmachines.com", contacts)
        assert len(results) >= 1
        assert results[0][0]["name"] == "Sol Santoro"

    def test_company_name_variation(self, contacts):
        results = match_contact("Intuitive Machines", contacts, threshold=50.0)
        assert len(results) >= 1
        assert any(c["name"] == "Intuition Machines" for c, _ in results)

    def test_alias_match(self, contacts):
        results = match_contact("Johnny", contacts, threshold=50.0)
        assert len(results) >= 1
        assert results[0][0]["name"] == "John Smith"

    def test_no_match(self, contacts):
        results = match_contact("Xyz Zzzzz Nonexistent", contacts, threshold=80.0)
        assert len(results) == 0

class TestIsLikelySameEntity:
    def test_same_name(self):
        assert is_likely_same_entity("Sol Santoro", "Sol Santoro")

    def test_typo(self):
        assert is_likely_same_entity("Intuitive Machines", "Intuition Machines")

    def test_reordered(self):
        assert is_likely_same_entity("Santoro, Sol", "Sol Santoro")

    def test_different_entities(self):
        assert not is_likely_same_entity("Alice Johnson", "Bob Williams")
