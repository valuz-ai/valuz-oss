/**
 * Read-only detail for an agent that lives on another machine.
 *
 * The library lists agents from every machine you may run on, but their
 * instructions, skills and connectors live over there — the local backend has
 * never heard of the slug, so the editable detail view cannot load it. This
 * renders what the list row already carries, and says where it runs.
 *
 * Only the (presentational) title-badge slot is rendered here: the right panel
 * is mounted through ``setRightPanel``, i.e. OUTSIDE the project outlet, and
 * edition action slots read that context.
 */

import { Bot } from "lucide-react";
import { useTranslation, type Agent } from "@valuz/core";
import { modelLabel } from "@valuz/shared";

import { SlotRenderer } from "@valuz/core";

import {
  RemoteAgentDetailActionSlot,
  ResourceTitleBadgeSlot,
} from "./ResourceActionSlot";

type TK = Parameters<ReturnType<typeof useTranslation>["t"]>[0];

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-surface-border py-2 last:border-b-0">
      <span className="text-xs text-ink-meta">{label}</span>
      <span className="truncate text-sm text-ink-body">{value}</span>
    </div>
  );
}

export function RemoteAgentDetail({ agent }: { agent: Agent }) {
  const { t } = useTranslation();
  const resource = agent as unknown as Record<string, unknown>;
  return (
    <div className="flex flex-col gap-4 p-5">
      {/* Same identity block as a local agent: name + tag, then the plain
          ``来源 · 模型 · 推理强度`` subtitle. Only the actions are missing —
          none of them apply to a machine you are only reading from. */}
      <div className="flex items-start gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-surface-soft text-ink-body">
          <Bot className="h-5 w-5" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex max-w-full items-center gap-2 truncate px-1 text-base font-medium text-ink-heading">
            {agent.name}
            <ResourceTitleBadgeSlot resourceType="agent" resource={resource} />
          </div>
          <div className="mt-0.5 truncate px-1 text-xs text-ink-body">
            {[
              t("agent.groupCustom" as TK),
              modelLabel(agent.model),
              agent.effort ?? "—",
            ].join(" · ")}
          </div>
        </div>
        <RemoteAgentDetailActionSlot resource={resource} />
      </div>

      {agent.description ? (
        <p className="px-1 text-xs leading-relaxed text-ink-meta">
          {agent.description}
        </p>
      ) : null}

      <p className="rounded-md bg-surface-soft px-3 py-2 text-xs leading-relaxed text-ink-meta">
        {t("agent.remoteDetailNote" as TK)}
      </p>

      {/* Full-bleed: the rule marks where the header ends, so it runs the
          width of the panel rather than the width of the text. */}
      <div className="-mx-5 border-b border-surface-border" />

      {/* Editions add what only they can know about an agent on another
          machine — the conversations you have had with it, say. */}
      <SlotRenderer
        name="resource.agent.remote.sections"
        context={{ resourceType: "agent", resource }}
      />

      <div className="flex flex-col">
        {agent.skills?.length ? (
          <Fact
            label={t("agent.skillsLabel" as TK)}
            value={String(agent.skills.length)}
          />
        ) : null}
        {agent.connector_types?.length ? (
          <Fact
            label={t("agent.connectorsLabel" as TK)}
            value={String(agent.connector_types.length)}
          />
        ) : null}
      </div>
    </div>
  );
}
