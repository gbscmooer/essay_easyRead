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
export type ResearchStage = 'collect' | 'expand' | 'chunk' | 'embed' | 'graph' | 'persist'

export interface ResearchRunCreate {
  mode: ResearchMode
  paper_ids?: number[] | null
  include_expanded?: boolean     // 是否纳入已下载的联网扩展论文（level>=2），默认 true
  deep?: number                  // 仅 online：扩展跳数（默认 1）
  expand_top_k?: number
  expand_total_limit?: number
  rebuild?: boolean
  similarity_threshold?: number  // 已弃用，相似边改用多尺度骨架提取
  knn_k?: number                 // 已弃用：旧的全局 kNN 条数，保留兼容
  backbone_alpha?: number        // 多尺度骨架显著性 α（Disparity Filter）：越小越稀越精炼
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
export type GraphEdgeKind = 'cites' | 'similar' | 'shared-method' | 'uses-dataset'

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
  }
}

export interface SimilarityComponent {
  facet: string            // purpose | method | baseline | dataset
  label: string            // 中文显示名
  sim: number              // 该维度相似度 [0,1]
  weight: number           // 归一化后的权重
  contribution: number     // sim × weight，对综合分的实际贡献
  reason: string           // 该维度的说明（LLM 理由或共享项描述）
  shared: string[]         // 共享项（数据集/baseline）
}

export interface SimilarityDetail {
  weight: number                     // 综合相似度
  components: SimilarityComponent[]   // 各维度分解
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
  detail?: SimilarityDetail
  contexts?: CitationContext[]
}

export interface ResearchGraph {
  workspace_id: number
  run_id: number
  nodes: GraphNode[]
  edges: GraphEdge[]
  stats: {
    node_count: number
    edge_count: number
    by_kind: Record<GraphEdgeKind, number>
    clusters?: number
    top_papers?: { id: string; label: string; importance: number }[]
  }
  updated_at: string
}

export interface RagCitation {
  paper_id: number
  paper_title: string
  section_name: string
  snippet: string
  score: number
}

export interface RagQueryResponse {
  answer: string
  citations: RagCitation[]
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
