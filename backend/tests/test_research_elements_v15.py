"""Generic method provenance, bounded element selection and real v15 controller."""

import asyncio
from copy import deepcopy
import pytest
from backend.models.research_core import (
    ResearchAsset,
    ResearchAnswerRevision,
    ResearchQuestion,
)
from backend.services.deep_research.research_loop.reading_v15 import (
    choose_candidates,
    validate,
)
from backend.services.deep_research.research_loop.answering_v15 import (
    check_claims,
    element_complete,
)
from backend.services.deep_research.research_loop.portfolio_v15 import validate_hints
from backend.services.deep_research.research_loop.source_refs_v15 import (
    wire_context,
    checker_payload,
    resolve_checks,
)
from backend.services.deep_research.research_loop.controller import Controller
from backend.services.deep_research.research_loop.contracts import (
    Action,
    AtomicFact,
    digest,
)
from backend.services.deep_research.research_loop.dossier import freeze
from backend.services.deep_research.research_loop.writer import build_report
from backend.tests.test_research_sources_v11 import (
    env11 as source_fixture,
    source_model,
)
from backend.tests.test_research_scoped_v14 import sample, claim, review


env15 = source_fixture


def test_late_own_elements_displace_repeated_early_background_with_fixed_four_limit():
    items = [
        (f"F{i}", {"source_id": "W1", "attribution": "background", "slot": "other"})
        for i in range(4)
    ]
    items += [
        (f"F{i + 4}", {"source_id": f"W{i + 2}", "attribution": "own", "slot": slot})
        for i, slot in enumerate(["input", "operation", "scoring", "scope"])
    ]
    mapping = {f"W{i}": ("doc", "actual original") for i in range(1, 6)}
    chosen = choose_candidates(items, mapping)
    assert len(chosen) == 4 and {x[1]["slot"] for x in chosen} == {
        "input",
        "operation",
        "scoring",
        "scope",
    }
    assert all(x[1]["attribution"] == "own" for x in chosen)


def test_slot_selection_is_per_document_and_not_model_output_position():
    items = []
    sources = {}
    for doc in ("a", "b"):
        for i, slot in enumerate(("input", "input", "input", "operation", "scoring")):
            wid = doc + str(i)
            sources[wid] = (doc, wid)
            items.append((wid, {"source_id": wid, "attribution": "own", "slot": slot}))
    selected = choose_candidates(items, sources)
    for doc in ("a", "b"):
        rows = [x for x in selected if sources[x[1]["source_id"]][0] == doc]
        assert len(rows) == 4 and {"input", "operation", "scoring"} <= {
            x[1]["slot"] for x in rows
        }


def hints(query="BetaLog indexed retrieval", quote="BetaLog offers indexed retrieval."):
    return {
        "next_searches": [
            {
                "source_id": "W1",
                "source_quote": quote,
                "query": query,
                "requirement_ids": ["u1"],
                "reason": "Read actual alternate mechanism",
            }
        ]
    }


def test_navigation_only_uses_real_quote_and_task_words():
    sources = {"W1": {"quote": "BetaLog offers indexed retrieval.", "asset_id": "doc"}}
    result = validate_hints(hints(), sources, [{"unit_id": "u1"}], "coverage_refresh")
    assert result[0]["source_asset_id"] == "doc" and result[0]["source_hash"]


@pytest.mark.parametrize(
    "fault",
    [
        "unknown_source",
        "fake_quote",
        "gold_name",
        "lowercase_gold",
        "unknown_unit",
        "wrong_stage",
    ],
)
def test_navigation_rejects_unseen_names_quotes_and_requirements(fault):
    raw = hints()
    stage = "coverage_refresh"
    sources = {"W1": {"quote": "BetaLog offers indexed retrieval.", "asset_id": "doc"}}
    if fault == "unknown_source":
        raw["next_searches"][0]["source_id"] = "W9"
    elif fault == "fake_quote":
        raw["next_searches"][0]["source_quote"] = "invented text"
    elif fault == "gold_name":
        raw["next_searches"][0]["query"] = "GoldNet indexed retrieval"
    elif fault == "lowercase_gold":
        raw["next_searches"][0]["query"] = "goldnet indexed retrieval"
    elif fault == "unknown_unit":
        raw["next_searches"][0]["requirement_ids"] = ["u9"]
    else:
        stage = "initial"
    with pytest.raises(ValueError):
        validate_hints(raw, sources, [{"unit_id": "u1"}], stage)


