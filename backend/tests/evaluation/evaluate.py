"""
对比实验评估脚本

用法：
    python -m backend.tests.evaluation.evaluate --paper-id resnet --system-result result.json

评估维度：
1. 字段级准确率（exact match / coverage）
2. 语义相似度（基于关键词重叠）
3. 系统 vs ChatGPT baseline 对比

输出格式化的评估表格，可直接用于论文。
"""

import json
import sys
import argparse
from pathlib import Path
from difflib import SequenceMatcher


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def keyword_overlap(text_a: str, text_b: str) -> float:
    """基于关键词重叠的语义相似度（简化版，无需外部依赖）"""
    def extract_keywords(text: str) -> set[str]:
        # 简单分词 + 去停用词
        stop_words = {
            "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一", "一个",
            "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好",
            "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "do", "does", "did", "will", "would", "could",
            "should", "may", "might", "shall", "can", "to", "of", "in", "for",
            "on", "with", "at", "by", "from", "as", "into", "through", "during",
            "before", "after", "above", "below", "between", "under", "again",
            "and", "but", "or", "nor", "not", "so", "yet", "both", "either",
            "neither", "each", "every", "all", "any", "few", "more", "most",
            "other", "some", "such", "no", "only", "own", "same", "than",
            "too", "very", "just", "because", "if", "when", "where", "how",
            "what", "which", "who", "whom", "this", "that", "these", "those",
            "it", "its", "they", "them", "their", "we", "us", "our", "he",
            "him", "his", "she", "her",
        }
        words = set()
        for w in text.lower().split():
            w = w.strip(".,;:!?()[]{}\"'`~@#$%^&*+=|\\/<>")
            if len(w) > 1 and w not in stop_words:
                words.add(w)
        return words

    kw_a = extract_keywords(text_a)
    kw_b = extract_keywords(text_b)
    if not kw_a or not kw_b:
        return 0.0
    intersection = len(kw_a & kw_b)
    union = len(kw_a | kw_b)
    return intersection / union if union > 0 else 0.0


def list_coverage(pred: list[str], gt: list[str]) -> float:
    """计算列表覆盖率：被命中的 ground-truth 数 / ground-truth 总数。

    以 ground truth 为分母逐项判定是否被某个预测命中，避免预测项多于
    ground truth 时出现覆盖率 > 1.0 的情况。
    """
    if not gt:
        return 1.0
    pred_lower = [p.lower() for p in pred]
    hits = 0
    for g in gt:
        g_lower = g.lower()
        if any(p in g_lower or g_lower in p for p in pred_lower):
            hits += 1
    return hits / len(gt)


def evaluate_field(field_name: str, field_type: str, metric: str, predicted, ground_truth) -> float:
    """评估单个字段"""
    if predicted is None or ground_truth is None:
        return 0.0

    if field_type == "list":
        if metric == "coverage":
            pred_list = predicted if isinstance(predicted, list) else [str(predicted)]
            gt_list = ground_truth if isinstance(ground_truth, list) else [str(ground_truth)]
            return list_coverage(pred_list, gt_list)
        elif metric == "exact_match":
            pred_set = set(str(x).lower().strip() for x in (predicted if isinstance(predicted, list) else [predicted]))
            gt_set = set(str(x).lower().strip() for x in (ground_truth if isinstance(ground_truth, list) else [ground_truth]))
            if not gt_set:
                return 1.0
            return len(pred_set & gt_set) / len(gt_set)
    else:  # text
        if metric == "semantic_similarity":
            return keyword_overlap(str(predicted), str(ground_truth))
        elif metric == "exact_match":
            return 1.0 if str(predicted).strip().lower() == str(ground_truth).strip().lower() else 0.0

    return 0.0


def _text(value) -> str:
    return str(value).strip() if value is not None else ""


def _extract_system_field(system_result: dict, field_name: str):
    """Extract a field from current AnalysisResponse, including evaluation aliases."""

    if field_name in system_result:
        return system_result.get(field_name)

    if field_name == "method_summary":
        method = system_result.get("method") if isinstance(system_result.get("method"), dict) else {}
        parts = [_text(method.get("overview")), _text(method.get("pipeline"))]
        for module in method.get("key_modules") or []:
            if not isinstance(module, dict):
                continue
            parts.append(" ".join(
                part for part in (
                    _text(module.get("name")),
                    _text(module.get("role")),
                    _text(module.get("innovation")),
                ) if part
            ))
        return " ".join(part for part in parts if part)

    if field_name == "experiments_datasets":
        experiments = system_result.get("experiments") if isinstance(system_result.get("experiments"), dict) else {}
        return experiments.get("datasets")

    if field_name == "main_result":
        experiments = system_result.get("experiments") if isinstance(system_result.get("experiments"), dict) else {}
        return experiments.get("main_results")

    return None


