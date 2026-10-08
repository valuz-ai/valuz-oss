import "@testing-library/jest-dom/vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAuthStore } from "../../../../core/src/store/auth-store";
import { MemoryRecordsPanel, MemorySection } from "./MemorySection";

const api = vi.hoisted(() => ({
  listRecords: vi.fn(),
  getSettings: vi.fn(),
  getMemory: vi.fn(),
  addRecord: vi.fn(),
  correctRecord: vi.fn(),
  forgetRecord: vi.fn(),
  forgetSource: vi.fn(),
  patchSettings: vi.fn(),
}));
const translate = (key: string) => key;
vi.mock("@valuz/core", async () => {
  const { useAuthStore } = await import(
    "../../../../core/src/store/auth-store"
  );
  return {
    useAuthStore,
    memoryApi: api,
    settingsApi: {
      getPreferences: async () => ({}),
      patchPreferences: async () => ({}),
    },
    useTranslation: () => ({ t: translate }),
  };
});
vi.mock("./section-header-actions", () => ({
  useSectionHeaderActions: () => null,
}));

const record = {
  id: "stable-memory-id",
  target: "user",
  content: "old preference",
  revision: 1,
  source: "auto",
  source_refs: [
    { kind: "session", source_id: "traceable-session", origin: "owner" },
  ],
  kind: "preference",
  namespace: "default",
  object_refs: [],
  observed_at: 1791400000,
  supersedes: [],
  confirmed: false,
};
const page = {
  revision: 1,
  records: [record],
  total: 1,
  offset: 0,
  limit: 20,
  context_notice: "Current context may retain old values.",
};
const receipt = {
  status: "applied",
  revision: 2,
  affected_ids: [],
  affected_project_ids: [],
  replayed: false,
  context_notice: "Original chats remain.",
};
const label = (suffix: string) => `settings.memory.${suffix}`;

beforeEach(() => {
  vi.resetAllMocks();
  act(() => useAuthStore.getState().setCurrentUser(null));
  api.getSettings.mockResolvedValue({
    enabled: true,
    auto_extract: true,
    custom_instructions: "",
  });
  api.listRecords.mockResolvedValue(page);
  api.addRecord.mockResolvedValue(receipt);
  api.correctRecord.mockResolvedValue(receipt);
  api.forgetRecord.mockResolvedValue(receipt);
  api.forgetSource.mockResolvedValue(receipt);
});

async function loaded(enabled = true) {
  render(<MemoryRecordsPanel enabled={enabled} />);
  await screen.findByText("old preference");
}

