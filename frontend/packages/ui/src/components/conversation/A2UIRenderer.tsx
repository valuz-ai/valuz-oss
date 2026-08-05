import {
  A2uiSurface,
  createBinderlessComponentImplementation,
  type ReactComponentImplementation,
} from "@a2ui/react/v0_9";
import {
  Catalog,
  MessageProcessor,
  type ComponentApi,
  type SurfaceModel,
} from "@a2ui/web_core/v0_9";
import * as OpenUI from "@openuidev/react-ui";
import { Modal as OpenUIModal } from "@openuidev/react-ui/Modal";
import { blockComponents, blockNames } from "@valuz/genui-blocks";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { createContext, useContext, useMemo, type CSSProperties, type ReactNode } from "react";
import { z } from "zod/v3";

import { completeJsonFragment } from "./partial-json";

export interface A2UIRendererProps {
  body: string;
}

type A2UIMessage = Record<string, unknown>;
type A2UIComponent = Record<string, unknown> & {
  id?: string;
  component?: string;
  type?: string;
};

type BuildChild = (id: string, basePath?: string) => ReactNode;

const CATALOG_ID = "openui";
const LEGACY_CATALOG_ID = "valuz";
const looseComponentSchema = z.object({}).passthrough() as unknown;

const OPENUI_COMPONENT_NAMES = [
  "Accordion",
  "AccordionItem",
  "AreaChart",
  "BarChart",
  "Button",
  "Buttons",
  "Callout",
  "Card",
  "CardHeader",
  "Carousel",
  "CheckBoxGroup",
  "CheckBoxItem",
  "CodeBlock",
  "Col",
  "DatePicker",
  "Form",
  "FormControl",
  "Grid",
  "Heading",
  "HorizontalBarChart",
  "Image",
  "ImageBlock",
  "ImageGallery",
  "Input",
  "KPI",
  "Label",
  "LineChart",
  "List",
  "ListBlock",
  "ListItem",
  "MarkDownRenderer",
  "Markdown",
  "Modal",
  "Paragraph",
  "PieChart",
  "Point",
  "RadarChart",
  "RadialChart",
  "RadioGroup",
  "RadioItem",
  "Row",
  "ScatterChart",
  "ScatterSeries",
  "Section",
  "Select",
  "SelectItem",
  "Separator",
  "Series",
  "SingleStackedBarChart",
  "Slice",
  "Slider",
  "Stack",
  "Steps",
  "StepsItem",
  "SwitchGroup",
  "SwitchItem",
  "TabItem",
  "Table",
  "Tabs",
  "Tag",
  "TagBlock",
  "Text",
  "TextArea",
  "TextCallout",
  "TextContent",
  "Title",
];

const BLOCK_BY_NAME = new Map(blockComponents.map((block) => [block.name, block]));

/**
 * Names retired in favour of a block, kept resolvable so payloads generated
 * before the change — and any prompt still naming them — keep rendering.
 * `map` reshapes the retired component's props onto the block's.
 */
const RETIRED_TO_BLOCK: Record<
  string,
  { block: string; map: (props: Record<string, unknown>) => Record<string, unknown> }
> = {
  FinanceMetric: {
    block: "StatsCard",
    map: (props) => ({
      label: readText(props.label ?? props.name ?? props.title),
      value: [readText(props.value ?? props.latest ?? props.text), readText(props.unit)]
        .filter(Boolean)
        .join(" "),
      delta: readText(props.changePct ?? props.change_pct ?? props.pct ?? props.change),
      description: readText(props.description),
    }),
  },
};

/**
 * Every name the A2UI runtime will accept: the hand-listed OpenUI names above,
 * every block in @valuz/genui-blocks, and the retired names.
 *
 * Derived rather than listed, because a name registered here but missing from
 * the block registry (or the reverse) fails silently — the model is told about
 * a component that renders as bare text, or a rendered component is never
 * offered to the model. Adding a block to that package is the only edit needed
 * to reach this protocol. Retired names stay registered so the runtime still
 * accepts them; the adapter maps them onto their replacement.
 */
const A2UI_COMPONENT_NAMES = [
  ...OPENUI_COMPONENT_NAMES,
  ...blockNames,
  ...Object.keys(RETIRED_TO_BLOCK),
];

/**
 * Render a block from an A2UI component model.
 *
 * One adapter serves every block because the two protocols differ in exactly
 * one place: children. A2UI passes child ids that `buildChild` turns into
 * React nodes, while a block expects to call `renderNode(props.children)`.
 * Handing it a `renderNode` that returns the already-built nodes closes the
 * gap; scalar and array props pass straight through, and the block's own zod
 * schema supplies defaults and coercion.
 */
function renderBlockComponent(
  name: string,
  rawProps: Record<string, unknown>,
  buildChild: BuildChild,
): ReactNode | null {
  const retired = RETIRED_TO_BLOCK[name];
  const blockName = retired ? retired.block : name;
  const block = BLOCK_BY_NAME.get(blockName);
  if (!block) return null;

  const props = retired ? retired.map(rawProps) : rawProps;
  const built = readChildren(props, buildChild);

  // Prefer the parsed value for its defaults and coercion, but never let a
  // schema miss blank the component: model output is untrusted, and a missing
  // optional field should degrade to an empty slot rather than a dropped block.
  const parsed = block.props.safeParse({ ...props, children: [] });
  const resolved: Record<string, unknown> = {
    ...props,
    ...(parsed.success ? (parsed.data as Record<string, unknown>) : {}),
    children: built,
  };

  const Impl = block.component as (renderProps: {
    props: Record<string, unknown>;
    renderNode: (value: unknown) => ReactNode;
  }) => ReactNode;

  return <Impl props={resolved} renderNode={(value) => value as ReactNode} />;
}

const createA2UIComponent = createBinderlessComponentImplementation as unknown as (
  api: ComponentApi,
  render: (props: {
    context: {
      componentModel: {
        type: string;
        properties: Record<string, unknown>;
      };
    };
    buildChild: BuildChild;
  }) => ReactNode,
) => ReactComponentImplementation;
const openuiA2UICatalog = new Catalog(CATALOG_ID, createOpenUIComponents());
const legacyOpenuiA2UICatalog = new Catalog(
  LEGACY_CATALOG_ID,
  createOpenUIComponents(),
);

/**
 * id → component, for resolving references that A2UI expresses as ids.
 *
 * A2UI nests by id: a chart carries `children: ["sector-series"]`, and the
 * Series is a sibling component elsewhere in the message. The runtime resolves
 * that for *rendering* (via `buildChild`), but chart data is not rendered — it
 * is read out of props — so a chart whose series arrives by reference had no
 * way to reach it. It rendered its category axis with no series at all: a tall
 * empty plot rather than an error.
 */
