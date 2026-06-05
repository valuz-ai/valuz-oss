import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Composer } from './Composer'
import { ToolCallCard } from './ToolCallCard'

describe('prototype desktop components', () => {
  it('renders a tool call card with compact metadata', () => {
    render(
      <ToolCallCard
        tc={{
          id: 'tc-1',
          kind: 'kb',
          title: 'kb_search',
          subtitle: '检索季度财报',
          status: 'success',
          output: '命中 3 份文档',
        }}
      />,
    )

    expect(screen.getByRole('button', { name: /kb_search/i })).toBeTruthy()
    expect(screen.getByText('完成')).toBeTruthy()
  })

  it('renders the prototype composer prompt and send affordance', () => {
    render(<Composer />)

    expect(screen.getByPlaceholderText('输入消息... 输入 / 调用 Skill，@ 引用文档')).toBeTruthy()
    expect(screen.getByText('Enter 发送')).toBeTruthy()
  })
})
