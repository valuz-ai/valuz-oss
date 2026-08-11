import { StrictMode, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Check, Copy, Search } from "lucide-react";

import {
  VALUZ_BASE_CATALOG_ID,
  ValuzA2UISurface,
  valuzBaseComponentApis,
} from "../src";
import "../src/styles.css";
import {
  GALLERY_CATEGORIES,
  type GalleryCategoryId,
  type GallerySpecimen,
} from "./gallery-data";
import "./styles.css";

const DEFAULT_CATEGORY: GalleryCategoryId = "layout";
interface CatalogApiView {
  name: string;
  schema: {
    description?: string;
    shape: Record<string, unknown>;
  };
}
const API_BY_NAME = new Map<string, CatalogApiView>(
  valuzBaseComponentApis.map((api) => [api.name, api as CatalogApiView]),
);

function categoryFromHash(): GalleryCategoryId {
  const candidate = window.location.hash.slice(1);
  return GALLERY_CATEGORIES.some((category) => category.id === candidate)
    ? candidate as GalleryCategoryId
    : DEFAULT_CATEGORY;
}

function propNames(name: string): string[] {
  const api = API_BY_NAME.get(name);
  const shape = api?.schema.shape;
  return shape ? Object.keys(shape) : [];
}

function catalogLine(name: string): string {
  return `${name}(${propNames(name).join(", ")})`;
}

function CopyLine({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  function copy() {
    const pending = navigator.clipboard?.writeText(text);
    if (!pending) return;
    void pending.then(() => {
      setCopied(true);
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setCopied(false), 1600);
    });
  }

  return (
    <button aria-label={`复制 ${text}`} className="demo-copy" onClick={copy} type="button">
      {copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
      {copied ? "已复制" : "复制"}
    </button>
  );
}

function SpecimenCard({
  specimen,
  theme,
  narrow,
}: {
  specimen: GallerySpecimen;
  theme: "light" | "dark";
  narrow: boolean;
}) {
  const fields = propNames(specimen.name);
  const line = catalogLine(specimen.name);
  const modelDescription = API_BY_NAME.get(specimen.name)?.schema.description ?? specimen.description;

  return (
    <article className="demo-specimen" data-component={specimen.name}>
      <header className="demo-specimen-header">
        <h3>{specimen.name}</h3>
        <span>{fields.length} 个字段</span>
        {specimen.componentNames.length > 1 ? <small>组合示例</small> : null}
      </header>

      <div className="demo-specimen-preview">
        <div className="demo-specimen-surface" data-narrow={narrow || undefined}>
          <ValuzA2UISurface surface={specimen.surface} theme={theme} />
        </div>
      </div>

      <div className="demo-specimen-contract">
        <div className="demo-contract-line">
          <code>{line}</code>
          <CopyLine text={`${line} — ${modelDescription}`} />
        </div>
        <details>
          <summary>
            <span>模型看到的说明</span>
            <em>{modelDescription}</em>
          </summary>
          <p>{modelDescription}</p>
        </details>
      </div>
    </article>
  );
}

export function Demo() {
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [narrow, setNarrow] = useState(false);
  const [query, setQuery] = useState("");
  const [categoryId, setCategoryId] = useState<GalleryCategoryId>(categoryFromHash);
  const normalizedQuery = query.trim().toLowerCase();

  useEffect(() => {
    const handleHashChange = () => setCategoryId(categoryFromHash());
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, []);

  const visibleCategories = useMemo(() => {
    if (!normalizedQuery) {
      return GALLERY_CATEGORIES.filter((category) => category.id === categoryId);
    }
    return GALLERY_CATEGORIES.map((category) => ({
      ...category,
      specimens: category.specimens.filter((specimen) => {
        const apiDescription = API_BY_NAME.get(specimen.name)?.schema.description ?? "";
        return [specimen.name, specimen.description, apiDescription]
          .some((value) => value.toLowerCase().includes(normalizedQuery));
      }),
    })).filter((category) => category.specimens.length > 0);
  }, [categoryId, normalizedQuery]);

  const shown = visibleCategories.reduce(
    (total, category) => total + category.specimens.length,
    0,
  );

  function selectCategory(nextCategory: GalleryCategoryId) {
    setCategoryId(nextCategory);
    setQuery("");
    window.history.replaceState(null, "", `#${nextCategory}`);
    window.scrollTo({ top: 0 });
  }

  return (
    <div className="demo-stage" data-theme={theme}>
      <div className="demo-shell">
        <header className="demo-header">
          <div className="demo-title-row">
            <div>
              <span>VALUZ OPEN SOURCE</span>
              <h1>A2UI Base Catalog</h1>
            </div>
            <code>{VALUZ_BASE_CATALOG_ID}</code>
          </div>
          <p>
            模型从这份词表里挑组件作答。下面是每个组件的实际渲染、协议字段和模型说明；示例数据固定，不连接业务数据。
          </p>
          <div className="demo-controls">
            <label className="demo-search">
              <Search aria-hidden="true" />
              <input
                aria-label="搜组件名或用途"
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜组件名或用途"
                type="search"
                value={query}
              />
            </label>
            <div className="demo-segmented" aria-label="预览宽度">
              <button aria-pressed={!narrow} onClick={() => setNarrow(false)} type="button">全宽</button>
              <button aria-pressed={narrow} onClick={() => setNarrow(true)} type="button">窄容器</button>
            </div>
            <button
              aria-pressed={theme === "dark"}
              className="demo-theme"
              onClick={() => setTheme((current) => current === "light" ? "dark" : "light")}
              type="button"
            >
              {theme === "light" ? "深色" : "浅色"}
            </button>
            <span className="demo-count" aria-live="polite">{shown} / 51</span>
          </div>
        </header>

        <div className="demo-workspace">
          <nav aria-label="分节" className="demo-nav">
            <div className="demo-nav-heading">
              <strong>基础组件</strong>
              <span>5</span>
            </div>
            <p>A2UI v0.9.1 通用词汇</p>
            <ul>
              {GALLERY_CATEGORIES.map((category) => (
                <li key={category.id}>
                  <button
                    aria-current={!normalizedQuery && category.id === categoryId ? "true" : undefined}
                    className={!normalizedQuery && category.id === categoryId ? "is-active" : undefined}
                    onClick={() => selectCategory(category.id)}
                    type="button"
                  >
                    <span>{category.label}</span>
                    <small>{category.specimens.length}</small>
                  </button>
                </li>
              ))}
            </ul>
          </nav>

          <main className="demo-content">
            {visibleCategories.length ? visibleCategories.map((category) => (
              <section className="demo-category" id={category.id} key={category.id}>
                <header className="demo-category-header">
                  <div>
                    <h2>{category.label}</h2>
                    <span>{category.specimens.length} 个组件</span>
                  </div>
                  <p>{category.description}</p>
                </header>
                <div className="demo-specimen-list">
                  {category.specimens.map((specimen) => (
                    <SpecimenCard
                      key={specimen.name}
                      narrow={narrow}
                      specimen={specimen}
                      theme={theme}
                    />
                  ))}
                </div>
              </section>
            )) : (
              <div className="demo-empty">
                <strong>没有匹配的组件</strong>
                <span>换一个名称或用途关键词试试。</span>
              </div>
            )}
          </main>
        </div>
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Demo />
  </StrictMode>,
);
