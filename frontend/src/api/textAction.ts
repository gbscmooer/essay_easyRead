import { post } from './client'


interface TextActionResponse {
  result: string
}


/**
 * 对选中文本执行操作（翻译/总结）
 */
export async function textAction(
  text: string,
  action: 'translate' | 'summarize'
): Promise<string> {
  const res = await post<TextActionResponse>('/analysis/text-action', { text, action })
  return res.result
}
