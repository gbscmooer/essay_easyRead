"""Offline v8 regressions: request omissions and claim-local source boundaries."""
import asyncio
from copy import deepcopy

import pytest
from sqlmodel import Session

from backend.models.exploration import ExplorationRun
from backend.models.research_core import ResearchAsset, ResearchQuestion
from backend.services.deep_research.research_loop.contracts import Action, AtomicFact, digest
from backend.services.deep_research.research_loop.repository import Repository
from backend.services.deep_research.research_loop.resources import Resources, limits_for
from backend.services.deep_research.research_loop.questions import initial_questions
from backend.services.deep_research.research_loop.tools import Tools
from backend.services.deep_research.research_loop.evidence import validated_evidence
from backend.services.deep_research.research_loop.intent_v8 import source_clauses, anchor_requests, coverage_gaps, make_anchored_plan
from backend.services.deep_research.research_loop.answering_v8 import bind_claims, check_claims, subject_claims, render_claims
from backend.services.deep_research.research_loop.queries import questions
from backend.services.deep_research.research_loop.dossier import freeze
from backend.services.deep_research.research_loop.writer import build_report, save_report


def specs():
    return initial_questions('比较 AlphaNet 和 BetaNet 队列满的处理。只研究有界队列，不做性能排名。')


def requests(data):
    clause = data['source_clauses'][0]
    return [{'question_id': 'main', 'clause_id': clause['clause_id'], 'requirement': f'说明 {name} 队列满的处理',
        'subject': name, 'phase': 'general', 'mode': ''} for name in ('AlphaNet', 'BetaNet')]


def approved(data):
    return {'coverage': [{'clause_id': c['clause_id'], 'kind': 'request' if i == 0 else 'constraint',
        'covered': True, 'unit_ids': [u['unit_id'] for u in data['requirements'] if u['clause_id'] == c['clause_id']],
        'reason': 'Preserves both named subjects and the original scope'} for i, c in enumerate(data['source_clauses'])],
        'unsupported_unit_ids': [], 'missing_requests': []}


class Planner:
    def __init__(self, unresolved=False):
        self.calls, self.unresolved = [], unresolved

    async def model(self, system, data, **kwargs):
        self.calls.append(kwargs['stage'])
        if kwargs['stage'] == 'planning':
            return {'queries': [{'question_id': 'main', 'query': 'bounded queue management'}],
                'answer_units': ['说明机制'], 'targets': [{'question_id': 'main', 'name': n} for n in ('AlphaNet', 'BetaNet')],
                'reading_queries': ['queue overflow mechanism'], 'requests': requests(data)[:1]}
        result = approved(data)
        if kwargs['stage'] == 'planning_check':
            result['coverage'][0]['covered'] = False
            result['missing_requests'] = requests(data)[1:]
        elif self.unresolved:
            result['coverage'][0]['covered'] = False
        return result


def test_bounded_repair_preserves_original_request_not_display_outline():
    planner = Planner()
    plan = asyncio.run(make_anchored_plan(planner, specs()))
    assert planner.calls == ['planning', 'planning_check', 'planning_check_repair']
    assert plan['intent_coverage']['complete'] is True
    assert len(plan['requirement_ledger']['main']) == 2
    assert {u['subject'] for u in plan['requirement_ledger']['main']} == {'AlphaNet', 'BetaNet'}
    assert plan['answer_units'] == ['说明机制']
    for unit in plan['requirement_ledger']['main']:
        assert specs()[0]['text'][unit['source_start']:unit['source_end']] == unit['source_quote']


def test_repair_still_incomplete_is_persisted_not_relabelled_complete():
    planner = Planner(unresolved=True)
    plan = asyncio.run(make_anchored_plan(planner, specs()))
    assert len(planner.calls) == 3
    assert not plan['intent_coverage']['complete'] and plan['intent_coverage']['gaps_by_question']['main']


