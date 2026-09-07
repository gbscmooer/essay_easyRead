"""Offline regression tests for exact citation rate inference and its data contract."""

from copy import deepcopy
import json

import pytest
from scipy.stats import binomtest, false_discovery_control

from backend.services.deep_research.citation_metrics import annotate_citation_metrics


def _paper(counts=(1, 1, 20, 20), *, year=2010, total=100):
    return {
        "year": year,
        "citation_count": total,
        "counts_by_year": [
            {"year": y, "cited_by_count": count}
            for y, count in zip(range(2022, 2026), counts)
        ],
    }


def _zero_coverage(paper, start=2022, end=2025):
    paper["counts_by_year_metadata"] = {
        "source": "openalex", "missing_years": "zero",
        "start_year": start, "end_year": end,
    }
    return paper


def test_exact_test_rates_complete_windows_and_legacy_fields():
    paper = _paper()
    original_history = deepcopy(paper["counts_by_year"])
    assert annotate_citation_metrics([paper], 2026) is None
    meta = paper["citation_metrics"]
    assert paper["citation_velocity"] == 6.25
    assert paper["recent_citations"] == 40
    assert paper["citation_velocity_2y"] == 20
    assert paper["trajectory"] == "rising"
    assert paper["surge"] is True  # Old papers can have a significant increase.
    assert meta["rate_ratio"] == 20
    assert meta["p_value"] == pytest.approx(binomtest(40, 42).pvalue)
    assert meta["p_adjusted"] == meta["p_value"]
    assert meta["baseline_years"] == [2022, 2023]
    assert meta["recent_years"] == [2024, 2025]
    assert meta["algorithm"] == "conditional_poisson_rate_test"
    assert meta["adjustment"] == "benjamini_hochberg"
    assert paper["counts_by_year"] == original_history


@pytest.mark.parametrize("counts,trajectory,ratio", [
    ((20, 20, 1, 1), "declining", 0.05),
    ((2, 2, 3, 3), "steady", 1.5),
    ((3, 3, 2, 2), "steady", 2 / 3),
    ((10, 10, 10, 10), "steady", 1),
    ((5, 5, 0, 0), "declining", 0),
])
def test_direction_and_effect_without_fixed_ratio_thresholds(counts, trajectory, ratio):
    paper = _paper(counts)
    annotate_citation_metrics([paper], 2026)
    assert paper["trajectory"] == trajectory
    assert paper["surge"] is False
    assert paper["citation_metrics"]["rate_ratio"] == pytest.approx(ratio)
    if trajectory == "steady":
        assert paper["citation_metrics"]["interpretation"] == "no_detected_change_not_equivalence"


@pytest.mark.parametrize("recent,trajectory,p_value", [(1, "steady", 1.0), (6, "rising", 0.03125)])
def test_zero_baseline_exact_small_counts_and_json_safe_infinity(recent, trajectory, p_value):
    paper = _paper((0, 0, 0, recent))
    annotate_citation_metrics([paper], 2026)
    meta = paper["citation_metrics"]
    assert meta["p_value"] == p_value
    assert meta["rate_ratio"] is None
    assert meta["rate_ratio_status"] == "infinite"
    assert paper["trajectory"] == trajectory
    assert json.loads(json.dumps(paper, allow_nan=False)) == paper


def test_bh_uses_collection_and_all_zero_null_tests_but_excludes_untestable():
    candidate = _paper((0, 0, 3, 3))  # Raw p=0.03125; adjusted p=0.09375.
    null = _paper((4, 4, 4, 4))
    zeros = _paper((0, 0, 0, 0))
    missing = {"year": 2010, "citation_count": 10000}
    papers = [candidate, null, zeros, missing]
    annotate_citation_metrics(papers, 2026)
    assert candidate["citation_metrics"]["p_value"] == 0.03125
    assert candidate["citation_metrics"]["p_adjusted"] == 0.09375
    assert candidate["trajectory"] == "steady"
    assert not any(p["surge"] for p in papers)
    assert all(p["citation_metrics"]["tests_in_family"] == 3 for p in papers)
    assert zeros["citation_metrics"]["p_value"] == 1
    assert zeros["citation_metrics"]["rate_ratio_status"] == "undefined_all_zero"
    assert zeros["citation_metrics"]["status"] == "all_zero"
    assert missing["trajectory"] is None
    assert missing["citation_metrics"]["p_adjusted"] is None


