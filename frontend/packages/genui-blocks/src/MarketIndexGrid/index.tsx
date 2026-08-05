"use client";

import { defineComponent } from "@openuidev/react-lang";
import { Card, CardHeader } from "@openuidev/react-ui";

import {
  inferTrend,
  isRecord,
  readRecord,
  readTextFromKeys,
  toArray,
} from "../lib/props";
import { MarketIndexCardSchema, MarketIndexGridSchema } from "./schema";

export { MarketIndexCardSchema, MarketIndexGridSchema } from "./schema";

/** One quote, after every alias has been resolved. */
interface Quote {
  asOf: string;
  change: string;
  changePct: string;
  code: string;
  latest: string;
  name: string;
  source: string;
  trend: string;
  turnover: string;
}

function readQuote(value: unknown): Quote {
  const record = readRecord(value);
  const changePct = readTextFromKeys(record, [
    "changePct",
    "change_pct",
    "pct",
    "percent",
    "changePercent",
  ]);
  const change = readTextFromKeys(record, ["change", "delta", "changeValue"]);

  return {
    asOf: readTextFromKeys(record, ["asOf", "time", "timestamp"]),
    change,
    changePct,
    code: readTextFromKeys(record, ["code", "symbol", "ticker"]),
    latest: readTextFromKeys(record, ["latest", "value", "price", "last"]),
    name: readTextFromKeys(record, ["name", "label", "title"]),
    source: readTextFromKeys(record, ["source"]),
    trend:
      readTextFromKeys(record, ["trend", "direction"]) ||
      inferTrend(changePct || change),
    turnover: readTextFromKeys(record, ["turnover", "amount", "volume"]),
  };
}

function readQuotes(value: unknown): Quote[] {
  return toArray(value).filter(isRecord).map(readQuote);
}

/**
 * The card itself, shared by `MarketIndexCard` and by `MarketIndexGrid`'s own
 * `indices`.
 *
 * `min-height: 100%` stays inline: the host's generative-UI stylesheet sets a
 * fixed floor on the same element from an attribute selector, and filling the
 * card it sits in is what keeps a grid row of quotes the same height.
 */
function QuoteCard({ quote }: { quote: Quote }) {
  const trend = inferTrend(quote.trend || quote.changePct || quote.change);

  return (
    <Card variant="card" width="full">
      <article
        className="vgb-market-card"
        data-slot="vgb-market-index-card"
        data-a2ui-component="market-index-card"
        data-a2ui-trend={trend}
        style={{ minHeight: "100%" }}
      >
        <div className="vgb-market-card-heading" data-a2ui-market-index-heading>
          <span className="vgb-market-card-name" data-a2ui-market-index-name>
            {quote.name || "指数"}
          </span>
          {quote.code ? (
            <span className="vgb-market-card-code" data-a2ui-market-index-code>
              {quote.code}
            </span>
          ) : null}
        </div>
        {quote.latest ? (
          <div className="vgb-market-card-value" data-a2ui-market-index-value>
            {quote.latest}
          </div>
        ) : null}
        {quote.changePct || quote.change ? (
          <div
            className="vgb-market-card-change-row"
            data-a2ui-market-index-change-row
          >
            {quote.changePct ? (
              <span
                className="vgb-market-card-change"
                data-a2ui-market-index-change
              >
                {quote.changePct}
              </span>
            ) : null}
            {quote.change ? (
              <span
                className="vgb-market-card-delta"
                data-a2ui-market-index-delta
              >
                涨跌额 {quote.change}
              </span>
            ) : null}
          </div>
        ) : null}
        {quote.turnover ? (
          <div className="vgb-market-card-meta" data-a2ui-market-index-meta>
            成交额 {quote.turnover}
          </div>
        ) : null}
        {quote.asOf || quote.source ? (
          <div
            className="vgb-market-card-footnote"
            data-a2ui-market-index-footnote
          >
            {[quote.asOf, quote.source].filter(Boolean).join(" · ")}
          </div>
        ) : null}
      </article>
    </Card>
  );
}

export const MarketIndexGrid = defineComponent({
  name: "MarketIndexGrid",
  props: MarketIndexGridSchema,
  description:
    "The board of headline quotes that opens a market answer: one card per index or ticker, laid out in a grid that reflows to the width available. " +
    "indices carries the quotes (name, code, latest, change, changePct, turnover, source, asOf) — pass every index in one grid rather than a row of separate cards, so they line up and share a heading. " +
    "title and description head the board; use MarketIndexCard alone only for a single quote.",
  component: ({ props, renderNode }) => {
    const raw = props as unknown as Record<string, unknown>;
    const quotes = readQuotes(raw.indices ?? raw.items ?? raw.data);
    const title = readTextFromKeys(raw, ["title", "label"]);
    const description = readTextFromKeys(raw, ["description", "subtitle"]);

    return (
      <section
        className="vgb-market-grid"
        data-slot="vgb-market-index-grid"
        data-a2ui-component="market-index-grid"
      >
        {title ? <CardHeader title={title} subtitle={description} /> : null}
        <div className="vgb-market-grid-list" data-a2ui-market-index-grid-list>
          {quotes.length
            ? quotes.map((quote, index) => (
                <QuoteCard
                  key={`${quote.name}-${quote.code}-${index}`}
                  quote={quote}
                />
              ))
            : renderNode(props.children ?? [])}
        </div>
      </section>
    );
  },
});

export const MarketIndexCard = defineComponent({
  name: "MarketIndexCard",
  props: MarketIndexCardSchema,
  description:
    "A single market quote as a card: name and code, the latest level set large, then the change — changePct signed (\"+0.56%\") with change as the absolute move — and turnover underneath. " +
    "source and asOf print as the footnote that tells the reader how fresh the figure is; fill them in whenever the data came from a tool. " +
    "Put two or more quotes in a MarketIndexGrid instead of repeating this card.",
  component: ({ props }) => <QuoteCard quote={readQuote(props)} />,
});