@pytest.mark.parametrize('change', ['other_question', 'invented_subject', 'invented_mode', 'unknown_clause'])
def test_requests_cannot_drift_across_source_boundaries(change):
    question_specs = [*specs(), {**specs()[0], 'question_id': 'q1'}]
    clauses = source_clauses(question_specs)
    raw = requests({'source_clauses': clauses})
    targets = [{'question_id': 'main', 'name': n} for n in ('AlphaNet', 'BetaNet')]
    if change == 'other_question':
        raw[0]['question_id'] = 'q1'
    elif change == 'invented_subject':
        raw[0]['subject'] = 'GammaNet'
    elif change == 'invented_mode':
        raw[0]['mode'] = '零样本'
    else:
        raw[0]['clause_id'] = 'not_in_source'
    with pytest.raises(ValueError):
        anchor_requests(raw, clauses, question_specs, targets)


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'unapproved', 'unsupported', 'wrong_clause'])
def test_every_source_clause_and_retained_unit_needs_own_unique_approval(change):
    plan = asyncio.run(make_anchored_plan(Planner(), specs()))
    clauses, units = plan['intent_coverage']['source_clauses'], plan['requirement_ledger']['main']
    check = deepcopy(plan['intent_coverage']['reviews'][-1])
    if change == 'missing':
        check['coverage'].pop()
    elif change == 'duplicate':
        check['coverage'].append(deepcopy(check['coverage'][0]))
    elif change == 'unapproved':
        check['coverage'][0]['unit_ids'].pop()
    elif change == 'unsupported':
        check['unsupported_unit_ids'] = [units[0]['unit_id']]
    else:
        check['coverage'][1]['unit_ids'] = [units[0]['unit_id']]
    assert coverage_gaps(check, clauses, units)['main']


@pytest.fixture
def v8_env(engine):
    with Session(engine) as session:
        run = ExplorationRun(question=specs()[0]['text'], depth='quick', workflow_version='academic_survey_v2',
            state_version=1, options={'search_online': False, 'workflow': {'loop_revision': 'question_loop_v8'}})
        session.add(run)
        session.commit()
        run_id = run.id
    repo = Repository(engine, run_id, 'v8-worker')
    assert repo.acquire()
    limits = limits_for('quick')
    repo.initialize(specs(), limits)
    tools = Tools(repo, Resources(repo, limits), {})
    plan = asyncio.run(make_anchored_plan(Planner(), specs()))
    tools.save_plan(plan)
    with repo.transaction() as session:
        for name, behavior in [('AlphaNet', 'drops the oldest entry'), ('BetaNet', 'rejects the newest entry')]:
            payload = tools.document_spec({'key': name, 'title': name + ': Bounded queues', 'year': 2023,
                'abstract': f'{name} stores a bounded queue and {behavior} when the queue is full.', 'primary_targets': [name]})
            payload['inline_payload']['source_kind'] = 'full_text'
            payload['content_hash'] = digest(payload['inline_payload'])
            session.add(ResearchAsset(run_id=run_id, **payload))
    evidence = [validated_evidence(run_id, 'main', a, AtomicFact(fact=a.inline_payload['text'], quote=a.inline_payload['text']),
        {'supported': True, 'relevant': True}, reviewer_version='question_loop_v8') for a in tools.documents()]
    assert all(evidence)
    repo.complete_task(*repo.claim_task(Action(tool='read_documents', question_id='main', asset_ids=[a.asset_id for a in tools.documents()])),
        {'evidence': evidence})
    return repo, tools


def material(tools):
    known = {e['evidence_id']: e for e in tools.evidence('main')}
    papers = {eid: tools.repo.asset(e['asset_id']).inline_payload['paper'] for eid, e in known.items()}
    return known, papers


def positive(claim, known):
    eid = claim['evidence_ids'][0]
    return {'claim_id': claim['claim_id'], 'supported': True, 'relevant': True, 'conditions_preserved': True,
        'support_mode': 'inference' if claim['kind'] == 'inference' else 'direct',
        'supported_by': [{'evidence_id': eid, 'quote': known[eid]['spans'][0]['quote']}],
        'added_information': False, 'reason': 'Original explicit clause'}


@pytest.mark.parametrize('change', ['other_id', 'other_quote', 'unknown_id', 'wrong_mode', 'duplicate_check', 'no_excerpt', 'unsupported'])
def test_claim_cannot_borrow_other_units_proof_despite_positive_verdict(v8_env, change):
    _, tools = v8_env
    known, _ = material(tools)
    left, right = list(known)
    claim = {'claim_id': 'cl_test', 'text': known[left]['fact'], 'kind': 'fact', 'evidence_ids': [left]}
    review = positive(claim, known)
    if change == 'other_id':
        review['supported_by'] = [{'evidence_id': right, 'quote': known[right]['spans'][0]['quote']}]
    elif change == 'other_quote':
        review['supported_by'][0]['quote'] = known[right]['spans'][0]['quote']
    elif change == 'unknown_id':
        review['supported_by'][0]['evidence_id'] = 'unknown'
    elif change == 'wrong_mode':
        review['support_mode'] = 'inference'
    elif change == 'no_excerpt':
        review['supported_by'] = []
    elif change == 'unsupported':
        review['supported'] = False
    reviews = [review, deepcopy(review)] if change == 'duplicate_check' else [review]
    assert not check_claims([claim], reviews, known)


