import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import AIModelSection from '@/features/settings/AIModelSection'
import type { AIModelConfig } from '@/features/settings/config'

const OPENAI_CONFIG: AIModelConfig = {
  api_key: 'test-key',
  base_url: 'https://api.openai.com/v1',
  model: 'gpt-4o-mini',
  system_prompt: '',
  temperature: 0.1,
  max_tokens: 4096,
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
      base_url: 'https://api.anthropic.com',
      model: 'claude-sonnet-4-6',
    })
  })
})
