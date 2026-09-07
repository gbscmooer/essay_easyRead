/**
 * TypeScript 类型定义
 *
 * 与后端 API Schema 一一对应。
 * 前端所有组件统一使用这些类型。
 */


/** 论文基础信息（列表用） */
export type PaperStatus = 'pending' | 'extracting' | 'analyzing' | 'done' | 'failed' | 'indexed'

export interface Paper {
  id: number
  filename: string
  title: string | null
  authors?: string | null        // 作者列表（逗号分隔），自动索引/解读填充
  venue: string | null
  year: number | null
  category?: string | null       // arXiv 学科分类，如 "cs.LG"，自动索引提取
  subjects?: string[]
  publication_type?: string
  publisher?: string | null
  providers?: string[]
  metadata_sources?: Record<string, string>
  metadata_updated_at?: string | null
  doi?: string | null
  arxiv_id?: string | null
  status: PaperStatus
  index_status?: 'none' | 'indexing' | 'indexed' | 'failed'  // 自动索引状态
  error_message: string | null
  code_url: string | null
  code_path: string | null
  code_status: 'none' | 'cloning' | 'ready' | 'failed'
  source?: 'uploaded' | 'expanded'
  level?: number   // 1=用户手动加；2+=联网扩展
  read_level?: 'full' | 'digest' | 'none'  // 解读层级：已精读 / 已速读 / 未解读
  created_at: string  // ISO 8601
  updated_at: string
}


/** 论文列表响应 */
export interface PaperListResponse {
  papers: Paper[]
  total: number
}


/** 方法中的关键模块 */
export interface KeyModule {
  name: string
  role: string
  innovation: string
}


/** 方法论 */
export interface Method {
  overview: string
  pipeline: string       // "\n" 分隔的步骤
  key_modules: KeyModule[]
}


/** 实验结果表格的一行 */
export interface ResultTableRow {
  method: string
  dataset: string
  metric: string
  value: string
}

/** 单个图表/表格的分析 */
export interface FigureAnalysis {
  name: string           // 图表名称，如 "Table 1", "Figure 3"
  caption: string        // 图表标题/说明
  page?: string          // 图表所在页码（纯数字），用于跳转
  chart_type: string     // 推荐图表类型: bar, line, scatter, table
  columns: string[]      // 表格列名
  rows: string[][]       // 表格数据，每行是字符串数组
  interpretation: string // AI 对该图表的解读
}

/** 实验设置与结果 */
export interface Experiments {
  datasets: string[]
  baselines: string[]
  main_results: string
  ablation: string
  result_table: ResultTableRow[]
  figures: FigureAnalysis[]  // 逐图分析
}


/** 论文概念 → 代码映射 */
export interface CodeMapping {
  concept: string
  likely_implementation: string
}


/** 面试问答卡片 */
export interface InterviewQA {
  q: string
  a: string
  source?: string       // 原文出处，如 "Section 3.2, Page 5"
  explanation?: string  // AI 进一步解释（按需生成）
}


/** 论文大纲条目 */
export interface OutlineItem {
  section: string
  page: string
  summary: string
}


/** 相关论文 */
export interface RelatedPaper {
  title: string
  authors: string
  venue: string
  year: number
  doi: string
  url: string
  relevance: string
  citation_count: number
}


/** 完整解析结果 */
export interface AnalysisResult {
  paper_id: number
  title: string
  authors: string
  venue: string
  year: number
  one_sentence: string
  problem: string
  key_contributions: string[]
  outline: OutlineItem[]
  method: Method
  experiments: Experiments
  code_mapping: CodeMapping[]
  interview_qa: InterviewQA[]
  limitations: string
  related_papers: RelatedPaper[]
  mermaid_chart: string | null
}


/** SSE 流式事件 */
export interface StreamEvent {
  event: 'status' | 'field' | 'done' | 'error'
  field_name: string | null
  data: Record<string, unknown>
}


/** 文件树节点 */
export interface FileNode {
  name: string
  type: 'file' | 'dir'
  path: string
  children?: FileNode[]
  size?: number
}


/** 模块依赖边 */
export interface DependencyEdge {
  source: string
  target: string
  imports: string[]
  circular?: boolean
  line?: number
}


