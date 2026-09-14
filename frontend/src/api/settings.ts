/**
 * 设置页专用 API：内置提示词、GROBID 服务状态/下载、LLM 配置档案
 */

import { get, post, put, del } from './client'
import type { LLMProfileInput, LLMProfilesState } from '@/features/settings/config'

export interface GrobidStatus {
  url: string
  alive: boolean
  image: string
  image_present: boolean
  pulling: boolean
  pull_ok: boolean | null
  pull_log: string
}

/** 内置（只读）解读系统提示词 */
export async function getBuiltinAnalysisPrompt(): Promise<string> {
  const r = await get<{ content: string }>('/analysis/builtin-analysis-prompt')
  return r.content
}

/** 查询 GROBID 服务情况（服务可达 + 镜像是否已下载 + 是否正在拉取） */
export async function getGrobidStatus(): Promise<GrobidStatus> {
  return get<GrobidStatus>('/analysis/grobid/status')
}

/** 触发后台下载 GROBID 镜像 */
export async function pullGrobidImage(): Promise<{ running: boolean; image: string }> {
  return post<{ running: boolean; image: string }>('/analysis/grobid/pull')
}

/* ── 配置文件管理（YAML / TOML / JSON 读写与导入导出） ──── */

export interface ConfigFileInfo {
  active_path: string
  full_path: string
  exists: boolean
  format: 'yaml' | 'toml' | 'json'
  supported_formats: string[]
  key_path?: string
  key_file_exists?: boolean
}

/** 获取配置文件元信息（当前文件路径、格式等） */
export async function getConfigFileInfo(): Promise<ConfigFileInfo> {
  return get<ConfigFileInfo>('/analysis/config/file-info')
}

/** 获取导出配置文件的下载链接 */
export function getExportConfigUrl(format: 'yaml' | 'toml' | 'json' = 'yaml', includeSecrets: boolean = true): string {
  return `/api/analysis/config/export?format=${format}&include_secrets=${includeSecrets}`
}

/** 上传配置文件（YAML/TOML/JSON）并应用 */
export async function importConfigFile(file: File): Promise<{ message: string; file_info: ConfigFileInfo }> {
  const { upload } = await import('./client')
  return upload<{ message: string; file_info: ConfigFileInfo }>('/analysis/config/import', file)
}

/** 导入配置文本（YAML/TOML/JSON）并应用 */
export async function importConfigText(content: string, format?: string): Promise<{ message: string; file_info: ConfigFileInfo }> {
  return post<{ message: string; file_info: ConfigFileInfo }>('/analysis/config/import', { content, format })
}

/* ── LLM 配置档案（多套可切换的全局模型接口设置） ──── */

export type { LLMProfile, LLMProfilesState, LLMProfileInput } from '@/features/settings/config'

/** 列出全部配置档案（api_key 掩码回显）与当前启用 id */
export async function listLLMProfiles(): Promise<LLMProfilesState> {
  return get<LLMProfilesState>('/analysis/llm-profiles')
}

/** 创建配置档案 */
export async function createLLMProfile(payload: LLMProfileInput): Promise<{ message: string } & LLMProfilesState> {
  return post<{ message: string } & LLMProfilesState>('/analysis/llm-profiles', payload)
}

/** 更新配置档案（api_key 掩码占位时后端保留已存密钥） */
export async function updateLLMProfile(id: string, payload: LLMProfileInput): Promise<{ message: string } & LLMProfilesState> {
  return put<{ message: string } & LLMProfilesState>(`/analysis/llm-profiles/${id}`, payload)
}

/** 删除配置档案（删除启用中的档案时全局生效配置保持不变） */
export async function deleteLLMProfile(id: string): Promise<{ message: string } & LLMProfilesState> {
  return del<{ message: string } & LLMProfilesState>(`/analysis/llm-profiles/${id}`)
}

/** 读取某档案的完整 API Key（设置页「显示」与「复制」带出真钥；本机自用信任边界） */
export async function revealLLMProfileKey(id: string): Promise<{ api_key: string }> {
  return get<{ api_key: string }>(`/analysis/llm-profiles/${id}/key`)
}

/** 启用配置档案：物化为全局 llm 配置，全部文本生成功能自动跟随 */
export async function activateLLMProfile(id: string): Promise<{ message: string } & LLMProfilesState> {
  return post<{ message: string } & LLMProfilesState>(`/analysis/llm-profiles/${id}/activate`, {})
}
