import { useEffect, useState } from 'react'
import { getResearchQuestions } from '@/api/exploration'
import type { ResearchQuestionState } from '@/types'

const labels: Record<string, string> = { unsearched: '正在寻找依据', reading: '正在阅读', leads_found: '已有线索', supported: '已有依据支持', conflicted: '存在冲突', insufficient: '依据仍不足' }
const phases: Record<string, string> = { training: '训练', inference: '推理' }

export function ResearchQuestions({ runId, refreshKey }: { runId: number; refreshKey: string }) {
  const [questions, setQuestions] = useState<ResearchQuestionState[]>([])
  const [error, setError] = useState('')
  useEffect(() => {
    const controller = new AbortController()
    setQuestions([])
    let loading = false
    const load = () => {
      if (loading) return
      loading = true
      return getResearchQuestions(runId, controller.signal).then(result => {
        if (!controller.signal.aborted) { setQuestions(result.items); setError('') }
      }).catch(() => { if (!controller.signal.aborted) setError('问题进度暂时不可用') }).finally(() => { loading = false })
    }
    void load()
    const timer = refreshKey.startsWith('running') ? window.setInterval(() => void load(), 5000) : null
    return () => { controller.abort(); if (timer !== null) window.clearInterval(timer) }
  }, [runId, refreshKey])
  return <section className="survey-section" aria-label="研究问题与缺口">
    <h3>正在解决的问题</h3>
    {error && <p role="status">{error}</p>}
    {!questions.length && !error && <p>正在整理问题与回答标准…</p>}
    {questions.map(question => <article key={`${question.question_id}-${question.protocol_version}`}>
      <h4>{question.text}</h4><p>{labels[question.status] ?? question.status}</p>
      {!!question.gaps?.length && <ul>{question.gaps.map((gap, index) => <li key={index}>{gap}</li>)}</ul>}
      {!!question.scope_constraints?.length && <p>研究范围：{question.scope_constraints.join("；")}</p>}
      {!!question.planning_gaps?.length && <p role="status">需求拆解仍需核查：{question.planning_gaps.join('；')}</p>}
      {!!question.requirements?.length && <details><summary>逐项研究计划（{question.requirements.length} 项）</summary>
        <ol>{question.requirements.map(item => <li key={item.unit_id}>
          <p>{item.requirement}{[item.subject, item.mode, item.phase ? phases[item.phase] : undefined].filter(Boolean).length > 0 &&
            `（${[item.subject, item.mode, item.phase ? phases[item.phase] : undefined].filter(Boolean).join(' · ')}）`}</p>
          <small>{item.source_origin === 'criterion' ? '用户标准' : '用户原句'}：{item.source_quote}</small>
        </li>)}</ol>
      </details>}
      {!!question.answer_criteria?.length && <details><summary>回答标准</summary><ul>{question.answer_criteria.map((item, index) => <li key={index}>{item}</li>)}</ul></details>}
    </article>)}
  </section>
}