/** 论文-代码映射 */
export interface PaperCodeMapping {
  concept: string
  file: string
  function: string
  explanation: string
}


/** 节点耦合指标 */
export interface NodeMetric {
  file: string
  fan_in: number
  fan_out: number
}

/** 代码分析结果 */
export interface CodeAnalysisResult {
  paper_id: number
  file_tree: FileNode
  data_flow: string | null
  dependency_graph: DependencyEdge[]
  paper_code_mapping: PaperCodeMapping[]
  node_metrics?: NodeMetric[]
  status: 'pending' | 'analyzing' | 'done' | 'failed'
  error_message: string | null
}


/** 错误响应 */
export interface ApiError {
  detail: string
  code: string
}


/** 解析进度状态（前端自定义） */
export type AnalysisStatus =
  | 'idle'
  | 'extracting'    // 正在提取 PDF 文本
  | 'analyzing'     // 正在调用 AI 分析
  | 'generating'    // 正在生成流程图
  | 'done'          // 完成
  | 'failed'        // 失败


// ============================================================
// Research 相关
// ============================================================

export type ResearchMode = 'local' | 'online'
export type ResearchStatus = 'pending' | 'running' | 'done' | 'failed'
export type ResearchStage = 'collect' | 'expand' | 'expand_arxiv' | 'chunk' | 'embed' | 'digest' | 'graph' | 'synthesize' | 'persist'

export interface ResearchRunCreate {
  mode: ResearchMode
  paper_ids?: number[] | null
  include_expanded?: boolean     // 是否纳入已下载的联网扩展论文（level>=2），默认 true
  deep?: number                  // 仅 online：扩展跳数（默认 1）
  expand_top_k?: number
  expand_total_limit?: number
  rebuild?: boolean
  map_density?: number           // 兼容旧请求，当前骨架算法忽略
  backbone_alpha?: number        // 多尺度骨架显著性 α（Disparity Filter）：局部显著性判据
  enable_shared_method?: boolean
}

export interface ResearchRun {
  id: number
  workspace_id: number
  mode: ResearchMode
  status: ResearchStatus
  current_stage: ResearchStage | null
  error_message: string | null
  params: Record<string, unknown>
  paper_count: number
  expanded_count: number
  chunk_count: number
  embed_done: number
  embed_total: number
  node_count: number
  edge_count: number
  created_at: string
  updated_at: string
}

export type GraphNodeKind = 'paper' | 'method' | 'dataset'
// related = 论文关联度边（v3 起唯一的关系边）；similar 为 v2 及以前持久化图谱的旧名
export type GraphEdgeKind = 'cites' | 'related' | 'similar' | 'bibcoupling' | 'cocitation' | 'shared-method' | 'uses-dataset'

export interface GraphNode {
  id: string
  kind: GraphNodeKind
  label: string
  meta: {
    paper_id?: number
    year?: number | null
    venue?: string | null
    source?: 'uploaded' | 'expanded'
    status?: string
    external_id?: string | null
    importance?: number        // PageRank 归一化 [0,1]，标识关键论文
    cluster?: number           // Louvain 社区编号（-1 为孤立点）
    cluster_label?: string     // 研究子方向标题（簇内最重要论文标题）
    foundational?: number      // 有向引用 PageRank [0,1]，奠基性
    centrality?: number        // 语义图加权 PageRank [0,1]，主题枢纽性
    read_level?: 'full' | 'digest' | 'none'  // 解读层级：全量精读 / 图谱速读 / 未解读
    summary?: string | null    // 图谱速读的简短总结
    citation_count?: number | null  // 召回带回的被引数（Scopus/OpenAlex）
  }
  /** 领域认知综合（stage_synthesize）的节点研判 */
  semantic?: NodeSemantic
}

/** 关联度单通道分解（对应 graphkit/relatedness.py 的八个通道） */
export interface RelationComponent {
  facet: string            // topic|purpose|method|term|dataset|baseline|bibcoupling|cocitation
  label: string            // 中文显示名
  sim: number              // 该通道归一化后的分数 [0,1]
  raw?: number             // 该通道的原始分（余弦 / Jaccard / Salton）
  weight: number           // 该对论文上该通道的实际生效权重
  contribution: number     // sim × weight，对总分的实际贡献（各通道之和 = 总分）
  reason: string           // 该通道的说明（相似度档位或共享项描述）
  shared: string[]         // 共享项（术语 / 数据集 / baseline）
  rank_source?: number | null
  rank_target?: number | null
}

