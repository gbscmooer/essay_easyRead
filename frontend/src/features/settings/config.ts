/**
 * 全局配置类型与默认值
 *
 * 从 SettingsPage 抽出，供设置页与深度研究设置共享，
 * 避免重复定义。与后端 backend/routers/analysis.py 的 AppConfig 一一对应。
 */
import type { Theme } from '@/theme/themes'


export interface AIModelConfig {
  api_key: string
  base_url: string
  model: string
  system_prompt: string
  temperature: number
  max_tokens: number
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
  enabled_sources: string[]
  rrf_k: number
  weights: RecallWeightsConfig
  candidate_limit: number
}

export interface HybridRetrievalConfig {
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
  provider: 'openalex' | 'semantic_scholar' | 'local'
  email: string
  api_key: string
  backbone_alpha?: number
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

/** 论文关联度通道权重（后端 graphkit/relatedness.py 的唯一一套算法） */
export interface RelationsConfig {
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
  translate: AIModelConfig
  analysis: AIModelConfig
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
  translate: {
    api_key: '',
    base_url: 'https://api.openai.com/v1',
    model: 'gpt-4o-mini',
    system_prompt: '',
    temperature: 0.3,
    max_tokens: 2048,
  },
  analysis: {
    api_key: '',
    base_url: 'https://api.openai.com/v1',
    model: 'gpt-4o-mini',
    system_prompt: '',
    temperature: 0.1,
    max_tokens: 16384,
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
    provider: 'openalex',
    email: '',
    api_key: '',
    backbone_alpha: 0.25,
    link_distance: 50,
    auto_digest: true,
    discovery_v2: false,
    recall: {
      enabled_sources: ['backward', 'forward', 'recommend', 'pdf_ref', 'graph_ppr'],
      rrf_k: 60,
      weights: {
        pdf_ref: 1.0,
        backward: 1.0,
        forward: 0.9,
        recommend: 0.85,
        graph_ppr: 0.8,
      },
      candidate_limit: 50,
    },
    hybrid_retrieval: {
      rrf_k: 60,
      weights: {
        dense: 1.0,
        bm25: 0.95,
        graph_ppr: 0.8,
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
    weights: {
      topic: 0.18,
      purpose: 0.24,
      method: 0.16,
      term: 0.14,
      dataset: 0.06,
      baseline: 0.04,
      bibcoupling: 0.12,
      cocitation: 0.06,
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