def test_bh_matches_scipy_and_is_order_invariant():
    papers = [_paper(c) for c in [(0, 0, 6, 6), (0, 0, 3, 3), (4, 4, 4, 4), (30, 30, 1, 1)]]
    annotate_citation_metrics(papers, 2026)
    expected = false_discovery_control([binomtest(sum(c["cited_by_count"] for c in p["counts_by_year"][2:]),
                                                 sum(c["cited_by_count"] for c in p["counts_by_year"])).pvalue
                                        for p in papers], method="bh")
    assert [p["citation_metrics"]["p_adjusted"] for p in papers] == pytest.approx(expected)
    before = deepcopy(papers)
    annotate_citation_metrics(list(reversed(papers)), 2026)
    assert papers == before


def test_current_and_future_rows_never_enter_history_metrics():
    paper = _paper()
    annotate_citation_metrics([paper], 2026)
    before = deepcopy(paper)
    paper["counts_by_year"] += [
        {"year": 2026, "cited_by_count": 10000000},
        {"year": 2027, "cited_by_count": 10000000},
        {"year": 2028, "cited_by_count": "invalid but out of window"},
    ]
    annotate_citation_metrics([paper], 2026)
    assert paper["citation_metrics"].pop("ignored_years") == [2026, 2027, 2028]
    before["citation_metrics"].pop("ignored_years")
    assert paper["citation_metrics"] == before["citation_metrics"]
    assert paper["recent_citations"] == 40


def test_missing_year_is_unknown_even_when_source_is_openalex():
    paper = _paper((0, 0, 0, 6))
    paper["counts_by_year"].pop(0)
    paper["counts_by_year_metadata"] = {"source": "openalex"}
    annotate_citation_metrics([paper], 2026)
    assert paper["recent_citations"] == 6
    assert paper["citation_metrics"]["missing_years"] == [2022]
    assert paper["citation_metrics"]["status"] == "incomplete_history"
    assert paper["citation_metrics"]["p_value"] is None
    assert paper["trajectory"] is None
    assert paper["surge"] is False


def test_missing_recent_year_cannot_be_divided_by_two_as_zero():
    paper = _paper()
    paper["counts_by_year"].pop()
    annotate_citation_metrics([paper], 2026)
    assert paper["recent_citations"] is None
    assert paper["citation_velocity_2y"] is None
    assert paper["trajectory"] is None


def test_explicit_sparse_zero_semantics_equal_dense_history_without_mutating_input():
    sparse = _zero_coverage(_paper((0, 0, 0, 6)))
    sparse["counts_by_year"] = [{"year": 2025, "cited_by_count": 6}]
    dense = _paper((0, 0, 0, 6))
    original = deepcopy(sparse["counts_by_year"])
    annotate_citation_metrics([sparse, dense], 2026)
    assert sparse["citation_metrics"]["zero_filled_years"] == [2022, 2023, 2024]
    assert sparse["citation_metrics"]["history_source"] == "openalex"
    assert sparse["citation_metrics"]["p_value"] == dense["citation_metrics"]["p_value"]
    assert sparse["trajectory"] == dense["trajectory"] == "rising"
    assert sparse["counts_by_year"] == original