const A2UIComponentIndex = createContext<Map<string, Record<string, unknown>>>(new Map());

export function A2UIRenderer({ body }: A2UIRendererProps) {
  const surfaces = useMemo(() => buildSurfaces(body), [body]);
  const index = useMemo(() => buildComponentIndex(body), [body]);
  if (!surfaces.length) return null;

  return (
    <A2UIComponentIndex.Provider value={index}>
      <div data-slot="a2ui-renderer">
        {surfaces.map((surface) => (
          <A2uiSurface key={surface.id} surface={surface} />
        ))}
      </div>
    </A2UIComponentIndex.Provider>
  );
}

function buildComponentIndex(body: string): Map<string, Record<string, unknown>> {
  const index = new Map<string, Record<string, unknown>>();
  for (const message of normalizeMessages(parseA2UIMessages(body))) {
    const update = (message as Record<string, unknown>).updateComponents;
    if (!isRecord(update)) continue;
    for (const component of toArray(update.components)) {
      if (!isRecord(component)) continue;
      const id = readText(component.id);
      if (id) index.set(id, component);
    }
  }
  return index;
}

function buildSurfaces(body: string): SurfaceModel<ReactComponentImplementation>[] {
  const messages = normalizeMessages(parseA2UIMessages(body));
  if (!messages.length) return [];

  const processor = new MessageProcessor<ReactComponentImplementation>([
    openuiA2UICatalog,
    legacyOpenuiA2UICatalog,
  ]);

  try {
    processor.processMessages(messages as never);
  } catch (error) {
    if (import.meta.env.DEV) {
      console.warn("[genui] failed to render A2UI payload", error);
    }
    return [];
  }

  return Array.from(processor.model.surfacesMap.values());
}

function createOpenUIComponents(): ReactComponentImplementation[] {
  return A2UI_COMPONENT_NAMES.map((name) => {
    const api = { name, schema: looseComponentSchema } as unknown as ComponentApi;
    return createA2UIComponent(
      api,
      ({ context, buildChild }) => (
        <OpenUIComponent
          name={normalizeOpenUIComponentName(context.componentModel.type)}
          props={context.componentModel.properties}
          buildChild={buildChild}
        />
      ),
    );
  });
}

