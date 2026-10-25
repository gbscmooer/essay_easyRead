import json
import logging

from backend.eval import ledger


def _report(stamp, scores, *, label=""):
    records = []
    for i, s in enumerate(scores):
        system = {"error": "RuntimeError: run failed"} if s is None else {
            "status": "ok", "words": 1000, "wall_s": 600.0 + i,
            "scores": [{"score": s, "ann_score": s - 0.1, "length": 0.0, "expertise": 0.5, "citations": 1.0,
                        "excerpts": 0.9, "most_important_item_0": 1.0, "most_important_item_0_evidence": 0.5,
                        "nice_to_have_item_0": 0.8, "nice_to_have_item_0_evidence": 0.0}]}
        records.append({"question": f"q{i}", "system": system})
    answered = [s for s in scores if s is not None]
    return {"meta": {"started": stamp, "seed": 0, "n": len(scores), "label": label},
            "summary": {"system": {"score": sum(answered) / len(answered), "ann_score": 0.5}},
            "records": records}


def _use_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(ledger, "OUT_DIR", tmp_path)
    monkeypatch.setattr(ledger, "LEDGER_JSONL", tmp_path / "LEDGER.jsonl")
    monkeypatch.setattr(ledger, "LEDGER_MD", tmp_path / "LEDGER.md")


def test_record_pairs_with_previous_complete_run_and_skips_partial(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    runs = [("20261025_100000", [0.5, 0.6, 0.7, 0.8, 0.6]),
            ("20261025_110000", [0.9, None, None, None, None]),    # 大半失败：不作对照
            ("20261025_120000", [0.6, 0.6, 0.8, 0.8, 0.7])]
    for stamp, scores in runs:
        out = tmp_path / f"scholarqa_cs_{stamp}.json"
        report = _report(stamp, scores)
        out.write_text(json.dumps(report), encoding="utf-8")
        digest = ledger.record(out, report)

    cmp = digest["comparison"]
    assert cmp["against"] == "scholarqa_cs_20261025_100000.json"
    assert cmp["compare"]["paired"] == 5
    assert abs(cmp["compare"]["diff"] - 0.06) < 1e-9
    assert digest["questions"][0]["must_evidence"] == 0.5
    assert (tmp_path / "scholarqa_cs_20261025_120000.digest.json").exists()
    entries = [json.loads(line) for line in (tmp_path / "LEDGER.jsonl").read_text().splitlines()]
    assert [e["answered"] for e in entries] == [5, 1, 5]
    md = (tmp_path / "LEDGER.md").read_text()
    assert md.count("\n## ") == 3 and "失败 4 题" in md

    # 重复登记同一文件只替换，不新增
    out = tmp_path / "scholarqa_cs_20261025_120000.json"
    ledger.record(out)
    assert len((tmp_path / "LEDGER.jsonl").read_text().splitlines()) == 3


def test_run_log_keeps_console_output_and_pipeline_warnings(tmp_path):
    out = tmp_path / "scholarqa_cs_x.json"
    log = ledger.open_log(out)
    try:
        print("进度行")
        logging.getLogger("backend.services.semantic_scholar").warning("S2 429，冷却")
        logging.getLogger("x").warning("OpenAlex request failed: OpenAlex disabled for this evaluation")
    finally:
        log.close()
    text = out.with_suffix(".log").read_text(encoding="utf-8")
    assert "进度行" in text
    assert "WARNING backend.services.semantic_scholar: S2 429，冷却" in text
    assert "disabled for this evaluation" not in text
