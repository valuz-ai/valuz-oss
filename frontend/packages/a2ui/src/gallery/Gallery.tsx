import {
  Suspense,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { Check, Copy, Search } from "lucide-react";

import { VALUZ_BASE_CATALOG_ID, valuzBaseComponentApis } from "../catalog";
import {
  createValuzMessageProcessor,
  ValuzA2UISurface,
} from "../react";
import {
  CHART_PALETTES,
  type ChartPaletteName,
} from "../react/chart-theme";
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
import type { A2uiMessage } from "@a2ui/web_core/v0_9";
import "./gallery.css";

const DEFAULT_CATEGORY: GalleryCategoryId = "layout";
const BASE_KEY_PREFIX = "base/";
const EXTENSION_KEY_PREFIX = "extension/";
const EMBEDDED_SELECTION_STATE_KEY = "__valuzA2uiGallerySelection";
const EMBEDDED_SCROLL_STATE_KEY = "__valuzA2uiGalleryScroll";

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

const PALETTE_DESCRIPTIONS: Record<ChartPaletteName, {
  colorSystem: string;
  usage: string;
}> = {
  ocean: { colorSystem: "蓝色连续色阶", usage: "趋势、单指标强度" },
  orchid: { colorSystem: "紫色连续色阶", usage: "专题、次级连续序列" },
  emerald: { colorSystem: "绿色连续色阶", usage: "增长、效率、健康度" },
  spectrum: { colorSystem: "蓝—浅色—红发散", usage: "偏离、贡献、相关性" },
  sunset: { colorSystem: "紫—洋红—橙—黄连续", usage: "热度、风险、概率" },
  vivid: { colorSystem: "红橙黄绿青蓝紫分类", usage: "公司、资产、离散类别" },
  steel: { colorSystem: "石墨—钢蓝—银灰连续", usage: "中性指标、基准、低强调数据" },
  amber: { colorSystem: "深棕—琥珀—暖黄连续", usage: "估值、收益率、商品、热度" },
};

function PaletteShowcase({ theme }: { theme: A2UIGalleryTheme }) {
  return (
    <aside
      aria-label="图表色板"
      className="demo-palette-showcase valuz-a2ui"
      data-theme={theme}
    >
      <header>
        <div>
          <h3>图表色板</h3>
        </div>
        <p>八套固定色板 · 每套 11 色 · 运行时从中间向两侧取色</p>
      </header>
      <div className="demo-palette-grid">
        {(Object.keys(CHART_PALETTES) as ChartPaletteName[]).map((name) => (
          <section className="demo-palette-card" key={name}>
            <div className="demo-palette-card__title">
              <code>{name}</code>
              <span>{PALETTE_DESCRIPTIONS[name].colorSystem}</span>
            </div>
            <div aria-label={`${name} 的 11 个颜色`} className="demo-palette-strip">
              {CHART_PALETTES[name].map((color, index) => (
                <i
                  key={color}
                  style={{ background: `var(--va2-chart-${name}-${index + 1}, ${color})` }}
                  title={`${index + 1} · ${color}`}
                />
              ))}
            </div>
            <p>{PALETTE_DESCRIPTIONS[name].usage}</p>
          </section>
        ))}
      </div>
    </aside>
  );
}

function PaletteStrip({ name }: { name: ChartPaletteName }) {
  return (
    <span aria-hidden="true" className="demo-palette-mini-strip">
      {CHART_PALETTES[name].map((color, index) => (
        <i
          key={color}
          style={{ background: `var(--va2-chart-${name}-${index + 1}, ${color})` }}
        />
      ))}
    </span>
  );
}

export function PalettePicker({
  componentName,
  value,
  onValueChange,
}: {
  componentName: string;
  value: ChartPaletteName;
  onValueChange: (value: ChartPaletteName) => void;
}) {
  const detailsRef = useRef<HTMLDetailsElement>(null);
  return (
    <details className="demo-palette-picker" ref={detailsRef}>
      <summary aria-label={`${componentName} 色板，当前 ${value}`}>
        <PaletteStrip name={value} />
        <code>{value}</code>
      </summary>
      <div aria-label={`${componentName} 选择色板`} className="demo-palette-menu" role="radiogroup">
        {(Object.keys(CHART_PALETTES) as ChartPaletteName[]).map((name) => (
          <button
            aria-checked={name === value}
            key={name}
            onClick={() => {
              onValueChange(name);
              detailsRef.current?.removeAttribute("open");
            }}
            role="radio"
            type="button"
          >
            <PaletteStrip name={name} />
            <code>{name}</code>
            <span>{PALETTE_DESCRIPTIONS[name].colorSystem}</span>
            {name === value ? <Check aria-hidden="true" /> : null}
          </button>
        ))}
      </div>
    </details>
  );
}

function surfaceWithPalette(
  specimen: GallerySpecimen,
  palette: ChartPaletteName,
) {
  const surfaceId = `gallery-${specimen.name.toLowerCase()}-${palette}`;
  const processor = createValuzMessageProcessor();
  const components = specimen.components.map((component) => (
    propNames(component.component).includes("palette")
      ? { ...component, palette }
      : component
  ));
  processor.processMessages([
    { version: "v0.9.1", createSurface: { surfaceId, catalogId: VALUZ_BASE_CATALOG_ID } },
    { version: "v0.9.1", updateDataModel: { surfaceId, path: "/", value: specimen.data } },
    { version: "v0.9.1", updateComponents: { surfaceId, components } },
  ] satisfies A2uiMessage[]);
  return processor.model.getSurface(surfaceId)!;
}

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

function embeddedKeyFromHistory(): string {
  if (typeof window === "undefined") return baseKey(DEFAULT_CATEGORY);
  const candidate = window.history.state?.[EMBEDDED_SELECTION_STATE_KEY];
  return typeof candidate === "string" && (
    candidate.startsWith(BASE_KEY_PREFIX) ||
    candidate.startsWith(EXTENSION_KEY_PREFIX)
  )
    ? candidate
    : baseKey(DEFAULT_CATEGORY);
}

function embeddedScrollFromHistory(key: string): number {
  if (typeof window === "undefined") return 0;
  const positions = window.history.state?.[EMBEDDED_SCROLL_STATE_KEY];
  const value = positions && typeof positions === "object"
    ? (positions as Record<string, unknown>)[key]
    : undefined;
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value
    : 0;
}

function rememberEmbeddedScroll(key: string, top: number): void {
  const state = window.history.state ?? {};
  const previous = state[EMBEDDED_SCROLL_STATE_KEY];
  const positions = previous && typeof previous === "object"
    ? previous as Record<string, unknown>
    : {};
  window.history.replaceState({
    ...state,
    [EMBEDDED_SCROLL_STATE_KEY]: { ...positions, [key]: top },
  }, "");
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
  const hasPalette = specimen.componentNames.some((name) => propNames(name).includes("palette"));
  const defaultPalette = specimen.components.find((component) => (
    propNames(component.component).includes("palette") && typeof component.palette === "string"
  ))?.palette as ChartPaletteName | undefined;
  const [palette, setPalette] = useState<ChartPaletteName>(defaultPalette ?? "ocean");
  const surface = useMemo(
    () => hasPalette ? surfaceWithPalette(specimen, palette) : specimen.surface,
    [hasPalette, palette, specimen],
  );

  return (
    <article className="demo-specimen" data-component={specimen.name}>
      <header className="demo-specimen-header">
        <h3>{specimen.name}</h3>
        <span>{fields.length} 个字段</span>
        {specimen.componentNames.length > 1 ? <small>组合示例</small> : null}
        {hasPalette ? (
          <PalettePicker
            componentName={specimen.name}
            onValueChange={setPalette}
            value={palette}
          />
        ) : null}
      </header>

      <div className="demo-specimen-preview" data-preview-theme={theme}>
        <div className="demo-specimen-surface" data-narrow={narrow || undefined}>
          <ValuzA2UISurface surface={surface} theme={theme} />
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
  // Embedded Gallery lives inside the host application's router. Its category
  // selection is entry-local UI state and must not replace the host route
  // hash. Keeping it in history state restores the same category after a
  // component opens a routed document and the user closes it again.
  const [selectedKey, setSelectedKey] = useState(() => (
    embedded ? embeddedKeyFromHistory() : keyFromHash()
  ));
  const contentRef = useRef<HTMLElement>(null);
  const menuScrollPositions = useRef(new Map<string, number>([
    [selectedKey, embedded ? embeddedScrollFromHistory(selectedKey) : 0],
  ]));
  const restoringMenuScroll = useRef(false);
  const normalizedQuery = query.trim().toLowerCase();

  const currentScrollTop = useCallback((): number => {
    return embedded ? contentRef.current?.scrollTop ?? 0 : window.scrollY;
  }, [embedded]);

  const rememberCurrentMenuScroll = useCallback((): void => {
    const top = currentScrollTop();
    menuScrollPositions.current.set(selectedKey, top);
    if (embedded) rememberEmbeddedScroll(selectedKey, top);
  }, [currentScrollTop, embedded, selectedKey]);

  const changeSelectedKey = useCallback((nextKey: string): void => {
    if (nextKey === selectedKey) return;
    rememberCurrentMenuScroll();
    restoringMenuScroll.current = true;
    setSelectedKey(nextKey);
  }, [rememberCurrentMenuScroll, selectedKey]);

  useEffect(() => {
    if (embedded) return;
    const handleHashChange = () => changeSelectedKey(keyFromHash());
    window.addEventListener("hashchange", handleHashChange);
    return () => window.removeEventListener("hashchange", handleHashChange);
  }, [changeSelectedKey, embedded]);

  useLayoutEffect(() => {
    const top = menuScrollPositions.current.get(selectedKey) ?? 0;
    restoringMenuScroll.current = true;
    if (embedded) contentRef.current?.scrollTo({ top });
    else window.scrollTo({ top });

    // A previously opened lazy edition view is normally already cached, but
    // repeat once after layout so a Suspense boundary or chart measurement
    // cannot clamp the restored position to the outgoing menu's height.
    const frame = window.requestAnimationFrame(() => {
      if (embedded) contentRef.current?.scrollTo({ top });
      else window.scrollTo({ top });
      restoringMenuScroll.current = false;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [embedded, selectedKey]);

  useEffect(() => {
    if (!embedded) return;
    const content = contentRef.current;
    if (!content) return;
    // Layout refs may already be cleared by the time route unmount cleanup
    // runs. Capture the viewport before an internal link starts navigation.
    const rememberBeforeNavigation = (event: MouseEvent) => {
      const target = event.target;
      if (!(target instanceof Element) || !target.closest("a[href]")) return;
      const top = content.scrollTop;
      menuScrollPositions.current.set(selectedKey, top);
      rememberEmbeddedScroll(selectedKey, top);
    };
    content.addEventListener("click", rememberBeforeNavigation, true);
    return () => content.removeEventListener("click", rememberBeforeNavigation, true);
  }, [embedded, selectedKey]);

  useEffect(() => {
    if (embedded) return;
    const rememberWindowScroll = () => {
      if (!restoringMenuScroll.current) {
        menuScrollPositions.current.set(selectedKey, window.scrollY);
      }
    };
    window.addEventListener("scroll", rememberWindowScroll, { passive: true });
    return () => window.removeEventListener("scroll", rememberWindowScroll);
  }, [embedded, selectedKey]);

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
    changeSelectedKey(nextKey);
    setQuery("");
    if (embedded) {
      window.history.replaceState(
        {
          ...(window.history.state ?? {}),
          [EMBEDDED_SELECTION_STATE_KEY]: nextKey,
        },
        "",
      );
    } else {
      window.history.replaceState(null, "", `#${encodeURI(hash)}`);
    }
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
            <div className="demo-segmented" aria-label="预览主题">
              <button
                aria-pressed={theme === "light"}
                onClick={() => setTheme("light")}
                type="button"
              >
                浅色
              </button>
              <button
                aria-pressed={theme === "dark"}
                onClick={() => setTheme("dark")}
                type="button"
              >
                深色
              </button>
            </div>
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

          <main
            className="demo-content"
            onScroll={(event) => {
              if (!restoringMenuScroll.current) {
                const top = event.currentTarget.scrollTop;
                menuScrollPositions.current.set(selectedKey, top);
                if (embedded) rememberEmbeddedScroll(selectedKey, top);
              }
            }}
            ref={contentRef}
          >
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
                {category.id === "charts" && !normalizedQuery ? (
                  <PaletteShowcase theme={theme} />
                ) : null}
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