def evaluate(paper_id: str, system_result: dict, ground_truth_data: dict) -> dict:
    """评估一篇论文的系统输出"""
    # 找到对应的 ground truth
    gt_paper = None
    for p in ground_truth_data["papers"]:
        if p["id"] == paper_id:
            gt_paper = p
            break

    if not gt_paper:
        print(f"Error: paper '{paper_id}' not found in ground_truth.json")
        sys.exit(1)

    gt = gt_paper["ground_truth"]
    fields = ground_truth_data["evaluation_fields"]

    results = []
    total_score = 0.0

    for f in fields:
        field_name = f["field"]
        # 从系统结果中提取字段（支持当前 AnalysisResponse 的嵌套结构和评估别名）
        predicted = _extract_system_field(system_result, field_name)
        ground_truth = gt.get(field_name)

        score = evaluate_field(field_name, f["type"], f["metric"], predicted, ground_truth)
        results.append({
            "field": field_name,
            "description": f["description"],
            "score": round(score, 3),
            "predicted": str(predicted)[:80] if predicted else "N/A",
            "ground_truth": str(ground_truth)[:80] if ground_truth else "N/A",
        })
        total_score += score

    avg_score = total_score / len(fields) if fields else 0.0

    return {
        "paper_id": paper_id,
        "paper_title": gt_paper["title"],
        "field_results": results,
        "average_score": round(avg_score, 3),
    }


def print_evaluation_table(result: dict):
    """打印格式化的评估表格"""
    print(f"\n{'='*70}")
    print(f"论文: {result['paper_title']}")
    print(f"{'='*70}")
    print(f"{'字段':<25} {'得分':>6} {'说明'}")
    print(f"{'-'*70}")
    for r in result["field_results"]:
        bar = "█" * int(r["score"] * 10) + "░" * (10 - int(r["score"] * 10))
        print(f"{r['field']:<25} {r['score']:>5.1%}  {bar} {r['description']}")
    print(f"{'-'*70}")
    print(f"{'平均':<25} {result['average_score']:>5.1%}")
    print()


def print_comparison_table(results: list[dict], baseline_results: list[dict] | None = None):
    """打印系统 vs baseline 对比表格"""
    print(f"\n{'='*70}")
    print("对比实验结果")
    print(f"{'='*70}")
    print(f"{'论文':<30} {'系统':>8}", end="")
    if baseline_results:
        print(f" {'Baseline':>10} {'提升':>8}", end="")
    print()
    print(f"{'-'*70}")

    for i, r in enumerate(results):
        line = f"{r['paper_title'][:28]:<30} {r['average_score']:>7.1%}"
        if baseline_results and i < len(baseline_results):
            bl = baseline_results[i]["average_score"]
            diff = r["average_score"] - bl
            line += f" {bl:>9.1%} {diff:>+7.1%}"
        print(line)

    if results:
        avg = sum(r["average_score"] for r in results) / len(results)
        line = f"{'平均':<30} {avg:>7.1%}"
        if baseline_results:
            bl_avg = sum(r["average_score"] for r in baseline_results) / len(baseline_results)
            diff = avg - bl_avg
            line += f" {bl_avg:>9.1%} {diff:>+7.1%}"
        print(f"{'-'*70}")
        print(line)
    print()


def main():
    parser = argparse.ArgumentParser(description="评估论文分析结果")
    parser.add_argument("--paper-id", required=True, help="论文 ID（对应 ground_truth.json 中的 id）")
    parser.add_argument("--system-result", required=True, help="系统输出 JSON 文件路径")
    parser.add_argument("--baseline-result", help="Baseline 输出 JSON 文件路径（可选）")
    parser.add_argument("--ground-truth", default=str(Path(__file__).parent / "ground_truth.json"),
                        help="Ground truth 文件路径")
    args = parser.parse_args()

    gt_data = load_json(args.ground_truth)
    sys_result = load_json(args.system_result)

    result = evaluate(args.paper_id, sys_result, gt_data)
    print_evaluation_table(result)

    if args.baseline_result:
        bl_result_data = load_json(args.baseline_result)
        bl_result = evaluate(args.paper_id, bl_result_data, gt_data)
        print_comparison_table([result], [bl_result])


if __name__ == "__main__":
    main()
