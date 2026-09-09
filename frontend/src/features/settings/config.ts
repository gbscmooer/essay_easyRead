/**
 * 全局配置类型与默认值
 *
 * 从 SettingsPage 抽出，供设置页与深度研究设置共享，
 * 避免重复定义。与后端 backend/routers/analysis.py 的 AppConfig 一一对应。
 */
import type { Theme } from '@/theme/themes'


/** 思考强度：off=关闭；仅对支持的推理模型生效（Claude 3.7+/4+、OpenAI o 系/gpt-5、DeepSeek v4+） */
export type ThinkingEffort = 'off' | 'low' | 'medium' | 'high'
export type LLMApiFormat = 'openai_chat' | 'anthropic_messages'

export interface AIModelConfig {
  provider: string
  api_format: LLMApiFormat
  api_key: string
  base_url: string
  model: string
  temperature: number
  max_tokens: number
  thinking_effort: ThinkingEffort
  timeout_seconds: number
}

export interface UIConfig {
  highlight_duration: number
  zoom_step: number
  zoom_min: number
  zoom_max: number
  pinch_zoom_step: number
  theme: string
  font: string
  custom_font: string
  custom_themes?: Theme[]
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

export interface RecallWeightsConfig {
  pdf_ref: number
  backward: number
  forward: number
  recommend: number
  graph_ppr: number
}

export interface RecallConfig {
  method?: string
  enabled_sources: string[]
  rrf_k: number
  weights: RecallWeightsConfig
  candidate_limit: number
}

export interface HybridRetrievalConfig {
  method?: string
  rrf_k: number
  weights: {
    dense: number
    bm25: number
    graph_ppr: number
  }
  graph: {
    ppr_alpha: number
    top_k: number
    bridge_weight_factor: number
  }
}

export interface ResearchConfig {
  provider: 'auto' | 'scopus' | 'openalex' | 'semantic_scholar' | 'local'
  email: string
  api_key: string
  /** Elsevier / Scopus API Key（机构订阅校外访问用；校园网内可留空） */
  elsevier_api_key?: string
  /** 领域认知综合（stage_synthesize）：图谱后注入节点角色/引用因果/流派/全景简报 */
  synthesize?: { enabled: boolean }
  /** 旧配置兼容；当前骨架算法忽略目标度数 */
  map_density?: number
  backbone_alpha?: number
  backbone_method?: string
  link_distance?: number
  auto_digest?: boolean
  /** 多路召回 + RRF 融合扩展（discovery v2） */
  discovery_v2?: boolean
  recall?: RecallConfig
  hybrid_retrieval?: HybridRetrievalConfig
}

export interface RerankerConfig {
  enabled: boolean
  provider: 'siliconflow' | 'custom'
  model: string
  api_key: string
  base_url: string
  candidate_k: number
  top_n: number
}

export type RelationChannelName =
  | 'topic' | 'purpose' | 'method' | 'term'
  | 'dataset' | 'baseline' | 'bibcoupling' | 'cocitation'

/** 论文关联度配置（后端 graphkit/relatedness.py 的唯一一套算法） */
export interface RelationsConfig {
  method?: string
  /** 旧配置兼容；当前算法不执行自动校准 */
  calibrate: boolean
  /** 全部通道共同归一的用户偏好，默认等权 */
  weights: Record<RelationChannelName, number>
}

export interface ArxivExpandConfig {
  enabled: boolean
  parser: 'grobid' | 'regex'
  grobid_url: string
  top_k: number
  use_llm_title: boolean
  max_llm_refs: number
}

export interface PromptTemplate {
  id: string
  name: string
  content: string
}

export interface AnalysisPromptConfig {
  active: string            // builtin | custom:<id>
  custom: PromptTemplate[]
}

export interface FindpapersConfig {
  enabled: boolean
  databases: string[]
  max_results: number
}

export interface AppConfig {
  llm: AIModelConfig
  analysis_prompt: AnalysisPromptConfig
  ui: UIConfig
  embedding: EmbeddingConfig
  research: ResearchConfig
  reranker: RerankerConfig
  relations: RelationsConfig
  arxiv_expand: ArxivExpandConfig
  findpapers: FindpapersConfig
}


export const DEFAULT_CONFIG: AppConfig = {
  llm: {
    provider: 'openai',
    api_format: 'openai_chat',
    api_key: '',
    base_url: 'https://api.openai.com/v1',
    model: 'gpt-4o-mini',
    temperature: 0.2,
    max_tokens: 16384,
    thinking_effort: 'off',
    timeout_seconds: 150,
  },
  analysis_prompt: {
    active: 'builtin',
    custom: [],
  },
  ui: {
    highlight_duration: 3000,
    zoom_step: 0.2,
    zoom_min: 0.4,
    zoom_max: 3.0,
    pinch_zoom_step: 0.02,
    theme: 'light',
    font: 'sans',
    custom_font: '',
    custom_themes: [],
  },
  embedding: {
    provider: 'local',
    model: 'BAAI/bge-m3',
    api_key: '',
    base_url: 'https://api.openai.com/v1',
    batch_size: 32,
    device: 'auto',
    max_input_tokens: 7000,
    max_input_chars: 0,
  },
  research: {
    provider: 'auto',
    email: '',
    api_key: '',
    elsevier_api_key: '',
    synthesize: { enabled: true },
    map_density: 8,
    backbone_alpha: 0.05,
    backbone_method: 'mutual_knn_with_disparity_annotation_v1',
    link_distance: 50,
    auto_digest: true,
    discovery_v2: false,
    recall: {
      method: 'weighted_rrf_v1',
      enabled_sources: ['backward', 'forward', 'recommend', 'pdf_ref', 'graph_ppr'],
      rrf_k: 60,
      weights: {
        pdf_ref: 1.0,
        backward: 1.0,
        forward: 1.0,
        recommend: 1.0,
        graph_ppr: 1.0,
      },
      candidate_limit: 50,
    },
    hybrid_retrieval: {
      method: 'weighted_rrf_v1',
      rrf_k: 60,
      weights: {
        dense: 1.0,
        bm25: 1.0,
        graph_ppr: 1.0,
      },
      graph: {
        ppr_alpha: 0.85,
        top_k: 50,
        bridge_weight_factor: 0,
      },
    },
  },
  reranker: {
    enabled: true,
    provider: 'siliconflow',
    model: 'BAAI/bge-reranker-v2-m3',
    api_key: '',
    base_url: '',
    candidate_k: 30,
    top_n: 5,
  },
  relations: {
    method: 'weighted_rrf_v1',
    calibrate: false,
    weights: {
      topic: 0.125,
      purpose: 0.125,
      method: 0.125,
      term: 0.125,
      dataset: 0.125,
      baseline: 0.125,
      bibcoupling: 0.125,
      cocitation: 0.125,
    },
  },
  arxiv_expand: {
    enabled: true,
    parser: 'grobid',
    grobid_url: 'http://localhost:8070',
    top_k: 5,
    use_llm_title: true,
    max_llm_refs: 15,
  },
  findpapers: {
    enabled: false,
    databases: ['arxiv', 'openalex', 'pubmed', 'semantic_scholar'],
    max_results: 10,
  },
}
