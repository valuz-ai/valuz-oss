/**
 * Decide whether a session create may override the bound agent's brain.
 *
 * The backend derives runtime / model / provider / effort from ``agent_slug``
 * (``_create_agent_bound_session``), and treats an explicit ``provider_id`` /
 * ``model_id`` / ``runtime_id`` / ``effort`` in the same create as a
 * PER-SESSION OVERRIDE that beats those defaults and is then frozen for the
 * session's lifetime (ADR-006). So the fields are not "extra context" the
 * client may always send — sending one asserts "the user picked this for this
 * conversation", and asserting it wrongly silently runs the agent on the wrong
 * channel.
 *
 * The composer's provider/model state is NOT that assertion by itself. It is
 * seeded from Settings → Default and from the project's last-used session, and
 * only converges on the bound agent's own brain once the member roster has
 * loaded and the ``selectedAgentBrain`` effect has run. Two ways that lost:
 *
 * 1. **The project-detail handoff.** That composer has no model picker at all,
 *    so its provider/model only ever held the project's last-used channel. It
 *    was forwarded through the send handoff and overrode every agent.
 * 2. **The roster race.** The handoff's send gate waits for the project
 *    binding, not for the member fetch that feeds ``selectedAgentBrain``. A
 *    create that won that race froze this page's own default channel instead
 *    of the agent's model.
 *
 * Hence: with an agent bound, only an explicit user pick (``composerTouched``)
 * travels. Agentless chats have no brain to inherit, so they always send.
 *
 * Extracted so the rule is testable — ConversationPage itself has no harness,
 * which is why this class of bug keeps reaching QA (see
 * ``conversation-project-handoff.ts`` for the same reasoning).
 */
export interface BrainOverrideInput<Runtime extends string, Effort> {
  /** The agent this session will bind to; null for an agentless chat. */
  agentSlug: string | null;
  /** True once the user changed a composer pick themselves. */
  composerTouched: boolean;
  providerId: string | null;
  modelId: string | null;
  runtimeId: Runtime | null;
  effort: Effort;
}

export interface BrainOverride<Runtime extends string, Effort> {
  provider_id?: string;
  model_id?: string;
  runtime_id?: Runtime;
  effort?: Effort;
}

export function resolveBrainOverride<Runtime extends string, Effort>(
  input: BrainOverrideInput<Runtime, Effort>,
): BrainOverride<Runtime, Effort> {
  const { agentSlug, composerTouched, providerId, modelId, runtimeId, effort } =
    input;
  // A bound agent owns the brain until the user says otherwise.
  if (agentSlug && !composerTouched) return {};
  return {
    provider_id: providerId ?? undefined,
    model_id: modelId ?? undefined,
    // Runtime rides with a complete (provider, model) pick only: on its own it
    // would re-point the session at a runtime the picked channel may not
    // serve.
    runtime_id: providerId && modelId ? (runtimeId ?? undefined) : undefined,
    effort,
  };
}