function OpenUIComponent({
  name,
  props,
  buildChild,
}: {
  name: string;
  props: Record<string, unknown>;
  buildChild: BuildChild;
}) {
  const children = readChildren(props, buildChild);
  const componentIndex = useContext(A2UIComponentIndex);
  const resolveRefs = (value: unknown): unknown[] =>
    materializeRefs(value, componentIndex);

  switch (name) {
    case "Stack":
      return (
        <StackBox
          component="stack"
          direction={readString(props.direction) ?? "column"}
          gap={readString(props.gap) ?? "m"}
          align={readString(props.align) ?? "stretch"}
          justify={readString(props.justify) ?? "start"}
          wrap={typeof props.wrap === "boolean" ? props.wrap : true}
        >
          {children}
        </StackBox>
      );
    case "Grid":
      return (
        <StackBox component="grid" direction="row" gap="m" wrap>
          {children}
        </StackBox>
      );
    case "Row":
      return (
        <StackBox
          component="row"
          direction="row"
          gap={readString(props.gap) ?? "m"}
          wrap={typeof props.wrap === "boolean" ? props.wrap : true}
        >
          {children}
        </StackBox>
      );
    case "Card":
      return (
        <CardBox variant={readVariant(props.variant)}>
          {children}
        </CardBox>
      );
    case "Section":
      return (
        <SectionBox
          title={readText(props.title)}
          description={readText(props.description ?? props.subtitle)}
        >
          {children}
        </SectionBox>
      );
    case "CardHeader":
      return (
        <OpenUI.CardHeader
          title={readText(props.title ?? props.text)}
          subtitle={readText(props.subtitle ?? props.description)}
        />
      );
    case "Text":
    case "Paragraph":
    case "Heading":
    case "Title":
    case "TextContent": {
      const content =
        props.text !== undefined || props.value !== undefined
          ? readText(props.text ?? props.value)
          : children;
      return (
        <TextBlock size={readString(props.size) ?? defaultTextSizeForComponent(name)}>
          {content}
        </TextBlock>
      );
    }
    case "Markdown":
    case "MarkDownRenderer":
      return (
        <OpenUI.MarkDownRenderer
          textMarkdown={readText(props.textMarkdown ?? props.text)}
        />
      );
    case "Callout":
      return (
        <OpenUI.Callout
          variant={readCalloutVariant(props.variant)}
          title={readText(props.title)}
          description={readText(props.description ?? props.text)}
        />
      );
    case "TextCallout":
      return (
        <OpenUI.TextCallout
          variant={readTextCalloutVariant(props.variant)}
          title={readText(props.title)}
          description={readText(props.description ?? props.text)}
        />
      );
    case "CodeBlock":
      return (
        <OpenUI.CodeBlock
          language={readString(props.language) ?? "text"}
          codeString={readText(props.codeString ?? props.code ?? props.text)}
        />
      );
    case "Image":
      return (
        <OpenUI.Image
          src={readString(props.src ?? props.url) ?? ""}
          alt={readString(props.alt ?? props.description) ?? ""}
        />
      );
    case "ImageBlock":
      return (
        <OpenUI.ImageBlock
          src={readString(props.src ?? props.url) ?? ""}
          alt={readString(props.alt ?? props.description) ?? ""}
        />
      );
    case "ImageGallery":
      return <OpenUI.ImageGallery images={readImages(props.images)} />;
    case "Table":
      return <MappedTable props={props} buildChild={buildChild} />;
    case "BarChart": {
      const data = buildChartData(props, resolveRefs);
      if (!data.length) return null;
      return (
        <OpenUI.BarChartCondensed
          data={data}
          categoryKey="category"
          variant={readString(props.variant) as never}
          xAxisLabel={readString(props.xLabel)}
          yAxisLabel={readString(props.yLabel)}
          isAnimationActive={false}
        />
      );
    }
    case "LineChart": {
      const data = buildChartData(props, resolveRefs);
      if (!data.length) return null;
      return (
        <OpenUI.LineChartCondensed
          data={data}
          categoryKey="category"
          variant={readString(props.variant) as never}
          xAxisLabel={readString(props.xLabel)}
          yAxisLabel={readString(props.yLabel)}
          isAnimationActive={false}
        />
      );
    }
    case "AreaChart": {
      const data = buildChartData(props, resolveRefs);
      if (!data.length) return null;
      return (
        <OpenUI.AreaChartCondensed
          data={data}
          categoryKey="category"
          variant={readString(props.variant) as never}
          xAxisLabel={readString(props.xLabel)}
          yAxisLabel={readString(props.yLabel)}
          isAnimationActive={false}
        />
      );
    }
    case "HorizontalBarChart": {
      const data = buildChartData(props, resolveRefs);
      if (!data.length) return null;
      return (
        <OpenUI.HorizontalBarChart
          data={data}
          categoryKey="category"
          variant={readString(props.variant) as never}
          isAnimationActive={false}
          height={readNumber(props.height)}
        />
      );
    }
    case "RadarChart": {
      const data = buildChartData(props, resolveRefs);
      if (!data.length) return null;
      return (
        <OpenUI.RadarChart
          data={data}
          categoryKey="category"
          variant={readString(props.variant) as never}
          isAnimationActive={false}
        />
      );
    }
    case "PieChart":
      return <PieLikeChart Component={OpenUI.PieChart} props={props} />;
    case "RadialChart":
      return <PieLikeChart Component={OpenUI.RadialChart} props={props} />;
    case "SingleStackedBarChart": {
      const data = buildSliceData(props);
      if (!data.length) return null;
      return (
        <OpenUI.SingleStackedBar
          data={data}
          categoryKey="category"
          dataKey="value"
          animated={false}
        />
      );
    }
    case "ScatterChart": {
      const data = buildScatterData(props);
      if (!data.length) return null;
      return (
        <OpenUI.ScatterChart
          data={data}
          xAxisDataKey="x"
          yAxisDataKey="y"
          isAnimationActive={false}
        />
      );
    }
    case "Form":
      return (
        <div role="form" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {readRefs(props.fields, buildChild)}
          {readRefs(props.buttons, buildChild)}
          {children}
        </div>
      );
    case "FormControl":
      return <OpenUI.FormControl>{children}</OpenUI.FormControl>;
    case "Label":
      return <OpenUI.Label>{readText(props.text ?? props.label)}</OpenUI.Label>;
    case "Input":
      return (
        <OpenUI.Input
          name={readString(props.name)}
          placeholder={readString(props.placeholder) ?? ""}
          type={readString(props.type) ?? "text"}
          defaultValue={readString(props.value)}
        />
      );
    case "TextArea":
      return (
        <OpenUI.TextArea
          name={readString(props.name)}
          placeholder={readString(props.placeholder) ?? ""}
          rows={readNumber(props.rows) ?? 3}
          defaultValue={readString(props.value)}
        />
      );
    case "Select":
      return <MappedSelect props={props} buildChild={buildChild} />;
    case "Slider":
      return (
        <OpenUI.SliderBlock
          label={readString(props.label) ?? readString(props.name) ?? ""}
          name={readString(props.name) ?? ""}
          variant="continuous"
          min={readNumber(props.min) ?? 0}
          max={readNumber(props.max) ?? 100}
          step={readNumber(props.step) ?? 1}
          defaultValue={[readNumber(props.defaultValue ?? props.value) ?? 0]}
        />
      );
    case "CheckBoxGroup":
      return (
        <OpenUI.CheckBoxGroup>
          {readRefs(props.items ?? props.children, buildChild) as never}
        </OpenUI.CheckBoxGroup>
      );
    case "CheckBoxItem":
      return (
        <OpenUI.CheckBoxItem
          name={readString(props.name)}
          label={readText(props.label)}
          description={readText(props.description)}
          defaultChecked={readBoolean(props.defaultChecked ?? props.checked)}
        />
      );
    case "RadioGroup":
      return (
        <OpenUI.RadioGroup name={readString(props.name)} defaultValue={readString(props.defaultValue)}>
          {readRefs(props.items ?? props.children, buildChild) as never}
        </OpenUI.RadioGroup>
      );
    case "RadioItem":
      return (
        <OpenUI.RadioItem
          value={readString(props.value) ?? ""}
          label={readText(props.label)}
          description={readText(props.description)}
        />
      );
    case "SwitchGroup":
      return (
        <OpenUI.SwitchGroup>
          {readRefs(props.items ?? props.children, buildChild) as never}
        </OpenUI.SwitchGroup>
      );
    case "SwitchItem":
      return (
        <OpenUI.SwitchItem
          name={readString(props.name)}
          label={readText(props.label)}
          description={readText(props.description)}
          defaultChecked={readBoolean(props.defaultChecked ?? props.checked)}
        />
      );
    case "Button":
      return (
        <OpenUI.Button variant={readButtonVariant(props.variant)} size="medium">
          {readText(props.label ?? props.text)}
        </OpenUI.Button>
      );
    case "Buttons":
      return (
        <OpenUI.Buttons variant={readString(props.direction) === "column" ? "vertical" : "horizontal"}>
          {readRefs(props.buttons ?? props.children, buildChild) as never}
        </OpenUI.Buttons>
      );
    case "Tabs":
      return <MappedTabs props={props} buildChild={buildChild} />;
    case "Accordion":
      return <MappedAccordion props={props} buildChild={buildChild} />;
    case "Steps":
      return <MappedSteps props={props} buildChild={buildChild} />;
    case "Carousel":
      return <MappedCarousel props={props} buildChild={buildChild} />;
    case "Separator":
      return <OpenUI.Separator orientation={readString(props.orientation) as never} />;
    case "Tag":
      return (
        <OpenUI.Tag
          text={readText(props.text ?? props.label)}
          size={readString(props.size) as never}
          variant={readTagVariant(props.variant)}
        />
      );
    case "TagBlock":
      return (
        <OpenUI.TagBlock>
          {readTags(props.tags).map((tag) => (
            <OpenUI.Tag key={tag} text={tag} />
          ))}
        </OpenUI.TagBlock>
      );
    case "Modal":
      return (
        <OpenUIModal title={readText(props.title)} open={readBoolean(props.open) ?? false} onOpenChange={() => undefined}>
          {children}
        </OpenUIModal>
      );
    default: {
      // Blocks resolve here rather than as cases above: they are registered by
      // name from the block catalog, so a switch arm per block would be a
      // second list to keep in step with the first.
      const block = renderBlockComponent(name, props, buildChild);
      if (block) return block;
      return <TextBlock>{readText(props.text ?? props.label ?? name)}</TextBlock>;
    }
  }
}

