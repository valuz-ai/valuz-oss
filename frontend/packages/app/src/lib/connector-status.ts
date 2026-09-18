/**
 * One connector row → the pill the user reads.
 *
 * Three surfaces render this pill (the connectors pane, the connectors page,
 * and an agent's connector list) and each used to carry its own copy of the
 * status→key map. They agreed by hand until they didn't: every copy carried the
 * comment *"disabled stays unlabeled (the user turned it off on purpose)"*,
 * but `disabled` is not a value `status` can take — off-ness lives in the
 * separate `enabled` boolean, which none of the three ever read. The tone
 * palette had `disabled: "neutral"` waiting the whole time.
 *
 * What that cost: a connector the owner had switched off still showed whatever
 * verdict the last probe left behind. A row reading 连接失败 was therefore
 * ambiguous between "this is broken right now" and "this is off, and the last
 * time anyone looked — possibly weeks ago — it failed". Only the second was
 * ever true for a disabled row, because nothing probes a disabled connector.
 *
 * Off-ness wins over status on purpose: it is the state the owner chose, it is
 * current by definition, and it is the reason no fresher verdict exists.
 */

import type { I18nKey } from "@valuz/shared";

export interface ConnectorStatusView {
  /** Feeds the status-tone palette (`disabled` → neutral). */
  status: string;
  /** Caller owns the `t()` call, as the pill component expects. */
  labelKey: I18nKey;
}

/**
 * The two "configured but not connected" states (`pending_auth` / `unknown`)
 * deliberately share one label: the difference between them is a detail of how
 * far setup got, not something a one-word pill can carry.
 */
const STATUS_LABEL_KEY: Record<string, I18nKey> = {
  connected: "connector.statusConnected",
  connecting: "connector.statusConnecting",
  error: "connector.statusError",
  pending_auth: "connector.statusNotConnected",
  unknown: "connector.statusNotConnected",
};

/**
 * `null` when there is nothing worth saying — an unrecognized status renders no
 * pill at all rather than a raw backend word.
 */
export function connectorStatusView(connector: {
  status?: string | null;
  enabled?: boolean | null;
}): ConnectorStatusView | null {
  if (connector.enabled === false) {
    return { status: "disabled", labelKey: "connector.statusDisabled" };
  }
  const status = String(connector.status ?? "");
  const labelKey = STATUS_LABEL_KEY[status];
  return labelKey ? { status, labelKey } : null;
}
