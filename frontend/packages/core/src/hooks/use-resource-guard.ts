export function useResourceGuard(_resource: {
  source?: string;
  readonly?: boolean;
  deletable?: boolean;
}) {
  // All agents — official or custom — are equally editable and deletable now.
  // Kept as a hook so call sites don't need to change.
  return {
    canEdit: true,
    canDelete: true,
  };
}
