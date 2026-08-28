import { useI18n } from "../../hooks/use-i18n";

/**
 * Purely informational capability badge for model picker rows, driven by the
 * model's declared ``input_modalities``.
 *
 * Data-driven by design: it renders ONLY when a declaration is present and
 * includes ``"image"`` — a channel with no declarations (every OSS user
 * channel today; the field is not exposed in the channel editor) renders
 * nothing, so the OSS picker looks exactly like before. Declared
 * text-only models also render nothing: absence of the badge is deliberately
 * ambiguous (undeclared vs text-only) — the badge is a positive capability
 * hint, not a warning surface.
 */
export const ModelCapabilityBadge = ({
  modalities,
}: {
  modalities: string[] | null | undefined;
}) => {
  const { t } = useI18n();
  if (!modalities || !modalities.includes("image")) return null;
  return (
    <span className="shrink-0 rounded bg-surface-soft px-1 text-2xs text-ink-meta">
      {t("ui.modelBadge.image" as Parameters<typeof t>[0])}
    </span>
  );
};
