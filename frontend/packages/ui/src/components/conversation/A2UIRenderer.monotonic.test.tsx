import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { A2UIRenderer } from "./A2UIRenderer";

/**
 * A generated page must only ever GROW on screen.
 *
 * Two things used to break that, both observed on a real research-desk
 * generation:
 *
 * 1. The runtime narrates its own gaps — a half-written component name renders
 *    as ``Unknown component: PageHea``, an undelivered child as
 *    ``[Loading card-brief...]``. Mid-stream that was most of the viewport.
 * 2. A turn can carry the same document twice (the canonical assistant text is
 *    the join of every model-end segment). The repeat arrives a character at a
 *    time and was merged over the finished page, so every component dissolved
 *    back into a skeleton and grew again.
 */

const ONE_COPY = [
  JSON.stringify({
    version: "v0.9.1",
    createSurface: { surfaceId: "main", catalogId: "https://valuz.io/a2ui/catalogs/base/v1" },
  }),
  JSON.stringify({
    version: "v0.9.1",
    updateComponents: {
      surfaceId: "main",
      components: [
        { id: "root", component: "Stack", children: ["a", "b"] },
        { id: "a", component: "TextContent", text: "第一块" },
        { id: "b", component: "TextContent", text: "第二块" },
      ],
    },
  }),
].join("\n");

/** What the turn actually hands over: the document, then the document again. */
const DOUBLED = `${ONE_COPY}\n${ONE_COPY}`;

const textAt = (body: string): string => {
  const { container } = render(<A2UIRenderer body={body} />);
  return (container.textContent ?? "").replace(/\s+/g, " ").trim();
};

const frames = (body: string, count: number): string[] => {
  const { container, rerender, unmount } = render(
    <A2UIRenderer body="" status="running" />,
  );
  const result: string[] = [];
  for (let i = 0; i < count; i += 1) {
    rerender(
      <A2UIRenderer
        body={body.slice(0, Math.round((body.length * (i + 1)) / count))}
        status="running"
      />,
    );
    result.push((container.textContent ?? "").replace(/\s+/g, " ").trim());
  }
  unmount();
  return result;
};