export interface RelationDetail {
  weight: number                    // 综合关联度
  components: RelationComponent[]   // 各通道分解
  method?: string
  selection_method?: string
  disparity_retained?: boolean
}

export interface CitationContext {
  section: string
  context: string
  intent?: string   // baseline | reuse | inspiration | background
}

export interface GraphEdge {
  source: string
  target: string
  kind: GraphEdgeKind
  weight: number
  bridge?: boolean
  selection_method?: string
  detail?: RelationDetail
  contexts?: CitationContext[]
  /** 领域认知综合的因果研判（仅 cites 边有；related 无向边不猜因果） */
  semantic?: EdgeSemantic
}

// ── 领域认知综合（stage_synthesize）产物，与后端 models/research.py 一一对应 ──

/** 节点学术角色 */
export type NodeRole = 'landmark' | 'rising_star' | 'benchmark' | 'tool_sota' | 'survey' | 'regular'

export interface NodeSemantic {
  role: NodeRole
  short_takeaway: string       // 一句话核心创新（≤25 字）
  key_techniques: string[]
}

/** cites 边因果类型 */
export type EdgeRelationType = 'improves' | 'replaces' | 'evaluates_on' | 'inspires' | 'cites'

export interface EdgeSemantic {
  relation_type: EdgeRelationType
  relation_summary: string     // 一句话因果说明
}

export interface ParadigmInfo {
  cluster_id: number
  name: string                 // 流派具象名称
  core_philosophy: string
  pros: string[]
  cons: string[]
  representative_node_ids: string[]  // 图谱节点 id（"paper:{id}"）
}

export interface BriefingMilestone {
  year: string
  event: string
}

export interface ExecutiveBriefing {
  domain_problem: string
  milestone_timeline: BriefingMilestone[]
  benchmark_standards: string[]
  recent_hotspots: string[]
  open_challenges: string[]
  markdown_report: string
}

export interface GraphSynthesis {
  briefing?: ExecutiveBriefing | null
  paradigms: ParadigmInfo[]
  enriched_nodes: Record<string, NodeSemantic>
  enriched_edges: Record<string, EdgeSemantic>
  stats?: Record<string, unknown>
  partial?: boolean
}

export interface ResearchGraph {
  workspace_id: number
  run_id: number
  nodes: GraphNode[]
  edges: GraphEdge[]
  /** 领域认知综合产物；旧图谱 / 未开启时为 null */
  synthesis?: GraphSynthesis | null
  stats: {
    node_count: number
    edge_count: number
    by_kind: Partial<Record<GraphEdgeKind, number>>
    clusters?: number
    modularity?: number
    top_papers?: { id: string; label: string; importance: number }[]
    /** 骨架提取的工作点：目标密度、实际平均度、连通兜底桥接边数 */
    backbone?: {
      map_density?: number
      method?: string
      knn_k?: number | null
      alpha: number
      mean_degree: number
      bridge_pairs: number
      candidate_pairs: number
    }
    /** 本次建图使用的关联度通道权重与各通道覆盖的论文/对数 */
    relation_channels?: {
      method?: string
      weights: Record<string, number>
      coverage: Record<string, number>
      /** 自监督权重校准报告（用工作区自身引用结构当弱标签） */
      calibration?: {
        applied: boolean
        reason: string
        shrink: number
        n_positives: number
        n_negatives: number
        weights: Record<string, number>
        channels: Record<string, { auc: number; gini: number; n_pos: number }>
      } | null
    }
  }
  updated_at: string
}

// ── 领域探索（explorer，问句驱动免读领域认知，与后端 models/exploration.py 一一对应）──

export interface CitationMethod {
  algorithm: string
  version?: number
  adjustment?: string
  alpha?: number
  recent_years?: number[]
  baseline_years?: number[]
}

