export interface KnowledgeSource {
  id: string
  title: string
  kind: 'file' | 'note' | 'link'
  updatedAt: string
}
