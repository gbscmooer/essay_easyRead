"""
Analysis 数据模型

定义 AI 解析结果的完整数据结构。
这些模型同时用于：数据库存储、API 响应、前端类型映射。
"""

from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON, ForeignKey, Integer


# ============================================================
# 子结构模型（嵌套在 AnalysisResult 中）
# ============================================================


class KeyModule(SQLModel):
    """
    方法中的关键模块

    对应论文 pipeline 中的一个组件/模块。
    Example:
        {"name": "Multi-Head Attention", "role": "捕获序列中的长距离依赖", "innovation": "并行多头注意力机制"}
    """

    name: str  # 模块名称
    role: str  # 该模块的作用
    innovation: str  # 相比现有方法的创新点


class MethodModel(SQLModel):
    """
    方法论描述

    包含方法总览、完整 pipeline、以及关键模块列表。
    pipeline 是 step-by-step 的文字描述，前端会基于此生成 Mermaid 流程图。
    """

    overview: str  # 方法总览（一段话）
    pipeline: str  # 完整 pipeline 描述，用 "\n" 分隔步骤
    key_modules: list[KeyModule] = []  # 关键模块列表


class ResultTableRow(SQLModel):
    """
    实验结果表格的一行

    对应一个方法在某个数据集上的某个指标值。
    Example:
        {"method": "Ours", "dataset": "COCO", "metric": "mAP", "value": "52.3"}
    """

    method: str  # 方法名称
    dataset: str  # 数据集名称
    metric: str  # 指标名称（如 mAP, BLEU, FID）
    value: str  # 数值（字符串，方便展示如 "52.3*"）


class ExperimentModel(SQLModel):
    """
    实验设置与结果

    datasets: 使用的数据集名称列表
    baselines: 对比的基线方法列表
    main_results: 主要实验结论（文字描述）
    ablation: 消融实验的结论
    result_table: 结构化实验结果，用于 Recharts 可视化
    """

    datasets: list[str] = []  # 数据集列表
    baselines: list[str] = []  # 基线方法列表
    main_results: str  # 主要实验结论
    ablation: str  # 消融实验说明
    result_table: list[ResultTableRow] = []  # 结构化实验数据


class CodeMapping(SQLModel):
    """
    论文概念 → 代码实现 映射

    将论文中的关键概念/公式映射到可能的代码实现思路。
    Example:
        {"concept": "Scaled Dot-Product Attention: softmax(QK^T/√d_k)V", "likely_implementation": "torch.matmul(Q, K.transpose(-2,-1)) / math.sqrt(d_k)"}
    """

    concept: str  # 论文中的概念或公式
    likely_implementation: str  # 对应的代码实现思路（PyTorch 风格）


class InterviewQA(SQLModel):
    """
    面试问答卡片

    一条面试题及其参考回答。
    Example:
        {"q": "这篇论文的核心创新是什么？", "a": "提出了 Transformer 架构，完全基于注意力机制..."}
    """

    q: str  # 面试问题
    a: str  # 参考回答


# ============================================================
# 主模型：AnalysisResult
# ============================================================


class AnalysisResult(SQLModel, table=True):
    """
    论文解析结果表

    与 Paper 表一对一关联（通过 paper_id 外键）。
    存储 AI 解析出的全部结构化信息。

    cascade="all, delete-orphan": 删除 Paper 时自动删除关联的 AnalysisResult，
    避免孤儿记录，无需在业务层手动处理。

    JSON 字段说明：
    - key_contributions: 贡献点列表
    - method: MethodModel 结构
    - experiments: ExperimentModel 结构
    - code_mapping: CodeMapping 列表
    - interview_qa: InterviewQA 列表
    - related_papers: 相关论文建议列表
    """

    __tablename__ = "analysis_results"

    id: Optional[int] = Field(default=None, primary_key=True)
    paper_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("papers.id", ondelete="CASCADE"),
            unique=True,
        ),
    )

    # 基本信息
    title: str  # 论文标题
    venue: str  # 会议/期刊
    year: int  # 发表年份
    one_sentence: str  # 一句话核心贡献（面试用）

    # 问题定义
    problem: str  # 解决什么问题，现有方法的局限性

    # 核心贡献（JSON 数组）
    key_contributions: list[str] = Field(default=[], sa_column=Column(JSON))

    # 方法论（JSON 对象）
    method: dict = Field(default={}, sa_column=Column(JSON))
    # method 字段结构对应 MethodModel，存入 DB 时序列化为 dict

    # 实验（JSON 对象）
    experiments: dict = Field(default={}, sa_column=Column(JSON))
    # experiments 字段结构对应 ExperimentModel

    # 代码映射（JSON 数组）
    code_mapping: list[dict] = Field(default=[], sa_column=Column(JSON))
    # 每个元素对应 CodeMapping

    # 面试问答（JSON 数组）
    interview_qa: list[dict] = Field(default=[], sa_column=Column(JSON))
    # 每个元素对应 InterviewQA

    # 局限性
    limitations: str  # 局限性和未来工作

    # 相关论文（JSON 数组）
    related_papers: list[str] = Field(default=[], sa_column=Column(JSON))

    # Mermaid 流程图（基于 pipeline 自动生成）
    mermaid_chart: Optional[str] = None  # Mermaid 语法字符串

    # 时间戳 — 在 database.py 的 CRUD 中手动设置
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
