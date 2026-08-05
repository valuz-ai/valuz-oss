import { z } from "zod/v4";

/**
 * Loose on purpose. Model output names these fields inconsistently — `label`
 * or `title`, `value` or `text` — and a strict object would strip the aliases
 * while parsing, leaving the component with nothing to render. The component
 * reads through `readTextFromKeys`, so the schema's job here is to document
 * the canonical names, not to police them.
 */
export const MetricSchema = z.looseObject({
  label: z.string().optional(),
  value: z.string().optional(),
});
