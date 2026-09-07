type Claim = {
  id: string; claim: string; label: string; reason?: string
  evidence?: { source_id: string; quote: string }[]
}

export function EvidenceAuditPanel({ audit }: { audit?: unknown }) {
  if (!audit || typeof audit !== 'object') return null
  const data = audit as Record<string, unknown>
  const claims = Array.isArray(data.claims) ? data.claims.filter((c): c is Claim =>
    !!c && typeof c === 'object' && typeof c.claim === 'string' && typeof c.label === 'string') : []
  const statuses: Record<string, string> = {
    completed: '已完成自动核验', partial: '部分完成', skipped: '未核验',
    error: '核验未完成', no_claims: '无可核验论断',
  }
  const labels: Record<string, string> = {
    supported: '材料支持', contradicted: '材料矛盾', insufficient: '证据不足',
  }
  return <details className="ex-evidence-audit">
    <summary>证据核验 · {statuses[String(data.status)] || '未核验'}
      {claims.length > 0 && ` · ${Number(data.supported_count) || 0}/${claims.length} 条材料支持`}
    </summary>
    <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>自动核验仅依据本轮材料，结果待人工复核。</p>
    {claims.map((claim, index) => <div key={claim.id || index} className="py-2 border-b" style={{ borderColor: 'var(--border)' }}>
      <strong className="text-xs">{labels[claim.label] || '证据不足'}</strong>
      <p>{claim.claim}</p>
      {claim.reason && <p className="text-xs">{claim.reason}</p>}
      {Array.isArray(claim.evidence) && claim.evidence.filter(entry => entry && typeof entry.quote === 'string').map((entry, i) => <blockquote key={i} className="text-xs pl-3 my-2 border-l-2">
        [{entry.source_id}] {entry.quote}
      </blockquote>)}
    </div>)}
  </details>
}
