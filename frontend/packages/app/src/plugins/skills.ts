import { definePlugin } from "@valuz/core";

import { SkillDetailPage, SkillsPage } from "../pages";
import { pageRoute } from "./define";
import { routePlacement } from "./layout";

/** Skills: the library page and a skill's detail / editing page. */
export const ossSkillsPlugin = definePlugin({
  id: "oss-skills",
  apply(ctx) {
    pageRoute(
      ctx,
      {
        id: "skills",
        path: "/skills",
        label: "sidebar.skills",
        description: "Official and custom skills available to the local agent.",
        layout: "project",
        showInNav: true,
        edition: "personal",
      },
      SkillsPage,
      routePlacement("skills"),
    );
    pageRoute(
      ctx,
      {
        id: "skill-detail",
        path: "/skills/:skillId",
        label: "route.skillDetail",
        description: "Detailed view and editing of a skill.",
        layout: "project",
        showInNav: false,
        edition: "personal",
      },
      SkillDetailPage,
      routePlacement("skill-detail"),
    );
  },
});
