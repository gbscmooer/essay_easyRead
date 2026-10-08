"""Validate available analysis fields without inventing missing model output."""

from pydantic import TypeAdapter, ValidationError
from ..models.analysis import MethodModel, ExperimentModel, CodeMapping, InterviewQA
from ..models.schemas import OutlineItem

REQUIRED_FIELDS = (
    "title",
    "one_sentence",
    "problem",
    "key_contributions",
    "outline",
    "method",
    "experiments",
    "code_mapping",
    "interview_qa",
    "limitations",
)
_ADAPTERS = {
    "method": TypeAdapter(MethodModel),
    "experiments": TypeAdapter(ExperimentModel),
    "outline": TypeAdapter(list[OutlineItem]),
    "code_mapping": TypeAdapter(list[CodeMapping]),
    "interview_qa": TypeAdapter(list[InterviewQA]),
    "key_contributions": TypeAdapter(list[str]),
}
_STRINGS = {"title", "authors", "venue", "one_sentence", "problem", "limitations"}


def valid_field(name, value):
    if name in _STRINGS:
        return isinstance(value, str) and (
            name not in {"title", "one_sentence", "problem"} or bool(value.strip())
        )
    if name == "year":
        return type(value) is int and (value == 0 or 1900 <= value <= 2100)
    if name in _ADAPTERS:
        try:
            _ADAPTERS[name].validate_python(value, strict=True)
        except (ValidationError, TypeError):
            return False
        if name in {"key_contributions", "outline"} and not value:
            return False
        if name == "method" and (
            not isinstance(value, dict)
            or (
                value.get("mermaid_chart") is not None
                and not isinstance(value["mermaid_chart"], str)
            )
        ):
            return False
        if name == "method" and not str(value.get("overview", "")).strip():
            return False
        return True
    return False


def validated_fields(result):
    return {name: value for name, value in result.items() if valid_field(name, value)}


def missing_fields(result):
    return [name for name in REQUIRED_FIELDS if not valid_field(name, result.get(name))]
