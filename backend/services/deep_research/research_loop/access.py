"""Dependency access checks for copied answer/report content."""
from sqlmodel import select

from ....models.research_core import ResearchAsset, ResearchEvidence, ResearchDossierRevision
from ...research_event_log import RunStateError


def require_assets(session, run_id, manifest):
    for asset_id, content_hash in manifest.items():
        asset = session.exec(select(ResearchAsset).where(ResearchAsset.run_id == run_id,
            ResearchAsset.asset_id == asset_id, ResearchAsset.content_kind == "document")).first()
        if asset and asset.status == "quarantined":
            raise RunStateError(403, "EVIDENCE_NOT_EXPOSED", "依据文档已隔离，不对外暴露派生内容")
        if not asset or asset.status not in ("active", "superseded") or asset.content_hash != content_hash:
            raise RunStateError(404, "EVIDENCE_NOT_FOUND", "依据文档或版本不可读")


def require_answer(session, run_id, answer):
    manifest = {}
    for eid in answer.evidence_ids:
        evidence = session.exec(select(ResearchEvidence).where(ResearchEvidence.run_id == run_id,
                                                              ResearchEvidence.evidence_id == eid)).first()
        if not evidence:
            raise RunStateError(404, "EVIDENCE_NOT_FOUND", "答案依据不可读")
        manifest[evidence.asset_id] = evidence.doc_content_hash
    require_assets(session, run_id, manifest)


def require_report(session, run_id, report):
    if not report.get("dossier_revision_id"):
        return
    dossier = session.exec(select(ResearchDossierRevision).where(ResearchDossierRevision.run_id == run_id,
        ResearchDossierRevision.revision_id == report["dossier_revision_id"])).first()
    if not dossier:
        raise RunStateError(404, "DOSSIER_NOT_FOUND", "报告材料版本不存在")
    require_assets(session, run_id, dossier.manifest.get("assets", {}))
