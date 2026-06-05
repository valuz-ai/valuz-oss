import { create } from "zustand";

interface PanelState {
  collapsed: boolean;
  setCollapsed: (v: boolean | ((prev: boolean) => boolean)) => void;
  toggle: () => void;
}

export const usePanelStore = create<PanelState>((set) => ({
  collapsed: true,
  setCollapsed: (v) =>
    set((s) => ({
      collapsed: typeof v === "function" ? v(s.collapsed) : v,
    })),
  toggle: () => set((s) => ({ collapsed: !s.collapsed })),
}));
