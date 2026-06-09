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
}

export interface ResearchConfig {
  provider: 'openalex' | 'semantic_scholar' | 'local'
  email: string
  api_key: string
  similarity_threshold: number
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

export interface SimilarityConfig {
  provider: string
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
  similarity: SimilarityConfig
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
    base_url: 'https://token-plan-cn.xiaomimimo.com/anthropic',
    model: 'mimo-v2.5-pro',
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
  },
  research: {
    provider: 'openalex',
    email: '',
    api_key: '',
    similarity_threshold: 0.9,
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
  similarity: {
    provider: 'bge_centroid',
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
