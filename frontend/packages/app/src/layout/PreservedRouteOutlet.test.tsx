import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { PreservedRouteOutlet } from "./PreservedRouteOutlet";

function TestLayout() {
  const location = useLocation();
  return (
    <div className="relative h-full">
      <PreservedRouteOutlet overlay={location.pathname === "/detail"} />
    </div>
  );
}

function SourcePage({ onMount }: { onMount: () => void }) {
  const navigate = useNavigate();
  const [count, setCount] = useState(() => {
    onMount();
    return 0;
  });
  return (
    <div data-testid="source-page">
      <span>count {count}</span>
      <button type="button" onClick={() => setCount((value) => value + 1)}>
        increment
      </button>
      <button type="button" onClick={() => navigate("/detail")}>
        open detail
      </button>
    </div>
  );
}

function DetailPage() {
  const navigate = useNavigate();
  return (
    <div data-testid="detail-page">
      detail
      <button type="button" onClick={() => navigate(-1)}>
        close detail
      </button>
    </div>
  );
}

describe("PreservedRouteOutlet", () => {
  it("keeps the source page instance mounted under an overlay route", () => {
    const onMount = vi.fn();
    render(
      <MemoryRouter initialEntries={["/source"]}>
        <Routes>
          <Route element={<TestLayout />}>
            <Route path="/source" element={<SourcePage onMount={onMount} />} />
            <Route path="/detail" element={<DetailPage />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: "increment" }));
    fireEvent.click(screen.getByRole("button", { name: "open detail" }));

    expect(screen.getByTestId("detail-page")).toBeTruthy();
    expect(screen.getByText("count 1")).toBeTruthy();
    expect(
      screen.getByTestId("source-page").parentElement?.hasAttribute("inert"),
    ).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "close detail" }));

    expect(screen.queryByTestId("detail-page")).toBeNull();
    expect(screen.getByText("count 1")).toBeTruthy();
    expect(onMount).toHaveBeenCalledTimes(1);
  });

  it("renders an overlay route as a normal page on a direct entry", () => {
    render(
      <MemoryRouter initialEntries={["/detail"]}>
        <Routes>
          <Route element={<TestLayout />}>
            <Route path="/detail" element={<DetailPage />} />
          </Route>
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByTestId("detail-page")).toBeTruthy();
    expect(document.querySelector("[data-route-overlay='true']")).toBeNull();
  });
});