def test_negative_relation_does_not_follow_from_an_abstract_omission():
    known, _, _ = sample()
    c = claim("c1", "ev_1")
    c["text"] = "AlphaNet未展开队列丢失机制。"
    r = review("c1", "S1")
    r.update(
        negative_explicit=True,
        supported_by=[
            {"evidence_id": "ev_1", "quote": known["ev_1"]["spans"][0]["quote"]}
        ],
    )
    accepted, rejected = check_claims([c], [r], known)
    assert not accepted and rejected[0]["code"] == "negative_not_explicit"


def element_data():
    known = {}
    accepted = {}
    elements = []
    for slot in ("input", "operation", "scoring"):
        eid = "e_" + slot
        cid = "c_" + slot
        known[eid] = {
            "asset_id": "doc1",
            "verification": {"source_attribution": "own", "mechanism_slot": slot},
        }
        accepted[cid] = {
            "evidence_ids": [eid],
            "check": {"supported_by": [{"evidence_id": eid}]},
        }
        elements.append(
            {"document_id": "doc1", "kind": slot, "covered": True, "claim_ids": [cid]}
        )
    return (
        known,
        accepted,
        {"scoring_chain_required": True, "elements": elements, "missing_elements": []},
    )


def test_complete_scoring_chain_requires_own_input_operation_and_test_score():
    known, accepted, r = element_data()
    assert element_complete(r, list(accepted), accepted, known)


@pytest.mark.parametrize(
    "fault",
    [
        "background",
        "loss_not_score",
        "missing_score",
        "uncited_claim",
        "wrong_method",
        "unsupported",
        "missing_element",
    ],
)
def test_checker_complete_boolean_cannot_override_a_missing_method_element(fault):
    known, accepted, r = element_data()
    if fault == "background":
        known["e_scoring"]["verification"]["source_attribution"] = "background"
    elif fault == "loss_not_score":
        known["e_scoring"]["verification"]["mechanism_slot"] = "operation"
    elif fault == "missing_score":
        r["elements"].pop()
    elif fault == "uncited_claim":
        r["elements"][-1]["claim_ids"] = ["c_unknown"]
    elif fault == "wrong_method":
        r["elements"][-1]["document_id"] = "doc2"
    elif fault == "unsupported":
        del accepted["c_scoring"]
    else:
        r["missing_elements"] = ["actual score"]
    assert not element_complete(
        r, ["c_input", "c_operation", "c_scoring"], accepted, known
    )


def test_new_navigation_metadata_and_coverage_ids_preserve_original_identity():
    known, papers, units = sample()
    known["ev_1"]["verification"] = {
        "source_attribution": "own",
        "mechanism_slot": "scoring",
        "method_subject": "AlphaNet",
    }
    ctx = wire_context(units, known, papers)
    payload, m = checker_payload(
        [claim("c1", "ev_1")], {"units": [], "relations": []}, ctx
    )
    assert payload["evidence_navigation"][0]["mechanism_slot"] == "scoring"
    r = resolve_checks(
        {
            "claims": [],
            "units": [
                {
                    "unit_id": "U1",
                    "elements": [
                        {
                            "document_id": "D1",
                            "claim_ids": ["C1"],
                            "kind": "scoring",
                            "covered": True,
                        }
                    ],
                }
            ],
            "conflicts": [],
        },
        ctx,
        m,
    )
    assert r["units"][0]["elements"][0]["document_id"] == "doc1" and r["units"][0][
        "elements"
    ][0]["claim_ids"] == ["c1"]


