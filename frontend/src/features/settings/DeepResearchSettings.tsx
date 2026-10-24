/**
 * 深度研究相关设置区块集合
 *
 * Embedding / Research Provider / Reranker / Relations / arXiv / findpapers
 * 共 6 个子组件，从 SettingsPage 拆出。
 * 只负责渲染与改写传入的 config 切片，保存逻辑由宿主页面处理。
 */
import { useEffect, useState } from 'react'
import type { Dispatch, SetStateAction } from 'react'
import { TestButton } from './TestButton'
import { GrobidPanel } from './GrobidPanel'
import { get, post } from '@/api/client'
import { DEFAULT_CONFIG } from './config'
import type {
  AppConfig,
  EmbeddingConfig,
  HybridRetrievalConfig,
  RecallConfig,
  RelationChannelName,
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
      className="px-3 py-1.5 text-xs border transition-colors"
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
          className="w-full px-3 py-2 text-sm border focus:outline-none transition-colors"
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
              className="w-full px-3 py-2 text-sm border focus:outline-none transition-colors"
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
              className="w-full px-3 py-2 text-sm border focus:outline-none transition-colors font-mono"
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
                className="w-full px-3 py-2 text-sm border focus:outline-none transition-colors"
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
                className="w-full px-3 py-2 text-sm border focus:outline-none transition-colors"
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
                className="w-full px-3 py-2 text-sm border focus:outline-none transition-colors"
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
            { key: 'auto' as const, label: '自动（Scopus 首选，失败回退 OpenAlex）' },
            { key: 'scopus' as const, label: 'Scopus（Elsevier）' },
            { key: 'openalex' as const, label: 'OpenAlex' },
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
        <p className="text-[10px] mt-1.5" style={LABEL}>
          自动 = 有 Elsevier API Key 时无条件 Scopus 首选（元数据质量与全学科覆盖优于免费源，
          自适应限速：成功加速 / 429 降速 + 并行检索）；无 Key 直接 OpenAlex——Scopus Search
          只认 Key，校园网机构 IP 不放行检索 API。鉴权 / 配额不可用自动熔断回退，检索不中断。
        </p>
      </div>

      {(config.research.provider === 'auto' || config.research.provider === 'scopus') && (
        <div>
          <label className="block text-xs mb-1" style={LABEL}>
            Elsevier API Key（Scopus 检索必填）
          </label>
          <input
            type="password"
            value={config.research.elsevier_api_key ?? ''}
            onChange={e => setConfig(prev => ({
              ...prev,
              research: { ...prev.research, elsevier_api_key: e.target.value },
            }))}
            placeholder="在 dev.elsevier.com 用山大邮箱免费申请"
            className="w-full px-3 py-2 text-sm border focus:outline-none transition-colors font-mono"
            style={INPUT}
          />
          <p className="text-[10px] mt-1.5" style={{ color: 'var(--text-disabled)' }}>
            优先级：此处填写 &gt; 环境变量 ELSEVIER_API_KEY。Scopus Search 只认 Key——校园网机构 IP
            不放行检索 API（实测 401），但校园 IP + 有效 Key 可享机构配额档；ScienceDirect 全文下载通道
            则认校园 IP（无需 Key）。Key 失效时自动熔断回退 OpenAlex，检索不中断。
          </p>
        </div>
      )}

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
            className="w-full px-3 py-2 text-sm border focus:outline-none transition-colors"
            style={INPUT}
          />
          <p className="text-[10px] mt-1" style={{ color: 'var(--text-disabled)' }}>
            填入邮箱可进入 OpenAlex polite pool，获得更稳定的速率配额。不填也能用。
          </p>
        </div>
      )}

      {['auto', 'scopus', 'openalex'].includes(config.research.provider) && (
        <div>
          <label className="block text-xs mb-1" style={LABEL}>
            OpenAlex API Key（建议填写 · 免费）
          </label>
          <input
            type="password"
            value={config.research.openalex_api_key ?? ''}
            onChange={e => setConfig(prev => ({
              ...prev,
              research: { ...prev.research, openalex_api_key: e.target.value },
            }))}
            placeholder="在 openalex.org 免费申请"
            className="w-full px-3 py-2 text-sm border focus:outline-none transition-colors font-mono"
            style={INPUT}
          />
          <p className="text-[10px] mt-1.5" style={{ color: 'var(--text-disabled)' }}>
            优先级：此处填写 &gt; 环境变量 OPENALEX_API_KEY。不填时与同一网络出口的所有人共用每日免费额度，
            额度用完后当天 OpenAlex 不可用（UTC 零点重置），预检与完整性提示会注明。
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
            className="w-full px-3 py-2 text-sm border focus:outline-none transition-colors font-mono"
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

      <div className="pt-3 space-y-3" style={{ borderTop: '1px solid var(--border)' }}>
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

        <label className="flex items-start gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={config.research.synthesize?.enabled ?? true}
            onChange={e => setConfig(prev => ({ ...prev, research: { ...prev.research, synthesize: { enabled: e.target.checked } } }))}
            className="mt-0.5 accent-[var(--accent)]"
          />
          <span>
            <span className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
              图谱后自动「领域认知综合」
            </span>
            <p className="text-[10px] mt-0.5" style={{ color: 'var(--text-muted)' }}>
              开启后，构建图谱会追加一个综合阶段：为骨干论文判定学术角色（奠基/黑马/基准/工具/综述）、
              为引用边标注因果关系（改进/替代/评测/借鉴）、归纳技术流派，并生成「📜 领域全景透视」简报 ——
              不读单篇论文即可掌握领域全局。消耗 LLM Token 约为数次批量调用。
            </p>
          </span>
        </label>
      </div>

      {/* 已移除参数说明 */}
      <details className="pt-2 opacity-70" style={{ borderTop: '1px solid var(--border)' }}>
        <summary className="text-[11px] cursor-pointer select-none" style={{ color: 'var(--text-muted)' }}>
          已移除的旧参数
        </summary>
        <p className="text-[10px] mt-2" style={{ color: 'var(--text-disabled)' }}>
          「相似度阈值 similarity_threshold」「kNN 条数 knn_k」「共享方法边」已删除：
          关系图使用互近邻选边，Disparity Filter 单独标记局部权重突出的边，
          边多重由「论文关联度」的通道权重决定。旧配置里的这些字段会被忽略。
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
  method: 'weighted_rrf_v1',
  enabled_sources: ['backward', 'forward', 'recommend', 'pdf_ref', 'graph_ppr'],
  rrf_k: 60,
  weights: { pdf_ref: 1.0, backward: 1.0, forward: 1.0, recommend: 1.0, graph_ppr: 1.0 },
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
  method: 'weighted_rrf_v1',
  rrf_k: 60,
  weights: { dense: 1.0, bm25: 1.0, graph_ppr: 1.0 },
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
            <code className="mx-1 px-1" style={{ backgroundColor: 'var(--bg-elevated)' }}>
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
        <p className="text-[11px] px-3 py-2" style={{ color: 'var(--warning, #eab308)', backgroundColor: 'color-mix(in srgb, var(--warning, #eab308) 12%, transparent)' }}>
          discovery v2 尚未通过 live/Scholar 发布门禁，默认关闭；可为实验评测手动启用。
        </p>
      )}

      <div>
        <label className="block text-xs mb-2" style={LABEL}>启用的召回源</label>
        <div className="flex flex-wrap gap-2">
          {RECALL_SOURCE_OPTIONS.map(opt => (
            <label
              key={opt.key}
              className="flex items-center gap-1.5 text-xs cursor-pointer px-2.5 py-1.5 border"
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
            className="w-full px-3 py-2 text-sm border focus:outline-none transition-colors"
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
            className="w-full px-3 py-2 text-sm border focus:outline-none transition-colors"
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
                className="w-full px-3 py-2 text-sm border focus:outline-none transition-colors"
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
              className="w-full px-3 py-2 text-sm border focus:outline-none transition-colors"
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
                className="w-full px-3 py-2 text-sm border focus:outline-none transition-colors"
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
              className="w-full px-3 py-2 text-sm border focus:outline-none transition-colors"
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
              className="w-full px-3 py-2 text-sm border focus:outline-none transition-colors"
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
            按问题相关性重排候选资料。
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
              className="w-full px-3 py-2 text-sm border focus:outline-none transition-colors"
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
                className="w-full px-3 py-2 text-sm border focus:outline-none transition-colors"
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
                className="w-full px-3 py-2 text-sm border focus:outline-none transition-colors font-mono"
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
                className="w-full px-3 py-2 text-sm border focus:outline-none transition-colors"
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
                className="w-full px-3 py-2 text-sm border focus:outline-none transition-colors"
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
// 论文关联度（Relations）—— 图谱唯一的关系打分
// ============================================================

interface RelationChannel {
  name: RelationChannelName
  label: string
  group: 'semantic' | 'evidence'
  default_weight: number
  hint: string
}

const GROUP_TITLE: Record<RelationChannel['group'], string> = {
  semantic: '语义通道',
  evidence: '证据通道',
}

// 本地兜底通道表：接口 /analysis/relations/channels 拉不到（后端未起 / 网络失败 / 返回空）
// 时用它渲染，避免整个区块空白导致用户连权重都改不了。字段与
// backend/services/deep_research/graphkit/relatedness.py 的 CHANNEL_LABELS /
// CHANNEL_HINTS / DEFAULT_WEIGHTS 保持一致；接口可用时以接口返回为准。
const FALLBACK_CHANNELS: RelationChannel[] = [
  { name: 'topic', label: '主题', group: 'semantic', default_weight: 0.125, hint: '全文/题录主题向量的余弦，粗粒度锚点。' },
  { name: 'purpose', label: '研究目的', group: 'semantic', default_weight: 0.125, hint: '研究目的文本（问题 + 一句话 + 核心贡献）的语义相似度。' },
  { name: 'method', label: '方法', group: 'semantic', default_weight: 0.125, hint: '方法概述与流程的语义相似度。' },
  { name: 'term', label: '术语', group: 'semantic', default_weight: 0.125, hint: 'IDF 加权术语重叠：共享的稀有专名越多越高。' },
  { name: 'dataset', label: '数据集', group: 'evidence', default_weight: 0.125, hint: '共享实验数据集（冷门数据集权重更高）。' },
  { name: 'baseline', label: '对比基线', group: 'evidence', default_weight: 0.125, hint: '共享对比基线。' },
  { name: 'bibcoupling', label: '文献耦合', group: 'evidence', default_weight: 0.125, hint: '引用了相同的参考文献（Salton 归一）。' },
  { name: 'cocitation', label: '共引', group: 'evidence', default_weight: 0.125, hint: '被相同论文共同引用（Salton 归一）。' },
]

export function RelationsSection({ config, setConfig }: SectionProps) {
  const [channels, setChannels] = useState<RelationChannel[]>(FALLBACK_CHANNELS)

  useEffect(() => {
    get<{ channels: RelationChannel[] }>('/analysis/relations/channels')
      .then(r => { if (r?.channels?.length) setChannels(r.channels) })
      .catch(() => {})   // 失败保持兜底列表，区块照常可用
  }, [])

  const weights = config.relations.weights
  // 滑条与数值框共用同一个 [0,1] 区间；手输的非法值（NaN / 越界）在这里夹逼，
  // 免得数值框写进一个滑条表达不了的数。
  const setWeight = (name: RelationChannelName, value: number) => {
    const safe = Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0
    setConfig(prev => ({
      ...prev,
      relations: { ...prev.relations, weights: { ...prev.relations.weights, [name]: safe } },
    }))
  }

  const positiveWeight = (c: RelationChannel) => {
    const value = weights[c.name] ?? c.default_weight
    return Number.isFinite(value) ? Math.max(0, value) : 0
  }
  const totalWeight = channels.reduce((total, c) => total + positiveWeight(c), 0)
  const preference = (c: RelationChannel) => totalWeight > 0 ? positiveWeight(c) / totalWeight : 1 / channels.length

  return (
    <div className="panel-section space-y-4" style={CARD}>
      <div>
        <h2 className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>
          论文关联度（知识图谱唯一的关系打分）
        </h2>
        <p className="text-xs mt-0.5" style={LABEL}>
          八通道排名通过 RRF 融合，默认等权；权重表示用户偏好，按全部通道的总和归一。
          缺少数据的通道不提供该项证据。关系图使用互近邻，社区使用 Leiden；Disparity Filter 独立标记。
          全部权重为 0 时使用等权默认值。
        </p>
      </div>

      {(['semantic', 'evidence'] as const).map(group => (
        <div key={group} className="space-y-2">
          <div className="text-[11px] font-medium" style={LABEL}>{GROUP_TITLE[group]}</div>
          {channels.filter(c => c.group === group).map(c => (
            <div key={c.name} className="space-y-1">
              <div className="flex items-center gap-3">
                <div className="w-20 shrink-0 text-xs" style={{ color: 'var(--text-primary)' }}>
                  {c.label}
                </div>
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.005}
                  value={weights[c.name] ?? c.default_weight}
                  onChange={e => setWeight(c.name, Number(e.target.value))}
                  className="flex-1"
                  aria-label={`${c.label} 权重`}
                />
                <input
                  type="number"
                  min={0}
                  max={1}
                  step={0.005}
                  value={weights[c.name] ?? c.default_weight}
                  onChange={e => setWeight(c.name, Number(e.target.value))}
                  className="w-16 px-2 py-1 text-xs border focus:outline-none tabular-nums"
                  style={INPUT}
                  aria-label={`${c.label} 权重数值`}
                />
                <div className="w-16 shrink-0 text-[10px] tabular-nums text-right" style={{ color: 'var(--text-disabled)' }}>
                  偏好 {(preference(c) * 100).toFixed(1)}%
                </div>
              </div>
              <p className="text-[10px] leading-relaxed pl-20" style={{ color: 'var(--text-disabled)' }}>
                {c.hint}
              </p>
            </div>
          ))}
        </div>
      ))}

      <div className="flex items-center gap-3">
        <PillButton
          active={false}
          onClick={() => setConfig(prev => ({
            ...prev,
            relations: {
              method: DEFAULT_CONFIG.relations.method,
              calibrate: false,
              weights: { ...DEFAULT_CONFIG.relations.weights },
            },
          }))}
        >
          恢复默认权重
        </PillButton>
        <span className="text-[10px]" style={{ color: 'var(--text-disabled)' }}>
          改完需要重新运行一次深度研究，图谱才会按新权重重建。
        </span>
      </div>
      <div>
        <label className="block text-xs mb-1" style={LABEL} htmlFor="relations-backbone-alpha">Disparity 标记阈值 α</label>
        <input
          id="relations-backbone-alpha"
          type="number" min={0.01} max={0.9} step={0.01}
          value={config.research.backbone_alpha ?? 0.05}
          onChange={e => {
            const value = Number(e.target.value)
            if (Number.isFinite(value)) setConfig(prev => ({ ...prev, research: {
              ...prev.research, backbone_alpha: Math.min(0.9, Math.max(0.01, value)),
            } }))
          }}
          className="w-24 px-2 py-1 text-xs border" style={INPUT}
        />
        <p className="text-[10px] mt-1" style={LABEL}>
          默认 α = 0.05，仅控制边详情中的 Disparity 标记，不改变互近邻选边与聚类。标记不代表关联正确率。
        </p>
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
            <code className="px-1" style={{ backgroundColor: 'var(--bg-elevated)' }}>[N]</code>{' '}
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
                className="w-full px-3 py-2 text-sm border focus:outline-none transition-colors"
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
                  className="w-48 px-3 py-2 text-sm border focus:outline-none transition-colors"
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
            <code className="px-1" style={{ backgroundColor: 'var(--bg-elevated)' }}>
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
                className="px-2.5 py-1 text-[11px] border transition-colors disabled:opacity-50"
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
                      className="px-2 py-0.5 text-[11px] border transition-colors disabled:opacity-50 shrink-0"
                      style={{ borderColor: 'var(--border)', color: 'var(--text-secondary)' }}
                    >
                      {busy ? '测试中…' : '测试'}
                    </button>

                    {r && !busy && (
                      <span className="flex items-center gap-1.5 min-w-0">
                        <span className="inline-block w-2 h-2 shrink-0" style={{ backgroundColor: statusColor(r) }} />
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
              「测试」会真的用 findpapers 搜该库并验证下载：免费库流式校验 PDF 头；付费库（IEEE/Scopus/WoS）会真实下载一篇完整论文、验证后删除临时文件，下载成功才算通过。
              🟢 可搜+可下载　🟡 可搜但下载失败　🔴 搜不到
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
                className="w-full px-3 py-2 text-sm border focus:outline-none transition-colors"
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
export function DeepResearchSettings({ config, setConfig }: {
  config: AppConfig
  setConfig: SetConfigFn
}) {
  return (
    <>
      <EmbeddingSection config={config} setConfig={setConfig} />
      <ResearchProviderSection config={config} setConfig={setConfig} />
      <RecallSection config={config} setConfig={setConfig} />
      <RerankerSection config={config} setConfig={setConfig} />
      <RelationsSection config={config} setConfig={setConfig} />
      <ArxivExpandSection config={config} setConfig={setConfig} />
      <FindpapersSection config={config} setConfig={setConfig} />
    </>
  )
}
