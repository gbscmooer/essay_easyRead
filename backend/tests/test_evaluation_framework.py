from backend.tests.evaluation.evaluate import evaluate


def test_evaluation_extracts_current_analysis_response_shape():
    ground_truth = {
        "papers": [
            {
                "id": "demo",
                "title": "Demo Paper",
                "ground_truth": {
                    "one_sentence": "retrieval grounded code agent",
                    "problem": "code agents hallucinate without evidence",
                    "key_contributions": ["evidence grounded workflow"],
                    "method_summary": "search retrieve brief",
                    "experiments_datasets": ["Local Workspace"],
                    "main_result": "better grounded answers",
                },
            }
        ],
        "evaluation_fields": [
            {"field": "one_sentence", "type": "text", "metric": "semantic_similarity", "description": ""},
            {"field": "problem", "type": "text", "metric": "semantic_similarity", "description": ""},
            {"field": "key_contributions", "type": "list", "metric": "coverage", "description": ""},
            {"field": "method_summary", "type": "text", "metric": "semantic_similarity", "description": ""},
            {"field": "experiments_datasets", "type": "list", "metric": "exact_match", "description": ""},
            {"field": "main_result", "type": "text", "metric": "semantic_similarity", "description": ""},
        ],
    }
    system_result = {
        "one_sentence": "retrieval grounded code agent",
        "problem": "code agents hallucinate without evidence",
        "key_contributions": ["evidence grounded workflow"],
        "method": {
            "overview": "search retrieve brief",
            "pipeline": "",
            "key_modules": [],
        },
        "experiments": {
            "datasets": ["Local Workspace"],
            "main_results": "better grounded answers",
        },
    }

    result = evaluate("demo", system_result, ground_truth)
    scores = {row["field"]: row["score"] for row in result["field_results"]}

    assert scores["method_summary"] > 0
    assert scores["experiments_datasets"] == 1.0
    assert scores["main_result"] > 0