def model15(tools, fault=None):
    base = source_model(tools)

    async def model(system, data, **kw):
        result = await base(system, data, **kw)
        if kw["stage"] == "extraction_batch":
            for f in result["facts"]:
                f.update(attribution="own", subject="AlphaNet", slot="operation")
        if kw["stage"] == "evidence_check_batch":
            for c in result["checks"]:
                c.update(
                    attribution_preserved=True,
                    slot_preserved=True,
                    negative_explicit=False,
                )
            if fault == "wrong_attribution":
                result["checks"][0]["attribution_preserved"] = False
        if kw["stage"] == "answer_check":
            result.pop("conditions_preserved")
            result["scope_checks"] = []
            for c in result["claims"]:
                c["negative_explicit"] = False
            for u in result["units"]:
                cs = [
                    c["claim_id"]
                    for c in data["claims"]
                    if c.get("unit_id") == u["unit_id"]
                ]
                u.update(
                    scoring_chain_required=fault == "missing_score",
                    elements=[
                        {
                            "document_id": "",
                            "kind": "other",
                            "covered": True,
                            "claim_ids": cs,
                        }
                    ],
                    missing_elements=[],
                )
        return kw["schema"].model_validate(result).model_dump()

    return model


@pytest.mark.parametrize("env15", ["question_loop_v15"], indirect=True)
@pytest.mark.parametrize("fault", [None, "missing_score"])
def test_actual_new_controller_delivers_supported_partial_and_preserves_method_metadata(
    env15, monkeypatch, fault
):
    repo, tools = env15
    monkeypatch.setattr(tools, "model", model15(tools, fault))
    stop = asyncio.run(Controller(tools).run())
    evidence = tools.evidence("main")
    assert evidence[0]["verification"]["source_attribution"] == "own"
    answer = repo.rows(ResearchAnswerRevision)[0]
    assert answer.conclusion and all(
        u["accepted_claims"] for u in answer.assessment["requirement_ledger"]
    )
    if fault:
        assert all(not u["resolved"] for u in answer.assessment["requirement_ledger"])
    else:
        assert stop == "required_questions_supported"
    report = build_report(tools, freeze(tools, stop))
    assert report["coverage"]["used"] and report["delivery"]["answer_status"] in (
        "partial",
        "complete",
    )
    assert any(
        a.content_kind == "support_check_attempt" for a in repo.rows(ResearchAsset)
    )


@pytest.mark.parametrize("env15", ["question_loop_v15"], indirect=True)
def test_explicit_abstract_limitation_is_preserved_but_bad_role_is_rejected(env15):
    repo, tools = env15
    asset = tools.documents()[0]
    question = repo.rows(ResearchQuestion)[0]
    fact = AtomicFact(
        fact="AlphaNet队列满时删除最旧条目。",
        quote=asset.inline_payload["text"],
        evidence_kind="limitation",
        conditions={"when": "full"},
        missing_fields=[],
    )
    good = {
        "supported": True,
        "negative_explicit": False,
        "relevant": True,
        "unsupported_parts": [],
        "attribution_preserved": True,
        "slot_preserved": True,
    }
    row, code = validate(tools, question, asset, fact, good)
    assert row and code is None and row["conditions"] == {"when": "full"}
    row, code = validate(
        tools, question, asset, fact, {**good, "attribution_preserved": False}
    )
    assert row is None and code == "source_role_mismatch"


