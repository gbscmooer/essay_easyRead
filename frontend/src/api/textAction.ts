import { post } from './client'


interface TextActionResponse {
  result: string
}


function isSingleWord(text: string): boolean {
  const trimmed = text.trim()
  return trimmed.length > 0 && trimmed.length < 30 && !/\s/.test(trimmed) && /^[\w.\-]+$/.test(trimmed)
}


async function lookupDictionary(word: string): Promise<string | null> {
  try {
    const res = await fetch(`https://api.dictionaryapi.dev/api/v2/entries/en/${encodeURIComponent(word)}`)
    if (!res.ok) return null
    const data = await res.json()
    if (!Array.isArray(data) || data.length === 0) return null

    const entry = data[0]
    const phonetic = entry.phonetic || ''
    const meanings = entry.meanings || []

    const lines: string[] = []
    if (phonetic) lines.push(`音标: ${phonetic}`)

    for (const meaning of meanings.slice(0, 3)) {
      const pos = meaning.partOfSpeech || ''
      const defs = meaning.definitions?.slice(0, 2) || []
      for (const d of defs) {
        lines.push(`${pos}: ${d.definition || ''}`)
        if (d.example) lines.push(`  例: ${d.example}`)
      }
    }

    return lines.length > 0 ? lines.join('\n') : null
  } catch {
    return null
  }
}


/**
 * 对选中文本执行操作（翻译/总结）
 * 单个英文单词优先使用免费词典 API
 */
export async function textAction(
  text: string,
  action: 'translate' | 'summarize'
): Promise<string> {
  if (action === 'translate' && isSingleWord(text)) {
    const dictResult = await lookupDictionary(text.trim())
    if (dictResult) return dictResult
  }

  const res = await post<TextActionResponse>('/analysis/text-action', { text, action })
  return res.result
}
