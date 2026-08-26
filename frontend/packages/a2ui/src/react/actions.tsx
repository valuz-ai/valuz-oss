/* A2UI component implementations are registry values, so this module also exports its registry list. */
/* eslint-disable react-refresh/only-export-components */
import { createComponentImplementation } from "@a2ui/react/v0_9";

import { ButtonApi, ButtonGroupApi, FollowUpBlockApi } from "../catalog";
import {
  RenderChildren,
  ValuzIcon,
  accessibilityProps,
  asString,
  invokeAction,
  weightStyle,
} from "./shared";

export const Button = createComponentImplementation(ButtonApi, ({ props }) => (
  <button
    className="va2-button"
    data-variant={props.variant ?? "default"}
    data-size={props.size ?? "default"}
    data-full-width={props.fullWidth ? "true" : "false"}
    type="button"
    disabled={props.disabled || props.isValid === false}
    onClick={props.action}
    style={weightStyle(props.weight)}
    {...accessibilityProps(props.accessibility)}
  >
    {props.icon && <ValuzIcon name={props.icon} size={props.size === "sm" ? 14 : 16} />}
    <span>{props.label}</span>
  </button>
));

export const ButtonGroup = createComponentImplementation(ButtonGroupApi, ({ props, buildChild }) => (
  <div
    className="va2-button-group"
    data-align={props.align ?? "start"}
    data-attached={props.attached ? "true" : "false"}
    style={weightStyle(props.weight)}
  >
    <RenderChildren children={props.children} buildChild={buildChild} />
  </div>
));

export const FollowUpBlock = createComponentImplementation(FollowUpBlockApi, ({ props }) => (
  <section className="va2-followups" data-layout={props.layout ?? "list"} style={weightStyle(props.weight)}>
    {props.title && <h4>{props.title}</h4>}
    <div>
      {(props.items ?? []).map((item, index) => (
        <button type="button" onClick={() => invokeAction(item.action)} key={`${asString(item.label)}-${index}`}>
          {item.icon && <ValuzIcon name={item.icon} size={16} />}
          <span>
            <strong>{asString(item.label)}</strong>
            {item.description && <small>{asString(item.description)}</small>}
          </span>
          <ValuzIcon name="next" size={15} />
        </button>
      ))}
    </div>
  </section>
));

export const actionComponents = [Button, ButtonGroup, FollowUpBlock];
