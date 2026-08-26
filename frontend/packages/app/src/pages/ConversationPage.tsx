import { useParams } from "react-router-dom";
import { ConversationView } from "./conversation/ConversationView";
import { NEW_SESSION_ID } from "./conversation/session-events";

/**
 * The conversation ROUTE. All orchestration and chrome (header, artifact
 * split pane, project handoff, title rename/delete, context panel) now live
 * in ``ConversationView``'s ``page`` variant — this is a thin shell that
 * just resolves the route param and hands it in. See
 * ``conversation/ConversationView.tsx`` for the full assembly (also reused,
 * as ``variant="panel"``, by embedding hosts like a fixed-width edition
 * workbench panel).
 */
export const ConversationPage = () => {
  const { id = NEW_SESSION_ID } = useParams<{ id: string }>();
  return <ConversationView variant="page" sessionId={id} />;
};
