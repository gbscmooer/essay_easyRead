/**
 * 设置页专用 API：内置提示词、GROBID 服务状态/下载
 */

import { get, post } from './client'

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
