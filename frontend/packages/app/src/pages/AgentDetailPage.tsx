import { useLocation, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "@valuz/core";
import { AgentDetailView } from "../components/AgentDetailView";

/** Router state passed when opening an agent from a project member row. */
interface FromProjectState {
  fromProject?: { id: string; name: string };
}

/**
 * Full-page agent detail route (/agents/:slug). Thin wrapper around the shared
 * AgentDetailView — the same view also renders inside the智能体库 master-detail
 * right panel (AgentsPage), so the 5-tab detail lives in exactly one place.
 *
 * When opened from a project member row (live-reference 派驻), the project
 * passes `{ fromProject }` in router state so the back affordance returns to
 * the project and reads "返回项目" — making it explicit that editing here is a
 * global edit of the shared agent (08-agents-module §派驻).
 */
export const AgentDetailPage = () => {
  const { slug = "" } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const { t } = useTranslation();
  const fromProject = (location.state as FromProjectState | null)?.fromProject;

  return (
    <AgentDetailView
      slug={slug}
      onBack={() => (fromProject ? navigate(-1) : navigate("/agents"))}
      backLabel={fromProject ? t("agent.backToProject") : undefined}
    />
  );
};
