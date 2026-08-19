/**
 * 把一段文本交给浏览器下载。
 *
 * 生成的 object URL 必须显式释放，否则每导出一次就漏一份内容在内存里。
 */
export function downloadText(filename: string, text: string, mime = 'text/plain'): void {
  const url = URL.createObjectURL(new Blob([text], { type: mime }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

/** 论文标题 → 可用作文件名的安全串（去掉路径分隔符等），空标题回落到 fallback。 */
export function safeFilename(title: string | null | undefined, fallback = 'paper'): string {
  const cleaned = (title || '')
    .replace(/[\\/:*?"<>|]/g, '')   // 文件系统保留字符
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 80)
  return cleaned || fallback
}