def test_inference_label_and_wrong_subject_partial_boundary(v8_env):
    _, tools = v8_env
    known, papers = material(tools)
    eid = next(e for e in known if papers[e]['primary_targets'] == ['BetaNet'])
    claim = {'claim_id': 'cl_inference', 'text': '依据拒绝新条目的条件，应核查队列满时的信息损失。', 'kind': 'inference', 'evidence_ids': [eid]}
    checked = check_claims([claim], [positive(claim, known)], known)
    assert '依据材料的推断：' in render_claims(list(checked.values()))
    assert not subject_claims({'subject': 'AlphaNet'}, list(checked.values()), known, papers)


@pytest.mark.parametrize('incomplete', [False, True])
def test_assessment_keeps_partial_support_gaps_and_drops_repeated_explanation(v8_env, monkeypatch, incomplete):
    repo, tools = v8_env
    known, papers = material(tools)
    async def model(system, data, **kwargs):
        if kwargs['stage'] == 'answer':
            units = []
            for unit in data['requirements']:
                eid = next(e for e in known if unit['subject'] in papers[e]['primary_targets'])
                units.append({'unit_id': unit['unit_id'], 'state': 'answered',
                    'claims': [{'text': known[eid]['fact'], 'kind': 'fact', 'evidence_ids': [eid]}]})
            return {'units': units, 'explanations': [{'title': '重复内容', 'claim': deepcopy(units[0]['claims'][0])}],
                'conditions': [], 'relations': []}
        if kwargs['stage'] == 'answer_check':
            return {'claims': [positive(c, known) for c in data['claims']],
                'units': [{'unit_id': u['unit_id'], 'complete': not incomplete or i != 0, 'reason': 'One original request may remain'}
                    for i, u in enumerate(data['units'])], 'relations': [], 'conditions_preserved': True, 'conflicts': []}
        assert kwargs['stage'] == 'answer_coverage'
        return {'next_steps': []}
    monkeypatch.setattr(tools, 'model', model)
    action = Action(tool='assess_answer', question_id='main')
    result = asyncio.run(tools.assess(action))
    repo.complete_task(*repo.claim_task(action), result)
    answer = result['answer']
    assert answer['status'] == ('insufficient' if incomplete else 'supported')
    assert answer['assessment']['accepted_explanations'] == 0
    assert '重复内容' not in answer['conclusion']
    assert len(answer['assessment']['requirement_ledger'][0]['accepted_claims']) == 1
    assert bool(answer['assessment']['intent_gaps']) is False
    if incomplete:
        assert answer['gaps'] and '尚未形成完整回答' in answer['conclusion']
    dossier = freeze(tools, 'test_partial' if incomplete else 'required_questions_supported')
    report = build_report(tools, dossier)
    save_report(tools, dossier, report)
    assert report['chapters'][0]['requirement_ledger'][0]['source_quote'] == specs()[0]['text'].split('。')[0]


def test_question_projection_only_exposes_verified_own_frozen_plan(v8_env):
    repo, tools = v8_env
    with Session(repo.engine) as session:
        view = questions(session, repo.run_id)[0]
        assert len(view['requirements']) == 2
        assert not view['planning_gaps']
    with repo.transaction() as session:
        asset = session.get(ResearchAsset, next(a.id for a in repo.rows(ResearchAsset) if a.content_kind == 'research_plan'))
        asset.content_hash = 'bad_hash'
        session.add(asset)
    with Session(repo.engine) as session:
        assert not questions(session, repo.run_id)[0]['requirements']


def test_question_api_source_plan_and_cors_without_paid_execution(v8_env, client, monkeypatch):
    from backend.config import settings
    from backend.routers import exploration
    repo, _ = v8_env
    monkeypatch.setattr(exploration, 'get_engine', lambda: repo.engine)
    path = f'/api/exploration/runs/{repo.run_id}/questions'
    origin = {'Origin': settings.frontend_origin}
    preflight = client.options(path, headers={**origin, 'Access-Control-Request-Method': 'GET'})
    assert preflight.status_code == 200
    response = client.get(path, headers=origin)
    assert response.status_code == 200
    assert response.headers['access-control-allow-origin'] == settings.frontend_origin
    units = response.json()['items'][0]['requirements']
    assert len(units) == 2 and {u['subject'] for u in units} == {'AlphaNet', 'BetaNet'}
    assert units[0]['source_quote'] == specs()[0]['text'].split('。')[0]
