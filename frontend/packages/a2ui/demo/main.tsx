import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";

import { VALUZ_BASE_CATALOG_ID, ValuzA2UISurface } from "../src";
import "../src/styles.css";
import { GALLERY_CATEGORIES, type GalleryCategoryId } from "./gallery-data";
import "./styles.css";

const DEFAULT_CATEGORY: GalleryCategoryId = "layout";

function categoryFromHash(): GalleryCategoryId {
  const candidate = window.location.hash.slice(1);
  return GALLERY_CATEGORIES.some((category) => category.id === candidate)
    ? candidate as GalleryCategoryId
    : DEFAULT_CATEGORY;
}

export function Demo() {
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [categoryId, setCategoryId] = useState<GalleryCategoryId>(categoryFromHash);
  const category = GALLERY_CATEGORIES.find((item) => item.id === categoryId)!;

  useEffect(() => {
    const handleHashChange = () => setCategoryId(categoryFromHash());
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, []);

  function selectCategory(nextCategory: GalleryCategoryId) {
    setCategoryId(nextCategory);
    window.history.replaceState(null, "", `#${nextCategory}`);
    window.scrollTo({ top: 0 });
  }

  return (
    <div className="demo-stage" data-theme={theme}>
      <main className="demo-shell">
        <header className="demo-header">
          <div className="demo-heading">
            <span>VALUZ OPEN SOURCE</span>
            <h1>A2UI Base Catalog</h1>
            <p>独立、完整、可复用的 A2UI 基础组件与 React 渲染实现。</p>
          </div>
          <div className="demo-header-actions">
            <code>{VALUZ_BASE_CATALOG_ID}</code>
            <button
              aria-pressed={theme === "dark"}
              onClick={() => setTheme((current) => current === "light" ? "dark" : "light")}
              type="button"
            >
              {theme === "light" ? "深色" : "浅色"}
            </button>
          </div>
        </header>

        <div className="demo-catalog-meta" aria-label="Catalog summary">
          <span><strong>51</strong> 个基础组件</span>
          <span><strong>16</strong> 种图表</span>
          <span>A2UI v0.9.1</span>
          <span>Strict schemas</span>
        </div>

        <nav className="demo-nav" aria-label="组件分类">
          {GALLERY_CATEGORIES.map((item) => (
            <button
              aria-current={item.id === categoryId ? "page" : undefined}
              className={item.id === categoryId ? "is-active" : undefined}
              key={item.id}
              onClick={() => selectCategory(item.id)}
              type="button"
            >
              <span>{item.label}</span>
              <small>{item.specimens.length}</small>
            </button>
          ))}
        </nav>

        <section className="demo-category" id={category.id}>
          <header className="demo-category-header">
            <div>
              <span>{category.eyebrow}</span>
              <h2>{category.label}</h2>
            </div>
            <p>{category.description}</p>
          </header>

          <div className={`demo-specimen-grid demo-specimen-grid--${category.id}`}>
            {category.specimens.map((specimen, index) => (
              <article
                className={`demo-specimen${specimen.name === "ComboChart" ? " demo-specimen--wide" : ""}`}
                data-component={specimen.name}
                key={specimen.name}
              >
                <header className="demo-specimen-header">
                  <div>
                    <h3>{specimen.name}</h3>
                    <p>{specimen.description}</p>
                  </div>
                  <span>{String(index + 1).padStart(2, "0")}</span>
                </header>
                <div className="demo-specimen-preview">
                  <ValuzA2UISurface surface={specimen.surface} theme={theme} />
                </div>
              </article>
            ))}
          </div>
        </section>
      </main>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Demo />
  </StrictMode>,
);
