import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { EvidenceAuditPanel } from '@/features/research/EvidenceAuditPanel'

describe('EvidenceAuditPanel', () => {
  it('keeps incomplete verification distinct from an accuracy score', () => {
    render(<EvidenceAuditPanel audit={{ status: 'partial', supported_count: 1, support_rate: null,
      claims: [{ id: 'c1', claim: 'Method result', label: 'insufficient',
        evidence: [{ source_id: 'P1', quote: 'Actual source text' }] }] }} />)
    expect(screen.getByText(/部分完成/)).toBeInTheDocument()
    expect(screen.getByText('证据不足')).toBeInTheDocument()
    expect(screen.getByText(/Actual source text/)).toBeInTheDocument()
    expect(screen.queryByText(/100%/)).not.toBeInTheDocument()
  })
  it('tolerates legacy or malformed audit metadata', () => {
    const { rerender } = render(<EvidenceAuditPanel />)
    expect(screen.queryByText(/证据核验/)).not.toBeInTheDocument()
    rerender(<EvidenceAuditPanel audit={{ status: 'error', claims: [null, 'bad'] }} />)
    expect(screen.getByText(/核验未完成/)).toBeInTheDocument()
  })
})
