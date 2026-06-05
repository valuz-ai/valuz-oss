import { WorkspaceLayoutBase } from "@valuz/app/layout";

export function WebWorkspaceLayout() {
  return (
    <WorkspaceLayoutBase
      logoSrc="/logo.png"
      directoryFieldMode="input"
      mascotSrc={null}
    />
  );
}