function StackBox({
  children,
  component,
  direction,
  gap = "m",
  align = "stretch",
  justify = "start",
  wrap,
}: {
  children: ReactNode;
  component: string;
  direction: string;
  gap?: string;
  align?: string;
  justify?: string;
  wrap?: boolean;
}) {
  return (
    <div
      data-a2ui-component={component}
      style={{
        display: "flex",
        flexDirection: direction === "row" ? "row" : "column",
        flexWrap: wrap ? "wrap" : undefined,
        gap: gapToCss(gap),
        alignItems: alignToCss(align),
        justifyContent: justifyToCss(justify),
      }}
    >
      {children}
    </div>
  );
}

function CardBox({
  children,
  variant,
}: {
  children: ReactNode;
  variant: "card" | "clear" | "sunk";
}) {
  return (
    <OpenUI.Card variant={variant} width="full">
      <div
        data-a2ui-card-content
        style={{
          display: "flex",
          minWidth: 0,
          flexDirection: "column",
          gap: "var(--openui-space-m)",
        }}
      >
        {children}
      </div>
    </OpenUI.Card>
  );
}

function SectionBox({
  children,
  description,
  title,
}: {
  children: ReactNode;
  description?: string;
  title?: string;
}) {
  return (
    <section
      data-a2ui-component="section"
      style={{
        display: "flex",
        minWidth: 0,
        flexDirection: "column",
        gap: "var(--openui-space-m)",
      }}
    >
      {title ? (
        <OpenUI.CardHeader title={title} subtitle={description} />
      ) : null}
      {children}
    </section>
  );
}

function TextBlock({
  children,
  size = "default",
}: {
  children: ReactNode;
  size?: string;
}) {
  return (
    <OpenUI.TextContent variant="clear">
      <span data-a2ui-text-size={size} style={textStyleForSize(size)}>
        {children}
      </span>
    </OpenUI.TextContent>
  );
}

function defaultTextSizeForComponent(name: string): string {
  if (name === "Heading" || name === "Title") return "large-heavy";
  return "default";
}

function textStyleForSize(size: string): CSSProperties {
  const base: CSSProperties = {
    display: "block",
    minWidth: 0,
    overflowWrap: "anywhere",
    letterSpacing: 0,
  };

  switch (size) {
    case "small":
      return {
        ...base,
        color: "var(--openui-text-neutral-secondary)",
        font: "var(--openui-text-body-sm)",
      };
    case "small-heavy":
      return {
        ...base,
        color: "var(--openui-text-neutral-primary)",
        font: "var(--openui-text-body-sm-heavy)",
      };
    case "medium-heavy":
      return {
        ...base,
        color: "var(--openui-text-neutral-primary)",
        font: "var(--openui-text-body-default-heavy)",
        fontVariantNumeric: "tabular-nums",
      };
    case "large":
      return {
        ...base,
        color: "var(--openui-text-neutral-primary)",
        font: "var(--openui-text-body-lg)",
      };
    case "large-heavy":
      return {
        ...base,
        color: "var(--openui-text-neutral-primary)",
        font: "var(--openui-text-heading-md)",
      };
    default:
      return {
        ...base,
        color: "var(--openui-text-neutral-primary)",
        font: "var(--openui-text-body-default)",
      };
  }
}

function MappedTable({
  props,
  buildChild,
}: {
  props: Record<string, unknown>;
  buildChild: BuildChild;
}) {
  const columns = readColumns(props, buildChild);
  const rowCount = columns.length
    ? Math.max(...columns.map((column) => column.values.length), 0)
    : 0;

  if (!columns.length) return null;

  return (
    <OpenUI.ScrollableTable>
      <OpenUI.TableHeader>
        <OpenUI.TableRow>
          {columns.map((column) => (
            <OpenUI.TableHead key={column.label}>{column.label}</OpenUI.TableHead>
          ))}
        </OpenUI.TableRow>
      </OpenUI.TableHeader>
      <OpenUI.TableBody>
        {Array.from({ length: rowCount }, (_, rowIndex) => (
          <OpenUI.TableRow key={rowIndex}>
            {columns.map((column) => (
              <OpenUI.TableCell key={column.label}>
                {renderCell(column.values[rowIndex], buildChild)}
              </OpenUI.TableCell>
            ))}
          </OpenUI.TableRow>
        ))}
      </OpenUI.TableBody>
    </OpenUI.ScrollableTable>
  );
}

function MappedSelect({
  props,
  buildChild,
}: {
  props: Record<string, unknown>;
  buildChild: BuildChild;
}) {
  const items = readOptionItems(props.items ?? props.children, buildChild);
  return (
    <OpenUI.Select name={readString(props.name)} defaultValue={readString(props.value)}>
      <OpenUI.SelectTrigger>
        <OpenUI.SelectValue placeholder={readString(props.placeholder) ?? "Select..."} />
      </OpenUI.SelectTrigger>
      <OpenUI.SelectContent>
        {items.map((item) => (
          <OpenUI.SelectItem key={item.value} value={item.value}>
            {item.label}
          </OpenUI.SelectItem>
        ))}
      </OpenUI.SelectContent>
    </OpenUI.Select>
  );
}

function MappedTabs({
  props,
  buildChild,
}: {
  props: Record<string, unknown>;
  buildChild: BuildChild;
}) {
  const items = readTabItems(props.items ?? props.children);
  if (!items.length) return null;
  const firstValue = items[0]?.value ?? "";
  return (
    <OpenUI.Tabs defaultValue={firstValue}>
      <OpenUI.TabsList>
        {items.map((item) => (
          <OpenUI.TabsTrigger key={item.value} value={item.value} text={item.trigger} />
        ))}
      </OpenUI.TabsList>
      {items.map((item) => (
        <OpenUI.TabsContent key={item.value} value={item.value}>
          {readRefs(item.content, buildChild)}
        </OpenUI.TabsContent>
      ))}
    </OpenUI.Tabs>
  );
}

function MappedAccordion({
  props,
  buildChild,
}: {
  props: Record<string, unknown>;
  buildChild: BuildChild;
}) {
  const items = readTabItems(props.items ?? props.children);
  if (!items.length) return null;
  return (
    <OpenUI.Accordion type="single" collapsible defaultValue={items[0]?.value}>
      {items.map((item) => (
        <OpenUI.AccordionItem key={item.value} value={item.value}>
          <OpenUI.AccordionTrigger text={item.trigger} />
          <OpenUI.AccordionContent>
            {readRefs(item.content, buildChild)}
          </OpenUI.AccordionContent>
        </OpenUI.AccordionItem>
      ))}
    </OpenUI.Accordion>
  );
}

