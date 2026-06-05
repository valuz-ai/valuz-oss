import type { PropsWithChildren, ReactNode } from 'react'
import { Badge } from './ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './ui/card'

interface SectionCardProps extends PropsWithChildren {
  eyebrow?: string
  title: string
  description: string
  accent?: ReactNode
}

export const SectionCard = ({ accent, children, description, eyebrow, title }: SectionCardProps) => (
  <Card>
    <CardHeader className="gap-4 sm:flex-row sm:items-start sm:justify-between">
      <div className="space-y-2">
        {eyebrow ? <Badge variant="outline">{eyebrow}</Badge> : null}
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </div>
      {accent ? <div className="shrink-0">{accent}</div> : null}
    </CardHeader>
    <CardContent>{children}</CardContent>
  </Card>
)