describe("memory record management", () => {
  it("uses records and settings only; displays identity, timestamp and provenance", async () => {
    render(<MemorySection />);
    await screen.findByText("old preference");
    await waitFor(() => expect(api.getSettings).toHaveBeenCalledTimes(1));
    expect(api.getMemory).not.toHaveBeenCalled();
    expect(screen.getByText(/stable-memory-id/)).toBeInTheDocument();
    expect(screen.getByText(/traceable-session/)).toBeInTheDocument();
    expect(screen.getByText(label("unconfirmed"))).toBeInTheDocument();
    expect(document.querySelector("time")?.getAttribute("datetime")).toBe(
      new Date(record.observed_at * 1000).toISOString(),
    );
  });

  it("allows viewing and confirmed forgetting while collection is disabled", async () => {
    await loaded(false);
    expect(
      screen.getByRole("button", { name: label("addRecord") }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: label("correctRecord") }),
    ).toBeDisabled();
    fireEvent.click(
      screen.getByRole("button", { name: label("forgetRecord") }),
    );
    expect(api.forgetRecord).not.toHaveBeenCalled();
    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByText(label("forgetRecordHint"), { exact: false }),
    ).toBeInTheDocument();
    fireEvent.click(
      within(dialog).getByRole("button", { name: label("confirmForget") }),
    );
    await waitFor(() =>
      expect(api.forgetRecord).toHaveBeenCalledWith("stable-memory-id", {
        operation_id: expect.any(String),
        base_revision: 1,
        target: "user",
        project_id: undefined,
      }),
    );
    expect(api.addRecord).not.toHaveBeenCalled();
    expect(api.correctRecord).not.toHaveBeenCalled();
  });

  it("shows authority unavailability instead of claiming memory is empty", async () => {
    api.listRecords.mockRejectedValue({ status: 503 });
    render(<MemoryRecordsPanel enabled />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      label("unavailable"),
    );
    expect(screen.queryByText(label("empty"))).not.toBeInTheDocument();
  });

  it("does not enable collection when the settings authority cannot be read", async () => {
    api.getSettings.mockRejectedValue({ status: 503 });
    render(<MemorySection />);
    await screen.findByText("old preference");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      label("unavailable"),
    );
    expect(
      screen.getByRole("button", { name: label("addRecord") }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: label("correctRecord") }),
    ).toBeDisabled();
    expect(api.getMemory).not.toHaveBeenCalled();
  });

  it("reloads a conflict and requires explicit reconfirmation against the new revision", async () => {
    api.listRecords.mockResolvedValueOnce(page).mockResolvedValue({
      ...page,
      revision: 4,
      records: [{ ...record, content: "latest external correction" }],
    });
    api.correctRecord
      .mockRejectedValueOnce({ status: 409 })
      .mockResolvedValue(receipt);
    await loaded();
    fireEvent.click(
      screen.getByRole("button", { name: label("correctRecord") }),
    );
    fireEvent.change(screen.getByLabelText(label("recordContent")), {
      target: { value: "my correction" },
    });
    expect(api.correctRecord).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: label("saveRecord") }));
    await screen.findByRole("button", { name: label("confirmLatest") });
    const conflictAlert = within(screen.getByRole("dialog")).getByRole("alert");
    expect(conflictAlert).toHaveTextContent(label("conflict"));
    expect(conflictAlert.closest('[aria-hidden="true"]')).toBeNull();
    expect(api.correctRecord).toHaveBeenCalledTimes(1);
    expect(
      within(screen.getByRole("dialog")).getByText(
        /latest external correction/,
      ),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: label("confirmLatest") }),
    );
    await waitFor(() => expect(api.correctRecord).toHaveBeenCalledTimes(2));
    expect(api.correctRecord.mock.calls[1][1]).toMatchObject({
      base_revision: 4,
      content: "my correction",
    });
    expect(api.correctRecord.mock.calls[1][1].operation_id).not.toBe(
      api.correctRecord.mock.calls[0][1].operation_id,
    );
  });

  it("reuses a submission token after an uncertain network failure", async () => {
    api.correctRecord
      .mockRejectedValueOnce({ status: 503 })
      .mockResolvedValue(receipt);
    await loaded();
    fireEvent.click(
      screen.getByRole("button", { name: label("correctRecord") }),
    );
    fireEvent.change(screen.getByLabelText(label("recordContent")), {
      target: { value: "correction" },
    });
    fireEvent.click(screen.getByRole("button", { name: label("saveRecord") }));
    await within(screen.getByRole("dialog")).findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "common.retry" }));
    await waitFor(() => expect(api.correctRecord).toHaveBeenCalledTimes(2));
    expect(api.correctRecord.mock.calls[1]).toEqual(
      api.correctRecord.mock.calls[0],
    );
  });

  it("keeps a lost reply visibly uncertain inside the dialog and replays the same submission", async () => {
    api.addRecord.mockRejectedValueOnce(new TypeError("Failed to fetch")).mockResolvedValue(receipt);
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: label("addRecord") }));
    fireEvent.change(screen.getByLabelText(label("recordContent")), { target: { value: "uncertain draft" } });
    fireEvent.click(screen.getByRole("button", { name: label("saveRecord") }));
    const dialog = screen.getByRole("dialog");
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(label("saveUncertain"));
    expect(within(dialog).queryByText(label("saveFailed"))).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "common.retry" }));
    await waitFor(() => expect(api.addRecord).toHaveBeenCalledTimes(2));
    expect(api.addRecord.mock.calls[1]).toEqual(api.addRecord.mock.calls[0]);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it.each([new TypeError("timeout"), { status: 502 }, { status: 422, body: JSON.stringify({ detail: { code: "MEMORY_WRITE_UNKNOWN" } }) }])("shows uncertain writes accessibly for transport and unknown server outcomes: %j", async (cause) => {
    api.correctRecord.mockRejectedValue(cause);
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: label("correctRecord") }));
    fireEvent.click(screen.getByRole("button", { name: label("saveRecord") }));
    const alert = await within(screen.getByRole("dialog")).findByRole("alert");
    expect(alert).toHaveTextContent(label("saveUncertain"));
    expect(alert.closest('[aria-hidden="true"]')).toBeNull();
    expect(screen.getByRole("button", { name: "common.retry" })).toBeEnabled();
  });

  it("keeps a definite validation rejection accessible in the form", async () => {
    api.correctRecord.mockRejectedValue({ status: 422 });
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: label("correctRecord") }));
    fireEvent.click(screen.getByRole("button", { name: label("saveRecord") }));
    expect(await within(screen.getByRole("dialog")).findByRole("alert")).toHaveTextContent(label("saveFailed"));
    expect(screen.queryByText(label("saveUncertain"))).not.toBeInTheDocument();
  });

  it("freezes an unknown add across attempted edits, target changes and dialog reopen", async () => {
    api.listRecords.mockResolvedValue({ ...page, authority_id: "original-authority", authority_epoch: 7 });
    api.addRecord.mockRejectedValueOnce(new TypeError("reply lost after commit")).mockResolvedValue(receipt);
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: label("addRecord") }));
    fireEvent.change(screen.getByLabelText(label("recordContent")), { target: { value: "original submitted content" } });
    fireEvent.click(screen.getByRole("button", { name: label("saveRecord") }));
    const dialog = screen.getByRole("dialog");
    await within(dialog).findByRole("alert");
    expect(within(dialog).getByLabelText(label("recordContent"))).toBeDisabled();
    expect(within(dialog).getByLabelText(label("scope"))).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText(label("recordContent")), { target: { value: "changed after lost reply" } });
    fireEvent.change(within(dialog).getByLabelText(label("scope")), { target: { value: "global" } });
    expect(within(dialog).getByLabelText(label("recordContent"))).toHaveValue("original submitted content");
    fireEvent.click(within(dialog).getByRole("button", { name: "common.cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: label("addRecord") })).toBeDisabled();
    expect(screen.getByRole("combobox", { name: label("scope") })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "common.retry" }));
    expect(screen.getByRole("dialog")).toHaveTextContent(label("saveUncertain"));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "common.retry" }));
    await waitFor(() => expect(api.addRecord).toHaveBeenCalledTimes(2));
    expect(api.addRecord.mock.calls[1]).toEqual(api.addRecord.mock.calls[0]);
    expect(api.addRecord.mock.calls[1][0]).toMatchObject({ content: "original submitted content", target: "user", base_revision: 1, authority_id: "original-authority", authority_epoch: 7 });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: label("addRecord") }));
    expect(screen.getByLabelText(label("recordContent"))).toBeEnabled();
    expect(screen.getByLabelText(label("recordContent"))).toHaveValue("");
  });

  it("keeps an unknown forget's exact record and token when its dialog is dismissed", async () => {
    api.forgetRecord.mockRejectedValueOnce(new TypeError("lost forget reply")).mockResolvedValue(receipt);
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: label("forgetRecord") }));
    fireEvent.click(screen.getByRole("button", { name: label("confirmForget") }));
    await within(screen.getByRole("dialog")).findByText(label("saveUncertain"), { exact: false });
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "common.cancel" }));
    expect(screen.getByRole("button", { name: label("forgetSource") })).toBeDisabled();
    expect(screen.getByRole("button", { name: label("correctRecord") })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "common.retry" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("stable-memory-id");
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "common.retry" }));
    await waitFor(() => expect(api.forgetRecord).toHaveBeenCalledTimes(2));
    expect(api.forgetRecord.mock.calls[1]).toEqual(api.forgetRecord.mock.calls[0]);
  });

  it("allows a new explicit payload after a definite rejection", async () => {
    api.addRecord.mockRejectedValueOnce({ status: 422 }).mockResolvedValue(receipt);
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: label("addRecord") }));
    fireEvent.change(screen.getByLabelText(label("recordContent")), { target: { value: "rejected content" } });
    fireEvent.click(screen.getByRole("button", { name: label("saveRecord") }));
    await within(screen.getByRole("dialog")).findByRole("alert");
    expect(screen.getByLabelText(label("recordContent"))).toBeEnabled();
    expect(within(screen.getByRole("dialog")).getByLabelText(label("scope"))).toBeEnabled();
    fireEvent.change(screen.getByLabelText(label("recordContent")), { target: { value: "new intent" } });
    fireEvent.change(within(screen.getByRole("dialog")).getByLabelText(label("scope")), { target: { value: "global" } });
    fireEvent.click(screen.getByRole("button", { name: label("saveRecord") }));
    await waitFor(() => expect(api.addRecord).toHaveBeenCalledTimes(2));
    expect(api.addRecord.mock.calls[1][0]).toMatchObject({ content: "new intent", target: "global" });
    expect(api.addRecord.mock.calls[1][0].operation_id).not.toBe(api.addRecord.mock.calls[0][0].operation_id);
  });

  it("keeps the original authority binding when a failed save refreshes to another epoch", async () => {
    api.listRecords.mockResolvedValue({ ...page, authority_id: "a", authority_epoch: 2 });
    api.correctRecord.mockRejectedValueOnce({ status: 503 }).mockResolvedValue(receipt);
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: label("correctRecord") }));
    fireEvent.change(screen.getByLabelText(label("recordContent")), { target: { value: "corrected" } });
    fireEvent.click(screen.getByRole("button", { name: label("saveRecord") }));
    await within(screen.getByRole("dialog")).findByRole("alert");
    fireEvent.click(screen.getByRole("button", { name: "common.retry" }));
    await waitFor(() => expect(api.correctRecord).toHaveBeenCalledTimes(2));
    expect(api.correctRecord.mock.calls[0][1]).toMatchObject({ authority_id: "a", authority_epoch: 2 });
    expect(api.correctRecord.mock.calls[1]).toEqual(api.correctRecord.mock.calls[0]);
  });

  it("does not close forgetting until maintenance completes, and retries the same operation", async () => {
    api.forgetRecord.mockResolvedValueOnce({ ...receipt, maintenance_complete: false }).mockResolvedValue(receipt);
    await loaded(false);
    fireEvent.click(screen.getByRole("button", { name: label("forgetRecord") }));
    fireEvent.click(screen.getByRole("button", { name: label("confirmForget") }));
    await within(screen.getByRole("dialog")).findByText(label("cleanupIncomplete"), { exact: false });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: label("confirmForget") })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: label("confirmForget") }));
    await waitFor(() => expect(api.forgetRecord).toHaveBeenCalledTimes(2));
    expect(api.forgetRecord.mock.calls[1]).toEqual(api.forgetRecord.mock.calls[0]);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("separately confirms forgetting all derivations of a tracked source", async () => {
    await loaded(false);
    fireEvent.click(
      screen.getByRole("button", { name: label("forgetSource") }),
    );
    expect(api.forgetSource).not.toHaveBeenCalled();
    const dialog = screen.getByRole("dialog");
    expect(
      within(dialog).getByText(label("forgetSourceHint"), { exact: false }),
    ).toBeInTheDocument();
    fireEvent.click(
      within(dialog).getByRole("button", { name: label("confirmForget") }),
    );
    await waitFor(() =>
      expect(api.forgetSource).toHaveBeenCalledWith({
        operation_id: expect.any(String),
        base_revision: 1,
        kind: "session",
        source_id: "traceable-session",
      }),
    );
    expect(api.forgetRecord).not.toHaveBeenCalled();
  });

  it("requests later pages and resets the offset when the scope changes", async () => {
    api.listRecords.mockResolvedValue({ ...page, total: 45 });
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: label("nextPage") }));
    await waitFor(() =>
      expect(api.listRecords).toHaveBeenLastCalledWith(
        { offset: 20, limit: 20 },
        expect.any(Object),
      ),
    );
    fireEvent.change(screen.getByRole("combobox", { name: label("scope") }), {
      target: { value: "global" },
    });
    await waitFor(() =>
      expect(api.listRecords).toHaveBeenLastCalledWith(
        { offset: 0, limit: 20, target: "global" },
        expect.any(Object),
      ),
    );
  });
});