function MappedSteps({
  props,
  buildChild,
}: {
  props: Record<string, unknown>;
  buildChild: BuildChild;
}) {
  const items = toArray(props.items ?? props.children);
  return (
    <OpenUI.Steps>
      {items.map((item, index) => {
        const record = isRecord(item) ? item : {};
        return (
          <OpenUI.StepsItem
            key={`${readString(record.value) ?? index}`}
            number={index + 1}
            title={readText(record.title ?? record.label)}
            details={
              readRefs(record.details ?? record.content, buildChild) ||
              readText(record.details ?? record.description)
            }
          />
        );
      })}
    </OpenUI.Steps>
  );
}

function MappedCarousel({
  props,
  buildChild,
}: {
  props: Record<string, unknown>;
  buildChild: BuildChild;
}) {
  const items = toArray(props.items ?? props.children);
  return (
    <OpenUI.Carousel showButtons variant={readString(props.variant) as never}>
      <OpenUI.CarouselContent>
        {items.map((item, index) => (
          <OpenUI.CarouselItem key={index}>
            {readRefs(item, buildChild) || renderCell(item, buildChild)}
          </OpenUI.CarouselItem>
        ))}
      </OpenUI.CarouselContent>
      <OpenUI.CarouselPrevious icon={<ChevronLeft />} />
      <OpenUI.CarouselNext icon={<ChevronRight />} />
    </OpenUI.Carousel>
  );
}

function PieLikeChart({
  Component,
  props,
}: {
  Component: typeof OpenUI.PieChart | typeof OpenUI.RadialChart;
  props: Record<string, unknown>;
}) {
  const data = buildSliceData(props);
  if (!data.length) return null;

  return (
    <Component
      data={data}
      categoryKey="category"
      dataKey="value"
      variant={readString(props.variant) as never}
      isAnimationActive={false}
    />
  );
}

function parseA2UIMessages(body: string): A2UIMessage[] {
  const trimmed = body.trim();
  if (!trimmed) return [];

  const parsed = safeJsonParse(trimmed);
  if (Array.isArray(parsed)) return parsed.filter(isRecord);
  if (isRecord(parsed) && Array.isArray(parsed.messages)) {
    return parsed.messages.filter(isRecord);
  }
  if (isRecord(parsed) && looksLikeA2UIMessage(parsed)) return [parsed];

  const lines = trimmed.split(/\r?\n/).map((line) => line.trim());
  const messages: A2UIMessage[] = [];
  for (const line of lines) {
    if (!line.startsWith("{")) continue;
    if (line.endsWith("}")) {
      const message = safeJsonParse(line);
      if (isRecord(message)) messages.push(message);
      continue;
    }
    // A line still arriving. One `updateComponents` message usually carries the
    // entire tree, so waiting for its closing brace means the document appears
    // in one jump at the end — no streaming at all. Salvaging the components
    // that are already complete is what makes it build up instead.
    const salvaged = salvagePartialComponents(line);
    if (salvaged) messages.push(salvaged);
  }
  return messages;
}

/**
 * Recover what has arrived of a half-written `updateComponents` line.
 *
 * `completeJsonFragment` closes the fragment rather than waiting for the
 * model to close it, so a component still being typed renders with the fields
 * it has — text grows as it streams instead of appearing whole. It never
 * fabricates: an unfinished key or number is dropped, only an unfinished
 * string value is kept and closed.
 */
function salvagePartialComponents(line: string): A2UIMessage | null {
  const completed = completeJsonFragment(line);
  if (!completed) return null;
  const parsed = safeJsonParse(completed);
  if (!isRecord(parsed)) return null;
  const update = parsed.updateComponents;
  if (!isRecord(update) || !Array.isArray(update.components)) return null;
  return update.components.some(isRecord) ? (parsed as A2UIMessage) : null;
}

function normalizeMessages(messages: A2UIMessage[]): A2UIMessage[] {
  const normalized = messages.map(normalizeMessage).filter(Boolean) as A2UIMessage[];
  const defaultSurfaceId = inferSurfaceId(normalized);
  if (!normalized.some((message) => isRecord(message.createSurface))) {
    normalized.unshift({
      version: "v0.9",
      createSurface: { surfaceId: defaultSurfaceId, catalogId: CATALOG_ID },
    });
  }
  if (!normalized.some((message) => isRecord(message.updateComponents))) {
    return normalized;
  }
  return ensureRootComponent(normalized, defaultSurfaceId);
}

function normalizeMessage(message: A2UIMessage): A2UIMessage | null {
  if (isRecord(message.createSurface)) {
    return {
      ...message,
      createSurface: {
        ...message.createSurface,
        catalogId: normalizeCatalogId(message.createSurface.catalogId),
      },
    };
  }

  if (!isRecord(message.updateComponents)) return message;

  const components = normalizeComponentList(
    toArray(message.updateComponents.components).filter(isA2UIComponent),
  );

  return {
    ...message,
    updateComponents: {
      ...message.updateComponents,
      components,
    },
  };
}

function normalizeComponentList(components: A2UIComponent[]): A2UIComponent[] {
  const flattened: A2UIComponent[] = [];
  for (const component of components) {
    flattened.push(...flattenComponent(component));
  }
  return flattened;
}

function flattenComponent(component: A2UIComponent): A2UIComponent[] {
  const merged = mergeProps(component);
  const id = readString(merged.id) ?? makeComponentId(merged);
  const normalized: A2UIComponent = {
    ...merged,
    id,
    component: normalizeOpenUIComponentName(merged.component ?? merged.type),
  };
  delete normalized.type;
  delete normalized.props;

  const extra: A2UIComponent[] = [];
  for (const key of structuralKeysForComponent(normalized.component)) {
    const normalizedNested = normalizeNestedRefs(normalized[key], id);
    if (normalizedNested.value !== undefined) normalized[key] = normalizedNested.value;
    extra.push(...normalizedNested.components);
  }

  return [normalized, ...extra];
}

function normalizeNestedRefs(
  value: unknown,
  parentId: string,
): { value: unknown; components: A2UIComponent[] } {
  if (Array.isArray(value)) {
    const components: A2UIComponent[] = [];
    const refs = value.map((item, index) => {
      const normalized = normalizeNestedRefs(item, `${parentId}_${index}`);
      components.push(...normalized.components);
      return normalized.value;
    });
    return { value: refs, components };
  }

  if (isA2UIComponent(value)) {
    const component = mergeProps(value);
    const id = readString(component.id) ?? makeComponentId(component, parentId);
    const flattened = flattenComponent({ ...component, id });
    return { value: id, components: flattened };
  }

  return { value, components: [] };
}

