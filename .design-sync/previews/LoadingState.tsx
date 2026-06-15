import { LoadingState } from 'essay-read-frontend'

const paper = {
  title: 'Deep Residual Learning for Image Recognition',
  venue: 'CVPR',
  year: 2016,
  one_sentence: '提出残差学习框架，使极深网络的训练变得可行。',
  problem: '随着网络加深，准确率出现退化（degradation）现象。',
  key_contributions: ['残差连接 (skip connection)', '152 层网络在 ImageNet 上夺冠'],
}

// Mid-analysis: top progress bar, percentage, step indicators, and the
// per-field checklist lighting up as results stream in.
export const Analyzing = () => (
  <div style={{ height: 440, width: 440, border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden' }}>
    <LoadingState status="analyzing" message="正在解析方法论…" partialResult={paper} progress={48} />
  </div>
)

// Terminal failure state.
export const Failed = () => (
  <div style={{ height: 280, width: 440, border: '1px solid var(--border)', borderRadius: 12, overflow: 'hidden' }}>
    <LoadingState status="failed" message="模型调用超时" partialResult={{}} />
  </div>
)