function account(id: string) {
  act(() =>
    useAuthStore
      .getState()
      .setCurrentUser({ id, displayName: id, role: "owner" }),
  );
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

it("clears old-owner records, drafts and source confirmations through the core identity remount", async () => {
  account("owner-a");
  render(<MemorySection />);
  await screen.findByText("old preference");
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: label("correctRecord") }),
    ).not.toBeDisabled(),
  );
  fireEvent.click(screen.getByRole("button", { name: label("correctRecord") }));
  fireEvent.change(screen.getByLabelText(label("recordContent")), {
    target: { value: "owner A private draft" },
  });
  api.listRecords.mockResolvedValue({
    ...page,
    records: [
      {
        ...record,
        id: "b-record",
        content: "B only memory",
        source_refs: [{ ...record.source_refs[0], source_id: "B-source" }],
      },
    ],
  });
  account("owner-b");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(
    screen.queryByDisplayValue("owner A private draft"),
  ).not.toBeInTheDocument();
  expect(screen.queryByText("old preference")).not.toBeInTheDocument();
  await screen.findByText("B only memory");
  fireEvent.click(screen.getByRole("button", { name: label("forgetSource") }));
  expect(screen.getByRole("dialog")).toHaveTextContent("B-source");
  api.listRecords.mockResolvedValue({ ...page, records: [], total: 0 });
  account("owner-c");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.queryByText(/B-source/)).not.toBeInTheDocument();
  expect(api.correctRecord).not.toHaveBeenCalled();
  expect(api.forgetSource).not.toHaveBeenCalled();
  await screen.findByText(label("empty"));
  await waitFor(() => expect(api.getSettings).toHaveBeenCalledTimes(3));
});

