import { Alert, AlertDescription, AlertTitle, Button } from "@valuz/ui";
import type { SessionStreamSnapshot } from "@valuz/core";

interface RecoveryBannerProps {
  connection: SessionStreamSnapshot;
  onReconnect: () => void;
}

/**
 * Surfaces the manual recovery affordance when the auto-reconnect
 * controller has exhausted its backoff schedule. Hidden in every
 * state except ``error``.
 */
export const RecoveryBanner = ({
  connection,
  onReconnect,
}: RecoveryBannerProps) => {
  if (connection.state !== "error") return null;
  return (
    <Alert variant="destructive" className="my-3">
      <AlertTitle>Connection lost</AlertTitle>
      <AlertDescription className="flex items-center justify-between gap-4">
        <span className="text-sm opacity-90">
          {connection.errorMessage ??
            "Could not reach the agent runtime after several attempts."}
        </span>
        <Button size="sm" variant="outline" onClick={onReconnect}>
          Reconnect
        </Button>
      </AlertDescription>
    </Alert>
  );
};
