/* A2UI component implementations are registry values, so this module also exports its registry list. */
/* eslint-disable react-refresh/only-export-components */
import { createComponentImplementation } from "@a2ui/react/v0_9";
import { Check, ChevronDown, ChevronLeft, ChevronRight, X } from "lucide-react";
import { useMemo, useState } from "react";

import {
  AccordionApi,
  CardApi,
  CarouselApi,
  GridApi,
  ModalApi,
  SeparatorApi,
  StackApi,
  StepsApi,
  TabsApi,
} from "../catalog";
import {
  RenderChildren,
  accessibilityProps,
  asBoolean,
  asString,
  weightStyle,
} from "./shared";

export const Stack = createComponentImplementation(StackApi, ({ props, buildChild }) => (
  <div
    className="va2-stack"
    data-direction={props.direction ?? "vertical"}
    data-gap={props.gap ?? "md"}
    data-align={props.align ?? "stretch"}
    data-justify={props.justify ?? "start"}
    data-wrap={props.wrap ? "true" : "false"}
    style={weightStyle(props.weight)}
    {...accessibilityProps(props.accessibility)}
  >
    <RenderChildren children={props.children} buildChild={buildChild} />
  </div>
));

export const Grid = createComponentImplementation(GridApi, ({ props, buildChild }) => {
  const style = {
    ...weightStyle(props.weight),
    gridTemplateColumns: props.minItemWidth
      ? `repeat(auto-fit, minmax(min(100%, ${props.minItemWidth}px), 1fr))`
      : `repeat(${props.columns ?? 2}, minmax(0, 1fr))`,
  };
  return (
    <div
      className="va2-grid"
      data-gap={props.gap ?? "md"}
      data-align={props.align ?? "stretch"}
      style={style}
      {...accessibilityProps(props.accessibility)}
    >
      <RenderChildren children={props.children} buildChild={buildChild} />
    </div>
  );
});

export const Card = createComponentImplementation(CardApi, ({ props, buildChild }) => (
  <section
    className="va2-card"
    data-variant={props.variant ?? "default"}
    data-padding={props.padding ?? "md"}
    style={weightStyle(props.weight)}
    {...accessibilityProps(props.accessibility)}
  >
    {(props.title || props.subtitle) && (
      <header className="va2-card__header">
        {props.title && <h3>{props.title}</h3>}
        {props.subtitle && <p>{props.subtitle}</p>}
      </header>
    )}
    <div className="va2-card__content">
      <RenderChildren children={props.children} buildChild={buildChild} />
    </div>
  </section>
));

export const Tabs = createComponentImplementation(TabsApi, ({ props, buildChild }) => {
  const items = props.items ?? [];
  const initialIndex = Math.max(
    0,
    items.findIndex((item) => item.value === props.defaultValue),
  );
  const [active, setActive] = useState(initialIndex);
  const selected = items[active] ?? items[0];
  return (
    <div className="va2-tabs" data-variant={props.variant ?? "underline"}>
      <div className="va2-tabs__list" role="tablist">
        {items.map((item, index) => (
          <button
            key={item.value ?? `${asString(item.label)}-${index}`}
            type="button"
            role="tab"
            aria-selected={index === active}
            disabled={asBoolean(item.disabled)}
            onClick={() => setActive(index)}
          >
            {asString(item.label)}
          </button>
        ))}
      </div>
      <div className="va2-tabs__panel" role="tabpanel">
        {selected?.child ? buildChild(selected.child) : null}
      </div>
    </div>
  );
});

