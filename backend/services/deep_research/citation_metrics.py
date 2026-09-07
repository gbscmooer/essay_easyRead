"""Collection-local citation statistics, with no provider, database or LLM calls.

The trend test conditions two independent Poisson counts on their sum, giving
an exact binomial test (Przyborowski & Wilenski, Biometrika 31, 1940,
doi:10.1093/biomet/31.3-4.313). Two fixed, disjoint two-year windows avoid
selecting a peak after observing the counts. Benjamini-Hochberg (1995,
doi:10.1111/j.2517-6161.1995.tb02031.x) adjusts all eligible papers together.
Poisson independence/constant rates within each window are assumptions, not
verified properties of citations; overdispersion and dependent papers can
invalidate nominal error control. This is a rate-change test, not burst detection.

Input history: counts_by_year=[{"year": 2025, "cited_by_count": 3}, ...].
Absent years are UNKNOWN, including for a list named counts_by_year. A source
adapter may explicitly supply counts_by_year_metadata={"source": "openalex",
"missing_years": "zero", "start_year": 2020, "end_year": 2025} to assert
complete sparse coverage (inclusive bounds). Zero filling is restricted to
that interval. A provider name alone never proves coverage or freshness.
Missing/None history remains unknown even with metadata; an explicit empty
list plus valid zero-coverage metadata can represent all-zero history.

Only years before now_year and strictly after publication are full exposure
years: publication month is unknown, so the publication year is excluded too.
The recent window is [now_year-2, now_year-1]; baseline is the preceding two
years. Incomplete windows are not shortened, interpolated or tested.

Requires scipy>=1.11 (binomtest, false_discovery_control, rankdata). No optional
heuristic fallback: a missing dependency should be fixed during installation.
"""

from collections import defaultdict
from numbers import Integral

from scipy.stats import binomtest, false_discovery_control, rankdata


FDR_ALPHA = 0.05
MIN_PERCENTILE_COHORT = 20
HIGHLY_CITED_PERCENTILE = 95.0
_MAX_COUNT = 2**53 - 1


def _integer(value, *, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, Integral):
        return None
    return int(value) if minimum <= value <= maximum else None


def _history(paper: dict, now_year: int, meta: dict) -> dict[int, int] | None:
    raw = paper.get("counts_by_year")
    if raw is None:
        meta["status"] = "missing_history"
        return None
    if not isinstance(raw, list):
        meta.update(status="invalid_history", reason="history_must_be_a_list")
        return None

    coverage = paper.get("counts_by_year_metadata")
    if coverage is None:
        coverage = {}
    if not isinstance(coverage, dict):
        meta.update(status="invalid_history", reason="invalid_coverage_metadata")
        return None
    source = coverage.get("source")
    meta["history_source"] = source if isinstance(source, str) else None
    policy = coverage.get("missing_years", "unknown")
    meta["missing_year_policy"] = policy if isinstance(policy, str) else "invalid"
    if policy not in ("unknown", "zero"):
        meta.update(status="invalid_history", reason="invalid_missing_year_policy")
        return None
    start = _integer(coverage.get("start_year"), minimum=1, maximum=9999)
    end = _integer(coverage.get("end_year"), minimum=1, maximum=9999)
    if policy == "zero" and (start is None or end is None or start > end):
        meta.update(status="invalid_history", reason="zero_policy_requires_coverage_bounds")
        return None
    if policy == "zero":
        meta["zero_coverage"] = [start, end]

    counts: dict[int, int] = {}
    for row in raw:
        if not isinstance(row, dict):
            meta.update(status="invalid_history", reason="invalid_history_row")
            return None
        year = _integer(row.get("year"), minimum=1, maximum=9999)
        if year is None:
            meta.update(status="invalid_history", reason="invalid_history_year")
            return None
        if year >= now_year:
            meta["ignored_years"].append(year)
            continue
        count = _integer(row.get("cited_by_count"), minimum=0, maximum=_MAX_COUNT)
        if count is None or year in counts:
            reason = "duplicate_history_year" if year in counts else "invalid_history_count"
            meta.update(status="invalid_history", reason=reason)
            return None
        counts[year] = count

    meta["ignored_years"] = sorted(set(meta["ignored_years"]))
    # Fill only the four required years, never outside the asserted coverage.
    if policy == "zero":
        for year in range(now_year - 4, now_year):
            if start <= year <= end and year not in counts:
                counts[year] = 0
                meta["zero_filled_years"].append(year)
    return counts