export interface ExplorationPaper {
  key: string
  paper_id?: number
  is_seed?: boolean
  source_kind?: string
  title: string
  year?: number | null
  citation_count: number
  /** 被引 / 年龄（引用速度）；年份未知为 null */
  citation_velocity?: number | null
  /** 新方法使用最近两个完整日历年；旧运行须按方法元数据区分 */
  recent_citations?: number | null
  citation_velocity_2y?: number | null
  /** 新方法为精确 Poisson 率检验 + BH；steady 仅表示未检出变化 */
  trajectory?: 'rising' | 'steady' | 'declining' | null
  /** 同年样本内百分位，不是全领域或 ESI 百分位 */
  citation_percentile?: number | null
  highly_cited?: boolean
  /** 新方法为显著上升；旧运行保留旧启发式标记 */
  surge?: boolean
  citation_metrics?: CitationMethod & {
    status?: string
    p_value?: number | null
    p_adjusted?: number | null
    significant?: boolean
    [key: string]: unknown
  }
  cluster: number
  cluster_label?: string
  doi?: string | null
  arxiv_id?: string | null
  url?: string | null
  pdf_url?: string | null
  scopus_id?: string | null
  authors?: string | null
  abstract?: string | null
  source_query?: string
  /** 指标富化：逐年引用 / 关键词 / 期刊（v1.4） */
  counts_by_year?: { year: number; cited_by_count: number }[]
  keywords?: string[]
  venue?: { name?: string | null; sjr?: number | null; snip?: number | null; citescore?: number | null } | null
}

export interface ExplorationVenueRanking {
  name: string
  count: number
  sjr?: number | null
  snip?: number | null
  citescore?: number | null
}

export interface ExplorationKeywordTrend {
  years: number[]
  rows: { keyword: string; counts: number[]; total: number }[]
}

export interface ExplorationDecompose {
  intent: 'novelty' | 'maturity' | 'landscape'
  en_queries: string[]
  focus_terms: string[]
  /** 无 LLM Key 时回退原句直检索 */
  fallback?: boolean
}

export interface ExplorationMetrics {
  total: number
  citation_method?: CitationMethod | string
  /** 年份 → 篇数（JSON 序列化后键为字符串） */
  by_year: Record<string, number>
  recent_share: number
  year_span: number[]
  top_cited: string[]
  rising: string[]
  /** 样本内同年高被引，不能解释为领域排名 */
  highly_cited?: string[]
  /** 主阵地期刊（语料内 ≥2 篇，带 SJR/SNIP，v1.4） */
  venue_ranking?: ExplorationVenueRanking[]
  /** 关键词 × 年份矩阵（热力图原料，v1.4） */
  keyword_trend?: ExplorationKeywordTrend
  /** 指标富化覆盖率 */
  enriched?: { with_history: number; with_venue_metrics: number }
  clusters: number
  modularity: number
}

export type NoveltyStatus = 'done_extensively' | 'done_partial' | 'emerging' | 'gap' | 'unclear'

export interface ExplorationVerdict {
  status: NoveltyStatus
  /** 仅保留旧数据兼容；未校准，不显示为概率或证据充分度 */
  confidence: number
  explanation: string
  evidence_keys: string[]
}

export interface ExplorationSynthesis {
  verdict?: ExplorationVerdict | null
  maturity: string
  landscape_summary: string
  hotspots: string[]
  gaps: string[]
  follow_ups: string[]
  /** 子方向流派（cluster_id 与 graph 节点 meta.cluster 对齐，复用范式卡片） */
  paradigms: ParadigmInfo[]
  markdown_report: string
  partial?: boolean
  stats?: Record<string, unknown>
}

export interface ExplorationResult {
  survey_report?: import('./survey').SurveyReport
  report_error?: string
  sources?: ResearchSourceOptions & { seed_count?: number; warnings?: string[] }
  decompose: ExplorationDecompose
  provider: string
  papers: ExplorationPaper[]
  metrics: ExplorationMetrics
  graph: {
    nodes: GraphNode[]
    edges: GraphEdge[]
    stats: {
      node_count: number
      edge_count: number
      by_kind: Partial<Record<GraphEdgeKind, number>>
      clusters?: number
      modularity?: number
    }
  }
  synthesis?: ExplorationSynthesis
}

export type ExplorationDepth = 'quick' | 'standard' | 'deep'

