import {
  Suspense,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { Check, Copy, Search } from "lucide-react";

import { VALUZ_BASE_CATALOG_ID, valuzBaseComponentApis } from "../catalog";
import { ValuzA2UISurface } from "../react";
import "../styles.css";
import {
  GALLERY_CATEGORIES,
  type GalleryCategoryId,
  type GallerySpecimen,
} from "./gallery-data";
import {
  getRegisteredA2UIGalleryExtensions,
  subscribeA2UIGalleryExtensions,
} from "./registry";
import type {
  A2UIGalleryTheme,
} from "./types";
import "./gallery.css";

const DEFAULT_CATEGORY: GalleryCategoryId = "layout";
const BASE_KEY_PREFIX = "base/";
const EXTENSION_KEY_PREFIX = "extension/";

interface A2UIGalleryProps {
  /** Fill an AppShell content region instead of owning the browser viewport. */
  embedded?: boolean;
}

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

function baseKey(categoryId: GalleryCategoryId) {
  return `${BASE_KEY_PREFIX}${categoryId}`;
}

function extensionKey(groupId: string, sectionId: string) {
  return `${EXTENSION_KEY_PREFIX}${groupId}/${sectionId}`;
}

function keyFromHash(): string {
  if (typeof window === "undefined") return baseKey(DEFAULT_CATEGORY);
  const candidate = decodeURIComponent(window.location.hash.slice(1));
  if (GALLERY_CATEGORIES.some((category) => category.id === candidate)) {
    return baseKey(candidate as GalleryCategoryId);
  }
  if (candidate.startsWith(EXTENSION_KEY_PREFIX)) return candidate;
  return baseKey(DEFAULT_CATEGORY);
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
  theme: A2UIGalleryTheme;
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

      <div className="demo-specimen-preview" data-preview-theme={theme}>
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

export function A2UIGallery({ embedded = false }: A2UIGalleryProps) {
  const extensions = useSyncExternalStore(
    subscribeA2UIGalleryExtensions,
    getRegisteredA2UIGalleryExtensions,
    getRegisteredA2UIGalleryExtensions,
  );
  const [theme, setTheme] = useState<A2UIGalleryTheme>("light");
  const [narrow, setNarrow] = useState(false);
  const [query, setQuery] = useState("");
  const [selectedKey, setSelectedKey] = useState(keyFromHash);
  const normalizedQuery = query.trim().toLowerCase();

  useEffect(() => {
    const handleHashChange = () => setSelectedKey(keyFromHash());
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, []);

  let selectedExtension: {
    group: (typeof extensions)[number];
    section: (typeof extensions)[number]["sections"][number];
  } | null = null;
  for (const group of extensions) {
    for (const section of group.sections) {
      if (extensionKey(group.id, section.id) === selectedKey) {
        selectedExtension = { group, section };
        break;
      }
    }
    if (selectedExtension) break;
  }
  const requestedBaseId = selectedKey.startsWith(BASE_KEY_PREFIX)
    ? selectedKey.slice(BASE_KEY_PREFIX.length) as GalleryCategoryId
    : null;
  const selectedBaseId = selectedExtension
    ? null
    : GALLERY_CATEGORIES.some(({ id }) => id === requestedBaseId)
      ? requestedBaseId
      : DEFAULT_CATEGORY;

  const visibleCategories = useMemo(() => {
    if (!normalizedQuery) {
      return GALLERY_CATEGORIES.filter((category) => category.id === selectedBaseId);
    }
    return GALLERY_CATEGORIES.map((category) => ({
      ...category,
      specimens: category.specimens.filter((specimen) => {
        const apiDescription = API_BY_NAME.get(specimen.name)?.schema.description ?? "";
        return [specimen.name, specimen.description, apiDescription]
          .some((value) => value.toLowerCase().includes(normalizedQuery));
      }),
    })).filter((category) => category.specimens.length > 0);
  }, [normalizedQuery, selectedBaseId]);

  const baseCount = GALLERY_CATEGORIES.reduce(
    (total, category) => total + category.specimens.length,
    0,
  );
  const extensionCount = extensions.reduce(
    (total, group) => total + group.sections.reduce(
      (groupTotal, section) => groupTotal + section.componentCount,
      0,
    ),
    0,
  );
  const shown = selectedExtension
    ? selectedExtension.section.componentCount
    : visibleCategories.reduce(
        (total, category) => total + category.specimens.length,
        0,
      );

  function select(nextKey: string, hash: string) {
    setSelectedKey(nextKey);
    setQuery("");
    window.history.replaceState(null, "", `#${encodeURI(hash)}`);
    document.querySelector(".demo-stage")?.scrollTo({ top: 0 });
    window.scrollTo({ top: 0 });
  }

  const ExtensionView = selectedExtension?.section.View ?? null;

  return (
    <div className="demo-stage" data-embedded={embedded || undefined}>
      <div className="demo-shell">
        <header className="demo-header">
          <div className="demo-title-row">
            <div>
              <span>VALUZ OPEN SOURCE</span>
              <h1>A2UI Component Gallery</h1>
            </div>
            <code>{VALUZ_BASE_CATALOG_ID}</code>
          </div>
          <p>
            模型从这些组件词表里挑选界面。基础组件由 OSS 提供；当前分发版可以按菜单分组注册自己的组件，并在打开分组时按需加载。
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
              {theme === "light" ? "深色预览" : "浅色预览"}
            </button>
            <span className="demo-count" aria-live="polite">
              {shown} / {baseCount + extensionCount}
            </span>
          </div>
        </header>

        <div className="demo-workspace">
          <nav aria-label="组件分组" className="demo-nav">
            <div className="demo-nav-group">
              <div className="demo-nav-heading">
                <strong>基础组件</strong>
                <span>{GALLERY_CATEGORIES.length}</span>
              </div>
              <p>A2UI v0.9.1 通用词汇</p>
              <ul>
                {GALLERY_CATEGORIES.map((category) => {
                  const active = !normalizedQuery && selectedBaseId === category.id;
                  return (
                    <li key={category.id}>
                      <button
                        aria-current={active ? "true" : undefined}
                        className={active ? "is-active" : undefined}
                        onClick={() => select(baseKey(category.id), category.id)}
                        type="button"
                      >
                        <span>{category.label}</span>
                        <small>{category.specimens.length}</small>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>

            {extensions.map((group) => (
              <div className="demo-nav-group" key={group.id}>
                <div className="demo-nav-heading">
                  <strong>{group.label}</strong>
                  <span>{group.sections.length}</span>
                </div>
                <p>{group.description}</p>
                <ul>
                  {group.sections.map((section) => {
                    const key = extensionKey(group.id, section.id);
                    const active = !normalizedQuery && selectedKey === key;
                    return (
                      <li key={section.id}>
                        <button
                          aria-current={active ? "true" : undefined}
                          className={active ? "is-active" : undefined}
                          onClick={() => select(key, key)}
                          type="button"
                        >
                          <span>{section.label}</span>
                          <small>{section.componentCount}</small>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </nav>

          <main className="demo-content">
            {ExtensionView && selectedExtension ? (
              <section className="demo-category" id={selectedExtension.section.id}>
                <header className="demo-category-header">
                  <div>
                    <h2>{selectedExtension.section.label}</h2>
                    <span>{selectedExtension.section.componentCount} 个组件</span>
                  </div>
                  <p>{selectedExtension.section.description}</p>
                </header>
                <Suspense fallback={<div className="demo-empty"><span>正在加载组件分组…</span></div>}>
                  <ExtensionView theme={theme} narrow={narrow} query={query} />
                </Suspense>
              </section>
            ) : visibleCategories.length ? visibleCategories.map((category) => (
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
