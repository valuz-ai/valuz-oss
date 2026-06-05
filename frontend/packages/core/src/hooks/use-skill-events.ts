import { useEffect, useRef } from 'react'
import { skillsApi } from '../api/skills-api'

export function useSkillEvents(onSkillChanged?: () => void) {
  const callbackRef = useRef(onSkillChanged)
  callbackRef.current = onSkillChanged

  useEffect(() => {
    const url = skillsApi.eventsStreamUrl()
    const source = new EventSource(url)

    source.addEventListener('skill.changed', () => {
      callbackRef.current?.()
    })

    source.addEventListener('workspace.skills_changed', () => {
      callbackRef.current?.()
    })

    return () => source.close()
  }, [])
}