describe("A2UI progressive paint", () => {
  it("should never narrate a half-written component name to the user", () => {
    const all = frames(ONE_COPY, 24).join("");
    expect(all).not.toMatch(/Unknown component/);
  });

  it("should not narrate undelivered children as loading text", () => {
    const all = frames(ONE_COPY, 24).join("");
    expect(all).not.toMatch(/\[Loading/);
  });

  it("should breathe a page skeleton while nothing resolves yet", () => {
    // The surface header has landed and its first component has not. A hole
    // reads as "nothing is happening"; the runtime's own answer is the literal
    // string ``[Loading root...]``.
    const { container } = render(
      <A2UIRenderer body={ONE_COPY.slice(0, 40)} status="running" />,
    );
    expect(
      container.querySelector('[data-slot="a2ui-generation-skeleton"]'),
    ).toBeTruthy();
  });

  it("should breathe a skeleton before the run writes its first byte", () => {
    // The model can reason for a minute before any document appears. That is a
    // wait, not an absence — the workbench must not sit blank through it.
    const { container } = render(<A2UIRenderer body="" status="running" />);
    expect(
      container.querySelector('[data-slot="a2ui-generation-skeleton"]'),
    ).toBeTruthy();
  });

  it("should drop the skeleton as soon as a real component resolves", () => {
    const { container } = render(<A2UIRenderer body={ONE_COPY} status="running" />);
    expect(
      container.querySelector('[data-slot="a2ui-generation-skeleton"]'),
    ).toBe(null);
    expect(container.textContent).toContain("第一块");
  });

  it("should render nothing at all for an empty payload once the run is over", () => {
    // A finished run with nothing in it is genuinely empty; a skeleton there
    // would promise a page that is never coming.
    const { container } = render(<A2UIRenderer body="" status="success" />);
    expect(container.firstChild).toBe(null);
  });

  it("should only ever grow while a single copy streams", () => {
    const seen = frames(ONE_COPY, 24);
    seen.forEach((frame, i) => {
      if (i === 0) return;
      expect(frame.startsWith(seen[i - 1] as string)).toBe(true);
    });
  });

  it("should only ever grow when the turn repeats the whole document", () => {
    // The regression: without the repeat being dropped, frames past the
    // half-way mark went BACKWARDS as the second copy overwrote the first.
    const seen = frames(DOUBLED, 24);
    seen.forEach((frame, i) => {
      if (i === 0) return;
      expect(frame.startsWith(seen[i - 1] as string)).toBe(true);
    });
  });

  it("should render a repeated document exactly once", () => {
    const once = textAt(ONE_COPY);
    expect(textAt(DOUBLED)).toBe(once);
    expect(once).toContain("第一块");
    expect(once).toContain("第二块");
  });

  it("should keep a genuine second surface that says something different", () => {
    // Only a re-emission of what we already have is dropped; a real restart
    // carrying different content must still render.
    const other = ONE_COPY.replace("第二块", "改过了");
    expect(textAt(`${ONE_COPY}\n${other}`)).toContain("改过了");
  });

  it("should keep the last good page when a mid-run build comes up empty", () => {
    // The A2UI state machine throws on some half-written shapes and the build
    // returns nothing for a byte or two. Rendering that gap dissolved a
    // finished-looking page into a full-screen skeleton and rebuilt it seconds
    // later — the "又被切回完全骨架" report.
    const { container, rerender } = render(
      <A2UIRenderer body={ONE_COPY} status="running" />,
    );
    expect(container.textContent).toContain("第一块");

    // A continuation of the SAME document that the processor cannot build —
    // an update aimed at a surface that was never created.
    const breaks = `${ONE_COPY}\n${JSON.stringify({
      version: "v0.9.1",
      updateComponents: {
        surfaceId: "never-created",
        components: [{ id: "x", component: "TextContent", text: "y" }],
      },
    })}`;
    rerender(<A2UIRenderer body={breaks} status="running" />);

    expect(container.textContent).toContain("第一块");
    expect(
      container.querySelector('[data-slot="a2ui-generation-skeleton"]'),
    ).toBe(null);
  });

  it("should not log expected validation failures for incomplete streaming JSON", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const { rerender } = render(<A2UIRenderer body={ONE_COPY} status="running" />);
    const breaks = `${ONE_COPY}\n${JSON.stringify({
      version: "v0.9.1",
      updateComponents: {
        surfaceId: "never-created",
        components: [{ id: "x", component: "TextContent", text: "y" }],
      },
    })}`;

    rerender(<A2UIRenderer body={breaks} status="running" />);

    expect(warn).not.toHaveBeenCalledWith(
      "[a2ui] failed to render payload",
      expect.anything(),
    );
    warn.mockRestore();
  });

  it("should let a run that ENDS empty render nothing", () => {
    // Holding the last good page is a mid-run courtesy, not a permanent latch.
    const { container, rerender } = render(
      <A2UIRenderer body={ONE_COPY} status="running" />,
    );
    rerender(<A2UIRenderer body="" status="success" />);

    expect(container.firstChild).toBe(null);
  });

  it("should mark the page as still being written while the run is live", () => {
    // Without it a page that pauses for a few seconds looks finished, and the
    // user applies half a document.
    const { container, rerender } = render(
      <A2UIRenderer body={ONE_COPY} status="running" />,
    );
    expect(
      container.querySelector('[data-slot="a2ui-generation-tail"]'),
    ).toBeTruthy();

    rerender(<A2UIRenderer body={ONE_COPY} status="success" />);
    expect(container.querySelector('[data-slot="a2ui-generation-tail"]')).toBe(
      null,
    );
  });

  it("should not carry a held page across a payload swap", () => {
    // The workbench toggles the SAME renderer element between its bound page
    // and the live one. A hold that survived that showed the old page — with a
    // loading tail under it — where the new generation belonged.
    const { container, rerender } = render(
      <A2UIRenderer body={ONE_COPY} status="running" />,
    );
    expect(container.textContent).toContain("第一块");

    // A different document that has not written anything renderable yet.
    const other = ONE_COPY.replace("main", "other").slice(0, 40);
    rerender(<A2UIRenderer body={other} status="running" />);

    expect(container.textContent).not.toContain("第一块");
    expect(
      container.querySelector('[data-slot="a2ui-generation-skeleton"]'),
    ).toBeTruthy();
  });
});