function ensureRootComponent(
  messages: A2UIMessage[],
  defaultSurfaceId: string,
): A2UIMessage[] {
  const hasRoot = messages.some(
    (message) =>
      isRecord(message.updateComponents) &&
      toArray(message.updateComponents.components).some(
        (component) => isRecord(component) && component.id === "root",
      ),
  );
  if (hasRoot) return messages;

  const firstComponentId = messages
    .flatMap((message) =>
      isRecord(message.updateComponents)
        ? toArray(message.updateComponents.components)
        : [],
    )
    .find((component) => isRecord(component) && typeof component.id === "string");

  const childId = isRecord(firstComponentId) ? readString(firstComponentId.id) : null;
  if (!childId) return messages;

  return [
    ...messages,
    {
      version: "v0.9",
      updateComponents: {
        surfaceId: defaultSurfaceId,
        components: [{ id: "root", component: "Stack", children: [childId] }],
      },
    },
  ];
}

function inferSurfaceId(messages: A2UIMessage[]): string {
  for (const message of messages) {
    if (isRecord(message.createSurface)) {
      const surfaceId = readString(message.createSurface.surfaceId);
      if (surfaceId) return surfaceId;
    }
    if (isRecord(message.updateComponents)) {
      const surfaceId = readString(message.updateComponents.surfaceId);
      if (surfaceId) return surfaceId;
    }
    if (isRecord(message.updateDataModel)) {
      const surfaceId = readString(message.updateDataModel.surfaceId);
      if (surfaceId) return surfaceId;
    }
  }
  return "main";
}

function mergeProps(component: A2UIComponent): A2UIComponent {
  const props = isRecord(component.props) ? component.props : {};
  return { ...component, ...props };
}

function readChildren(props: Record<string, unknown>, buildChild: BuildChild) {
  return readRefs(props.children, buildChild);
}

function readRefs(value: unknown, buildChild: BuildChild): ReactNode {
  const refs = toArray(value);
  if (!refs.length) return null;
  return refs.map((item, index) => {
    if (typeof item === "string") return buildChild(item);
    if (isA2UIComponent(item)) {
      return (
        <OpenUIComponent
          key={readString(item.id) ?? index}
          name={normalizeOpenUIComponentName(item.component ?? item.type)}
          props={mergeProps(item)}
          buildChild={buildChild}
        />
      );
    }
    return <span key={index}>{renderCell(item, buildChild)}</span>;
  });
}

function readColumns(props: Record<string, unknown>, buildChild: BuildChild) {
  const explicitColumns = toArray(props.columns ?? props.children);
  if (explicitColumns.length) {
    return explicitColumns
      .map((column) => readColumn(column, buildChild))
      .filter((column): column is { label: string; values: unknown[] } => Boolean(column));
  }

  const labels = toArray(props.labels ?? props.headers);
  const rows = toArray(props.rows);
  return labels.map((label, columnIndex) => ({
    label: readText(label),
    values: rows.map((row) => readCell(row, columnIndex)),
  }));
}

function readColumn(column: unknown, buildChild: BuildChild) {
  if (typeof column === "string") {
    return { label: column, values: [] };
  }
  if (!isRecord(column)) return null;
  const record = isA2UIComponent(column) ? mergeProps(column) : column;
  if (typeof record.id === "string" && !record.label && !record.data && !record.values) {
    return { label: record.id, values: [buildChild(record.id)] };
  }
  return {
    label: readText(record.label ?? record.title ?? record.name),
    values: toArray(record.data ?? record.values ?? record.children),
  };
}

function readOptionItems(value: unknown, buildChild: BuildChild) {
  return toArray(value)
    .map((item) => {
      if (typeof item === "string") return { value: item, label: item };
      if (!isRecord(item)) return null;
      const record = isA2UIComponent(item) ? mergeProps(item) : item;
      if (typeof record.id === "string" && !record.value && !record.label) {
        return { value: record.id, label: buildChild(record.id) };
      }
      const valueText =
        readString(record.value) ?? readString(record.name) ?? readText(record.label);
      return {
        value: valueText,
        label: readText(record.label ?? record.text ?? valueText),
      };
    })
    .filter((item): item is { value: string; label: ReactNode } => Boolean(item?.value));
}

function readTabItems(
  value: unknown,
): { value: string; trigger: ReactNode; content: unknown }[] {
  const items: { value: string; trigger: ReactNode; content: unknown }[] = [];
  for (const item of toArray(value)) {
    if (!isRecord(item)) continue;
    const record = isA2UIComponent(item) ? mergeProps(item) : item;
    const valueText = readString(record.value) ?? readString(record.id);
    if (!valueText) continue;
    items.push({
      value: valueText,
      trigger: readText(record.trigger ?? record.label ?? record.title ?? valueText),
      content: record.content ?? record.children,
    });
  }
  return items;
}

function readImages(value: unknown): OpenUI.ImageItem[] {
  return toArray(value)
    .filter(isRecord)
    .map((item) => ({
      src: readString(item.src ?? item.url) ?? "",
      alt: readString(item.alt ?? item.description) ?? "",
      details: readString(item.details),
    }))
    .filter((item) => item.src);
}

function readTags(value: unknown): string[] {
  return toArray(value)
    .map((item) => readText(item))
    .filter(Boolean);
}

function renderCell(value: unknown, buildChild: BuildChild): ReactNode {
  if (typeof value === "string" && value.startsWith("#")) return buildChild(value.slice(1));
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (isA2UIComponent(value)) {
    return (
      <OpenUIComponent
        name={normalizeOpenUIComponentName(value.component ?? value.type)}
        props={mergeProps(value)}
        buildChild={buildChild}
      />
    );
  }
  if (isRecord(value) && typeof value.id === "string") return buildChild(value.id);
  if (value === null || value === undefined) return "";
  return JSON.stringify(value);
}

/** Array props that can hold nested components, inline or by id. */
const NESTED_PROPS = ["children", "data", "points", "series", "slices", "columns", "items"];

/**
 * Expand id references into the components they name — all the way down.
 *
 * A2UI nests by id, and how deeply is the model's choice: one payload put a
 * chart's series in `children`, the next put the series' points one level below
 * that, with no axis on the chart at all. Resolving a fixed number of levels
 * means every new depth is a fresh bug that presents identically — a chart that
 * silently renders nothing. Resolving to the bottom removes the whole class.
 *
 * Bounded on both axes that could hang it: `seen` breaks reference cycles on
 * the current path, and `depth` stops a pathological payload from walking
 * forever. Both limits are far above any real document.
 */
