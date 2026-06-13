/**
 * 深度研究相关设置区块集合
 *
 * Embedding / Research Provider / Reranker / Similarity / arXiv / findpapers
 * 共 6 个子组件，从 SettingsPage 拆出。
 * 只负责渲染与改写传入的 config 切片，保存逻辑由宿主页面处理。
 */
import type { Dispatch, SetStateAction } from 'react'
import { TestButton } from './TestButton'
import { GrobidPanel } from './GrobidPanel'
import type { AppConfig, EmbeddingConfig } from './config'


type SetConfigFn = Dispatch<SetStateAction<AppConfig>>

interface SectionProps {
  config: AppConfig
  setConfig: SetConfigFn
}


// ============================================================
// Embedding 模型
// ============================================================

export function EmbeddingSection({ config, setConfig }: SectionProps) {
  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-gray-100">Embedding 模型</h2>
          <p className="text-xs text-gray-500 mt-0.5">用于深度研究的向量嵌入模型。本地模式首次运行需下载模型（~2GB）。</p>
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
        <label className="block text-xs text-gray-500 mb-2">Provider</label>
        <div className="flex gap-2">
          {([
            { key: 'local' as const, label: '本地 (BGE-M3)' },
            { key: 'siliconflow' as const, label: '硅基流动' },
            { key: 'openai' as const, label: 'OpenAI' },
          ]).map(p => (
            <button
              key={p.key}
              onClick={() => setConfig(prev => {
                const defaults: Record<string, Partial<EmbeddingConfig>> = {
                  local: { model: 'BAAI/bge-m3', base_url: 'https://api.openai.com/v1' },
                  siliconflow: { model: 'BAAI/bge-m3', base_url: 'https://api.siliconflow.cn/v1' },
                  openai: { model: 'text-embedding-3-small', base_url: 'https://api.openai.com/v1' },
                }
                return { ...prev, embedding: { ...prev.embedding, provider: p.key, ...defaults[p.key] } }
              })}
              className={`px-3 py-1.5 text-xs rounded-lg border transition-colors ${
                config.embedding.provider === p.key
                  ? 'border-blue-500 bg-blue-500/10 text-blue-400'
                  : 'border-gray-700 text-gray-400 hover:border-gray-600'
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* 模型名 */}
      <div>
        <label className="block text-xs text-gray-500 mb-1">模型名称</label>
        <input
          type="text"
          value={config.embedding.model}
          onChange={e => setConfig(prev => ({ ...prev, embedding: { ...prev.embedding, model: e.target.value } }))}
          placeholder={config.embedding.provider === 'local' ? 'BAAI/bge-m3' : config.embedding.provider === 'siliconflow' ? 'BAAI/bge-m3' : 'text-embedding-3-small'}
          className="w-full px-3 py-2 text-sm bg-gray-800 border border-gray-700 rounded-lg text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500 transition-colors"
        />
      </div>

      {/* OpenAI / SiliconFlow 专用配置 */}
      {(config.embedding.provider === 'openai' || config.embedding.provider === 'siliconflow') && (
        <>
          <div>
            <label className="block text-xs text-gray-500 mb-1">API Base URL</label>
            <input
              type="text"
              value={config.embedding.base_url}
              onChange={e => setConfig(prev => ({ ...prev, embedding: { ...prev.embedding, base_url: e.target.value } }))}
              className="w-full px-3 py-2 text-sm bg-gray-800 border border-gray-700 rounded-lg text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500 transition-colors"
            />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">API Key</label>
            <input
              type="password"
              value={config.embedding.api_key}
              onChange={e => setConfig(prev => ({ ...prev, embedding: { ...prev.embedding, api_key: e.target.value } }))}
              placeholder="sk-..."
              className="w-full px-3 py-2 text-sm bg-gray-800 border border-gray-700 rounded-lg text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500 transition-colors font-mono"
            />
          </div>
        </>
      )}

      {config.embedding.provider === 'local' && (
        <p className="text-[10px] text-gray-600">
          本地模式使用 sentence-transformers 加载模型，首次运行会自动下载。切换 provider 后建议重建索引以避免向量维度不匹配。
        </p>
      )}
    </div>
  )
}


// ============================================================
// 深度研究 — 论文扩展源
// ============================================================

export function ResearchProviderSection({ config, setConfig }: SectionProps) {
  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 space-y-4">
      <div>
        <h2 className="text-base font-semibold text-gray-100">深度研究 — 论文扩展源</h2>
        <p className="text-xs text-gray-500 mt-0.5">
          决定"联网扩展"阶段从哪里抓取参考文献。本地模式表示不联网，只用工作区内已上传的论文构建图谱。
        </p>
      </div>

      <div>
        <label className="block text-xs text-gray-500 mb-2">Provider</label>
        <div className="flex gap-2 flex-wrap">
          {([
            { key: 'openalex' as const, label: 'OpenAlex（推荐）' },
            { key: 'semantic_scholar' as const, label: 'Semantic Scholar' },
            { key: 'local' as const, label: '本地（不联网）' },
          ]).map(p => (
            <button
              key={p.key}
              onClick={() => setConfig(prev => ({
                ...prev,
                research: { ...prev.research, provider: p.key },
              }))}
              className={`px-3 py-1.5 text-xs rounded-lg border transition-colors ${
                config.research.provider === p.key
                  ? 'border-blue-500 bg-blue-500/10 text-blue-400'
                  : 'border-gray-700 text-gray-400 hover:border-gray-600'
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {config.research.provider === 'openalex' && (
        <div>
          <label className="block text-xs text-gray-500 mb-1">
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
            className="w-full px-3 py-2 text-sm bg-gray-800 border border-gray-700 rounded-lg text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500 transition-colors"
          />
          <p className="text-[10px] text-gray-600 mt-1">
            填入邮箱可进入 OpenAlex polite pool，获得更稳定的速率配额。不填也能用。
          </p>
        </div>
      )}

      {config.research.provider === 'semantic_scholar' && (
        <div>
          <label className="block text-xs text-gray-500 mb-1">
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
            className="w-full px-3 py-2 text-sm bg-gray-800 border border-gray-700 rounded-lg text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500 transition-colors font-mono"
          />
          <p className="text-[10px] text-gray-600 mt-1">
            申请地址：
            <a
              href="https://www.semanticscholar.org/product/api"
              target="_blank"
              rel="noreferrer"
              className="text-blue-400 hover:underline ml-1"
            >
              semanticscholar.org/product/api
            </a>
            （免费，约 1–2 天通过）
          </p>
        </div>
      )}

      {config.research.provider === 'local' && (
        <p className="text-[10px] text-gray-600">
          本地模式下，"联网扩展"阶段会被直接跳过；图谱仅基于工作区已上传的论文构建。
        </p>
      )}

      {/* 相似度阈值 */}
      <div className="pt-2 border-t border-gray-800">
        <div className="flex items-center justify-between mb-1">
          <div>
            <span className="text-xs font-medium text-gray-300">默认相似度阈值</span>
            <p className="text-[10px] text-gray-500 mt-0.5">
              两篇论文向量余弦相似度超过此值时图谱连一条「相似」边。值越大边越少越精炼。可在每次开始研究时临时调整。
            </p>
          </div>
          <span className="text-xs font-mono text-gray-300 ml-4 shrink-0">
            {config.research.similarity_threshold.toFixed(2)}
          </span>
        </div>
        <input
          type="range"
          min={0.5}
          max={0.95}
          step={0.01}
          value={config.research.similarity_threshold}
          onChange={e => setConfig(prev => ({
            ...prev,
            research: { ...prev.research, similarity_threshold: Number(e.target.value) },
          }))}
          className="w-full h-1 accent-blue-500"
        />
        <div className="flex justify-between text-[10px] text-gray-600 mt-0.5">
          <span>0.50 宽松</span>
          <span>0.95 严格</span>
        </div>
      </div>

      <div className="pt-3 border-t border-gray-800">
        <label className="flex items-start gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={config.research.auto_digest ?? true}
            onChange={e => setConfig(prev => ({ ...prev, research: { ...prev.research, auto_digest: e.target.checked } }))}
            className="mt-0.5 accent-blue-500"
          />
          <span>
            <span className="text-xs font-medium text-gray-300">深度研究时自动「图谱速读」未解读论文</span>
            <p className="text-[10px] text-gray-500 mt-0.5">
              开启后，每次深度研究在构建图谱前会对未精读的论文做一次轻量速读（只抽研究目的/方法/数据集/基线 + 简短总结），
              让它们也获得可解释的图谱关系。会消耗少量 LLM Token（约为全量精读的 1/5~1/10）。关闭则只能手动点「速读未解读论文」。
            </p>
          </span>
        </label>
      </div>
    </div>
  )
}


// ============================================================
// 重排序模型（Reranker）
// ============================================================

export function RerankerSection({ config, setConfig }: SectionProps) {
  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-gray-100">重排序模型（Reranker）</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            在 RAG 跨论文问答时使用。向量检索召回 candidate_k 条候选，再由重排序模型按语义重排，最终取 top_n 喂给 LLM。提升答非所问的概率。
          </p>
        </div>
        <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer">
          <input
            type="checkbox"
            checked={config.reranker.enabled}
            onChange={e => setConfig(prev => ({ ...prev, reranker: { ...prev.reranker, enabled: e.target.checked } }))}
            className="accent-blue-500"
          />
          启用
        </label>
      </div>

      {config.reranker.enabled && (
        <>
          <div>
            <label className="block text-xs text-gray-500 mb-1">模型</label>
            <input
              type="text"
              value={config.reranker.model}
              onChange={e => setConfig(prev => ({ ...prev, reranker: { ...prev.reranker, model: e.target.value } }))}
              placeholder="BAAI/bge-reranker-v2-m3"
              className="w-full px-3 py-2 text-sm bg-gray-800 border border-gray-700 rounded-lg text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500 transition-colors"
            />
            <p className="text-[10px] text-gray-600 mt-1">默认 BAAI/bge-reranker-v2-m3（硅基流动）</p>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">API Base URL</label>
              <input
                type="text"
                value={config.reranker.base_url}
                onChange={e => setConfig(prev => ({ ...prev, reranker: { ...prev.reranker, base_url: e.target.value } }))}
                placeholder="留空 = 继承 embedding 的 base_url"
                className="w-full px-3 py-2 text-sm bg-gray-800 border border-gray-700 rounded-lg text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500 transition-colors"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">API Key</label>
              <input
                type="password"
                value={config.reranker.api_key}
                onChange={e => setConfig(prev => ({ ...prev, reranker: { ...prev.reranker, api_key: e.target.value } }))}
                placeholder="留空 = 继承 embedding 的 api_key"
                className="w-full px-3 py-2 text-sm bg-gray-800 border border-gray-700 rounded-lg text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-500 transition-colors font-mono"
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">candidate_k（召回数）</label>
              <input
                type="number"
                min={5}
                max={200}
                value={config.reranker.candidate_k}
                onChange={e => setConfig(prev => ({ ...prev, reranker: { ...prev.reranker, candidate_k: Number(e.target.value) || 30 } }))}
                className="w-full px-3 py-2 text-sm bg-gray-800 border border-gray-700 rounded-lg text-gray-200 focus:outline-none focus:border-blue-500 transition-colors"
              />
              <p className="text-[10px] text-gray-600 mt-1">向量检索的初筛数量，越大重排越充分但越慢</p>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">top_n（送 LLM）</label>
              <input
                type="number"
                min={1}
                max={20}
                value={config.reranker.top_n}
                onChange={e => setConfig(prev => ({ ...prev, reranker: { ...prev.reranker, top_n: Number(e.target.value) || 5 } }))}
                className="w-full px-3 py-2 text-sm bg-gray-800 border border-gray-700 rounded-lg text-gray-200 focus:outline-none focus:border-blue-500 transition-colors"
              />
              <p className="text-[10px] text-gray-600 mt-1">重排序后保留的最终 chunk 数</p>
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
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 space-y-4">
      <div>
        <h2 className="text-base font-semibold text-gray-100">论文级相似度计算（Similarity Provider）</h2>
        <p className="text-xs text-gray-500 mt-0.5">
          深度研究知识图谱中"相似"边的计算方式。可插拔接口，可在 <code className="px-1 rounded bg-gray-800">backend/services/deep_research/similarity/</code> 自行新增 provider 并在 REGISTRY 注册。
        </p>
      </div>

      <div>
        <label className="block text-xs text-gray-500 mb-1">Provider</label>
        <select
          value={config.similarity.provider}
          onChange={e => setConfig(prev => ({ ...prev, similarity: { ...prev.similarity, provider: e.target.value } }))}
          className="w-full px-3 py-2 text-sm bg-gray-800 border border-gray-700 rounded-lg text-gray-200 focus:outline-none focus:border-blue-500 transition-colors"
        >
          {simProviders.map(p => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
        <p className="text-[10px] text-gray-600 mt-1">
          当前默认 <code className="px-1 rounded bg-gray-800">unified</code>：给<b>每篇</b>论文挑最佳主题向量（ChromaDB 质心 &gt; title/abstract embedding），
          全量连成研究地图；已解读论文对再挂可解释的 facet 分解。去噪用<b>互近邻</b>（mutual-kNN）。
          <code className="px-1 rounded bg-gray-800">bge_centroid</code> 等旧法保留可切换对比。
        </p>
        <div className="mt-3 px-3 py-2 rounded-lg bg-gray-800/50 border border-gray-700/50">
          <p className="text-[11px] text-gray-400 leading-relaxed">
            <span className="text-gray-300 font-medium">未来可扩展（已在 base.py 列出 TODO）：</span><br />
            ·  <span className="text-gray-300">specter2 / scincl</span> —— 科研论文专用 embedding，用引用对训练，区分研究主题更准<br />
            ·  <span className="text-gray-300">bibcoupling</span> —— 两篇论文共享引用的 Jaccard，捕获"被同一领域引用"<br />
            ·  <span className="text-gray-300">hybrid</span> —— 多信号融合<br />
            ·  <span className="text-gray-300">cross_encoder</span> —— 复用 BAAI/bge-reranker-v2-m3 当 cross-encoder
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
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-gray-100">arXiv 引用列表扩展</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            联网模式下，从 L1 论文 PDF 文本解析 <code className="px-1 rounded bg-gray-800">[N]</code> 引用块，
            提取 arXiv ID（正则优先，可选 LLM 兜底），从 arXiv 下载 PDF。
            与 OpenAlex/S2 扩展互补。
          </p>
        </div>
        <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer shrink-0 ml-4">
          <input
            type="checkbox"
            checked={config.arxiv_expand.enabled}
            onChange={e => setConfig(prev => ({ ...prev, arxiv_expand: { ...prev.arxiv_expand, enabled: e.target.checked } }))}
            className="accent-blue-500"
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
              <label className="block text-xs text-gray-500 mb-1">Top-K（按引用数取前 K 篇）</label>
              <input
                type="number"
                min={1}
                max={50}
                value={config.arxiv_expand.top_k}
                onChange={e => setConfig(prev => ({ ...prev, arxiv_expand: { ...prev.arxiv_expand, top_k: Number(e.target.value) || 5 } }))}
                className="w-full px-3 py-2 text-sm bg-gray-800 border border-gray-700 rounded-lg text-gray-200 focus:outline-none focus:border-blue-500 transition-colors"
              />
              <p className="text-[10px] text-gray-600 mt-1">从候选论文中取前 K 篇（引用列表越靠前越重要）</p>
            </div>
          </div>

          <div className="flex items-start gap-4">
            <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer">
              <input
                type="checkbox"
                checked={config.arxiv_expand.use_llm_title}
                onChange={e => setConfig(prev => ({ ...prev, arxiv_expand: { ...prev.arxiv_expand, use_llm_title: e.target.checked } }))}
                className="accent-blue-500"
              />
              LLM 标题兜底
            </label>
            {config.arxiv_expand.use_llm_title && (
              <div className="flex-1">
                <label className="block text-xs text-gray-500 mb-1">
                  每篇 seed 最多 LLM+arXiv 搜索次数
                </label>
                <input
                  type="number"
                  min={0}
                  max={50}
                  value={config.arxiv_expand.max_llm_refs}
                  onChange={e => setConfig(prev => ({ ...prev, arxiv_expand: { ...prev.arxiv_expand, max_llm_refs: Number(e.target.value) || 15 } }))}
                  className="w-48 px-3 py-2 text-sm bg-gray-800 border border-gray-700 rounded-lg text-gray-200 focus:outline-none focus:border-blue-500 transition-colors"
                />
                <p className="text-[10px] text-gray-600 mt-1">
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

export function FindpapersSection({ config, setConfig }: SectionProps) {
  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-5 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold text-gray-100">findpapers 多源搜索</h2>
          <p className="text-xs text-gray-500 mt-0.5">
            启用后使用 findpapers 库（arXiv、CrossRef、OpenAlex、PubMed 等 8 源并行搜索 + 自动去重）
            替代 Semantic Scholar 进行相关论文推荐和 PDF 下载。
            需要 <code className="px-1 rounded bg-gray-800">pip install findpapers</code>。
          </p>
        </div>
        <label className="flex items-center gap-2 text-xs text-gray-300 cursor-pointer shrink-0 ml-4">
          <input
            type="checkbox"
            checked={config.findpapers.enabled}
            onChange={e => setConfig(prev => ({ ...prev, findpapers: { ...prev.findpapers, enabled: e.target.checked } }))}
            className="accent-blue-500"
          />
          启用
        </label>
      </div>

      {config.findpapers.enabled && (
        <>
          <div>
            <label className="block text-xs text-gray-500 mb-2">启用的数据库</label>
            <div className="flex flex-wrap gap-2">
              {[
                { key: 'arxiv', label: 'arXiv', free: true },
                { key: 'openalex', label: 'OpenAlex', free: true },
                { key: 'pubmed', label: 'PubMed', free: true },
                { key: 'semantic_scholar', label: 'Semantic Scholar', free: true },
                { key: 'ieee', label: 'IEEE Xplore', free: false },
                { key: 'scopus', label: 'Scopus', free: false },
                { key: 'wos', label: 'Web of Science', free: false },
              ].map(db => (
                <label key={db.key} className="flex items-center gap-1.5 text-xs text-gray-300 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={config.findpapers.databases.includes(db.key)}
                    onChange={e => {
                      const dbs = e.target.checked
                        ? [...config.findpapers.databases, db.key]
                        : config.findpapers.databases.filter(d => d !== db.key)
                      setConfig(prev => ({ ...prev, findpapers: { ...prev.findpapers, databases: dbs } }))
                    }}
                    className="accent-blue-500"
                  />
                  {db.label}
                  {!db.free && <span className="text-[10px] text-amber-500">(需 key)</span>}
                </label>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">搜索结果数量上限</label>
              <input
                type="number"
                min={1}
                max={50}
                value={config.findpapers.max_results}
                onChange={e => setConfig(prev => ({ ...prev, findpapers: { ...prev.findpapers, max_results: Number(e.target.value) || 10 } }))}
                className="w-full px-3 py-2 text-sm bg-gray-800 border border-gray-700 rounded-lg text-gray-200 focus:outline-none focus:border-blue-500 transition-colors"
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
      <RerankerSection config={config} setConfig={setConfig} />
      <SimilaritySection config={config} setConfig={setConfig} simProviders={simProviders || []} />
      <ArxivExpandSection config={config} setConfig={setConfig} />
      <FindpapersSection config={config} setConfig={setConfig} />
    </>
  )
}
