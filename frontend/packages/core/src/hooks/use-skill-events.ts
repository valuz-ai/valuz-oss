import { useEffect, useRef } from "react";
import { fetchEventSource } from "../api/fetch-event-source";
import { skillsApi } from "../api/skills-api";

export function useSkillEvents(onSkillChanged?: () => void) {
  const callbackRef = useRef(onSkillChanged);
  callbackRef.current = onSkillChanged;

  useEffect(() => {
    // fetch-based SSE (not EventSource) so the request carries auth headers.
    return fetchEventSource(
      () => skillsApi.eventsStreamUrl(),
      (frame) => {
        if (
          frame.event === "skill.changed" ||
          frame.event === "project.skills_changed"
        ) {
          callbackRef.current?.();
        }
      },
    );
  }, []);
}
