/**
 * 领域探索页（/explore）—— GOAL.md 核心命题的产品入口
 *
 * 学者提一个自然语言问句（「用 AI 识别肿瘤有没有人做，做得怎样」），系统自动：
 * 拆解检索式 → 多源检索（Scopus 首选 / OpenAlex 回退）→ 计量与聚类（纯算法）→
 * LLM 跨文献综合。全程免读单篇论文，结论挂真实证据，可回溯核验。
 *
 * 数据流：POST /api/exploration/runs → SSE（stage_* / recall_progress / metrics_ready /
 * synthesis_ready / done）。历史 run 通过 SSE 回放（后端 done 状态会重发产物事件）。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { toast } from '@/hooks/useToast'
import { DomainOverview } from '@/features/research/DomainOverview'
import { useExplorationStream } from '@/hooks/useExplorationStream'
import { createExploration, deleteExploration, listExplorations } from '@/api/exploration'
import type {
  ExplorationDepth, ExplorationKeywordTrend, ExplorationPaper, ExplorationRunSummary,
  GraphNode, GraphSynthesis, NoveltyStatus, ResearchGraph,
} from '@/types'
import { clusterInk, inkWash } from '@/theme/graphPalette'

const EXAMPLES = [
  '用 AI 识别肿瘤有没有人做？做得怎样？',
  '多模态大模型在医学影像诊断上进展如何？',
  '状态空间模型（Mamba 一类）会取代 Transformer 吗？',
]

const DEPTHS: { value: ExplorationDepth; label: string; hint: string }[] = [
  { value: 'quick', label: '快览', hint: '3 式 · 上限 80 篇' },
  { value: 'standard', label: '标准', hint: '4 式 · 上限 160 篇' },
  { value: 'deep', label: '深挖', hint: '5 式 · 上限 240 篇' },
]

const STAGE_META: { key: string; label: string; hint: string }[] = [
  { key: 'decompose', label: '① 拆解检索式', hint: 'LLM 把口语问题译成学术检索式' },
  { key: 'recall', label: '② 多源检索', hint: 'Scopus 首选 / OpenAlex 回退' },
  { key: 'enrich', label: '③ 指标富化', hint: '逐年引用 · 摘要 · 关键词 · 期刊 SJR/SNIP（OpenAlex 补查 + Scopus Serial Title）' },
  { key: 'metrics', label: '④ 计量与聚类', hint: '同年百分位 · 近2年动量 · 引用轨迹 · 子方向（纯算法）' },
  { key: 'synthesize', label: '⑤ 跨文献综合', hint: 'LLM 只基于召回材料作答' },
  { key: 'persist', label: '⑥ 落盘', hint: '产物持久化' },
]

const VERDICT_META: Record<NoveltyStatus, { label: string; ink: string; note: string }> = {
  done_extensively: { label: '已被充分实现', ink: '#d94a38', note: '多个成熟实现，赛道拥挤' },
  done_partial: { label: '部分实现', ink: '#d3a52f', note: '有相关工作，未完全覆盖问题条件' },
  emerging: { label: '新兴方向', ink: '#2f7d78', note: '近 1~2 年出现，尚无公认范式' },
  gap: { label: '检索未见实现', ink: '#3969a8', note: '材料中无直接实现——注意区分检索失败' },
  unclear: { label: '证据不足', ink: '#6b7079', note: '现有材料不足以判定' },
}

const INTENT_LABEL: Record<string, string> = {
  novelty: '查新（有没有人做）',
  maturity: '成熟度（做到什么程度）',
  landscape: '全景（领域现状）',
}

function fmtCitations(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n >= 10000) return `${(n / 1000).toFixed(0)}k`
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}

function paperLink(p: ExplorationPaper): string | null {
  if (p.doi) return `https://doi.org/${p.doi}`
  if (p.arxiv_id) return `https://arxiv.org/abs/${p.arxiv_id}`
  return null
}

export default function ExplorePage() {
  const [question, setQuestion] = useState('')
  const [depth, setDepth] = useState<ExplorationDepth>('standard')
  const [runId, setRunId] = useState<number | null>(null)
  const [history, setHistory] = useState<ExplorationRunSummary[]>([])
  const [selected, setSelected] = useState<ExplorationPaper | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const stream = useExplorationStream(runId)
  const { result, synthesis } = stream
  const running = stream.status === 'running' || submitting

  const refreshHistory = useCallback(async () => {
    try { setHistory(await listExplorations(20)) } catch { /* 历史加载失败不打断主流程 */ }
  }, [])

  useEffect(() => { void refreshHistory() }, [refreshHistory])
  useEffect(() => { if (stream.status === 'done') void refreshHistory() }, [stream.status, refreshHistory])
  useEffect(() => { setSelected(null) }, [runId])

  const submit = useCallback(async () => {
    const q = question.trim()
    if (q.length < 2) { toast({ type: 'error', message: '请先输入想探索的问题' }); return }
    setSubmitting(true)
    try {
      const run = await createExploration(q, depth)
      setRunId(run.id)
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : '探索任务创建失败'
      toast({ type: 'error', message })
    } finally {
      setSubmitting(false)
    }
  }, [question, depth])

  const loadRun = useCallback(async (id: number) => {
    setRunId(null)  // 先断开旧流，再接新 run
    requestAnimationFrame(() => setRunId(id))
  }, [])

  const removeRun = useCallback(async (id: number) => {
    try {
      await deleteExploration(id)
      if (runId === id) { setRunId(null) }
      void refreshHistory()
    } catch (e: unknown) {
      toast({ type: 'error', message: e instanceof Error ? e.message : '删除失败' })
    }
  }, [runId, refreshHistory])

  // 证据定位：graph.nodes[i] ↔ papers[i]（后端按同序构建）
  const handleSelectNode = useCallback((node: GraphNode | null) => {
    if (!node || !result) { setSelected(null); return }
    const idx = result.graph.nodes.findIndex(n => n.id === node.id)
    setSelected(idx >= 0 ? result.papers[idx] ?? null : null)
  }, [result])

  const overviewGraph: ResearchGraph | null = useMemo(() => {
    if (!result || !result.graph.nodes.length) return null
    return {
      workspace_id: 0,
      run_id: runId ?? 0,
      nodes: result.graph.nodes,
      edges: result.graph.edges,
      stats: result.graph.stats,
      updated_at: new Date().toISOString(),
    }
  }, [result, runId])

  const overviewSynthesis: GraphSynthesis | null = useMemo(() => (
    synthesis && synthesis.paradigms.length
      ? { paradigms: synthesis.paradigms, enriched_nodes: {}, enriched_edges: {} }
      : null
  ), [synthesis])

  const papersByKey = useMemo(() => {
    const m = new Map<string, ExplorationPaper>()
    result?.papers.forEach(p => m.set(p.key, p))
    return m
  }, [result])

  const verdict = synthesis?.verdict
  const verdictMeta = verdict ? VERDICT_META[verdict.status] : null
  const total = result?.metrics.total ?? 0

  const currentStageMeta = useMemo(() => {
    if (submitting) return STAGE_META[0]
    if (!stream.stage) return null
    const matched = STAGE_META.find(s => stream.stage?.startsWith(s.key))
    return matched ?? null
  }, [stream.stage, submitting])

  const displayProgress = useMemo(() => {
    if (stream.status === 'done') return 100
    if (submitting) return 6
    if (typeof stream.progress === 'number' && stream.progress > 0) return stream.progress
    if (stream.status === 'running') return 10
    return 0
  }, [stream.status, stream.progress, submitting])

  return (
    <div className="er-page er-page--explore">
      <header className="er-page-head">
        <p className="er-page-head__eyebrow">领域探索 · 问句驱动的免读领域认知</p>
        <h1 className="er-page-head__title">领域探索</h1>
        <p className="ex-lead">
          提一个问题，系统自动拆解检索式、检索真实文献、统计被引与突增、聚类子方向，
          最后给出只基于召回证据的跨文献结论——不读单篇论文，也不依赖大模型的记忆。
        </p>
      </header>

      {/* 提问表单 */}
      <section className="ex-ask" aria-label="探索提问">
        <textarea
          className="ex-ask__input"
          value={question}
          onChange={e => setQuestion(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void submit() }
          }}
          placeholder="例：我想看看用 AI 识别肿瘤有没有人做，做的怎样"
          rows={2}
          disabled={running}
        />
        <div className="ex-ask__row">
          <div className="ex-ask__depth" role="radiogroup" aria-label="探索深度">
            {DEPTHS.map(d => (
              <button
                key={d.value}
                type="button"
                role="radio"
                aria-checked={depth === d.value}
                className={depth === d.value ? 'is-active' : ''}
                onClick={() => setDepth(d.value)}
                disabled={running}
                title={d.hint}
              >
                {d.label}
              </button>
            ))}
          </div>
          <button type="button" className="ex-ask__run" onClick={() => void submit()} disabled={running}>
            {running ? '探索中…' : '开始探索'}
          </button>
        </div>
        <div className="ex-ask__examples">
          {EXAMPLES.map(ex => (
            <button key={ex} type="button" onClick={() => setQuestion(ex)} disabled={running}>{ex}</button>
          ))}
        </div>
        <p className="ex-ask__hint">⌘/Ctrl + Enter 提交 · 结论只基于检索到的真实文献，每条可点开核验</p>
      </section>

      {/* 探索进行中：进度条与实时动态分析反馈 */}
      {(stream.status === 'running' || submitting) && (
        <section className="ex-progress" aria-label="探索进度">
          <div className="ex-progress__header">
            <div className="ex-progress__title-group">
              <span className="ex-progress__spinner" aria-hidden="true" />
              <div>
                <div className="ex-progress__title">
                  {submitting
                    ? '正在启动领域探索…'
                    : (currentStageMeta?.label ? `正在进行：${currentStageMeta.label}` : '领域探索分析中…')}
                </div>
                <div className="ex-progress__subtitle">
                  {stream.message || (submitting ? '正在准备分析环境并连接学术检索总线…' : (currentStageMeta?.hint ?? '系统正在自动检索学术文献并计算文献计量指标'))}
                </div>
              </div>
            </div>
            <div className="ex-progress__meta">
              <span className="ex-progress__percent tabular-nums font-mono font-semibold">
                {displayProgress}%
              </span>
            </div>
          </div>

          {/* 进度条轨道与动态填充 */}
          <div
            className="ex-progress__bar-track"
            role="progressbar"
            aria-valuenow={displayProgress}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="探索总体进度"
          >
            <div
              className="ex-progress__bar-fill"
              style={{ width: `${Math.min(100, Math.max(displayProgress, 4))}%` }}
            />
          </div>

          {/* 阶段步骤指示器 */}
          <ol className="ex-progress__stages">
            {STAGE_META.map((s, idx) => {
              const done = (stream.stage ?? '').startsWith(`${s.key} 完成`) || isAfterStage(stream.stage, s.key, result ?? undefined, synthesis)
              const active = stream.stage === s.key || (idx === 0 && (submitting || !stream.stage))
              return (
                <li key={s.key} className={active ? 'is-active' : done ? 'is-done' : ''} title={s.hint}>
                  <span className="ex-progress__dot" aria-hidden="true">
                    {done ? '✓' : ''}
                  </span>
                  <span className="ex-progress__stage-label">{s.label}</span>
                </li>
              )
            })}
          </ol>
        </section>
      )}

      {/* 历史记录 */}
      {history.length > 0 && (
        <section className="ex-history" aria-label="历史探索">
          <span className="ex-history__label">最近探索</span>
          <div className="ex-history__list">
            {history.map(h => (
              <span key={h.id} className={`ex-history__item${runId === h.id ? ' is-active' : ''}`}>
                <button type="button" onClick={() => void loadRun(h.id)} title={h.question}>
                  {h.status === 'failed' ? '⚠ ' : ''}{h.question.length > 26 ? `${h.question.slice(0, 26)}…` : h.question}
                </button>
                <button
                  type="button"
                  className="ex-history__del"
                  aria-label="删除该记录"
                  onClick={() => void removeRun(h.id)}
                  disabled={h.status === 'running' || h.status === 'pending'}
                >×</button>
              </span>
            ))}
          </div>
        </section>
      )}

      {stream.status === 'failed' && stream.error && (
        <section className="ex-empty ex-empty--error">探索失败：{stream.error}</section>
      )}

      {/* 结果 */}
      {result && (
        <div className="ex-result">
          {/* 结论卡 */}
          {verdict && verdictMeta && (
            <section className="ex-verdict" aria-label="查新结论">
              <div className="ex-verdict__head">
                <span className="ex-verdict__badge" style={{ borderColor: verdictMeta.ink, color: verdictMeta.ink, background: inkWash(verdictMeta.ink, 10) }}>
                  {verdictMeta.label}
                </span>
                <span className="ex-verdict__note">{verdictMeta.note}</span>
                <span className="ex-verdict__conf" title="证据充分度">
                  证据充分度
                  <i style={{ width: `${Math.round(verdict.confidence * 100)}%`, background: verdictMeta.ink }} />
                </span>
              </div>
              <p className="ex-verdict__expl">{verdict.explanation}</p>
              {synthesis?.maturity && (
                <p className="ex-verdict__maturity"><strong>实现程度：</strong>{synthesis.maturity}</p>
              )}
              {verdict.evidence_keys.length > 0 && (
                <div className="ex-verdict__evidence">
                  <span>支撑证据：</span>
                  {verdict.evidence_keys.map(k => {
                    const p = papersByKey.get(k)
                    if (!p) return null
                    return (
                      <button key={k} type="button" onClick={() => setSelected(p)} title={p.title}>
                        {p.title.length > 34 ? `${p.title.slice(0, 34)}…` : p.title}
                      </button>
                    )
                  })}
                </div>
              )}
            </section>
          )}

          {/* 检索透明度：客观性的可见性 */}
          <section className="ex-transparency" aria-label="检索透明度">
            <div className="ex-transparency__row">
              <span className="ex-transparency__label">检索式（{INTENT_LABEL[result.decompose.intent] ?? result.decompose.intent}）</span>
              <div className="ex-transparency__queries">
                {result.decompose.en_queries.map(q => <code key={q}>{q}</code>)}
              </div>
            </div>
            <div className="ex-transparency__stats">
              <span>数据源 <strong>{result.provider === 'stub' ? '测试' : result.provider}</strong></span>
              <span>去重后 <strong>{total}</strong> 篇</span>
              {result.metrics.year_span.length === 2 && (
                <span>年份 <strong>{result.metrics.year_span[0]}–{result.metrics.year_span[1]}</strong></span>
              )}
              <span>近 2 年占比 <strong>{Math.round(result.metrics.recent_share * 100)}%</strong></span>
              <span>子方向 <strong>{result.metrics.clusters}</strong> 个</span>
              {result.metrics.enriched && result.metrics.enriched.with_history > 0 && (
                <span title="OpenAlex 按 DOI 补齐的逐年引用曲线覆盖数（突增与轨迹判定的数据底座）">
                  逐年引用富化 <strong>{result.metrics.enriched.with_history}/{total}</strong>
                </span>
              )}
            </div>
            {result.decompose.fallback && (
              <p className="ex-transparency__warn">
                未配置 LLM Key：跳过了检索式拆解与综合报告，使用原句直检索（统计视图照常可用）。
              </p>
            )}
            {total === 0 && (
              <p className="ex-transparency__warn">
                检索未命中：换一个更具体的表述，或在设置页检查检索源配置。系统不会用模型记忆填补空白。
              </p>
            )}
          </section>

          {/* 证据详情 */}
          {selected && (
            <section className="ex-evidence-card" aria-label="证据详情">
              <div className="ex-evidence-card__head">
                <h3>{selected.title}</h3>
                <button type="button" aria-label="关闭" onClick={() => setSelected(null)}>×</button>
              </div>
              <div className="ex-evidence-card__meta">
                {selected.year ? <span>{selected.year} 年</span> : null}
                <span>被引 {fmtCitations(selected.citation_count)}</span>
                {selected.citation_velocity != null && <span>速度 {selected.citation_velocity}/年</span>}
                {selected.recent_citations != null && (
                  <span title="OpenAlex 逐年引用曲线的近两个日历年合计（含预印本引用）">
                    近2年被引 {fmtCitations(selected.recent_citations)}
                  </span>
                )}
                {selected.citation_percentile != null && selected.citation_percentile >= 90 && (
                  <span title="同年语料内被引百分位（ESI 高被引的语料内复刻，年龄无关的公平指标）">
                    同年 P{selected.citation_percentile}
                  </span>
                )}
                {selected.highly_cited && <span className="ex-surge">★ 同年前5%</span>}
                {selected.trajectory === 'rising' && <span title="近 2 年引用均速 ≥ 之前 2 年 ×1.5">↗ 引用爬升</span>}
                {selected.trajectory === 'declining' && <span title="近 2 年引用均速 ≤ 之前 2 年 ×0.5">↘ 引用回落</span>}
                {selected.surge && <span className="ex-surge">🔥 突增</span>}
                {selected.venue?.name && (
                  <span title={selected.venue.sjr != null ? 'SJR = SCImago 期刊等级分（Serial Title API 实时值）' : undefined}>
                    {selected.venue.name}{selected.venue.sjr != null ? ` · SJR ${selected.venue.sjr}` : ''}
                  </span>
                )}
                {selected.cluster >= 0 && (
                  <span>
                    <i className="ex-cluster-dot" style={{ background: clusterInk(selected.cluster) }} />
                    {selected.cluster_label || `子方向 ${selected.cluster}`}
                  </span>
                )}
                {selected.authors && <span className="ex-evidence-card__authors">{selected.authors}</span>}
                {paperLink(selected) && (
                  <a href={paperLink(selected) ?? '#'} target="_blank" rel="noreferrer">打开原文 ↗</a>
                )}
              </div>
              {selected.keywords && selected.keywords.length > 0 && (
                <div className="ex-evidence-card__kws">
                  {selected.keywords.map(k => <code key={k}>{k}</code>)}
                </div>
              )}
              {selected.abstract && <p className="ex-evidence-card__abstract">{selected.abstract}</p>}
            </section>
          )}

          {/* 领域总览（复用：热力图 + 战略坐标 + 范式卡片） */}
          {overviewGraph && (
            <section className="ex-section" aria-label="领域总览">
              <h2 className="ex-section__title">领域总览 · 子方向 × 年份</h2>
              <DomainOverview
                graph={overviewGraph}
                synthesis={overviewSynthesis}
                onSelectNode={handleSelectNode}
              />
            </section>
          )}

          {/* 主阵地期刊：哪些期刊在承载这个方向 */}
          {result.metrics.venue_ranking && result.metrics.venue_ranking.length > 0 && (
            <section className="ex-section" aria-label="主阵地期刊">
              <h2 className="ex-section__title">主阵地期刊 · 该方向论文集中的发表地</h2>
              <ul className="ex-venue">
                {result.metrics.venue_ranking.map(v => (
                  <li key={v.name}>
                    <span className="ex-venue__name" title={v.name}>{v.name}</span>
                    <span className="ex-venue__count">{v.count} 篇</span>
                    {v.sjr != null && (
                      <span className="ex-venue__metric" title="SCImago 期刊等级分（Scopus Serial Title 实时值）">SJR {v.sjr}</span>
                    )}
                    {v.snip != null && (
                      <span className="ex-venue__metric" title="来源标准化影响因子（学科口径归一）">SNIP {v.snip}</span>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/* 关键词 × 年份热力图（GOAL 承诺的热力图，v1.4 兑现） */}
          {result.metrics.keyword_trend && result.metrics.keyword_trend.rows.length > 0 && (
            <section className="ex-section" aria-label="关键词趋势热力图">
              <h2 className="ex-section__title">关键词 × 年份热力图 · 子话题的时间分布</h2>
              <KeywordHeatmap trend={result.metrics.keyword_trend} />
              <p className="ex-heat__note">颜色越深 = 当年含该关键词的新发表论文越多（语料内计数，≥3 篇才入图）</p>
            </section>
          )}

          {/* 证据列表：高被引经典 + 近期上升 */}
          {total > 0 && (
            <section className="ex-section" aria-label="证据列表">
              <h2 className="ex-section__title">证据列表 · 点击查看摘要与原文链接</h2>
              <div className="ex-evidence-cols">
                <EvidenceList
                  title="🏛️ 高被引经典"
                  hint="领域证据的骨干"
                  keys={result.metrics.top_cited}
                  papersByKey={papersByKey}
                  onSelect={setSelected}
                />
                <EvidenceList
                  title="🔥 近期上升"
                  hint="按引用速度排序的新工作"
                  keys={result.metrics.rising}
                  papersByKey={papersByKey}
                  onSelect={setSelected}
                />
              </div>
            </section>
          )}

          {/* 热点 / 空白 / 后续 */}
          {synthesis && (
            <section className="ex-section" aria-label="热点与空白">
              <h2 className="ex-section__title">热点 · 空白 · 后续动作</h2>
              <div className="ex-evidence-cols">
                <ChipList title="📈 近期热点" items={synthesis.hotspots} ink="#d94a38" />
                <ChipList title="🧭 尚未解决的空白" items={synthesis.gaps} ink="#2f7d78" />
              </div>
              {synthesis.follow_ups.length > 0 && (
                <div className="ex-followups">
                  <span>建议下一步：</span>
                  {synthesis.follow_ups.map((f, i) => (
                    <button key={i} type="button" onClick={() => setQuestion(f)} title="点击填入提问框">{f}</button>
                  ))}
                </div>
              )}
            </section>
          )}

          {/* Markdown 报告 */}
          {synthesis?.markdown_report && (
            <section className="ex-section" aria-label="探索报告">
              <h2 className="ex-section__title">探索报告</h2>
              <div className="ex-report">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{synthesis.markdown_report}</ReactMarkdown>
              </div>
            </section>
          )}
        </div>
      )}

      {/* 尚无结果时的引导 */}
      {!result && stream.status !== 'running' && !submitting && stream.status !== 'failed' && (
        <section className="ex-empty">
          {runId ? '该探索没有可展示的产物。' : '输入一个问题开始。系统会给出：查新结论 → 检索透明度 → 子方向×年份热力图 → 高被引与上升黑马证据 → 跨文献报告。'}
        </section>
      )}
    </div>
  )
}

// ── 子组件 ────────────────────────────────────────────────────────────────

function isAfterStage(stage: string | null, key: string, result?: { metrics?: unknown }, synthesis?: unknown): boolean {
  if (!stage) return false
  const order = STAGE_META.map(s => s.key)
  const cur = order.findIndex(k => stage.startsWith(k))
  if (cur < 0) return false
  const idx = order.indexOf(key)
  if (idx < cur) return true
  // 阶段完成但 stage 文案还没翻页的兜底：产物已到即视为该阶段之后
  if (key === 'metrics' && result) return true
  if (key === 'synthesize' && synthesis) return true
  return false
}

function EvidenceList({
  title, hint, keys, papersByKey, onSelect,
}: {
  title: string
  hint: string
  keys: string[]
  papersByKey: Map<string, ExplorationPaper>
  onSelect: (p: ExplorationPaper) => void
}) {
  const papers = keys.map(k => papersByKey.get(k)).filter((p): p is ExplorationPaper => Boolean(p))
  return (
    <div className="ex-evlist">
      <div className="ex-evlist__head" title={hint}>{title}</div>
      <ol>
        {papers.map((p, i) => (
          <li key={p.key}>
            <button type="button" className="ex-evlist__row" onClick={() => onSelect(p)}>
              <span className="ex-evlist__idx">{i + 1}</span>
              <span className="ex-evlist__title">{p.title}</span>
              <span className="ex-evlist__nums">
                {p.year ?? '—'} · 被引{fmtCitations(p.citation_count)}
                {p.citation_velocity != null && ` · ${p.citation_velocity}/年`}
                {p.recent_citations != null && ` · 2y ${fmtCitations(p.recent_citations)}`}
                {p.highly_cited ? ' ★' : ''}
                {p.surge ? ' 🔥' : ''}
              </span>
            </button>
          </li>
        ))}
        {papers.length === 0 && <li className="ex-evlist__empty">暂无</li>}
      </ol>
    </div>
  )
}

function KeywordHeatmap({ trend }: { trend: ExplorationKeywordTrend }) {
  const { years, rows } = trend
  if (!rows.length) return null
  const max = Math.max(...rows.flatMap(r => r.counts), 1)
  // 年份列数动态：经 --heat-cols 注入 grid 模板（repeat 的次数参数）
  const gridStyle = { '--heat-cols': years.length } as React.CSSProperties
  return (
    <div className="ex-heat" role="table" aria-label="关键词与年份交叉的热力图">
      <div className="ex-heat__row ex-heat__row--head" role="row" style={gridStyle}>
        <span className="ex-heat__kw" role="columnheader" aria-label="关键词" />
        {years.map(y => (
          <span key={y} className="ex-heat__year" role="columnheader">{String(y).slice(2)}’</span>
        ))}
        <span className="ex-heat__total" role="columnheader" title="语料内总篇数">Σ</span>
      </div>
      {rows.map(r => (
        <div key={r.keyword} className="ex-heat__row" role="row" style={gridStyle}>
          <span className="ex-heat__kw" role="rowheader" title={r.keyword}>{r.keyword}</span>
          {r.counts.map((c, i) => (
            <span
              key={i}
              className="ex-heat__cell"
              role="cell"
              style={c ? { background: inkWash('#3969a8', 16 + Math.round(68 * Math.sqrt(c / max))) } : undefined}
            >
              {c > 0 ? c : ''}
            </span>
          ))}
          <span className="ex-heat__total" role="cell">{r.total}</span>
        </div>
      ))}
    </div>
  )
}

function ChipList({ title, items, ink }: { title: string; items: string[]; ink: string }) {
  return (
    <div className="ex-chips">
      <div className="ex-chips__head" style={{ color: ink }}>{title}</div>
      {items.length === 0 && <p className="ex-chips__empty">—</p>}
      <ul>
        {items.map((t, i) => (
          <li key={i} style={{ borderColor: inkWash(ink, 45), background: inkWash(ink, 8) }}>{t}</li>
        ))}
      </ul>
    </div>
  )
}
