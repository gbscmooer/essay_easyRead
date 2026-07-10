/**
 * 深度研究相关设置区块集合
 *
 * Embedding / Research Provider / Reranker / Similarity / arXiv / findpapers
 * 共 6 个子组件，从 SettingsPage 拆出。
 * 只负责渲染与改写传入的 config 切片，保存逻辑由宿主页面处理。
 */
import { useState } from 'react'
import type { Dispatch, SetStateAction } from 'react'
import { TestButton } from './TestButton'
import { GrobidPanel } from './GrobidPanel'
import { post } from '@/api/client'
import type {
  AppConfig,
  EmbeddingConfig,
  HybridRetrievalConfig,
  RecallConfig,
} from './config'


type SetConfigFn = Dispatch<SetStateAction<AppConfig>>

interface SectionProps {
  config: AppConfig
  setConfig: SetConfigFn
}

/* ── 通用样式常量 ─────────────────────────────────── */

const CARD: React.CSSProperties = {}

const LABEL: React.CSSProperties = { color: 'var(--text-muted)' }

const INPUT: React.CSSProperties = {
  backgroundColor: 'var(--bg-elevated)',
  borderColor: 'var(--border)',
  color: 'var(--text-primary)',
}

function PillButton({
  active,
  children,
  onClick,
}: {
  active: boolean
  children: React.ReactNode
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className="px-3 py-1.5 text-xs rounded-lg border transition-colors"
      style={{
        borderColor: active ? 'var(--accent)' : 'var(--border)',
        backgroundColor: active ? 'var(--accent-subtle)' : 'transparent',
        color: active ? 'var(--accent)' : 'var(--text-secondary)',
      }}
    >
      {children}
    </button>
  )
}


// ============================================================
// Embedding 模型
// ============================================================

export function EmbeddingSection({ config, setConfig }: SectionProps) {
  return (
    <div className="panel-section space-y-4" style={CARD}>
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>
            Embedding 模型
          </h2>
          <p className="text-xs mt-0.5" style={LABEL}>
            用于深度研究的向量嵌入模型。本地模式首次运行需下载模型（~2GB）。
          </p>
        </div>
        <TestButton
          type="embedding"
          api_key={config.embedding.api_key}
          base_url={config.embedding.base_url}
          model={config.embedding.model}
          provider={config.embedding.provider}
        />
      </div>

      {/* Provider 选择 */}
      <div>
        <label className="block text-xs mb-2" style={LABEL}>Provider</label>
        <div className="flex gap-2">
          {([
            { key: 'local' as const, label: '本地 (BGE-M3)' },
            { key: 'siliconflow' as const, label: '硅基流动' },
            { key: 'openai' as const, label: 'OpenAI' },
          ]).map(p => (
            <PillButton
              key={p.key}
              active={config.embedding.provider === p.key}
              onClick={() => setConfig(prev => {
                const defaults: Record<string, Partial<EmbeddingConfig>> = {
                  local: { model: 'BAAI/bge-m3', base_url: 'https://api.openai.com/v1' },
                  siliconflow: { model: 'BAAI/bge-m3', base_url: 'https://api.siliconflow.cn/v1' },
                  openai: { model: 'text-embedding-3-small', base_url: 'https://api.openai.com/v1' },
                }
                return { ...prev, embedding: { ...prev.embedding, provider: p.key, ...defaults[p.key] } }
              })}
            >
              {p.label}
            </PillButton>
          ))}
        </div>
      </div>

      {/* 模型名 */}
      <div>
        <label className="block text-xs mb-1" style={LABEL}>模型名称</label>
        <input
          type="text"
          value={config.embedding.model}
          onChange={e => setConfig(prev => ({ ...prev, embedding: { ...prev.embedding, model: e.target.value } }))}
          placeholder={config.embedding.provider === 'local' ? 'BAAI/bge-m3' : config.embedding.provider === 'siliconflow' ? 'BAAI/bge-m3' : 'text-embedding-3-small'}
          className="w-full px-3 py-2 text-sm rounded-lg border focus:outline-none transition-colors"
          style={INPUT}
        />
      </div>

      {/* OpenAI / SiliconFlow 专用配置 */}
      {(config.embedding.provider === 'openai' || config.embedding.provider === 'siliconflow') && (
        <>
          <div>
            <label className="block text-xs mb-1" style={LABEL}>API Base URL</label>
            <input
              type="text"
              value={config.embedding.base_url}
              onChange={e => setConfig(prev => ({ ...prev, embedding: { ...prev.embedding, base_url: e.target.value } }))}
              className="w-full px-3 py-2 text-sm rounded-lg border focus:outline-none transition-colors"
              style={INPUT}
            />
          </div>
          <div>
            <label className="block text-xs mb-1" style={LABEL}>API Key</label>
            <input
              type="password"
              value={config.embedding.api_key}
              onChange={e => setConfig(prev => ({ ...prev, embedding: { ...prev.embedding, api_key: e.target.value } }))}
              placeholder="sk-..."
              className="w-full px-3 py-2 text-sm rounded-lg border focus:outline-none transition-colors font-mono"
              style={INPUT}
            />
          </div>
        </>
      )}

      {config.embedding.provider === 'local' && (
        <p className="text-[10px]" style={{ color: 'var(--text-disabled)' }}>
          本地模式使用 sentence-transformers 加载模型，首次运行会自动下载。切换 provider 后建议重建索引以避免向量维度不匹配。
        </p>
      )}

      {/* 高级参数（按模型上下文长度可调） */}
      <details className="group pt-2" style={{ borderTop: '1px solid var(--border)' }}>
        <summary
          className="flex items-center gap-1.5 cursor-pointer text-xs select-none"
          style={{ color: 'var(--text-secondary)' }}
        >
          <span className="transition-transform group-open:rotate-90">▸</span>
          高级参数（截断 / 批大小）
        </summary>
        <div className="mt-3 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs mb-1" style={LABEL}>单条输入 token 上限</label>
              <input
                type="number"
                min={64}
                max={32768}
                value={config.embedding.max_input_tokens ?? 7000}
                onChange={e => setConfig(prev => ({ ...prev, embedding: { ...prev.embedding, max_input_tokens: Number(e.target.value) || 7000 } }))}
                className="w-full px-3 py-2 text-sm rounded-lg border focus:outline-none transition-colors"
                style={INPUT}
              />
              <p className="text-[10px] mt-1" style={{ color: 'var(--text-disabled)' }}>
                远程模型的单条截断上限（CJK≈1 字/token，英文≈4 字/token）。
                <b>bge-m3</b> 8192 → 填 7000；<b>bge-large-en-v1.5</b> 512 → 填 480；更大上下文的模型可调更高。
              </p>
            </div>
            <div>
              <label className="block text-xs mb-1" style={LABEL}>硬字符上限（0=关闭）</label>
              <input
                type="number"
                min={0}
                max={200000}
                value={config.embedding.max_input_chars ?? 0}
                onChange={e => setConfig(prev => ({ ...prev, embedding: { ...prev.embedding, max_input_chars: Number(e.target.value) || 0 } }))}
                className="w-full px-3 py-2 text-sm rounded-lg border focus:outline-none transition-colors"
                style={INPUT}
              />
              <p className="text-[10px] mt-1" style={{ color: 'var(--text-disabled)' }}>
                token 截断之外再加一道字符硬上限保险。一般留 0；只在仍偶发 400 时设个保守值（如 6000）。
              </p>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs mb-1" style={LABEL}>批大小 batch_size</label>
              <input
                type="number"
                min={1}
                max={256}
                value={config.embedding.batch_size}
                onChange={e => setConfig(prev => ({ ...prev, embedding: { ...prev.embedding, batch_size: Number(e.target.value) || 32 } }))}
                className="w-full px-3 py-2 text-sm rounded-lg border focus:outline-none transition-colors"
                style={INPUT}
              />
              <p className="text-[10px] mt-1" style={{ color: 'var(--text-disabled)' }}>
                本地模式每批编码条数（显存不足可调小）。
              </p>
            </div>
          </div>
          <p className="text-[10px]" style={{ color: 'color-mix(in srgb, var(--warning, #eab308) 80%, transparent)' }}>
            提示：换 embedding 模型后必须「重建索引」重新嵌入 —— bge-large 与 bge-m3 同为 1024 维，不重建会让新旧向量混在一起，相似度全错。
          </p>
        </div>
      </details>
    </div>
  )
}


