import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

export interface PaperReference {
  key?: string
  paper_id?: number | null
  title: string
  doi?: string | null
  arxiv_id?: string | null
  url?: string | null
  pdf_url?: string | null
  scopus_id?: string | null
}

export function safePaperUrl(value?: string | null): string | null {
  if (!value) return null
  try {
    const url = new URL(value)
    return ['https:', 'http:'].includes(url.protocol) ? url.href : null
  } catch { return null }
}

export function paperLink(p: PaperReference): string | null {
  const doi = p.doi?.replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, '').replace(/^doi:/i, '').trim()
  if (doi) return `https://doi.org/${encodeURI(doi).replace(/[?#]/g, encodeURIComponent)}`
  if (p.arxiv_id) return `https://arxiv.org/abs/${encodeURIComponent(p.arxiv_id.replace(/^arxiv:/i, ''))}`
  const direct = safePaperUrl(p.url) ?? safePaperUrl(p.pdf_url)
  if (direct) return direct
  if (p.scopus_id && /^\d+$/.test(p.scopus_id)) return `https://www.scopus.com/record/display.uri?eid=2-s2.0-${p.scopus_id}&origin=resultslist`
  if (p.key?.startsWith('doi:')) return paperLink({ ...p, doi: p.key.slice(4), key: undefined })
  if (p.key?.startsWith('arxiv:')) return paperLink({ ...p, arxiv_id: p.key.slice(6), key: undefined })
  if (p.key?.startsWith('openalex:')) return safePaperUrl(`https://openalex.org/${p.key.slice(9)}`)
  if (p.paper_id && Number.isSafeInteger(p.paper_id) && p.paper_id > 0) return `/reader/${p.paper_id}`
  return null
}

export function PaperTitle({ paper, className, onSelect }: {
  paper: PaperReference; className?: string; onSelect?: () => void
}) {
  const url = paperLink(paper)
  return url
    ? <a className={className} href={url} title={paper.title} target="_blank" rel="noopener noreferrer">{paper.title}</a>
    : onSelect
      ? <button type="button" className={className} title={paper.title} onClick={onSelect}>{paper.title}</button>
      : <span className={className} title={paper.title}>{paper.title}</span>
}

interface MarkdownNode { type: string; value?: string; url?: string; children?: MarkdownNode[] }

/** Link only known titles/citation IDs in text nodes, leaving existing Markdown links intact. */
export function paperReferencesPlugin(options: { papers: PaperReference[]; citations?: Record<string, string> }) {
  const refs = new Map<string, string>()
  const aliases = new Map<string, Set<string>>()
  for (const paper of options.papers) {
    const prefix = paper.title.split(/[:：]/)[0].trim()
    const url = paperLink(paper)
    if (url && prefix !== paper.title && prefix.length >= 4 && prefix.length <= 60) {
      const key = prefix.toLowerCase()
      if (!aliases.has(key)) aliases.set(key, new Set())
      aliases.get(key)!.add(url)
    }
  }
  for (const [label, urls] of aliases) if (urls.size === 1) refs.set(label, [...urls][0])
  for (const paper of options.papers) {
    const url = paperLink(paper)
    if (url && paper.title.length >= 4) refs.set(paper.title, url)
    if (url) for (const [id, key] of Object.entries(options.citations ?? {})) {
      if (key === paper.key) refs.set(`[${id}]`, url)
    }
  }
  const labels = [...refs.keys()].sort((a, b) => b.length - a.length)
  return (tree: MarkdownNode) => {
    function visit(node: MarkdownNode) {
      if (!node.children || ['link', 'linkReference', 'code', 'inlineCode'].includes(node.type)) return
      node.children = node.children.flatMap(child => {
        if (child.type !== 'text' || !child.value) { visit(child); return [child] }
        const output: MarkdownNode[] = []
        let rest = child.value
        while (rest) {
          let at = rest.length
          let match = ''
          for (const label of labels) {
            let i = rest.toLowerCase().indexOf(label.toLowerCase())
            while (i >= 0) {
              const before = rest[i - 1] ?? ''
              const after = rest[i + label.length] ?? ''
              const joinsWord = (/[a-z0-9]$/i.test(before) && /^[a-z0-9]/i.test(label))
                || (/[a-z0-9]$/i.test(label) && /^[a-z0-9-]/i.test(after))
              if (!joinsWord) break
              i = rest.toLowerCase().indexOf(label.toLowerCase(), i + 1)
            }
            if (i >= 0 && i < at) { at = i; match = label }
          }
          if (!match) { output.push({ type: 'text', value: rest }); break }
          if (at) output.push({ type: 'text', value: rest.slice(0, at) })
          output.push({ type: 'link', url: refs.get(match), children: [{ type: 'text', value: rest.slice(at, at + match.length) }] })
          rest = rest.slice(at + match.length)
        }
        return output
      })
    }
    visit(tree)
  }
}

export function EvidenceMarkdown({ text, papers, citations }: {
  text: string; papers: PaperReference[]; citations?: Record<string, string>
}) {
  return <ReactMarkdown
    remarkPlugins={[remarkGfm, [paperReferencesPlugin, { papers, citations }]]}
    components={{ a: ({ children, href }) => safePaperUrl(href) || /^\/reader\/\d+$/.test(href ?? '')
      ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
      : <span>{children}</span> }}
  >{text}</ReactMarkdown>
}
