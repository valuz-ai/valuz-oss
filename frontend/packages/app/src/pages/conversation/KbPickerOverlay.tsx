import type { Dispatch, SetStateAction } from "react";
import type { SessionAttachmentItem } from "@valuz/core";
import { KnowledgeFileTreePicker, type KbBindingTreeNode } from "@valuz/ui";
import type { useConversationSend } from "./useConversationSend";

type KbPickerOverlayProps = {
  kbPickerOpen: boolean;
  pickerKbTree: KbBindingTreeNode[];
  pickerKbLoading: boolean;
  pickerExpandFolder: (kbId: string, folderId: string) => Promise<void>;
  sessionAttachments: SessionAttachmentItem[];
  handleKbPickerConfirm: ReturnType<
    typeof useConversationSend
  >["handleKbPickerConfirm"];
  setKbPickerOpen: Dispatch<SetStateAction<boolean>>;
};

/**
 * ── Knowledge-Base picker overlay ────────────────────────────────────
 *
 * The modal file picker the composer's "attach from KB" action opens.
 * Extracted verbatim from ConversationPage's return JSX — behavior and
 * markup unchanged; every referenced page value arrives as a same-named
 * prop.
 */
export function KbPickerOverlay({
  kbPickerOpen,
  pickerKbTree,
  pickerKbLoading,
  pickerExpandFolder,
  sessionAttachments,
  handleKbPickerConfirm,
  setKbPickerOpen,
}: KbPickerOverlayProps) {
  return (
    <>
      {/* Knowledge Base file picker overlay — tree view: documents are
          organised under their KB and folders; folders are expandable
          for navigation but only files are selectable. */}
      {kbPickerOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="flex h-[600px] max-h-[85vh] w-[720px] max-w-[92vw] flex-col rounded-xl border border-surface-border bg-card p-4 shadow-xl">
            <KnowledgeFileTreePicker
              kbTree={pickerKbTree}
              loading={pickerKbLoading}
              onExpandFolder={pickerExpandFolder}
              // Pre-check only the *pending* KB picks — the ones still
              // staged for the next turn. Already-consumed picks are
              // session history, not part of the current staging set,
              // so re-opening the picker shouldn't show them ticked.
              selected={sessionAttachments
                .filter(
                  (a) =>
                    a.source_kind === "kb_doc" &&
                    a.source_kb_doc_id &&
                    !a.consumed_at,
                )
                .map((a) => a.source_kb_doc_id as string)}
              onConfirm={handleKbPickerConfirm}
              onCancel={() => setKbPickerOpen(false)}
            />
          </div>
        </div>
      )}
    </>
  );
}
