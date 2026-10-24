import type { ExplorationDepth } from '@/types'

export interface ResearchRevisionInput {
  question: string
  expected_state_version: number
  depth?: ExplorationDepth
  additional_questions?: string[]
  reuse_materials?: boolean
  exclude_asset_ids?: string[]
  workspace_id?: number
  seed_paper_ids?: number[]
  search_online?: boolean
}

export interface ResearchVersion {
  run_id: number
  question: string
  status: string
  depth: string
  created_at: string
  parent_run_id: number | null
  usage: { resource: string; used: number; usage_known: boolean }[]
}

export interface ResearchVersions {
  run_id: number
  state_version: number
  status: string
  worker_active: boolean
  ancestors: ResearchVersion[]
  children: ResearchVersion[]
  materials: { asset_id: string; title: string; source_kind: string }[]
}

/** 八阶段研究的版本链（后端 _legacy_versions） */
export interface LegacyVersionRow {
  id: number
  question: string
  status: string
  depth: string
  created_at: string
  verdict: string | null
  conditions: string[]
  reused: boolean
}

export interface LegacyVersions {
  run_id: number
  workflow_version: 'legacy_v1'
  current: LegacyVersionRow
  ancestors: LegacyVersionRow[]
  children: LegacyVersionRow[]
}
