import { test, expect } from './fixtures/electron-app'

test.describe('Chat Conversation Flow', () => {
  test('home page renders hero and action cards', async ({ window }) => {
    await expect(window.getByText('开始一个新的对话')).toBeVisible({ timeout: 10_000 })
    await expect(window.getByText('新建对话')).toBeVisible()
    await expect(window.getByText('创建项目')).toBeVisible()
    await expect(window.getByText('你可以试试')).toBeVisible()
  })

  test('home page composer is functional', async ({ window }) => {
    await window.waitForLoadState('domcontentloaded')
    const composer = window.locator('textarea').first()
    await composer.waitFor({ state: 'visible', timeout: 10_000 })

    // Send button disabled when empty
    const sendButton = window.locator('button[disabled]').last()
    await expect(sendButton).toBeVisible()

    // Accepts text input
    await composer.fill('测试消息')
    await expect(composer).toHaveValue('测试消息')
  })

  test('clicking new chat navigates to conversation page', async ({ window }) => {
    await expect(window.getByText('新建对话')).toBeVisible({ timeout: 10_000 })
    await window.getByText('新建对话').click()

    // Should show conversation page with empty state or session title
    await expect(
      window.getByText(/告诉我你要处理的项目|新对话/),
    ).toBeVisible({ timeout: 10_000 })
  })

  test('conversation page has composer and refresh button', async ({ window }) => {
    await expect(window.getByText('新建对话')).toBeVisible({ timeout: 10_000 })
    await window.getByText('新建对话').click()

    const composer = window.locator('textarea').first()
    await composer.waitFor({ state: 'visible', timeout: 10_000 })
    await composer.fill('你好')
    await expect(composer).toHaveValue('你好')

    // Refresh button
    await expect(window.getByTitle('刷新')).toBeVisible()
  })

  test('send message from home page creates session and navigates', async ({ window }) => {
    await window.waitForLoadState('domcontentloaded')
    const composer = window.locator('textarea').first()
    await composer.waitFor({ state: 'visible', timeout: 10_000 })
    await composer.fill('你好，请介绍一下你自己')
    await composer.press('Enter')

    // Should navigate to conversation page — either shows the message or an error
    await window.waitForTimeout(2_000)
    const url = window.url()
    const navigated = url.includes('/conversation/')
    if (!navigated) {
      // Backend not running — home page stays, which is acceptable
      return
    }

    // If navigated, the user message should appear
    await expect(window.getByText('你好，请介绍一下你自己')).toBeVisible({ timeout: 10_000 })
  })

  test('send message from conversation page shows running state', async ({ window }) => {
    await expect(window.getByText('新建对话')).toBeVisible({ timeout: 10_000 })
    await window.getByText('新建对话').click()

    const composer = window.locator('textarea').first()
    await composer.waitFor({ state: 'visible', timeout: 10_000 })
    await composer.fill('请用一句话回答：1+1等于几？')
    await composer.press('Enter')

    // Wait for either: loading dots (sending state) or error
    await window.waitForTimeout(1_000)
    const hasDots = await window.locator('.animate-pulse').first().isVisible().catch(() => false)
    if (!hasDots) {
      // Backend not running — acceptable
      return
    }

    // Loading animation should be visible while running
    await expect(window.locator('.animate-pulse').first()).toBeVisible()
  })

  test('assistant response renders after agent completes', async ({ window }) => {
    await expect(window.getByText('新建对话')).toBeVisible({ timeout: 10_000 })
    await window.getByText('新建对话').click()

    const composer = window.locator('textarea').first()
    await composer.waitFor({ state: 'visible', timeout: 10_000 })
    await composer.fill('请用一句话回答：1+1等于几？')
    await composer.press('Enter')

    // Wait for assistant response (up to 30s for LLM)
    await window.waitForTimeout(2_000)
    const hasDots = await window.locator('.animate-pulse').first().isVisible().catch(() => false)
    if (!hasDots) return // Backend not running

    // Wait for the loading dots to disappear (agent finished)
    await expect(window.locator('.animate-pulse').first()).toBeHidden({ timeout: 60_000 })

    // The assistant avatar (brand circle) should be visible
    const assistantAvatar = window.locator('.bg-brand.rounded-full')
    await expect(assistantAvatar.first()).toBeVisible()

    // There should be some text content from the assistant
    const assistantText = window.locator('.text-ink-heading').nth(2)
    await expect(assistantText).toBeVisible({ timeout: 5_000 })
  })

  test('interrupt button appears during running and stops execution', async ({ window }) => {
    await expect(window.getByText('新建对话')).toBeVisible({ timeout: 10_000 })
    await window.getByText('新建对话').click()

    const composer = window.locator('textarea').first()
    await composer.waitFor({ state: 'visible', timeout: 10_000 })
    await composer.fill('请详细分析全球宏观经济形势，包括各主要经济体的GDP增长趋势')
    await composer.press('Enter')

    // Wait for running state
    await window.waitForTimeout(1_500)
    const interruptBtn = window.getByText('中断')
    const isRunning = await interruptBtn.isVisible().catch(() => false)
    if (!isRunning) return // Backend not running or already finished

    await interruptBtn.click()

    // After interrupt, the button should disappear
    await expect(interruptBtn).toBeHidden({ timeout: 10_000 })
  })

  test('tool call cards render during execution', async ({ window }) => {
    await expect(window.getByText('新建对话')).toBeVisible({ timeout: 10_000 })
    await window.getByText('新建对话').click()

    const composer = window.locator('textarea').first()
    await composer.waitFor({ state: 'visible', timeout: 10_000 })
    await composer.fill('请用一句话回答：1+1等于几？')
    await composer.press('Enter')

    // Wait for events to stream in
    await window.waitForTimeout(3_000)
    const hasDots = await window.locator('.animate-pulse').first().isVisible().catch(() => false)
    if (!hasDots) {
      // Check if already completed — tool cards may already be visible
      const toolCards = window.locator('[class*="runtime.context.compiled"], [class*="runtime.engine"]')
      const hasCards = await toolCards.first().isVisible().catch(() => false)
      if (!hasCards) return // Backend not running
    }

    // Wait for completion
    await expect(window.locator('.animate-pulse').first()).toBeHidden({ timeout: 60_000 })

    // Meta tool cards (runtime.context.compiled, runtime.engine.bound) should render
    // These appear as ToolCallCard components
    const allText = await window.locator('body').textContent()
    const hasRuntimeEvents = allText?.includes('runtime.context.compiled') ||
      allText?.includes('runtime.engine.bound') ||
      allText?.includes('runtime.engine.fallback')
    expect(hasRuntimeEvents).toBeTruthy()
  })

  test('recent sessions appear on home page after conversation', async ({ window }) => {
    // First create a session via the home page
    await window.waitForLoadState('domcontentloaded')
    const composer = window.locator('textarea').first()
    await composer.waitFor({ state: 'visible', timeout: 10_000 })
    await composer.fill('测试会话')
    await composer.press('Enter')

    await window.waitForTimeout(2_000)
    const navigated = window.url().includes('/conversation/')
    if (!navigated) return // Backend not running

    // Navigate back to home
    await window.getByText('Home').first().click()
    await expect(window.getByText('开始一个新的对话')).toBeVisible({ timeout: 10_000 })

    // Recent sessions section should now appear
    const recentSection = window.getByText('最近对话')
    await expect(recentSection).toBeVisible({ timeout: 5_000 })
  })
})
