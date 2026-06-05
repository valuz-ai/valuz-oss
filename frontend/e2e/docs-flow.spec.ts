import { test, expect } from './fixtures/electron-app'

test.describe('Document Library Flow', () => {
  test('knowledge page loads with empty state', async ({ window }) => {
    await window.getByText('Knowledge').first().click()
    await expect(window.getByText('0 docs')).toBeVisible()
    await expect(window.getByText('No documents yet')).toBeVisible()
  })

  test('upload a text file and verify it appears in the list', async ({ window }) => {
    await window.getByText('Knowledge').first().click()

    const uploadButton = window.getByText('Upload')
    await uploadButton.first().click()

    const fileInput = window.locator('input[type="file"]')
    await fileInput.setInputFiles('./e2e/fixtures/test-doc.txt')

    await expect(window.getByText('test-doc.txt')).toBeVisible({ timeout: 10_000 })
    await expect(window.getByText('1 docs')).toBeVisible()
  })

  test('search filters the document list', async ({ window }) => {
    await window.getByText('Knowledge').first().click()
    await expect(window.getByText('test-doc.txt')).toBeVisible({ timeout: 5_000 })

    const searchInput = window.getByPlaceholder('Search documents...')
    await searchInput.fill('nonexistent')
    await expect(window.getByText('No documents yet')).toBeVisible()

    await searchInput.fill('test-doc')
    await expect(window.getByText('test-doc.txt')).toBeVisible()
  })

  test('select document and view preview in detail panel', async ({ window }) => {
    await window.getByText('Knowledge').first().click()
    await expect(window.getByText('test-doc.txt')).toBeVisible({ timeout: 5_000 })

    await window.getByText('test-doc.txt').click()
    await expect(window.getByText('sample text')).toBeVisible({ timeout: 5_000 })
  })

  test('delete a document', async ({ window }) => {
    await window.getByText('Knowledge').first().click()
    await expect(window.getByText('test-doc.txt')).toBeVisible({ timeout: 5_000 })

    await window.getByText('test-doc.txt').click()
    await window.getByText('Delete').first().click()

    const confirmButton = window.getByRole('button', { name: 'Delete' }).last()
    await confirmButton.click()

    await expect(window.getByText('Document deleted')).toBeVisible({ timeout: 5_000 })
    await expect(window.getByText('0 docs')).toBeVisible()
  })
})
