import "@testing-library/jest-dom/vitest";

// jsdom does not implement scrollIntoView. Components that call it from a mount
// effect (e.g. keyboard-navigable popups like SkillSearchMenu) would otherwise
// throw during render in tests. Provide a no-op so those components mount.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}

if (!Element.prototype.scrollTo) {
  Element.prototype.scrollTo = () => {};
}

if (!window.matchMedia) {
  window.matchMedia = () =>
    ({
      matches: false,
      media: "",
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList;
}

// jsdom measures every box as 0×0, and recharts' ResponsiveContainer draws
// nothing at zero size — so the stub reports a fixed size synchronously on
// observe (assertions run on the same stack as render; a microtask is late).
if (!globalThis.ResizeObserver) {
  globalThis.ResizeObserver = class ResizeObserver {
    private readonly callback: globalThis.ResizeObserverCallback;

    constructor(callback: globalThis.ResizeObserverCallback) {
      this.callback = callback;
    }

    observe(target: Element) {
      const rect = { width: 640, height: 200, top: 0, left: 0, x: 0, y: 0 };
      this.callback(
        [
          {
            target,
            contentRect: rect,
            borderBoxSize: [{ inlineSize: rect.width, blockSize: rect.height }],
            contentBoxSize: [
              { inlineSize: rect.width, blockSize: rect.height },
            ],
          },
        ] as unknown as globalThis.ResizeObserverEntry[],
        this,
      );
    }

    unobserve() {}
    disconnect() {}
  };
}