@pytest.mark.parametrize("start,end,missing", [(2023, 2025, [2022]), (2022, 2024, [2025])])
def test_sparse_zero_fill_does_not_extrapolate_coverage(start, end, missing):
    paper = _zero_coverage(_paper(), start, end)
    paper["counts_by_year"] = []
    annotate_citation_metrics([paper], 2026)
    assert paper["citation_metrics"]["missing_years"] == missing
    assert paper["citation_metrics"]["p_value"] is None
    assert paper["trajectory"] is None


@pytest.mark.parametrize("raw,coverage,status", [
    (None, False, "missing_history"), (None, True, "missing_history"),
    ([], False, "incomplete_history"), ([], True, "all_zero"),
])
def test_empty_history_does_not_imply_zero_without_explicit_coverage(raw, coverage, status):
    paper = _paper()
    paper["counts_by_year"] = raw
    if coverage:
        _zero_coverage(paper)
    annotate_citation_metrics([paper], 2026)
    assert paper["citation_metrics"]["status"] == status
    assert paper["recent_citations"] == (0 if status == "all_zero" else None)
    assert not paper["surge"]


@pytest.mark.parametrize("year,recent", [(2022, 40), (2023, 40), (2024, None), (2026, None)])
def test_publication_year_is_partial_exposure_and_young_papers_are_not_tested(year, recent):
    paper = _paper(year=year)
    paper["counts_by_year"] = [r for r in paper["counts_by_year"] if r["year"] >= year]
    annotate_citation_metrics([paper], 2026)
    assert paper["recent_citations"] == recent
    assert paper["citation_metrics"]["status"] == "insufficient_history"
    assert paper["trajectory"] is None
    assert paper["surge"] is False


@pytest.mark.parametrize("year", [None, True, "2020", 2020.0, -1, 0, 2027])
def test_invalid_or_future_publication_year_is_not_a_cohort_member(year):
    paper = _paper(year=year)
    annotate_citation_metrics([paper], 2026)
    assert paper["citation_velocity"] is None
    assert paper["citation_percentile"] is None
    assert paper["recent_citations"] is None
    assert paper["trajectory"] is None
    assert not paper["highly_cited"]
    assert paper["citation_metrics"]["status"] == "invalid_publication_year"


@pytest.mark.parametrize("bad", [None, True, -1, 1.5, float("nan"), float("inf"), "3", 2**60])
def test_invalid_history_counts_fail_closed_without_poisoning_peers(bad):
    invalid = _paper()
    invalid["counts_by_year"][0]["cited_by_count"] = bad
    good = _paper()
    annotate_citation_metrics([invalid, good], 2026)
    assert invalid["citation_metrics"]["reason"] == "invalid_history_count"
    assert invalid["recent_citations"] is None
    assert invalid["surge"] is False
    assert good["surge"] is True
    assert good["citation_metrics"]["tests_in_family"] == 1


@pytest.mark.parametrize("history,reason", [
    ({2022: 3}, "history_must_be_a_list"),
    ([None], "invalid_history_row"),
    ([{"year": "2022", "cited_by_count": 3}], "invalid_history_year"),
    ([{"year": True, "cited_by_count": 3}], "invalid_history_year"),
    ([{"year": 2022}], "invalid_history_count"),
    ([{"year": 2022, "cited_by_count": 3}] * 2, "duplicate_history_year"),
    ([{"year": 2009, "cited_by_count": 1}], "positive_count_before_publication"),
])
def test_malformed_history_reasons(history, reason):
    paper = _paper()
    paper["counts_by_year"] = history
    annotate_citation_metrics([paper], 2026)
    assert paper["citation_metrics"]["status"] == "invalid_history"
    assert paper["citation_metrics"]["reason"] == reason
    assert paper["trajectory"] is None


@pytest.mark.parametrize("metadata", [
    [], {"missing_years": []}, {"missing_years": "interpolate"},
    {"missing_years": "zero"},
    {"missing_years": "zero", "start_year": 2025, "end_year": 2022},
    {"missing_years": "zero", "start_year": "2022", "end_year": 2025},
])
def test_invalid_coverage_never_authorizes_zero_fill(metadata):
    paper = _paper()
    paper["counts_by_year_metadata"] = metadata
    annotate_citation_metrics([paper], 2026)
    assert paper["citation_metrics"]["status"] == "invalid_history"
    assert paper["trajectory"] is None