// ============================================================
// 深度研究 — 论文扩展源
// ============================================================

export function ResearchProviderSection({ config, setConfig }: SectionProps) {
  return (
    <div className="panel-section space-y-4" style={CARD}>
      <div>
        <h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>
          深度研究 — 论文扩展源
        </h2>
        <p className="text-xs mt-0.5" style={LABEL}>
          决定"联网扩展"阶段从哪里抓取参考文献。本地模式表示不联网，只用工作区内已上传的论文构建图谱。
        </p>
      </div>

      <div>
        <label className="block text-xs mb-2" style={LABEL}>Provider</label>
        <div className="flex gap-2 flex-wrap">
          {([
            { key: 'openalex' as const, label: 'OpenAlex（推荐）' },
            { key: 'semantic_scholar' as const, label: 'Semantic Scholar' },
            { key: 'local' as const, label: '本地（不联网）' },
          ]).map(p => (
            <PillButton
              key={p.key}
              active={config.research.provider === p.key}
              onClick={() => setConfig(prev => ({
                ...prev,
                research: { ...prev.research, provider: p.key },
              }))}
            >
              {p.label}
            </PillButton>
          ))}
        </div>
      </div>

      {config.research.provider === 'openalex' && (
        <div>
          <label className="block text-xs mb-1" style={LABEL}>
            邮箱（可选 · polite pool）
          </label>
          <input
            type="email"
            value={config.research.email}
            onChange={e => setConfig(prev => ({
              ...prev,
              research: { ...prev.research, email: e.target.value },
            }))}
            placeholder="you@example.com"
            className="w-full px-3 py-2 text-sm rounded-lg border focus:outline-none transition-colors"
            style={INPUT}
          />
          <p className="text-[10px] mt-1" style={{ color: 'var(--text-disabled)' }}>
            填入邮箱可进入 OpenAlex polite pool，获得更稳定的速率配额。不填也能用。
          </p>
        </div>
      )}

      {config.research.provider === 'semantic_scholar' && (
        <div>
          <label className="block text-xs mb-1" style={LABEL}>
            API Key（强烈建议填写）
          </label>
          <input
            type="password"
            value={config.research.api_key}
            onChange={e => setConfig(prev => ({
              ...prev,
              research: { ...prev.research, api_key: e.target.value },
            }))}
            placeholder="留空使用公共配额（容易 429）"
            className="w-full px-3 py-2 text-sm rounded-lg border focus:outline-none transition-colors font-mono"
            style={INPUT}
          />
          <p className="text-[10px] mt-1" style={{ color: 'var(--text-disabled)' }}>
            申请地址：
            <a
              href="https://www.semanticscholar.org/product/api"
              target="_blank"
              rel="noreferrer"
              className="ml-1"
              style={{ color: 'var(--accent)' }}
            >
              semanticscholar.org/product/api
            </a>
            （免费，约 1–2 天通过）
          </p>
        </div>
      )}

      {config.research.provider === 'local' && (
        <p className="text-[10px]" style={{ color: 'var(--text-disabled)' }}>
          本地模式下，"联网扩展"阶段会被直接跳过；图谱仅基于工作区已上传的论文构建。
        </p>
      )}

      <div className="pt-3" style={{ borderTop: '1px solid var(--border)' }}>
        <label className="flex items-start gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={config.research.auto_digest ?? true}
            onChange={e => setConfig(prev => ({ ...prev, research: { ...prev.research, auto_digest: e.target.checked } }))}
            className="mt-0.5 accent-[var(--accent)]"
          />
          <span>
            <span className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
              深度研究时自动「图谱速读」未解读论文
            </span>
            <p className="text-[10px] mt-0.5" style={{ color: 'var(--text-muted)' }}>
              开启后，每次深度研究在构建图谱前会对未精读的论文做一次轻量速读（只抽研究目的/方法/数据集/基线 + 简短总结），
              让它们也获得可解释的图谱关系。会消耗少量 LLM Token（约为全量精读的 1/5~1/10）。关闭则只能手动点「速读未解读论文」。
            </p>
          </span>
        </label>
      </div>

      {/* 已弃用参数：仅展示不回写误导 */}
      <details className="pt-2 opacity-70" style={{ borderTop: '1px solid var(--border)' }}>
        <summary className="text-[11px] cursor-pointer select-none" style={{ color: 'var(--text-muted)' }}>
          已弃用参数（不再影响图谱）
        </summary>
        <p className="text-[10px] mt-2" style={{ color: 'var(--text-disabled)' }}>
          「相似度阈值」与「共享方法边」已被多尺度骨架提取（骨架密度 α）和结构边（文献耦合 / 共引）取代。
          请在研究页「高级设置」中调整骨架密度 α。
          {config.research.similarity_threshold != null && (
            <> 旧配置 similarity_threshold={config.research.similarity_threshold.toFixed(2)} 已忽略。</>
          )}
        </p>
      </details>
    </div>
  )
}