export const Accordion = createComponentImplementation(AccordionApi, ({ props, buildChild }) => {
  const [openItems, setOpenItems] = useState<Set<number>>(
    () => new Set(props.defaultOpen ?? []),
  );
  const toggle = (index: number) => {
    setOpenItems((current) => {
      const next = props.multiple ? new Set(current) : new Set<number>();
      if (current.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  };
  return (
    <div className="va2-accordion">
      {(props.items ?? []).map((item, index) => {
        const isOpen = openItems.has(index);
        return (
          <section className="va2-accordion__item" key={`${asString(item.title)}-${index}`}>
            <button
              className="va2-accordion__trigger"
              type="button"
              aria-expanded={isOpen}
              onClick={() => toggle(index)}
            >
              <span>
                <strong>{asString(item.title)}</strong>
                {item.description && <small>{asString(item.description)}</small>}
              </span>
              <ChevronDown aria-hidden="true" size={17} />
            </button>
            {isOpen && <div className="va2-accordion__content">{buildChild(item.child)}</div>}
          </section>
        );
      })}
    </div>
  );
});

export const Steps = createComponentImplementation(StepsApi, ({ props, buildChild }) => (
  <ol className="va2-steps" data-orientation={props.orientation ?? "vertical"}>
    {(props.items ?? []).map((item, index) => (
      <li className="va2-steps__item" data-status={item.status ?? "pending"} key={`${asString(item.title)}-${index}`}>
        <span className="va2-steps__marker">
          {item.status === "complete" ? <Check size={14} /> : index + 1}
        </span>
        <div className="va2-steps__body">
          <strong>{asString(item.title)}</strong>
          {item.description && <p>{asString(item.description)}</p>}
          {item.child && <div className="va2-steps__content">{buildChild(item.child)}</div>}
        </div>
      </li>
    ))}
  </ol>
));

export const Carousel = createComponentImplementation(CarouselApi, ({ props, buildChild }) => {
  const children = Array.isArray(props.children) ? props.children : [];
  const [active, setActive] = useState(() =>
    Math.min(Math.max(props.initialIndex ?? 0, 0), Math.max(children.length - 1, 0)),
  );
  const selected = children[active];
  const renderSelected = () => {
    if (typeof selected === "string") return buildChild(selected);
    if (selected && typeof selected === "object" && "id" in selected) {
      return buildChild(selected.id, selected.basePath);
    }
    return null;
  };
  return (
    <div className="va2-carousel">
      <div className="va2-carousel__viewport">{renderSelected()}</div>
      {children.length > 1 && (
        <div className="va2-carousel__controls">
          <button type="button" aria-label="Previous" onClick={() => setActive((active - 1 + children.length) % children.length)}>
            <ChevronLeft size={17} />
          </button>
          {props.showIndicators !== false && (
            <div className="va2-carousel__indicators" aria-label="Slides">
              {children.map((_, index) => (
                <button
                  key={index}
                  type="button"
                  aria-label={`Go to item ${index + 1}`}
                  aria-current={index === active}
                  onClick={() => setActive(index)}
                />
              ))}
            </div>
          )}
          <button type="button" aria-label="Next" onClick={() => setActive((active + 1) % children.length)}>
            <ChevronRight size={17} />
          </button>
        </div>
      )}
    </div>
  );
});

export const Separator = createComponentImplementation(SeparatorApi, ({ props }) => (
  <div
    className="va2-separator"
    data-has-label={props.label ? "true" : "false"}
    data-orientation={props.orientation ?? "horizontal"}
    role="separator"
  >
    {props.label && <span>{props.label}</span>}
  </div>
));

export const Modal = createComponentImplementation(ModalApi, ({ props, buildChild }) => {
  const [open, setOpen] = useState(false);
  const trigger = useMemo(() => buildChild(props.triggerChild), [buildChild, props.triggerChild]);
  const show = () => {
    setOpen(true);
    props.onOpen?.();
  };
  return (
    <>
      <span className="va2-modal__entry" onClick={show}>{trigger}</span>
      {open && (
        <div className="va2-modal" role="presentation" onMouseDown={() => props.dismissible !== false && setOpen(false)}>
          <section className="va2-modal__dialog" role="dialog" aria-modal="true" aria-label={props.title} onMouseDown={(event) => event.stopPropagation()}>
            <header>
              <div>
                {props.title && <h3>{props.title}</h3>}
                {props.description && <p>{props.description}</p>}
              </div>
              {props.dismissible !== false && (
                <button type="button" aria-label="Close" onClick={() => setOpen(false)}><X size={18} /></button>
              )}
            </header>
            <div className="va2-modal__content">{buildChild(props.contentChild)}</div>
          </section>
        </div>
      )}
    </>
  );
});

export const layoutComponents = [Stack, Grid, Card, Tabs, Accordion, Steps, Carousel, Separator, Modal];
