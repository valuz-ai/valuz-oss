"use client";

import { defineComponent } from "@openuidev/react-lang";
import { CardHeader } from "@openuidev/react-ui";

import {
  inferTrend,
  readRecord,
  readText,
  readTextFromKeys,
  toArray,
} from "../lib/props";
import { DataListItemSchema, DataListSchema } from "./schema";

export { DataListItemSchema, DataListSchema } from "./schema";

/**
 * One row, after every alias has been resolved.
 *
 * Reading happens once, up front, so the markup below never has to ask which
 * key a field came from — and so a row that carries nothing can be dropped
 * before it renders as an empty line.
 */
interface Row {
  description: string;
  meta: string;
  rank: string;
  title: string;
  trend: string;
  value: string;
}

function readRow(value: unknown): Row {
  if (typeof value === "string" || typeof value === "number") {
    return {
      description: "",
      meta: "",
      rank: "",
      title: readText(value),
      trend: "",
      value: "",
    };
  }

  const record = readRecord(value);
  const meta =
    readTextFromKeys(record, [
      "meta",
      "changePct",
      "change_pct",
      "pct",
      "percent",
      "delta",
    ]) || readTextFromKeys(record, ["change"]);

  return {
    description: readTextFromKeys(record, [
      "description",
      "subtitle",
      "subTitle",
      "details",
    ]),
    meta,
    rank: readTextFromKeys(record, ["rank", "index", "position", "no"]),
    title: readTextFromKeys(record, ["title", "label", "name", "text"]),
    trend: readTextFromKeys(record, ["trend", "direction"]) || inferTrend(meta),
    value: readTextFromKeys(record, ["value", "amount", "score", "metric"]),
  };
}

function readRows(value: unknown): Row[] {
  return toArray(value)
    .map(readRow)
    .filter((row) => row.title || row.value || row.meta);
}

/**
 * The row itself, shared by `DataListItem` and by `DataList`'s own `items`.
 *
 * Two declarations stay inline rather than moving to `market.css`: the column
 * template depends on whether the row is ranked, and the host's generative-UI
 * stylesheet sets both it and the gap from an attribute selector that would
 * otherwise win over a class.
 */
function DataListRow({ row }: { row: Row }) {
  const trend = inferTrend(row.trend || row.meta);

  return (
    <div
      className="vgb-data-list-row"
      data-slot="vgb-data-list-item"
      data-a2ui-data-list-row
      data-a2ui-trend={trend}
      role="listitem"
      style={{
        gridTemplateColumns: row.rank
          ? "max-content minmax(0, 1fr) max-content max-content"
          : "minmax(0, 1fr) max-content max-content",
        gap: "var(--openui-space-s)",
      }}
    >
      {row.rank ? (
        <span className="vgb-data-list-rank" data-a2ui-data-list-rank>
          {row.rank}
        </span>
      ) : null}
      <span className="vgb-data-list-main" data-a2ui-data-list-main>
        <span className="vgb-data-list-title" data-a2ui-data-list-title>
          {row.title}
        </span>
        {row.description ? (
          <span
            className="vgb-data-list-description"
            data-a2ui-data-list-description
          >
            {row.description}
          </span>
        ) : null}
      </span>
      {row.value ? (
        <span className="vgb-data-list-value" data-a2ui-data-list-value>
          {row.value}
        </span>
      ) : null}
      {row.meta ? (
        <span className="vgb-data-list-meta" data-a2ui-data-list-meta>
          {row.meta}
        </span>
      ) : null}
    </div>
  );
}

export const DataList = defineComponent({
  name: "DataList",
  props: DataListSchema,
  description:
    "A ranked or ordered list of entries, each one line: an optional rank, the entry's name with an optional supporting phrase, a figure, and the change that figure made. " +
    "Reach for it for leaderboards, sector and industry rankings, top movers, and any 'best/worst N' answer — it is denser and easier to scan than a Table when every row carries the same three fields. " +
    "items is the data (title, value, meta for the change, plus optional description, rank and trend up|down|flat); title and description head the list. " +
    "Pass DataListItem children instead only when a row has to be built by hand.",
  component: ({ props, renderNode }) => {
    const raw = props as unknown as Record<string, unknown>;
    const rows = readRows(raw.items ?? raw.rows ?? raw.data);
    const title = readTextFromKeys(raw, ["title", "label"]);
    const description = readTextFromKeys(raw, ["description", "subtitle"]);

    return (
      <section
        className="vgb-data-list"
        data-slot="vgb-data-list"
        data-a2ui-component="data-list"
        role="list"
      >
        {title ? <CardHeader title={title} subtitle={description} /> : null}
        <div className="vgb-data-list-rows" data-a2ui-data-list-rows>
          {rows.length
            ? rows.map((row, index) => (
                <DataListRow key={`${row.rank}-${row.title}-${index}`} row={row} />
              ))
            : renderNode(props.children ?? [])}
        </div>
      </section>
    );
  },
});

export const DataListItem = defineComponent({
  name: "DataListItem",
  props: DataListItemSchema,
  description:
    "One row of a DataList: title on the left with an optional description under it, then value, then meta — the change figure, written signed (\"+8.77%\") so its direction reads without a legend. " +
    "rank is the position number when the list is a ranking, and trend (up|down|flat) states the direction explicitly when meta is not a signed figure. " +
    "Only use this inside a DataList; a list built from items renders the same rows without it.",
  component: ({ props }) => <DataListRow row={readRow(props)} />,
});