function materializeRefs(
  value: unknown,
  index: Map<string, Record<string, unknown>>,
  depth = 0,
  seen: ReadonlySet<string> = new Set(),
): unknown[] {
  if (depth > 8) return toArray(value);
  return toArray(value).map((item) => {
    let record: unknown = item;
    if (typeof item === "string") {
      if (seen.has(item)) return item;
      const resolved = index.get(item);
      if (!resolved) return item;
      record = resolved;
      seen = new Set([...seen, item]);
    }
    if (!isRecord(record)) return record;
    const expanded: Record<string, unknown> = { ...record };
    for (const key of NESTED_PROPS) {
      if (key in expanded) {
        expanded[key] = materializeRefs(expanded[key], index, depth + 1, seen);
      }
    }
    return expanded;
  });
}

function buildChartData(
  props: Record<string, unknown>,
  resolve: (value: unknown) => unknown[] = toArray,
) {
  // `series` may be inline, or `children` may name sibling components by id.
  const declared = resolve(props.series ?? props.children);
  const rowsFromSeriesData = buildRowsFromSeriesData(declared);
  if (rowsFromSeriesData.length) return rowsFromSeriesData;

  // The axis arrives as `labels` or `categories` depending on how the model
  // phrased it; reading only one leaves the chart with no axis, which the guard
  // below then turns into no chart at all — and an orphaned heading above it.
  const labels = toArray(props.labels ?? props.categories).map(readText);
  const series = readSeries(declared);
  // Labels alone are not data. Returning a row per label would clear the
  // caller's `!data.length` guard while carrying no numeric key at all, and the
  // chart would reserve a full-height plot to draw nothing in — which is how
  // this surfaced: a category axis of sixteen sectors above an empty box.
  if (series.length) {
    return labels.map((label, index) => {
      const row: Record<string, string | number> = { category: label };
      for (const item of series) {
        row[item.category] = Number(item.values[index] ?? 0);
      }
      return row;
    });
  }

  // Last resort: the shapes above key on prop names the model has used so far,
  // and it keeps inventing new ones. Rather than render nothing, scan the
  // materialised subtree for records that pair a label with a number — which is
  // what a data point is, whatever it happens to be called.
  const scanned = scanForPoints([props]);
  if (scanned.length) return scanned;

  warnUnreadableChart(props);
  return [];
}

/**
 * Pull (label, value) pairs out of an arbitrary materialised subtree.
 *
 * Deliberately last: it cannot tell which series a point belongs to, so a
 * multi-series chart flattens into one. That is a worse chart than the readers
 * above produce, and a much better outcome than no chart at all.
 */
function scanForPoints(value: unknown): Record<string, string | number>[] {
  const rows: Record<string, string | number>[] = [];
  const walk = (node: unknown, depth: number) => {
    if (depth > 6 || rows.length > 200) return;
    for (const item of toArray(node)) {
      if (!isRecord(item)) continue;
      const label = readText(item.label ?? item.category ?? item.name ?? item.x);
      const numeric = readNumber(item.value ?? item.y ?? item.amount ?? item.count);
      if (label && numeric !== undefined) rows.push({ category: label, value: numeric });
      // Any array, not just the known nesting props: this path exists precisely
      // for the case where the key is one nobody has seen before.
      for (const nested of Object.values(item)) {
        if (Array.isArray(nested)) walk(nested, depth + 1);
      }
    }
  };
  walk(value, 0);
  return rows;
}

/**
 * A chart that resolves to no data removes itself, leaving whatever heading sat
 * above it stranded. That is a silent failure, and every instance so far cost a
 * screenshot and a trip through the session database to identify. Naming the
 * keys the payload actually carried turns the next unknown shape into a console
 * line instead.
 */
function warnUnreadableChart(props: Record<string, unknown>): void {
  if (!import.meta.env.DEV) return;
  console.warn(
    "[genui] chart had no readable data — keys:",
    Object.keys(props).join(", "),
  );
}

/** A record's nested arrays, in the order data is likeliest to live. */
function nestedOf(record: Record<string, unknown>): unknown {
  return record.data ?? record.points ?? record.children ?? record.series;
}

/** True when `record` directly holds things that look like plotted points. */
function holdsPoints(record: Record<string, unknown>): boolean {
  return toArray(nestedOf(record))
    .filter(isRecord)
    .some(
      (point) =>
        readText(point.category ?? point.name ?? point.label) !== "" &&
        readNumber(point.value ?? point.y ?? point.data) !== undefined,
    );
}

/**
 * Find the nodes that actually carry points, however they are wrapped.
 *
 * The model puts a series directly under the chart, or under one or more
 * grouping components first. Reading only the top level makes a wrapper look
 * like an empty series, so this descends until it finds nodes holding points.
 */
function collectSeriesNodes(
  value: unknown,
  depth = 0,
  out: Record<string, unknown>[] = [],
): Record<string, unknown>[] {
  if (depth > 8) return out;
  for (const item of toArray(value).filter(isRecord)) {
    const record = isA2UIComponent(item) ? mergeProps(item) : item;
    if (holdsPoints(record)) out.push(record);
    else collectSeriesNodes(nestedOf(record), depth + 1, out);
  }
  return out;
}

function buildRowsFromSeriesData(value: unknown): Record<string, string | number>[] {
  const rows = new Map<string, Record<string, string | number>>();
  // `value` arrives materialised; resolving again would restart the cycle
  // guard from empty and re-enter any self-reference.
  for (const item of collectSeriesNodes(toArray(value))) {
    const record = isA2UIComponent(item) ? mergeProps(item) : item;
    const seriesKey = readText(
      record.category ?? record.name ?? record.label ?? "value",
    );
    // Already materialised at the entry point — resolving again here would
    // restart the cycle guard from empty and re-enter a self-reference, turning
    // a series that points at itself into a bogus zero-valued category.
    const points = toArray(nestedOf(record));
    let consumedNamedPoint = false;
    for (const point of points) {
      if (!isRecord(point)) continue;
      const category = readText(point.category ?? point.name ?? point.label);
      if (!category) continue;
      const row = rows.get(category) ?? { category };
      row[seriesKey] = readNumber(point.value ?? point.y ?? point.data) ?? 0;
      rows.set(category, row);
      consumedNamedPoint = true;
    }
    if (!consumedNamedPoint) continue;
  }
  return Array.from(rows.values());
}

