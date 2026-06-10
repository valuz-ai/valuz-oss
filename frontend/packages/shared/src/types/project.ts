export interface ProjectItem {
  id: string
  name: string
  kind: 'chat' | 'project'
  root_path: string | null
  icon: string | null
  instructions_md?: string | null
  memory_summary?: string | null
}