def _annotate_one(paper: dict, now_year: int) -> dict:
    paper.update(citation_velocity=None, recent_citations=None,
                 citation_velocity_2y=None, trajectory=None, surge=False,
                 citation_percentile=None, highly_cited=False)
    meta = {
        "algorithm": "conditional_poisson_rate_test",
        "version": 1,
        "implementation": "scipy.stats.binomtest",
        "reference": "10.1093/biomet/31.3-4.313",
        "alternative": "two-sided",
        "adjustment": "benjamini_hochberg",
        "adjustment_implementation": "scipy.stats.false_discovery_control",
        "adjustment_scope": "eligible_papers_in_supplied_collection",
        "alpha": FDR_ALPHA,
        "now_year": now_year,
        "baseline_years": [now_year - 4, now_year - 3],
        "recent_years": [now_year - 2, now_year - 1],
        "exposure_years": {"baseline": 2, "recent": 2},
        "publication_year_excluded": True,
        "history_source": None,
        "missing_year_policy": "unknown",
        "zero_filled_years": [],
        "ignored_years": [],
        "missing_years": [],
        "status": "not_tested",
        "reason": None,
        "baseline_citations": None,
        "recent_citations": None,
        "rate_ratio": None,
        "rate_ratio_status": "unavailable",
        "p_value": None,
        "p_adjusted": None,
        "tests_in_family": 0,
        "significant": False,
        "interpretation": "insufficient_information",
        "assumptions": "independent_poisson_counts_constant_rate_within_each_window",
        "fdr_assumptions": "independent_or_positive_regression_dependent_tests",
        "citation_velocity_basis": "reported_total_divided_by_max_1_calendar_age",
        "citation_velocity_includes_partial_year": True,
        "citation_velocity_is_historical_estimate": False,
    }
    paper["citation_metrics"] = meta
    year = _integer(paper.get("year"), minimum=1, maximum=now_year)
    total = _integer(paper.get("citation_count"), minimum=0, maximum=_MAX_COUNT)
    if year is not None and total is not None:
        # Compatibility-only descriptive rate; never used for trend inference.
        paper["citation_velocity"] = round(total / max(1, now_year - year), 2)

    counts = _history(paper, now_year, meta)
    if counts is None:
        return meta
    if year is None:
        meta["status"] = "invalid_publication_year"
        return meta
    if any(count > 0 for y, count in counts.items() if y < year):
        meta.update(status="invalid_history", reason="positive_count_before_publication")
        return meta

    required = meta["baseline_years"] + meta["recent_years"]
    meta["missing_years"] = [y for y in required if y not in counts]
    if year < meta["recent_years"][0] and all(y in counts for y in meta["recent_years"]):
        recent = sum(counts[y] for y in meta["recent_years"])
        paper["recent_citations"] = meta["recent_citations"] = recent
        paper["citation_velocity_2y"] = round(recent / 2, 2)
    if year >= required[0]:
        meta["status"] = "insufficient_history"
        return meta
    if meta["missing_years"]:
        meta["status"] = "incomplete_history"
        return meta

    baseline = sum(counts[y] for y in meta["baseline_years"])
    recent = paper["recent_citations"]
    meta["baseline_citations"] = baseline
    if baseline + recent > _MAX_COUNT:
        meta.update(status="invalid_history", reason="test_count_out_of_range")
        return meta
    if baseline:
        meta.update(rate_ratio=recent / baseline, rate_ratio_status="finite")
    else:
        # JSON cannot represent infinity or 0/0; preserve their distinct meanings.
        meta["rate_ratio_status"] = "infinite" if recent else "undefined_all_zero"
    meta["status"] = "tested" if baseline + recent else "all_zero"
    meta["p_value"] = (
        float(binomtest(recent, baseline + recent, p=0.5, alternative="two-sided").pvalue)
        if baseline + recent else 1.0
    )
    return meta