function buildSliceData(props: Record<string, unknown>) {
  const labels = toArray(props.labels).map(readText);
  const values = toArray(props.values).map((value) => Number(value) || 0);
  if (labels.length && values.length) {
    return labels.map((label, index) => ({
      category: label,
      value: values[index] ?? 0,
    }));
  }
  return toArray(props.slices ?? props.children)
    .filter(isRecord)
    .map((slice) => {
      const record = isA2UIComponent(slice) ? mergeProps(slice) : slice;
      return {
        category: readText(record.category ?? record.label ?? record.name),
        value: readNumber(record.value ?? record.data) ?? 0,
      };
    });
}

function buildScatterData(props: Record<string, unknown>) {
  return toArray(props.datasets ?? props.series)
    .filter(isRecord)
    .map((dataset) => ({
      name: readText(dataset.name ?? dataset.category),
      data: toArray(dataset.points ?? dataset.values)
        .filter(isRecord)
        .map((point) => ({
          x: readNumber(point.x) ?? 0,
          y: readNumber(point.y) ?? 0,
          z: readNumber(point.z),
        })),
    }));
}

function readSeries(value: unknown): { category: string; values: number[] }[] {
  return toArray(value)
    .filter(isRecord)
    .map((item) => {
      const record = isA2UIComponent(item) ? mergeProps(item) : item;
      return {
        category: readText(record.category ?? record.name ?? record.label),
        values: toArray(record.values ?? record.data).map((point) => Number(point) || 0),
      };
    })
    .filter((item) => item.category);
}

function readCell(row: unknown, columnIndex: number): unknown {
  if (Array.isArray(row)) return row[columnIndex];
  if (isRecord(row)) return Object.values(row)[columnIndex];
  return row;
}

function normalizeCatalogId(value: unknown): string {
  const catalog = readString(value);
  if (!catalog || catalog === LEGACY_CATALOG_ID || catalog === CATALOG_ID) {
    return catalog ?? CATALOG_ID;
  }
  return CATALOG_ID;
}

function normalizeOpenUIComponentName(value: unknown): string {
  if (typeof value !== "string" || !value) return "TextContent";
  const aliases: Record<string, string> = {
    Breadth: "MarketBreadth",
    FinancialMetric: "FinanceMetric",
    Heading: "TextContent",
    IndexCard: "MarketIndexCard",
    IndexGrid: "MarketIndexGrid",
    IndustryRankingList: "DataList",
    KPI: "Metric",
    Leaderboard: "DataList",
    List: "DataList",
    ListBlock: "DataList",
    ListItem: "DataListItem",
    MarketIndices: "MarketIndexGrid",
    Markdown: "MarkDownRenderer",
    Paragraph: "TextContent",
    RankedList: "DataList",
    RankingList: "DataList",
    SectorRankingList: "DataList",
    StockIndexCard: "MarketIndexCard",
    Title: "TextContent",
  };
  return aliases[value] ?? value;
}

function structuralKeysForComponent(value: unknown): string[] {
  const name = normalizeOpenUIComponentName(value);
  if (
    ["Stack", "Row", "Grid", "Card", "Section", "DataList", "MarketIndexGrid"].includes(
      name,
    )
  ) return ["children"];
  if (name === "Form") return ["fields", "buttons", "children"];
  if (name === "FormControl") return ["children"];
  if (name === "Buttons") return ["buttons", "children"];
  if (name === "Modal") return ["children"];
  return [];
}

function makeComponentId(component: A2UIComponent, fallback = "component"): string {
  return `${fallback}_${normalizeOpenUIComponentName(component.component ?? component.type).toLowerCase()}_${Math.random()
    .toString(36)
    .slice(2, 8)}`;
}

function looksLikeA2UIMessage(value: Record<string, unknown>): boolean {
  return ["createSurface", "updateComponents", "updateDataModel", "deleteSurface"].some(
    (key) => key in value,
  );
}

function isA2UIComponent(value: unknown): value is A2UIComponent {
  return (
    isRecord(value) &&
    (typeof value.component === "string" || typeof value.type === "string")
  );
}

function toArray(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  if (value === undefined || value === null) return [];
  return [value];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function readString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function readNumber(value: unknown): number | undefined {
  if (typeof value === "number") return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : undefined;
  }
  return undefined;
}

function readBoolean(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

function readText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function readVariant(value: unknown): "card" | "sunk" | "clear" {
  return value === "sunk" || value === "clear" ? value : "card";
}

function readCalloutVariant(
  value: unknown,
): "info" | "danger" | "warning" | "success" | "neutral" {
  return value === "danger" ||
    value === "warning" ||
    value === "success" ||
    value === "neutral"
    ? value
    : "info";
}

function readTextCalloutVariant(
  value: unknown,
): "neutral" | "info" | "warning" | "success" | "danger" {
  return value === "info" ||
    value === "warning" ||
    value === "success" ||
    value === "danger"
    ? value
    : "neutral";
}

function readButtonVariant(value: unknown): "primary" | "secondary" | "tertiary" {
  return value === "secondary" || value === "tertiary" || value === "ghost"
    ? value === "ghost"
      ? "tertiary"
      : value
    : "primary";
}

function readTagVariant(
  value: unknown,
): "neutral" | "info" | "success" | "warning" | "danger" | undefined {
  return value === "info" ||
    value === "success" ||
    value === "warning" ||
    value === "danger" ||
    value === "neutral"
    ? value
    : undefined;
}

function gapToCss(value: string): string {
  const gapMap: Record<string, string> = {
    none: "0",
    xs: "var(--openui-space-xs)",
    s: "var(--openui-space-s)",
    m: "var(--openui-space-m)",
    l: "var(--openui-space-l)",
    xl: "var(--openui-space-xl)",
    "2xl": "var(--openui-space-2xl)",
  };
  return gapMap[value] ?? gapMap.m;
}

function alignToCss(value: string): string {
  const alignMap: Record<string, string> = {
    baseline: "baseline",
    center: "center",
    end: "flex-end",
    start: "flex-start",
    stretch: "stretch",
  };
  return alignMap[value] ?? "stretch";
}

function justifyToCss(value: string): string {
  const justifyMap: Record<string, string> = {
    around: "space-around",
    between: "space-between",
    center: "center",
    end: "flex-end",
    evenly: "space-evenly",
    start: "flex-start",
  };
  return justifyMap[value] ?? "flex-start";
}

function safeJsonParse(value: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    return undefined;
  }
}
