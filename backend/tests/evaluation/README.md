# 对比实验评估框架

## 概述

本框架用于评估论文精读系统的分析质量，并与 baseline（直接使用 ChatGPT）进行对比。

## 评估论文

选取 5 篇经典 CV 论文，覆盖不同子领域：

| ID | 论文 | 年份 | 会议 | 子领域 |
|----|------|------|------|--------|
| resnet | Deep Residual Learning for Image Recognition | 2016 | CVPR | 网络架构 |
| vit | An Image is Worth 16x16 Words | 2021 | ICLR | Vision Transformer |
| detr | End-to-End Object Detection with Transformers | 2020 | ECCV | 目标检测 |
| clip | Learning Transferable Visual Models From Natural Language | 2021 | ICML | 多模态 |
| dino | Emerging Properties in Self-Supervised Vision Transformers | 2021 | ICCV | 自监督学习 |

## 评估维度

| 字段 | 类型 | 指标 | 说明 |
|------|------|------|------|
| one_sentence | text | 语义相似度 | 一句话摘要的准确性 |
| problem | text | 语义相似度 | 问题定义的准确性 |
| key_contributions | list | 覆盖率 | 关键贡献的命中率 |
| method_summary | text | 语义相似度 | 方法描述的准确性 |
| experiments_datasets | list | 精确匹配 | 数据集名称匹配 |
| main_result | text | 语义相似度 | 主要结果的准确性 |

## 用法

### 1. 准备系统输出

上传论文 PDF 到系统，等待分析完成，导出分析结果 JSON：

```bash
curl http://localhost:8000/api/analysis/{paper_id} > system_result.json
```

### 2. 准备 Baseline 输出

将同一篇论文的 PDF 文本直接发给 ChatGPT，使用相同 prompt，保存输出：

```bash
# 示例 prompt：
# "请分析这篇论文，输出以下字段的 JSON：
#  - one_sentence: 一句话总结
#  - problem: 研究问题
#  - key_contributions: 关键贡献列表
#  - method_summary: 方法概述
#  - experiments_datasets: 使用的数据集列表
#  - main_result: 主要实验结果"
```

### 3. 运行评估

```bash
# 单篇评估
python -m backend.tests.evaluate --paper-id resnet --system-result system_result.json

# 与 baseline 对比
python -m backend.tests.evaluate --paper-id resnet --system-result system_result.json --baseline-result baseline_result.json
```

### 4. 输出示例

```
======================================================================
论文: Deep Residual Learning for Image Recognition
======================================================================
字段                          得分 说明
----------------------------------------------------------------------
one_sentence                72.3%  ███████░░░ 一句话摘要的语义准确性
problem                     65.1%  ██████░░░░ 问题定义的准确性
key_contributions           80.0%  ████████░░ 关键贡献的覆盖率
method_summary              58.4%  █████░░░░░ 方法描述的准确性
experiments_datasets       100.0%  ██████████ 数据集名称的精确匹配
main_result                 71.2%  ███████░░░ 主要结果的准确性
----------------------------------------------------------------------
平均                         74.5%
```

## 答辩使用

1. 对 5 篇论文分别运行系统和 baseline
2. 生成对比表格，展示系统在各维度的优势
3. 分析系统优势来源：结构化 prompt + 增量解析 + 断点续传