@pytest.mark.parametrize("env15", ["question_loop_v15"], indirect=True)
@pytest.mark.parametrize("navigation_fault", [False, True])
def test_classification_read_and_anchored_search_precede_final_synthesis(
    env15, monkeypatch, navigation_fault
):
    repo, tools = env15
    with repo.transaction() as session:
        run = repo.run()
        run.options = {**run.options, "search_online": True}
        session.add(run)
        session.add(
            ResearchAsset(
                run_id=repo.run_id,
                asset_id="taxonomy",
                family_key="taxonomy",
                content_kind="document",
                content_hash="taxonomy-v1",
                parse_status="parsed",
                inline_payload={
                    "paper": {
                        "key": "taxonomy",
                        "title": "Queue mechanism taxonomy",
                        "research_role": "survey",
                        "relevant_questions": ["main"],
                    },
                    "source_kind": "full_text",
                    "text": "Queue mechanisms include bounded storage and indexed retrieval. BetaLog offers indexed retrieval.",
                },
            )
        )
    plan = tools.plan()
    plan["reading_roles"] = [{"question_id": "main", "role": "overview"}]
    with repo.transaction() as session:
        for asset in repo.rows(ResearchAsset):
            if asset.content_kind == "research_plan":
                asset.status = "superseded"
                session.add(asset)
    tools.save_plan(plan)
    original = tools.documents()[0]
    method = next(a for a in tools.documents() if a.asset_id != "taxonomy")
    reqs = [u["unit_id"] for u in plan["requirement_ledger"]["main"]]
    choices = [
        {
            "asset_id": "taxonomy",
            "role": "classification",
            "requirement_ids": reqs,
            "focus_queries": ["queue mechanism taxonomy"],
            "reason": "actual classification",
        },
        {
            "asset_id": method.asset_id,
            "role": "representative",
            "requirement_ids": reqs,
            "focus_queries": ["queue storage operation"],
            "reason": "actual method",
        },
    ]
    base = model15(tools)

    async def model(system, data, **kw):
        if kw["stage"] == "reading_portfolio":
            nav = []
            if data["stage"] == "coverage_refresh":
                source = next(
                    s
                    for s in data["sources"]
                    if s["title"] == "Queue mechanism taxonomy"
                )
                nav = [
                    {
                        "source_id": source["source_id"],
                        "source_quote": "BetaLog offers indexed retrieval.",
                        "query": "GoldNet indexed retrieval"
                        if navigation_fault
                        else "BetaLog indexed retrieval",
                        "requirement_ids": reqs,
                        "reason": "actual alternate mechanism",
                    }
                ]
            return (
                kw["schema"]
                .model_validate(
                    {
                        "choices": choices,
                        "gaps": ["indexed retrieval representative missing"],
                        "next_searches": nav,
                    }
                )
                .model_dump()
            )
        if (
            kw["stage"] == "extraction_batch"
            and data["sources"][0]["title"] == "Queue mechanism taxonomy"
        ):
            return {
                "facts": [
                    {
                        "source_id": data["sources"][0]["source_id"],
                        "attribution": "taxonomy",
                        "subject": "Queue mechanisms",
                        "slot": "taxonomy",
                        "fact": "队列机制包括有界存储与索引检索。",
                        "evidence_kind": "fact",
                        "conditions": {},
                        "missing_fields": [],
                    }
                ]
            }
        return await base(system, data, **kw)

    monkeypatch.setattr(tools, "model", model)
    controller = Controller(tools)

    async def execute(action):
        result = await tools.execute(action)
        repo.complete_task(*repo.claim_task(action), result)

    first = asyncio.run(controller.choose())
    assert first.tool == "plan_reading"
    asyncio.run(execute(first))
    second = asyncio.run(controller.choose())
    assert second.tool == "read_documents" and second.asset_ids == ["taxonomy"]
    asyncio.run(execute(second))
    third = asyncio.run(controller.choose())
    assert third.tool == "plan_reading" and third.query == "coverage_refresh"
    asyncio.run(execute(third))
    fourth = asyncio.run(controller.choose())
    if navigation_fault:
        assert fourth.tool == "fetch_document"
        latest = [
            a for a in repo.rows(ResearchAsset) if a.content_kind == "reading_portfolio"
        ][-1].inline_payload
        assert (
            latest["navigation_rejections"]
            and latest["gaps"]
            and latest["next_searches"] == []
        )
        assert not repo.rows(ResearchAnswerRevision)
        return
    assert (
        fourth.tool == "search_papers" and fourth.query == "BetaLog indexed retrieval"
    )
    assert not repo.rows(ResearchAnswerRevision)
    # An empty search is explicitly recorded and does not cause endless navigation.
    repo.complete_task(*repo.claim_task(fourth), {"assets": []})
    fifth = asyncio.run(controller.choose())
    assert fifth.tool == "plan_reading" and fifth.query == "after_coverage_search"
    asyncio.run(execute(fifth))
    assert asyncio.run(controller.choose()).tool == "fetch_document"
