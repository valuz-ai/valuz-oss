/** @vitest-environment jsdom */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { initI18n } from "@valuz/shared/i18n";
import { setExecutionTargets, type ExecutionTarget } from "@valuz/core";

import { CreateKbDialog } from "./CreateKbDialog";

beforeAll(() => initI18n({ locale: "en-US", fallbackLocale: "en-US" }));
afterEach(() => setExecutionTargets([]));

const LOCAL: ExecutionTarget = {
  id: "local",
  labelKey: "commercial.exec.local",
  baseUrl: "http://localhost:8000",
  isDefault: true,
};
// ``remote`` with no ``selectDirectory`` is what makes a target managed-cwd.
const CLOUD: ExecutionTarget = {
  id: "cloud",
  labelKey: "commercial.exec.cloud",
  baseUrl: "http://cloud:8010",
  remote: true,
};

const AUTO_DISCOVER = /auto-discover new files/i;

const renderDialog = (props: Partial<Parameters<typeof CreateKbDialog>[0]>) => {
  const onSubmit = vi.fn().mockResolvedValue(undefined);
  render(
    <CreateKbDialog
      open
      onOpenChange={() => {}}
      onSubmit={onSubmit}
      {...props}
    />,
  );
  return onSubmit;
};

describe("CreateKbDialog auto-discover option", () => {
  it("offers it for a user-picked local directory", () => {
    renderDialog({ directoryFieldMode: "picker" });
    expect(screen.getByText(AUTO_DISCOVER)).toBeTruthy();
  });

  it("keeps offering it on a managed root that the backend does rescan", () => {
    // OSS headless: no directory picker, but the managed root is a real
    // directory someone can drop files into, and the scan is how they land.
    renderDialog({ directoryFieldMode: "managed" });
    expect(screen.getByText(AUTO_DISCOVER)).toBeTruthy();
  });

  it("hides it on a managed root the backend never rescans", () => {
    renderDialog({
      directoryFieldMode: "managed",
      managedRootAutoDiscovers: false,
    });
    expect(screen.queryByText(AUTO_DISCOVER)).toBeNull();
  });

  it("sends auto_discover false when the option was never shown", async () => {
    // The checkbox defaults to checked, so an unguarded submit would ask for
    // a scan the user was never offered and the backend will never run.
    const onSubmit = renderDialog({
      directoryFieldMode: "managed",
      managedRootAutoDiscovers: false,
    });
    fireEvent.change(screen.getByPlaceholderText(/knowledge base/i), {
      target: { value: "cloud kb" },
    });
    fireEvent.click(screen.getByRole("button", { name: /create/i }));
    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith(
        expect.objectContaining({ auto_discover: false }),
      ),
    );
  });

  it("follows the selected target, not the dialog", () => {
    // Multi-target edition: the same dialog creates a scannable local KB and
    // an unscannable cloud one. Defaulting to local, the option is offered.
    setExecutionTargets([LOCAL, CLOUD]);
    renderDialog({
      directoryFieldMode: "picker",
      managedRootAutoDiscovers: false,
    });
    expect(screen.getByText(AUTO_DISCOVER)).toBeTruthy();
  });

  it("hides it once a managed-cwd target is the effective one", () => {
    setExecutionTargets([{ ...LOCAL, isDefault: false }, CLOUD]);
    // No default flagged → the first target wins; make that the cloud one.
    setExecutionTargets([CLOUD, { ...LOCAL, isDefault: false }]);
    renderDialog({
      directoryFieldMode: "picker",
      managedRootAutoDiscovers: false,
    });
    expect(screen.queryByText(AUTO_DISCOVER)).toBeNull();
  });
});
