"""Freeze user criteria before research. The original question always remains required."""
from ....models.research_core import ResearchQuestion
from .contracts import QuestionSpec


def initial_questions(question: str, options: dict | None = None) -> list[dict]:
    options = options or {}
    main = QuestionSpec(question_id="main", text=question,
        answer_criteria=options.get("answer_criteria") or ["回答原问题全部限定条件；每项事实提供材料依据；证据不足明确说明"],
        expected_evidence=options.get("expected_evidence") or ["fact"])
    specs = [main]
    for idx, raw in enumerate(options.get("questions") or []):
        item = {**raw, "question_id": f"q{idx + 1}"}
        specs.append(QuestionSpec.model_validate(item))
    if len(specs) > 8:
        raise ValueError("初始问题最多 8 项（包括原问题）")
    return [x.model_dump() for x in specs]


def question_view(row: ResearchQuestion) -> dict:
    return row.model_dump(exclude={"id", "run_id"})