// ============================================================
// 多路召回 / RRF 融合（discovery v2）
// ============================================================

const RECALL_SOURCE_OPTIONS: { key: string; label: string; hint: string }[] = [
  { key: 'backward', label: '反向引用', hint: 'seed 论文参考文献列表（被引文献）' },
  { key: 'forward', label: '正向引用', hint: '引用 seed 的后续论文（施引文献）' },
  { key: 'recommend', label: '相关推荐', hint: 'Provider 语义相关推荐' },
  { key: 'pdf_ref', label: 'PDF 引用', hint: 'GROBID/regex 从 PDF 正文解析的引用（需启用 arXiv 扩展）' },
  { key: 'graph_ppr', label: '图谱 PPR', hint: '复用工作区已有图谱做结构召回；无历史图谱时自动跳过' },
]

const DEFAULT_RECALL: RecallConfig = {
  enabled_sources: ['backward', 'forward', 'recommend', 'pdf_ref', 'graph_ppr'],
  rrf_k: 60,
  weights: { pdf_ref: 1.0, backward: 1.0, forward: 0.9, recommend: 0.85, graph_ppr: 0.8 },
  candidate_limit: 50,
}

function ensureRecall(research: AppConfig['research']): RecallConfig {
  return {
    ...DEFAULT_RECALL,
    ...(research.recall ?? {}),
    weights: { ...DEFAULT_RECALL.weights, ...(research.recall?.weights ?? {}) },
  }
}

const DEFAULT_HYBRID: HybridRetrievalConfig = {
  rrf_k: 60,
  weights: { dense: 1.0, bm25: 0.95, graph_ppr: 0.8 },
  graph: { ppr_alpha: 0.85, top_k: 50, bridge_weight_factor: 0 },
}

function ensureHybrid(research: AppConfig['research']): HybridRetrievalConfig {
  return {
    ...DEFAULT_HYBRID,
    ...(research.hybrid_retrieval ?? {}),
    weights: {
      ...DEFAULT_HYBRID.weights,
      ...(research.hybrid_retrieval?.weights ?? {}),
    },
    graph: {
      ...DEFAULT_HYBRID.graph,
      ...(research.hybrid_retrieval?.graph ?? {}),
    },
  }
}

