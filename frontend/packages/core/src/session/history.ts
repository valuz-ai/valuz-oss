import type { SessionListItem } from "@valuz/shared";

export const sortSessionsByUpdatedAt = (sessions: SessionListItem[]) =>
  [...sessions].sort((left, right) => right.updated_at - left.updated_at);
