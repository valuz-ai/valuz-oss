/** @vitest-environment jsdom */
import { fireEvent, render } from "@testing-library/react";
import { useRef } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { usePersistentScroll } from "./use-persistent-scroll";

function Fixture({ storageKey }: { storageKey: string }) {
  const ref = useRef<HTMLDivElement>(null);
  usePersistentScroll(ref, storageKey);
  return <div ref={ref} data-testid="scroll" />;
}

beforeEach(() => {
  window.sessionStorage.clear();
  vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
    callback(0);
    return 1;
  });
  vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => undefined);
});

describe("usePersistentScroll", () => {
  it("restores an opaque pane key after unmounting and reopening", () => {
    const first = render(<Fixture storageKey="valuz.reader.qaScroll:opaque" />);
    const node = first.getByTestId("scroll");
    node.scrollTop = 420;
    fireEvent.scroll(node);
    first.unmount();

    const second = render(<Fixture storageKey="valuz.reader.qaScroll:opaque" />);

    expect(second.getByTestId("scroll").scrollTop).toBe(420);
  });

  it("does not share positions between document panes", () => {
    window.sessionStorage.setItem("pane:a", "200");

    const rendered = render(<Fixture storageKey="pane:b" />);

    expect(rendered.getByTestId("scroll").scrollTop).toBe(0);
  });
});
