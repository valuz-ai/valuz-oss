import { Badge, Spinner } from "@valuz/ui";
import { selectConnectionLabel, type SessionStreamSnapshot } from "@valuz/core";
import { useEffect, useState } from "react";

interface ConnectionStatusBadgeProps {
  connection: SessionStreamSnapshot;
}

export const ConnectionStatusBadge = ({
  connection,
}: ConnectionStatusBadgeProps) => {
  const label = selectConnectionLabel(connection.state);

  // Live countdown for the reconnecting state so the user has visible
  // proof the client is still trying.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (connection.state !== "reconnecting" || connection.nextRetryAt == null) {
      return;
    }
    const t = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(t);
  }, [connection.state, connection.nextRetryAt]);

  const variant: "default" | "secondary" | "destructive" | "outline" =
    connection.state === "connected"
      ? "default"
      : connection.state === "error"
        ? "destructive"
        : connection.state === "reconnecting" ||
            connection.state === "connecting"
          ? "secondary"
          : "outline";

  return (
    <Badge
      variant={variant}
      className="gap-1.5 px-2.5 py-1 text-[11px] font-medium"
      data-testid="connection-status"
    >
      {(connection.state === "connecting" ||
        connection.state === "reconnecting") && <Spinner className="size-3" />}
      {connection.state === "connected" && (
        <span className="size-1.5 rounded-full bg-emerald-500" />
      )}
      <span>{label}</span>
      {connection.state === "reconnecting" &&
        connection.nextRetryAt != null && (
          <span className="tabular-nums opacity-70">
            {Math.max(0, Math.ceil((connection.nextRetryAt - now) / 1000))}s
          </span>
        )}
      {connection.attempt > 0 && connection.state === "reconnecting" && (
        <span className="opacity-70">· try {connection.attempt}</span>
      )}
    </Badge>
  );
};
