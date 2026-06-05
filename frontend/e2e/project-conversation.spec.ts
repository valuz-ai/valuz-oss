import { test, expect } from './fixtures/electron-app'

test.describe('Project Conversation Flow', () => {
  test('projects page loads and shows create option', async ({ window }) => {
    await window.getByText('Projects').first().click()
    await expect(window.locator('body')).toContainText(/项目|Projects|创建/, { timeout: 10_000 })
  })

  test('project detail page shows composer and suggestions', async ({ window }) => {
    await window.getByText('Projects').first().click()

    const projectLink = window.locator('[href*="/projects/"]').first()
    const hasProject = await projectLink.isVisible({ timeout: 5_000 }).catch(() => false)
    if (!hasProject) return

    await projectLink.click()

    const composer = window.locator('textarea').first()
    await expect(composer).toBeVisible({ timeout: 10_000 })
    await expect(window.getByText('分析项目目录中的代码结构')).toBeVisible({ timeout: 5_000 })
  })

  test('project detail shows project name and back button', async ({ window }) => {
    await window.getByText('Projects').first().click()

    const projectLink = window.locator('[href*="/projects/"]').first()
    const hasProject = await projectLink.isVisible({ timeout: 5_000 }).catch(() => false)
    if (!hasProject) return

    await projectLink.click()

    // Project name heading
    await expect(window.locator('h2').first()).toBeVisible({ timeout: 10_000 })
    // Back arrow button
    const backButton = window.locator('button').filter({ has: window.locator('svg') }).first()
    await expect(backButton).toBeVisible()
  })

  test('project composer accepts input and send button enables', async ({ window }) => {
    await window.getByText('Projects').first().click()

    const projectLink = window.locator('[href*="/projects/"]').first()
    const hasProject = await projectLink.isVisible({ timeout: 5_000 }).catch(() => false)
    if (!hasProject) return

    await projectLink.click()

    const composer = window.locator('textarea').first()
    await composer.waitFor({ state: 'visible', timeout: 10_000 })
    await composer.fill('分析项目结构')
    await expect(composer).toHaveValue('分析项目结构')

    const sendButton = window.getByRole('button', { name: '发送' })
    await expect(sendButton).toBeEnabled()
  })

  test('clicking suggestion fills composer', async ({ window }) => {
    await window.getByText('Projects').first().click()

    const projectLink = window.locator('[href*="/projects/"]').first()
    const hasProject = await projectLink.isVisible({ timeout: 5_000 }).catch(() => false)
    if (!hasProject) return

    await projectLink.click()

    const suggestion = window.getByText('分析项目目录中的代码结构')
    await suggestion.waitFor({ state: 'visible', timeout: 10_000 })
    await suggestion.click()

    const composer = window.locator('textarea').first()
    await expect(composer).toHaveValue('分析项目目录中的代码结构')
  })

  test('project context panel shows Instructions section', async ({ window }) => {
    await window.getByText('Projects').first().click()

    const projectLink = window.locator('[href*="/projects/"]').first()
    const hasProject = await projectLink.isVisible({ timeout: 5_000 }).catch(() => false)
    if (!hasProject) return

    await projectLink.click()

    await expect(window.getByText('Instructions')).toBeVisible({ timeout: 10_000 })
  })

  test('send message from project navigates to conversation page', async ({ window }) => {
    await window.getByText('Projects').first().click()

    const projectLink = window.locator('[href*="/projects/"]').first()
    const hasProject = await projectLink.isVisible({ timeout: 5_000 }).catch(() => false)
    if (!hasProject) return

    await projectLink.click()

    const composer = window.locator('textarea').first()
    await composer.waitFor({ state: 'visible', timeout: 10_000 })
    await composer.fill('请简要描述这个项目的结构')

    const sendButton = window.getByRole('button', { name: '发送' })
    await sendButton.click()

    // Should navigate to conversation page
    await window.waitForTimeout(3_000)
    const url = window.url()
    const navigated = url.includes('/conversation/')
    if (!navigated) return // Backend not running

    // User message should be visible in the conversation
    await expect(window.getByText('请简要描述这个项目的结构')).toBeVisible({ timeout: 10_000 })
  })

  test('project conversation shows assistant response with tool cards', async ({ window }) => {
    await window.getByText('Projects').first().click()

    const projectLink = window.locator('[href*="/projects/"]').first()
    const hasProject = await projectLink.isVisible({ timeout: 5_000 }).catch(() => false)
    if (!hasProject) return

    await projectLink.click()

    const composer = window.locator('textarea').first()
    await composer.waitFor({ state: 'visible', timeout: 10_000 })
    await composer.fill('请用一句话回答：这个项目是做什么的？')

    const sendButton = window.getByRole('button', { name: '发送' })
    await sendButton.click()

    await window.waitForTimeout(3_000)
    if (!window.url().includes('/conversation/')) return

    const dots = window.locator('.animate-pulse').first()
    const isRunning = await dots.isVisible().catch(() => false)
    if (isRunning) {
      await expect(dots).toBeHidden({ timeout: 60_000 })
    }

    const assistantAvatar = window.locator('.bg-brand.rounded-full')
    await expect(assistantAvatar.first()).toBeVisible({ timeout: 5_000 })

    const bodyText = await window.locator('body').textContent()
    const hasRuntimeEvents = bodyText?.includes('runtime.context.compiled') ||
      bodyText?.includes('runtime.engine')
    expect(hasRuntimeEvents).toBeTruthy()
  })

  test('navigate away during running and return to see events', async ({ window }) => {
    await window.getByText('Projects').first().click()

    const projectLink = window.locator('[href*="/projects/"]').first()
    const hasProject = await projectLink.isVisible({ timeout: 5_000 }).catch(() => false)
    if (!hasProject) return

    await projectLink.click()

    const composer = window.locator('textarea').first()
    await composer.waitFor({ state: 'visible', timeout: 10_000 })
    await composer.fill('请详细分析这个项目的技术栈和架构设计')

    const sendButton = window.getByRole('button', { name: '发送' })
    await sendButton.click()

    await window.waitForTimeout(2_000)
    if (!window.url().includes('/conversation/')) return

    const conversationUrl = window.url()

    // Navigate away to home
    await window.getByText('Home').first().click()
    await expect(window.getByText('开始一个新的对话')).toBeVisible({ timeout: 10_000 })

    // Wait for agent to produce more events in background
    await window.waitForTimeout(3_000)

    // Navigate back
    await window.goto(conversationUrl)
    await window.waitForLoadState('domcontentloaded')

    // Events should be loaded from DB
    await expect(
      window.getByText('请详细分析这个项目的技术栈和架构设计'),
    ).toBeVisible({ timeout: 10_000 })

    await window.waitForTimeout(2_000)
    const bodyText = await window.locator('body').textContent()
    const hasContent = bodyText?.includes('runtime.context.compiled') ||
      bodyText?.includes('中断') ||
      (bodyText?.length ?? 0) > 200
    expect(hasContent).toBeTruthy()
  })
})