it("ignores old settings and record promises that resolve after switching the active owner", async () => {
  account("owner-a");
  const oldSettings = deferred<{
    enabled: boolean;
    auto_extract: boolean;
    custom_instructions: string;
  }>();
  const oldRecords = deferred<typeof page>();
  api.getSettings.mockReturnValueOnce(oldSettings.promise).mockResolvedValue({
    enabled: false,
    auto_extract: false,
    custom_instructions: "B instructions",
  });
  api.listRecords.mockReturnValueOnce(oldRecords.promise).mockResolvedValue({
    ...page,
    records: [
      {
        ...record,
        id: "b-record",
        content: "B only memory",
        source_refs: [],
      },
    ],
  });
  render(<MemorySection />);
  await waitFor(() => expect(api.getSettings).toHaveBeenCalledTimes(1));
  account("owner-b");
  await screen.findByText("B only memory");
  await screen.findByDisplayValue("B instructions");
  expect(api.getSettings.mock.calls[0][0].signal.aborted).toBe(true);
  await act(async () => {
    oldSettings.resolve({
      enabled: true,
      auto_extract: true,
      custom_instructions: "A secret instructions",
    });
    oldRecords.resolve(page);
    await Promise.all([oldSettings.promise, oldRecords.promise]);
  });
  expect(screen.queryByText("old preference")).not.toBeInTheDocument();
  expect(
    screen.queryByDisplayValue("A secret instructions"),
  ).not.toBeInTheDocument();
  expect(screen.getByDisplayValue("B instructions")).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: label("addRecord") }),
  ).toBeDisabled();
});

it("drops the prior owner's uncertain draft when identity changes", async () => {
  account("owner-a");
  api.addRecord.mockRejectedValue(new TypeError("reply lost"));
  render(<MemorySection />);
  await screen.findByText("old preference");
  fireEvent.click(screen.getByRole("button", { name: label("addRecord") }));
  fireEvent.change(screen.getByLabelText(label("recordContent")), { target: { value: "A submitted private draft" } });
  fireEvent.click(screen.getByRole("button", { name: label("saveRecord") }));
  await within(screen.getByRole("dialog")).findByRole("alert");
  api.listRecords.mockResolvedValue({ ...page, records: [], total: 0 });
  account("owner-b");
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  await waitFor(() => expect(screen.getByRole("button", { name: label("addRecord") })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: label("addRecord") }));
  expect(screen.getByLabelText(label("recordContent"))).toHaveValue("");
  expect(screen.getByLabelText(label("recordContent"))).toBeEnabled();
  expect(screen.queryByText(label("saveUncertain"))).not.toBeInTheDocument();
});