@pytest.mark.parametrize("bad_total", [None, True, -1, "100", 3.5, float("nan"), float("inf")])
def test_invalid_lifetime_total_does_not_disable_valid_independent_yearly_history(bad_total):
    paper = _paper(total=bad_total)
    annotate_citation_metrics([paper], 2026)
    assert paper["citation_velocity"] is None
    assert paper["citation_percentile"] is None
    assert paper["highly_cited"] is False
    assert paper["surge"] is True


def test_sample_midrank_ties_small_cohorts_and_year_separation():
    papers = [{"year": 2020, "citation_count": c} for c in [0, 10, 10, 30]]
    other_year = {"year": 2021, "citation_count": 100000}
    unknown_total = {"year": 2020}
    annotate_citation_metrics(papers + [other_year, unknown_total], 2026)
    assert [p["citation_percentile"] for p in papers] == [12.5, 50, 50, 87.5]
    assert other_year["citation_percentile"] == 50
    assert unknown_total["citation_percentile"] is None
    assert not any(p["highly_cited"] for p in papers + [other_year])
    assert papers[0]["citation_metrics"]["percentile"]["cohort_size"] == 4
    assert papers[0]["citation_metrics"]["percentile"]["population_percentile"] is False


@pytest.mark.parametrize("n,expected", [(10, False), (19, False), (20, True), (100, True)])
def test_badge_requires_sample_size_and_sample_top_five_percent(n, expected):
    papers = [{"year": 2020, "citation_count": c} for c in range(n)]
    annotate_citation_metrics(papers, 2026)
    assert papers[-1]["highly_cited"] is expected
    assert papers[-1]["citation_percentile"] == round(100 * (n - 0.5) / n, 1)


@pytest.mark.parametrize("count", [0, 100])
def test_all_tied_cohort_is_midrank_not_highly_cited(count):
    papers = [{"year": 2020, "citation_count": count} for _ in range(30)]
    annotate_citation_metrics(papers, 2026)
    assert all(p["citation_percentile"] == 50 and not p["highly_cited"] for p in papers)


def test_repeated_dictionary_references_do_not_inflate_test_or_cohort_size():
    paper = _paper()
    annotate_citation_metrics([paper] * 20, 2026)
    assert paper["citation_metrics"]["tests_in_family"] == 1
    assert paper["citation_metrics"]["percentile"]["cohort_size"] == 1
    assert not paper["highly_cited"]


def test_reannotation_clears_stale_results_when_history_is_removed():
    paper = _paper()
    annotate_citation_metrics([paper], 2026)
    paper.pop("counts_by_year")
    annotate_citation_metrics([paper], 2026)
    assert paper["surge"] is False
    assert paper["trajectory"] is None
    assert paper["recent_citations"] is None
    assert paper["citation_metrics"]["p_adjusted"] is None
    assert paper["citation_metrics"]["rate_ratio"] is None


@pytest.mark.parametrize("now_year", [None, True, "2026", 2026.5, 0, 10000])
def test_invalid_now_year_raises_before_mutation(now_year):
    paper = _paper()
    original = deepcopy(paper)
    with pytest.raises(ValueError):
        annotate_citation_metrics([paper], now_year)
    assert paper == original


def test_invalid_collection_raises_before_mutation_and_empty_collection_is_noop():
    paper = _paper()
    original = deepcopy(paper)
    with pytest.raises(TypeError):
        annotate_citation_metrics([paper, None], 2026)
    with pytest.raises(TypeError):
        annotate_citation_metrics({}, 2026)
    assert paper == original
    assert annotate_citation_metrics([], 2026) is None
