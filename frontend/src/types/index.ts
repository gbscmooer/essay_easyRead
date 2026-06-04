/**
 * TypeScript 类型定义
 *
 * 与后端 API Schema 一一对应。
 * 前端所有组件统一使用这些类型。
 */


/** 论文基础信息（列表用） */
export interface Paper {
  id: number
  filename: string
  title: string | null
  venue: string | null
  year: number | null
  status: 'pending' | 'extracting' | 'analyzing' | 'done' | 'failed'
  error_message: string | null
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

/** 实验设置与结果 */
export interface Experiments {
  datasets: string[]
  baselines: string[]
  main_results: string
  ablation: string
  result_table: ResultTableRow[]
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
}


/** 完整解析结果 */
export interface AnalysisResult {
  paper_id: number
  title: string
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
