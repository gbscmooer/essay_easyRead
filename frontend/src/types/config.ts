/**
 * 配置类型统一入口
 *
 * 从 features/settings/config.ts 重新导出，
 * 供全局消费者使用（不需要知道具体存放位置）。
 */
export type {
  AIModelConfig,
  LLMProxyConfig,
  UIConfig,
  EmbeddingConfig,
  ResearchConfig,
  RerankerConfig,
  RelationsConfig,
  RelationChannelName,
  ArxivExpandConfig,
  PromptTemplate,
  AnalysisPromptConfig,
  FindpapersConfig,
  AppConfig,
} from '@/features/settings/config'

export { DEFAULT_CONFIG } from '@/features/settings/config'