export interface ExplorationMessage {
  id: number
  run_id: number
  question: string
  answer: string
  status: 'pending' | 'running' | 'done' | 'failed'
  search_more: boolean
  selected_keys: string[]
  papers: ExplorationPaper[]
  evidence_keys: string[]
  context: { citations?: Record<string, string>; queries?: string[]; warnings?: string[]; paper_keys?: string[]; evidence_audit?: unknown }
  error_message: string | null
  created_at: string
  updated_at: string
}

export interface ExplorationRun {
  id: number
  question: string
  depth: ExplorationDepth
  status: 'pending' | 'running' | 'done' | 'failed'
  current_stage?: string | null
  error_message?: string | null
  provider: string
  options?: ResearchSourceOptions
  decompose?: ExplorationDecompose | null
  result?: ExplorationResult | null
  created_at: string
  updated_at: string
}

export interface ExplorationRunSummary {
  id: number
  question: string
  depth: string
  status: string
  provider: string
  options?: ResearchSourceOptions
  created_at: string
}

export interface ResearchSourceOptions {
  workspace_id?: number | null
  workspace_name?: string
  seed_paper_ids?: number[] | null
  search_online?: boolean
}

export interface RagCitationDebug {
  source_hits?: Record<string, number>
  fused_score?: number | null
  rerank_score?: number | null
  graph_score?: number | null
  chunk_id?: string | null
  channel_ranks?: Record<string, number>
}

export interface RagCitation {
  paper_id: number
  paper_title: string
  section_name: string
  snippet: string
  score: number
  download_status?: 'none' | 'available' | 'failed' | 'unavailable' | 'pending' | null
  metadata_only?: boolean | null
  debug?: RagCitationDebug | null
}

export interface RagPathEvidence {
  type?: string
  path?: string[]
  node_ids?: string[]
  paper_ids?: Array<number | null>
  edges?: Array<{ source: string; target: string; kind: string; weight: number }>
  text: string
  score?: number
}

export interface RagQueryResponse {
  answer: string
  citations: RagCitation[]
  path_evidence?: RagPathEvidence[]
  debug?: RagCitationDebug[]
}

/** expand 阶段 SSE recall_stats 单路摘要 */
export interface RecallChannelStats {
  channel: string
  hit_count: number
  failed: number
  latency_ms?: number
  errors?: string[]
  stage?: string
  error?: string
}

export interface EmbeddingConfig {
  provider: 'local' | 'openai' | 'siliconflow'
  model: string
  api_key: string
  base_url: string
  batch_size: number
  device: string
  max_input_tokens?: number   // 远程单条输入 token 截断上限（适配 bge-m3 8192；512 模型设 ~480）
  max_input_chars?: number    // 可选硬字符上限（0=不启用）
}


// ============================================================
// Tags
// ============================================================

export interface Tag {
  id: number
  name: string
  color: string
  paper_count?: number
}


// ============================================================
// Notes
// ============================================================

export interface Note {
  id: number
  paper_id: number
  content: string
  note_type: 'general' | 'section'
  section_name: string | null
  page_number: number | null
  created_at: string
  updated_at: string
}


// ============================================================
// Full-text search
// ============================================================

export interface SearchMatch {
  field: string
  snippet: string
  line?: number  // 全文匹配时的行号
}

export interface SearchResult {
  paper_id: number
  title: string
  authors?: string | null
  venue?: string | null
  year?: number | null
  source?: 'uploaded' | 'expanded'
  subjects?: string[]
  publication_type?: string
  providers?: string[]
  doi?: string | null
  arxiv_id?: string | null
  matched_fields: SearchMatch[]
}

export interface SearchResponse {
  results: SearchResult[]
  total: number
  query: string
}


// ============================================================
// Annotations (PDF markup)
// ============================================================

export type AnnotationType = 'highlight' | 'underline' | 'strikethrough'

export interface Annotation {
  id: number
  paper_id: number
  page_number: number
  x: number
  y: number
  width: number
  height: number
  annotation_type: AnnotationType
  color: string
  text: string
  group_id: string | null
  created_at: string
}

export interface AnnotationCreate {
  page_number: number
  x: number
  y: number
  width: number
  height: number
  annotation_type: AnnotationType
  color: string
  text: string
  group_id?: string | null
}
