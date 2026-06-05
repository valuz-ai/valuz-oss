import type { ServiceInfo } from "@valuz/shared";
import { formatStatusLabel } from "@valuz/shared";
import { Badge } from "../ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "../ui/card";

interface ServiceDashboardProps {
  services: ServiceInfo[];
}

export const ServiceDashboard = ({ services }: ServiceDashboardProps) => (
  <Card className="dark:border-white/10 dark:bg-white/5 border-surface-border bg-surface-soft">
    <CardHeader className="gap-2">
      <CardTitle className="text-base dark:text-white text-ink-heading">
        Service dashboard
      </CardTitle>
      <CardDescription className="dark:text-slate-300 text-ink-body">
        Desktop startup watches these services before revealing the main shell.
      </CardDescription>
    </CardHeader>
    <CardContent className="grid gap-3 md:grid-cols-2 xl:grid-cols-1">
      {services.map((service) => (
        <div
          key={service.name}
          className="rounded-2xl border dark:border-white/10 dark:bg-black/20 border-surface-border bg-surface-base backdrop-blur-sm"
        >
          <div className="flex items-start justify-between gap-4">
            <div className="space-y-1">
              <p className="font-medium dark:text-white text-ink-heading">
                {service.name}
              </p>
              <p className="text-sm dark:text-slate-300 text-ink-body">
                {service.port
                  ? `Port ${service.port}`
                  : (service.detail ?? "Pending")}
              </p>
            </div>
            <Badge
              variant={service.status === "running" ? "default" : "secondary"}
            >
              {formatStatusLabel(service.status)}
            </Badge>
          </div>
        </div>
      ))}
    </CardContent>
  </Card>
);
