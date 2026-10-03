import { definePlugin } from "@valuz/core";

import { TaskDetailPage } from "../pages";
import { pageRoute } from "./define";
import { routePlacement } from "./layout";

/** Lead-dispatch tasks: the task timeline / runs / interventions page. */
export const ossTasksPlugin = definePlugin({
  id: "oss-tasks",
  apply(ctx) {
    pageRoute(
      ctx,
      {
        id: "task-detail",
        path: "/tasks/:taskId",
        label: "task.detailTitle",
        description: "Lead-dispatch task timeline, runs, and interventions.",
        layout: "project",
        showInNav: false,
        edition: "personal",
      },
      TaskDetailPage,
      routePlacement("task-detail"),
    );
  },
});
