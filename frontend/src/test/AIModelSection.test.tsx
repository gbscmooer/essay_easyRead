import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import AIModelSection from '@/features/settings/AIModelSection'
import type { AIModelConfig } from '@/features/settings/config'

const OPENAI_CONFIG: AIModelConfig = {
  provider: 'openai',
  api_format: 'openai_chat',
  api_key: 'test-key',
  base_url: 'https://api.openai.com/v1',
  model: 'gpt-4o-mini',
  temperature: 0.1,
  max_tokens: 4096,
  thinking_effort: 'medium',
  timeout_seconds: 150,
}

describe('AIModelSection', () => {
  it('switches both endpoint and model when selecting another protocol preset', async () => {
    const onChange = vi.fn()
    const user = userEvent.setup()

    render(
      <AIModelSection
        title="解读模型"
        description="测试配置"
        config={OPENAI_CONFIG}
        onChange={onChange}
        defaultMaxTokens={4096}
      />,
    )

    await user.click(screen.getByRole('button', { name: 'Anthropic' }))

    expect(onChange).toHaveBeenCalledWith({
      ...OPENAI_CONFIG,
      provider: 'anthropic',
      api_format: 'anthropic_messages',
      base_url: 'https://api.anthropic.com',
      model: 'claude-sonnet-4-6',
    })
  })

  it('changes thinking effort from advanced params (default medium)', async () => {
    const onChange = vi.fn()
    const user = userEvent.setup()

    render(
      <AIModelSection
        title="解读模型"
        description="测试配置"
        config={OPENAI_CONFIG}
        onChange={onChange}
        defaultMaxTokens={4096}
      />,
    )

    expect(OPENAI_CONFIG.thinking_effort).toBe('medium')

    await user.click(screen.getByRole('button', { name: '高级参数' }))
    const group = screen.getByRole('radiogroup', { name: '思考强度' })
    expect(group).toBeDefined()
    expect(screen.getByRole('radio', { name: '中' })).toBeChecked()

    await user.click(screen.getByRole('radio', { name: '高' }))

    expect(onChange).toHaveBeenCalledWith({
      ...OPENAI_CONFIG,
      thinking_effort: 'high',
    })
  })
})
