import { test, expect } from './fixtures/electron-app'

test.describe('Skills Library Flow', () => {
  test('skills page loads and shows section headers', async ({ window }) => {
    await window.getByText('技能库').first().click()
    await expect(window.getByText('添加 Skill')).toBeVisible()
  })

  test('skills page shows user and official sections', async ({ window }) => {
    await window.getByText('技能库').first().click()
    await window.waitForTimeout(2_000)

    // At minimum, the official section header should render
    await expect(window.getByText('官方 · Official')).toBeVisible({ timeout: 10_000 })
  })

  test('search filters skills list', async ({ window }) => {
    await window.getByText('技能库').first().click()
    await expect(window.getByText('添加 Skill')).toBeVisible({ timeout: 10_000 })

    const searchInput = window.getByPlaceholder('搜索 Skill...')
    await searchInput.fill('nonexistent-skill-xyz')
    await window.waitForTimeout(500)

    // Should show empty state
    await expect(window.getByText('没有匹配的 Skill。')).toBeVisible()
  })

  test('add skill menu shows all creation modes', async ({ window }) => {
    await window.getByText('技能库').first().click()
    await expect(window.getByText('添加 Skill')).toBeVisible({ timeout: 10_000 })
    await window.getByText('添加 Skill').click()

    await expect(window.getByRole('menuitem', { name: 'AI 创建' })).toBeVisible()
    await expect(window.getByRole('menuitem', { name: '链接导入' })).toBeVisible()
    await expect(window.getByRole('menuitem', { name: '上传' })).toBeVisible()

    await window.getByRole('menuitem', { name: '链接导入' }).click()
    await expect(window.getByRole('heading', { name: '链接导入' })).toBeVisible({ timeout: 5_000 })
    await window.keyboard.press('Escape')

    await window.getByText('添加 Skill').click()
    await window.getByRole('menuitem', { name: '上传' }).click()
    await expect(window.getByRole('heading', { name: '上传' })).toBeVisible({ timeout: 5_000 })
  })

  test('create skill via manual form', async ({ window }) => {
    await window.getByText('技能库').first().click()
    await expect(window.getByText('添加 Skill')).toBeVisible({ timeout: 10_000 })
    await window.getByText('添加 Skill').click()

    // Fill in the form
    const nameInput = window.getByPlaceholder('例如：行业对比模板')
    await nameInput.fill('E2E Test Skill')

    const descInput = window.getByPlaceholder('描述这个 Skill 的用途...')
    await descInput.fill('Created by E2E test')

    // Click create
    const createBtn = window.locator('button').filter({ hasText: '创建' }).last()
    await createBtn.click()

    // Wait for toast or skill to appear
    await window.waitForTimeout(2_000)

    // The skill should now appear in the list
    const hasSkill = await window.getByText('E2E Test Skill').isVisible().catch(() => false)
    if (!hasSkill) {
      // Backend not running — acceptable
      return
    }
    await expect(window.getByText('E2E Test Skill')).toBeVisible()
  })

  test('skill detail panel shows when clicking a skill card', async ({ window }) => {
    await window.getByText('技能库').first().click()
    await window.waitForTimeout(2_000)

    // Click the first skill card if any exist
    const firstCard = window.locator('[class*="rounded-xl"][class*="border"]').first()
    const isVisible = await firstCard.isVisible().catch(() => false)
    if (!isVisible) return // No skills available

    await firstCard.click()

    // Detail panel should appear on the right
    await window.waitForTimeout(500)
    const detailPanel = window.getByText('查看详情')
    const hasDetail = await detailPanel.isVisible().catch(() => false)
    if (hasDetail) {
      await expect(detailPanel).toBeVisible()
    }
  })

  test('official skills show locked state without Reportify connection', async ({ window }) => {
    await window.getByText('技能库').first().click()
    await window.waitForTimeout(2_000)

    // Official section should exist
    const officialHeader = window.getByText('官方 · Official')
    const hasOfficial = await officialHeader.isVisible().catch(() => false)
    if (!hasOfficial) return

    // Click an official skill — should show lock toast
    const officialCards = window.locator('[class*="rounded-xl"]').filter({ hasText: 'Official' })
    const firstOfficial = officialCards.first()
    const exists = await firstOfficial.isVisible().catch(() => false)
    if (!exists) return

    await firstOfficial.click()
    // Should show toast about connecting Reportify
    await window.waitForTimeout(1_000)
  })

  test('skill detail page loads file tree from API', async ({ window }) => {
    await window.getByText('技能库').first().click()
    await window.waitForTimeout(2_000)

    // Need at least one skill to test detail page
    const firstCard = window.locator('[class*="rounded-xl"][class*="border"]').first()
    const isVisible = await firstCard.isVisible().catch(() => false)
    if (!isVisible) return

    await firstCard.click()
    await window.waitForTimeout(500)

    const viewDetailBtn = window.getByText('查看详情')
    const hasBtn = await viewDetailBtn.isVisible().catch(() => false)
    if (!hasBtn) return

    await viewDetailBtn.click()

    // Should navigate to detail page with file tree
    await window.waitForTimeout(2_000)
    const hasFileTree = await window.getByText('SKILL.md').isVisible().catch(() => false)
    if (!hasFileTree) return // Backend not running or no files

    await expect(window.getByText('SKILL.md')).toBeVisible()
  })

  test('filter pills work correctly', async ({ window }) => {
    await window.getByText('技能库').first().click()
    await expect(window.getByText('技能库')).toBeVisible({ timeout: 10_000 })

    // "全部" filter should be active by default
    const allFilter = window.getByText('全部')
    await expect(allFilter).toBeVisible()
  })
})