def _percentiles(papers: list[dict], now_year: int) -> None:
    cohorts: dict[int, list[dict]] = defaultdict(list)
    for paper in papers:
        year = _integer(paper.get("year"), minimum=1, maximum=now_year)
        total = _integer(paper.get("citation_count"), minimum=0, maximum=_MAX_COUNT)
        paper["citation_metrics"]["percentile"] = {
            "algorithm": "sample_midrank",
            "implementation": "scipy.stats.rankdata(method=average)",
            "formula": "100 * (average_rank - 0.5) / cohort_size",
            "scope": "supplied_collection_same_publication_year",
            "field_normalized": False,
            "population_percentile": False,
            "cohort_size": 0,
            "minimum_badge_cohort": MIN_PERCENTILE_COHORT,
            "badge_threshold": HIGHLY_CITED_PERCENTILE,
            "status": "invalid_year_or_total",
        }
        if year is not None and total is not None:
            cohorts[year].append(paper)
    for cohort in cohorts.values():
        n = len(cohort)
        ranks = rankdata([p["citation_count"] for p in cohort], method="average")
        for paper, rank in zip(cohort, ranks):
            percentile = 100 * (float(rank) - 0.5) / n
            paper["citation_percentile"] = round(percentile, 1)
            paper["highly_cited"] = bool(
                n >= MIN_PERCENTILE_COHORT and percentile >= HIGHLY_CITED_PERCENTILE
                and paper["citation_count"] > 0
            )
            paper["citation_metrics"]["percentile"].update(
                cohort_size=n, status="ok" if n >= MIN_PERCENTILE_COHORT else "small_sample",
            )


def annotate_citation_metrics(papers: list[dict], now_year: int) -> None:
    """Mutate the seven legacy fields and citation_metrics provenance; return None.

    Call once on the complete, deduplicated collection, before UI filtering.
    Repeated dictionary references are processed once. Distinct dicts must be
    deduplicated by the caller's canonical paper identity. Invalid paper data
    produces null metrics/false flags with a reason, without poisoning peers.
    Invalid container types or now_year raise before mutating any paper.

    trajectory is rising/declining only at BH-adjusted p <= 0.05. Otherwise a
    tested paper is steady (no detected change, NOT evidence of equivalence).
    Untested papers have trajectory=None. surge means significant increase,
    at any publication age, never a lifetime-velocity or fold-change threshold.
    citation_metrics contains p_value, p_adjusted, rate_ratio and its status;
    an infinite rate ratio is null plus rate_ratio_status="infinite".

    citation_velocity and percentile use the supplied citation_count snapshot,
    which may include this year's citations or a different provider's counts.
    now_year controls history windows, not historical reconstruction of totals.
    Percentiles are descriptive ranks in this sample, not ESI or global ranks.
    """
    valid_now = _integer(now_year, minimum=5, maximum=9999)
    if valid_now is None:
        raise ValueError("now_year must be an integer in [5, 9999]")
    if not isinstance(papers, list) or any(not isinstance(p, dict) for p in papers):
        raise TypeError("papers must be a list of dictionaries")
    unique = list({id(p): p for p in papers}.values())
    tested = []
    for paper in unique:
        meta = _annotate_one(paper, valid_now)
        if meta["p_value"] is not None:
            tested.append(paper)
    for paper in unique:
        paper["citation_metrics"]["tests_in_family"] = len(tested)
    if tested:
        adjusted = false_discovery_control(
            [p["citation_metrics"]["p_value"] for p in tested], method="bh",
        )
        for paper, p_adjusted in zip(tested, adjusted):
            meta = paper["citation_metrics"]
            meta["p_adjusted"] = float(p_adjusted)
            meta["significant"] = bool(p_adjusted <= FDR_ALPHA)
            paper["trajectory"] = "steady"
            meta["interpretation"] = "no_detected_change_not_equivalence"
            if meta["significant"]:
                rising = meta["recent_citations"] > meta["baseline_citations"]
                paper["trajectory"] = "rising" if rising else "declining"
                paper["surge"] = rising
                meta["interpretation"] = "significant_rate_" + ("increase" if rising else "decrease")
    _percentiles(unique, valid_now)