export function RecallSection({ config, setConfig }: SectionProps) {
  const recall = ensureRecall(config.research)
  const hybrid = ensureHybrid(config.research)
  const discoveryOn = config.research.discovery_v2 ?? false

  const patchRecall = (patch: Partial<RecallConfig>) => {
    setConfig(prev => ({
      ...prev,
      research: {
        ...prev.research,
        recall: { ...ensureRecall(prev.research), ...patch },
      },
    }))
  }

  const toggleSource = (key: string) => {
    const next = recall.enabled_sources.includes(key)
      ? recall.enabled_sources.filter(s => s !== key)
      : [...recall.enabled_sources, key]
    patchRecall({ enabled_sources: next })
  }

  const patchHybrid = (patch: Partial<HybridRetrievalConfig>) => {
    setConfig(prev => ({
      ...prev,
      research: {
        ...prev.research,
        hybrid_retrieval: { ...ensureHybrid(prev.research), ...patch },
      },
    }))
  }

  return (
    <div className="panel-section space-y-4" style={CARD}>
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>
            多路召回与 RRF 融合
          </h2>
          <p className="text-xs mt-0.5" style={LABEL}>
            联网扩展时并行拉取多源候选，按 RRF 公式融合：
            <code className="mx-1 px-1 rounded" style={{ backgroundColor: 'var(--bg-elevated)' }}>
              Σ wₛ/(k+rank)
            </code>
            默认 k=60。单源失败不阻断整体流程。
          </p>
        </div>
        <label className="flex items-center gap-2 text-xs cursor-pointer shrink-0" style={{ color: 'var(--text-secondary)' }}>
          <input
            type="checkbox"
            checked={discoveryOn}
            onChange={e => setConfig(prev => ({
              ...prev,
              research: { ...prev.research, discovery_v2: e.target.checked },
            }))}
            className="accent-[var(--accent)]"
          />
          discovery v2
        </label>
      </div>

      {!discoveryOn && (
        <p className="text-[11px] px-3 py-2 rounded-lg" style={{ color: 'var(--warning, #eab308)', backgroundColor: 'color-mix(in srgb, var(--warning, #eab308) 12%, transparent)' }}>
          discovery v2 尚未通过 live/Scholar 发布门禁，默认关闭；可为实验评测手动启用。
        </p>
      )}

      <div>
        <label className="block text-xs mb-2" style={LABEL}>启用的召回源</label>
        <div className="flex flex-wrap gap-2">
          {RECALL_SOURCE_OPTIONS.map(opt => (
            <label
              key={opt.key}
              className="flex items-center gap-1.5 text-xs cursor-pointer px-2.5 py-1.5 rounded-lg border"
              style={{
                borderColor: recall.enabled_sources.includes(opt.key) ? 'var(--accent)' : 'var(--border)',
                color: recall.enabled_sources.includes(opt.key) ? 'var(--accent)' : 'var(--text-secondary)',
              }}
              title={opt.hint}
            >
              <input
                type="checkbox"
                checked={recall.enabled_sources.includes(opt.key)}
                onChange={() => toggleSource(opt.key)}
                className="accent-[var(--accent)]"
              />
              {opt.label}
            </label>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs mb-1" style={LABEL}>RRF 常数 k</label>
          <input
            type="number"
            min={1}
            max={200}
            value={recall.rrf_k}
            onChange={e => patchRecall({ rrf_k: Number(e.target.value) || 60 })}
            className="w-full px-3 py-2 text-sm rounded-lg border focus:outline-none transition-colors"
            style={INPUT}
          />
          <p className="text-[10px] mt-1" style={{ color: 'var(--text-disabled)' }}>
            越大则排名靠后的候选也有机会入选（更平滑）
          </p>
        </div>
        <div>
          <label className="block text-xs mb-1" style={LABEL}>融合后候选上限</label>
          <input
            type="number"
            min={5}
            max={500}
            value={recall.candidate_limit}
            onChange={e => patchRecall({ candidate_limit: Number(e.target.value) || 50 })}
            className="w-full px-3 py-2 text-sm rounded-lg border focus:outline-none transition-colors"
            style={INPUT}
          />
        </div>
      </div>

      <details className="group">
        <summary className="flex items-center gap-1.5 cursor-pointer text-xs select-none" style={{ color: 'var(--text-secondary)' }}>
          <span className="transition-transform group-open:rotate-90">▸</span>
          各通道 RRF 权重
        </summary>
        <div className="mt-3 grid grid-cols-2 gap-3">
          {RECALL_SOURCE_OPTIONS.map(opt => (
            <div key={opt.key}>
              <label className="block text-xs mb-1" style={LABEL}>{opt.label}</label>
              <input
                type="number"
                min={0}
                max={3}
                step={0.05}
                value={recall.weights[opt.key as keyof typeof recall.weights] ?? 1}
                onChange={e => patchRecall({
                  weights: {
                    ...recall.weights,
                    [opt.key]: Number(e.target.value) || 0,
                  },
                })}
                className="w-full px-3 py-2 text-sm rounded-lg border focus:outline-none transition-colors"
                style={INPUT}
              />
            </div>
          ))}
        </div>
      </details>

      <details className="group">
        <summary className="flex items-center gap-1.5 cursor-pointer text-xs select-none" style={{ color: 'var(--text-secondary)' }}>
          <span className="transition-transform group-open:rotate-90">▸</span>
          工作区 Hybrid RAG 参数
        </summary>
        <div className="mt-3 grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs mb-1" style={LABEL}>Hybrid RRF k</label>
            <input
              type="number"
              min={1}
              max={200}
              value={hybrid.rrf_k}
              onChange={e => patchHybrid({ rrf_k: Number(e.target.value) || 60 })}
              className="w-full px-3 py-2 text-sm rounded-lg border focus:outline-none transition-colors"
              style={INPUT}
            />
          </div>
          {([
            ['dense', 'Dense 权重'],
            ['bm25', 'BM25 权重'],
            ['graph_ppr', 'Graph PPR 权重'],
          ] as const).map(([key, label]) => (
            <div key={key}>
              <label className="block text-xs mb-1" style={LABEL}>{label}</label>
              <input
                type="number"
                min={0}
                max={3}
                step={0.05}
                value={hybrid.weights[key]}
                onChange={e => patchHybrid({
                  weights: { ...hybrid.weights, [key]: Number(e.target.value) || 0 },
                })}
                className="w-full px-3 py-2 text-sm rounded-lg border focus:outline-none transition-colors"
                style={INPUT}
              />
            </div>
          ))}
          <div>
            <label className="block text-xs mb-1" style={LABEL}>PPR α</label>
            <input
              type="number"
              min={0.05}
              max={0.99}
              step={0.01}
              value={hybrid.graph.ppr_alpha}
              onChange={e => patchHybrid({
                graph: { ...hybrid.graph, ppr_alpha: Number(e.target.value) || 0.85 },
              })}
              className="w-full px-3 py-2 text-sm rounded-lg border focus:outline-none transition-colors"
              style={INPUT}
            />
          </div>
          <div>
            <label className="block text-xs mb-1" style={LABEL}>图召回论文上限</label>
            <input
              type="number"
              min={1}
              max={500}
              value={hybrid.graph.top_k}
              onChange={e => patchHybrid({
                graph: { ...hybrid.graph, top_k: Number(e.target.value) || 50 },
              })}
              className="w-full px-3 py-2 text-sm rounded-lg border focus:outline-none transition-colors"
              style={INPUT}
            />
          </div>
        </div>
      </details>
    </div>
  )
}


// ============================================================
// 重排序模型（Reranker）
// ============================================================

export function RerankerSection({ config, setConfig }: SectionProps) {
  return (
    <div className="panel-section space-y-4" style={CARD}>
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>
            重排序模型（Reranker）
          </h2>
          <p className="text-xs mt-0.5" style={LABEL}>
            在 RAG 跨论文问答时使用。向量检索召回 candidate_k 条候选，再由重排序模型按语义重排，最终取 top_n 喂给 LLM。提升答非所问的概率。
          </p>
        </div>
        <label className="flex items-center gap-2 text-xs cursor-pointer" style={{ color: 'var(--text-secondary)' }}>
          <input
            type="checkbox"
            checked={config.reranker.enabled}
            onChange={e => setConfig(prev => ({ ...prev, reranker: { ...prev.reranker, enabled: e.target.checked } }))}
            className="accent-[var(--accent)]"
          />
          启用
        </label>
      </div>

      {config.reranker.enabled && (
        <>
          <div>
            <label className="block text-xs mb-1" style={LABEL}>模型</label>
            <input
              type="text"
              value={config.reranker.model}
              onChange={e => setConfig(prev => ({ ...prev, reranker: { ...prev.reranker, model: e.target.value } }))}
              placeholder="BAAI/bge-reranker-v2-m3"
              className="w-full px-3 py-2 text-sm rounded-lg border focus:outline-none transition-colors"
              style={INPUT}
            />
            <p className="text-[10px] mt-1" style={{ color: 'var(--text-disabled)' }}>
              默认 BAAI/bge-reranker-v2-m3（硅基流动）
            </p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs mb-1" style={LABEL}>API Base URL</label>
              <input
                type="text"
                value={config.reranker.base_url}
                onChange={e => setConfig(prev => ({ ...prev, reranker: { ...prev.reranker, base_url: e.target.value } }))}
                placeholder="留空 = 继承 embedding 的 base_url"
                className="w-full px-3 py-2 text-sm rounded-lg border focus:outline-none transition-colors"
                style={INPUT}
              />
            </div>
            <div>
              <label className="block text-xs mb-1" style={LABEL}>API Key</label>
              <input
                type="password"
                value={config.reranker.api_key}
                onChange={e => setConfig(prev => ({ ...prev, reranker: { ...prev.reranker, api_key: e.target.value } }))}
                placeholder="留空 = 继承 embedding 的 api_key"
                className="w-full px-3 py-2 text-sm rounded-lg border focus:outline-none transition-colors font-mono"
                style={INPUT}
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs mb-1" style={LABEL}>candidate_k（召回数）</label>
              <input
                type="number"
                min={5}
                max={200}
                value={config.reranker.candidate_k}
                onChange={e => setConfig(prev => ({ ...prev, reranker: { ...prev.reranker, candidate_k: Number(e.target.value) || 30 } }))}
                className="w-full px-3 py-2 text-sm rounded-lg border focus:outline-none transition-colors"
                style={INPUT}
              />
              <p className="text-[10px] mt-1" style={{ color: 'var(--text-disabled)' }}>
                向量检索的初筛数量，越大重排越充分但越慢
              </p>
            </div>
            <div>
              <label className="block text-xs mb-1" style={LABEL}>top_n（送 LLM）</label>
              <input
                type="number"
                min={1}
                max={20}
                value={config.reranker.top_n}
                onChange={e => setConfig(prev => ({ ...prev, reranker: { ...prev.reranker, top_n: Number(e.target.value) || 5 } }))}
                className="w-full px-3 py-2 text-sm rounded-lg border focus:outline-none transition-colors"
                style={INPUT}
              />
              <p className="text-[10px] mt-1" style={{ color: 'var(--text-disabled)' }}>
                重排序后保留的最终 chunk 数
              </p>
            </div>
          </div>
        </>
      )}
    </div>
  )
}


// ============================================================
// 论文级相似度计算（Similarity Provider）
// ============================================================

export function SimilaritySection({ config, setConfig, simProviders }: SectionProps & { simProviders: string[] }) {
  return (
    <div className="panel-section space-y-4" style={CARD}>
      <div>
        <h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>
          论文级相似度计算（Similarity Provider）
        </h2>
        <p className="text-xs mt-0.5" style={LABEL}>
          深度研究知识图谱中"相似"边的计算方式。可插拔接口，可在{' '}
          <code
            className="px-1 rounded"
            style={{ backgroundColor: 'var(--bg-elevated)' }}
          >
            backend/services/deep_research/similarity/
          </code>{' '}
          自行新增 provider 并在 REGISTRY 注册。
        </p>
      </div>

      <div>
        <label className="block text-xs mb-1" style={LABEL}>Provider</label>
        <select
          value={config.similarity.provider}
          onChange={e => setConfig(prev => ({ ...prev, similarity: { ...prev.similarity, provider: e.target.value } }))}
          className="w-full px-3 py-2 text-sm rounded-lg border focus:outline-none transition-colors"
          style={INPUT}
        >
          {simProviders.map(p => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
        <p className="text-[10px] mt-1" style={{ color: 'var(--text-disabled)' }}>
          当前默认{' '}
          <code className="px-1 rounded" style={{ backgroundColor: 'var(--bg-elevated)' }}>unified</code>
          ：给<b>每篇</b>论文挑最佳主题向量（ChromaDB 质心 &gt; title/abstract embedding），
          全量连成研究地图；已解读论文对再挂可解释的 facet 分解。去噪用<b>互近邻</b>（mutual-kNN）。
          <code className="px-1 rounded" style={{ backgroundColor: 'var(--bg-elevated)' }}>bge_centroid</code>{' '}
          等旧法保留可切换对比。
        </p>
        <div
          className="mt-3 px-3 py-2 rounded-lg border"
          style={{
            backgroundColor: 'color-mix(in srgb, var(--bg-elevated) 50%, transparent)',
            borderColor: 'color-mix(in srgb, var(--border) 50%, transparent)',
          }}
        >
          <p className="text-[11px] leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
            <span className="font-medium" style={{ color: 'var(--text-primary)' }}>
              当前可用：
            </span><br />
            ·  <span style={{ color: 'var(--text-secondary)' }}>unified / bge_centroid / bibcoupling / hybrid / facet_weighted / cross_encoder</span><br />
            ·  <span style={{ color: 'var(--text-secondary)' }}>specter2 / scincl</span> 等后续可继续扩展
          </p>
        </div>
      </div>
    </div>
  )
}


// ============================================================
// arXiv 引用列表扩展
// ============================================================

export function ArxivExpandSection({ config, setConfig }: SectionProps) {
  return (
    <div className="panel-section space-y-4" style={CARD}>
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>
            arXiv 引用列表扩展
          </h2>
          <p className="text-xs mt-0.5" style={LABEL}>
            联网模式下，从 L1 论文 PDF 文本解析{' '}
            <code className="px-1 rounded" style={{ backgroundColor: 'var(--bg-elevated)' }}>[N]</code>{' '}
            引用块，提取 arXiv ID（正则优先，可选 LLM 兜底），从 arXiv 下载 PDF。
            与 OpenAlex/S2 扩展互补。
          </p>
        </div>
        <label className="flex items-center gap-2 text-xs cursor-pointer shrink-0 ml-4" style={{ color: 'var(--text-secondary)' }}>
          <input
            type="checkbox"
            checked={config.arxiv_expand.enabled}
            onChange={e => setConfig(prev => ({ ...prev, arxiv_expand: { ...prev.arxiv_expand, enabled: e.target.checked } }))}
            className="accent-[var(--accent)]"
          />
          启用
        </label>
      </div>

      {config.arxiv_expand.enabled && (
        <>
          <GrobidPanel
            parser={config.arxiv_expand.parser}
            grobidUrl={config.arxiv_expand.grobid_url}
            onChangeParser={p => setConfig(prev => ({ ...prev, arxiv_expand: { ...prev.arxiv_expand, parser: p } }))}
            onChangeUrl={u => setConfig(prev => ({ ...prev, arxiv_expand: { ...prev.arxiv_expand, grobid_url: u } }))}
          />

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs mb-1" style={LABEL}>Top-K（按引用数取前 K 篇）</label>
              <input
                type="number"
                min={1}
                max={50}
                value={config.arxiv_expand.top_k}
                onChange={e => setConfig(prev => ({ ...prev, arxiv_expand: { ...prev.arxiv_expand, top_k: Number(e.target.value) || 5 } }))}
                className="w-full px-3 py-2 text-sm rounded-lg border focus:outline-none transition-colors"
                style={INPUT}
              />
              <p className="text-[10px] mt-1" style={{ color: 'var(--text-disabled)' }}>
                从候选论文中取前 K 篇（引用列表越靠前越重要）
              </p>
            </div>
          </div>

          <div className="flex items-start gap-4">
            <label className="flex items-center gap-2 text-xs cursor-pointer" style={{ color: 'var(--text-secondary)' }}>
              <input
                type="checkbox"
                checked={config.arxiv_expand.use_llm_title}
                onChange={e => setConfig(prev => ({ ...prev, arxiv_expand: { ...prev.arxiv_expand, use_llm_title: e.target.checked } }))}
                className="accent-[var(--accent)]"
              />
              LLM 标题兜底
            </label>
            {config.arxiv_expand.use_llm_title && (
              <div className="flex-1">
                <label className="block text-xs mb-1" style={LABEL}>
                  每篇 seed 最多 LLM+arXiv 搜索次数
                </label>
                <input
                  type="number"
                  min={0}
                  max={50}
                  value={config.arxiv_expand.max_llm_refs}
                  onChange={e => setConfig(prev => ({ ...prev, arxiv_expand: { ...prev.arxiv_expand, max_llm_refs: Number(e.target.value) || 15 } }))}
                  className="w-48 px-3 py-2 text-sm rounded-lg border focus:outline-none transition-colors"
                  style={INPUT}
                />
                <p className="text-[10px] mt-1" style={{ color: 'var(--text-disabled)' }}>
                  对无 arXiv ID 的引用，调用解读模型提取标题再搜索（消耗 Token）。
                  每次 arXiv 搜索间隔 3 秒，过多会显著增加运行时间。
                </p>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}


// ============================================================
// findpapers 多源学术搜索
// ============================================================

interface DbTestResult {
  database: string
  label: string
  searchable: boolean
  downloadable: boolean
  needs_key: boolean
  latency_ms: number | null
  message: string
}

const FP_DATABASES = [
  { key: 'arxiv', label: 'arXiv', free: true },
  { key: 'openalex', label: 'OpenAlex', free: true },
  { key: 'pubmed', label: 'PubMed', free: true },
  { key: 'semantic_scholar', label: 'Semantic Scholar', free: true },
  { key: 'ieee', label: 'IEEE Xplore', free: false },
  { key: 'scopus', label: 'Scopus', free: false },
  { key: 'wos', label: 'Web of Science', free: false },
]

export function FindpapersSection({ config, setConfig }: SectionProps) {
  const [testing, setTesting] = useState<Record<string, boolean>>({})
  const [results, setResults] = useState<Record<string, DbTestResult>>({})
  const [fpInstalled, setFpInstalled] = useState<boolean | null>(null)
  const [testError, setTestError] = useState<string | null>(null)

  // 实测一组库（单库=单独测，多库=测全部）：能否搜索 + 能否下载 PDF
  const runTest = async (dbs: string[]) => {
    if (dbs.length === 0) return
    setTestError(null)
    setTesting(prev => ({ ...prev, ...Object.fromEntries(dbs.map(d => [d, true])) }))
    try {
      const resp = await post<{ findpapers_installed: boolean; results: DbTestResult[] }>(
        '/analysis/findpapers/test-databases',
        { databases: dbs },
      )
      setFpInstalled(resp.findpapers_installed)
      setResults(prev => ({ ...prev, ...Object.fromEntries(resp.results.map(r => [r.database, r])) }))
    } catch (e) {
      setTestError(e instanceof Error ? e.message : '测试失败')
    } finally {
      setTesting(prev => ({ ...prev, ...Object.fromEntries(dbs.map(d => [d, false])) }))
    }
  }

  const anyTesting = Object.values(testing).some(Boolean)
  // 绿=可搜可下；黄=可搜但下不了；红=搜不到
  const statusColor = (r: DbTestResult) =>
    r.downloadable ? 'var(--success, #22c55e)'
      : r.searchable ? 'var(--warning, #eab308)'
        : 'var(--error, #ef4444)'

  return (
    <div className="panel-section space-y-4" style={CARD}>
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>
            findpapers 多源搜索
          </h2>
          <p className="text-xs mt-0.5" style={LABEL}>
            启用后使用 findpapers 库（arXiv、CrossRef、OpenAlex、PubMed 等 8 源并行搜索 + 自动去重）
            替代 Semantic Scholar 进行相关论文推荐和 PDF 下载。
            需要{' '}
            <code className="px-1 rounded" style={{ backgroundColor: 'var(--bg-elevated)' }}>
              pip install findpapers
            </code>。
          </p>
        </div>
        <label className="flex items-center gap-2 text-xs cursor-pointer shrink-0 ml-4" style={{ color: 'var(--text-secondary)' }}>
          <input
            type="checkbox"
            checked={config.findpapers.enabled}
            onChange={e => setConfig(prev => ({ ...prev, findpapers: { ...prev.findpapers, enabled: e.target.checked } }))}
            className="accent-[var(--accent)]"
          />
          启用
        </label>
      </div>

      {config.findpapers.enabled && (
        <>
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="block text-xs" style={LABEL}>启用的数据库（可单独测试或一键测全部）</label>
              <button
                type="button"
                onClick={() => runTest(FP_DATABASES.map(d => d.key))}
                disabled={anyTesting}
                className="px-2.5 py-1 text-[11px] rounded-md border transition-colors disabled:opacity-50"
                style={{ borderColor: 'var(--border)', color: 'var(--text-secondary)' }}
              >
                {anyTesting ? '测试中…' : '测试全部'}
              </button>
            </div>

            <div className="space-y-1.5">
              {FP_DATABASES.map(db => {
                const checked = config.findpapers.databases.includes(db.key)
                const r = results[db.key]
                const busy = testing[db.key]
                return (
                  <div key={db.key} className="flex items-center gap-2 text-xs">
                    <label className="flex items-center gap-1.5 cursor-pointer shrink-0 w-48" style={{ color: 'var(--text-secondary)' }}>
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={e => {
                          const dbs = e.target.checked
                            ? [...config.findpapers.databases, db.key]
                            : config.findpapers.databases.filter(d => d !== db.key)
                          setConfig(prev => ({ ...prev, findpapers: { ...prev.findpapers, databases: dbs } }))
                        }}
                        className="accent-[var(--accent)]"
                      />
                      {db.label}
                      {!db.free && (
                        <span className="text-[10px]" style={{ color: 'var(--warning, #eab308)' }}>(需 key)</span>
                      )}
                    </label>

                    <button
                      type="button"
                      onClick={() => runTest([db.key])}
                      disabled={busy}
                      className="px-2 py-0.5 text-[11px] rounded-md border transition-colors disabled:opacity-50 shrink-0"
                      style={{ borderColor: 'var(--border)', color: 'var(--text-secondary)' }}
                    >
                      {busy ? '测试中…' : '测试'}
                    </button>

                    {r && !busy && (
                      <span className="flex items-center gap-1.5 min-w-0">
                        <span className="inline-block w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: statusColor(r) }} />
                        <span className="truncate" style={{ color: 'var(--text-muted)' }}>
                          {r.message}{r.latency_ms != null ? ` · ${r.latency_ms}ms` : ''}
                        </span>
                      </span>
                    )}
                  </div>
                )
              })}
            </div>

            <p className="text-[11px] mt-2" style={LABEL}>
              「测试」会真的用 findpapers 搜该库并尝试下载一篇 PDF：🟢 可搜+可下载　🟡 可搜但下不了　🔴 搜不到
            </p>
            {fpInstalled === false && (
              <p className="text-[11px] mt-1" style={{ color: 'var(--warning, #eab308)' }}>
                findpapers 库未安装（pip install findpapers）
              </p>
            )}
            {testError && (
              <p className="text-[11px] mt-1" style={{ color: 'var(--error, #ef4444)' }}>{testError}</p>
            )}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs mb-1" style={LABEL}>搜索结果数量上限</label>
              <input
                type="number"
                min={1}
                max={50}
                value={config.findpapers.max_results}
                onChange={e => setConfig(prev => ({ ...prev, findpapers: { ...prev.findpapers, max_results: Number(e.target.value) || 10 } }))}
                className="w-full px-3 py-2 text-sm rounded-lg border focus:outline-none transition-colors"
                style={INPUT}
              />
            </div>
          </div>
        </>
      )}
    </div>
  )
}

/** 组合 6 个子组件的包装组件，供 ResearchSettingsDrawer 等外部使用 */
export function DeepResearchSettings({ config, setConfig, simProviders }: {
  config: AppConfig
  setConfig: SetConfigFn
  simProviders?: string[]
}) {
  return (
    <>
      <EmbeddingSection config={config} setConfig={setConfig} />
      <ResearchProviderSection config={config} setConfig={setConfig} />
      <RecallSection config={config} setConfig={setConfig} />
      <RerankerSection config={config} setConfig={setConfig} />
      <SimilaritySection config={config} setConfig={setConfig} simProviders={simProviders || []} />
      <ArxivExpandSection config={config} setConfig={setConfig} />
      <FindpapersSection config={config} setConfig={setConfig} />
    </>
  )
}
