/* A2UI component implementations are registry values, so this module also exports its registry list. */
/* eslint-disable react-refresh/only-export-components */
import { createComponentImplementation } from "@a2ui/react/v0_9";
import { Check, ChevronDown } from "lucide-react";
import { type ReactNode, useId } from "react";

import {
  CheckboxGroupApi,
  DatePickerApi,
  FormApi,
  InputApi,
  RadioGroupApi,
  SelectApi,
  SliderApi,
  SwitchGroupApi,
  TextAreaApi,
  ToggleGroupApi,
} from "../catalog";
import {
  RenderChildren,
  accessibilityProps,
  asBoolean,
  asString,
  invokeAction,
  weightStyle,
} from "./shared";

interface FieldShellProps {
  id: string;
  label: unknown;
  description?: unknown;
  required?: boolean;
  errors?: string[];
  children: ReactNode;
}

function FieldShell({
  id,
  label,
  description,
  required,
  errors,
  children,
}: FieldShellProps) {
  const descriptionText = asString(description);
  const descriptionId = descriptionText ? `${id}-description` : undefined;
  const errorId = errors?.length ? `${id}-error` : undefined;
  return (
    <div className="va2-field" data-invalid={errors?.length ? "true" : "false"}>
      <label htmlFor={id}>
        {asString(label)}
        {required && <span aria-hidden="true">*</span>}
      </label>
      {children}
      {descriptionText ? <small id={descriptionId}>{descriptionText}</small> : null}
      {errors?.length ? <small className="va2-field__error" id={errorId}>{errors[0]}</small> : null}
    </div>
  );
}

function fieldAccessibility(
  id: string,
  description: unknown,
  errors: string[] | undefined,
  isValid: boolean | undefined,
) {
  return {
    "aria-describedby": asString(description) ? `${id}-description` : undefined,
    "aria-errormessage": errors?.length ? `${id}-error` : undefined,
    "aria-invalid": isValid === false || Boolean(errors?.length),
  } as const;
}

function GroupShell({
  label,
  description,
  required,
  errors,
  children,
}: Omit<FieldShellProps, "id">) {
  const descriptionText = asString(description);
  return (
    <fieldset className="va2-field va2-field--group" data-invalid={errors?.length ? "true" : "false"}>
      <legend>
        {asString(label)}
        {required && <span aria-hidden="true">*</span>}
      </legend>
      {descriptionText ? <small>{descriptionText}</small> : null}
      {children}
      {errors?.length ? <small className="va2-field__error">{errors[0]}</small> : null}
    </fieldset>
  );
}

export const Form = createComponentImplementation(FormApi, ({ props, buildChild }) => (
  <form
    className="va2-form"
    data-layout={props.layout ?? "vertical"}
    onSubmit={(event) => {
      event.preventDefault();
      invokeAction(props.submit);
    }}
    style={weightStyle(props.weight)}
    {...accessibilityProps(props.accessibility)}
  >
    <div className="va2-form__fields">
      <RenderChildren children={props.children} buildChild={buildChild} />
    </div>
    {props.submit && props.submitLabel ? (
      <button className="va2-button" data-size="default" data-variant="default" disabled={props.disabled} type="submit">
        {props.submitLabel}
      </button>
    ) : null}
  </form>
));

export const Input = createComponentImplementation(InputApi, ({ props }) => {
  const id = useId();
  return (
    <FieldShell id={id} label={props.label} description={props.description} required={props.required} errors={props.validationErrors}>
      <input
        id={id}
        className="va2-control"
        type={props.type ?? "text"}
        value={props.value ?? ""}
        placeholder={props.placeholder}
        autoComplete={props.autocomplete}
        disabled={props.disabled}
        required={props.required}
        {...fieldAccessibility(id, props.description, props.validationErrors, props.isValid)}
        onChange={(event) => props.setValue(event.target.value)}
      />
    </FieldShell>
  );
});

export const TextArea = createComponentImplementation(TextAreaApi, ({ props }) => {
  const id = useId();
  return (
    <FieldShell id={id} label={props.label} description={props.description} required={props.required} errors={props.validationErrors}>
      <textarea
        id={id}
        className="va2-control va2-control--textarea"
        value={props.value ?? ""}
        placeholder={props.placeholder}
        rows={props.rows ?? 4}
        maxLength={props.maxLength}
        disabled={props.disabled}
        required={props.required}
        {...fieldAccessibility(id, props.description, props.validationErrors, props.isValid)}
        onChange={(event) => props.setValue(event.target.value)}
      />
    </FieldShell>
  );
});

export const Select = createComponentImplementation(SelectApi, ({ props }) => {
  const id = useId();
  return (
    <FieldShell id={id} label={props.label} description={props.description} required={props.required} errors={props.validationErrors}>
      <span className="va2-select">
        <select
          id={id}
          className="va2-control"
          value={props.value ?? ""}
          disabled={props.disabled}
          required={props.required}
          {...fieldAccessibility(id, props.description, props.validationErrors, props.isValid)}
          onChange={(event) => props.setValue(event.target.value)}
        >
          {props.placeholder ? <option value="">{props.placeholder}</option> : null}
          {(props.options ?? []).map((option) => (
            <option disabled={asBoolean(option.disabled)} key={option.value} value={option.value}>
              {asString(option.label)}
            </option>
          ))}
        </select>
        <ChevronDown aria-hidden="true" size={14} />
      </span>
    </FieldShell>
  );
});

export const RadioGroup = createComponentImplementation(RadioGroupApi, ({ props }) => (
  <GroupShell label={props.label} description={props.description} required={props.required} errors={props.validationErrors}>
    <div className="va2-choice-group" data-orientation={props.orientation ?? "vertical"}>
      {(props.options ?? []).map((option) => (
        <label className="va2-choice" key={option.value}>
          <input
            type="radio"
            name={props.label}
            value={option.value}
            checked={props.value === option.value}
            disabled={props.disabled || asBoolean(option.disabled)}
            required={props.required}
            onChange={() => props.setValue(option.value)}
          />
          <span className="va2-choice__control" aria-hidden="true" />
          <span><strong>{asString(option.label)}</strong>{option.description && <small>{asString(option.description)}</small>}</span>
        </label>
      ))}
    </div>
  </GroupShell>
));

export const CheckboxGroup = createComponentImplementation(CheckboxGroupApi, ({ props }) => {
  const selected = Array.isArray(props.value) ? props.value : [];
  const toggle = (value: string) => {
    props.setValue(selected.includes(value) ? selected.filter((item) => item !== value) : [...selected, value]);
  };
  return (
    <GroupShell label={props.label} description={props.description} required={props.required} errors={props.validationErrors}>
      <div className="va2-choice-group" data-orientation={props.orientation ?? "vertical"}>
        {(props.options ?? []).map((option) => (
          <label className="va2-choice" key={option.value}>
            <input
              type="checkbox"
              value={option.value}
              checked={selected.includes(option.value)}
              disabled={props.disabled || asBoolean(option.disabled)}
              onChange={() => toggle(option.value)}
            />
            <span className="va2-choice__control" aria-hidden="true"><Check size={12} /></span>
            <span><strong>{asString(option.label)}</strong>{option.description && <small>{asString(option.description)}</small>}</span>
          </label>
        ))}
      </div>
    </GroupShell>
  );
});

export const Slider = createComponentImplementation(SliderApi, ({ props }) => {
  const id = useId();
  const min = props.min ?? 0;
  const max = Math.max(props.max ?? 100, min);
  const value = Math.min(Math.max(props.value ?? min, min), max);
  const progress = max === min ? 0 : ((value - min) / (max - min)) * 100;
  return (
    <FieldShell id={id} label={props.label} description={props.description} required={props.required} errors={props.validationErrors}>
      <div className="va2-slider">
        <input
          id={id}
          type="range"
          value={value}
          min={min}
          max={max}
          step={props.step ?? 1}
          disabled={props.disabled}
          {...fieldAccessibility(id, props.description, props.validationErrors, props.isValid)}
          style={{ "--va2-range-progress": `${progress}%` } as React.CSSProperties}
          onChange={(event) => props.setValue(Number(event.target.value))}
        />
        {props.showValue !== false && <output htmlFor={id}>{value}{props.unit ?? ""}</output>}
      </div>
    </FieldShell>
  );
});

export const DatePicker = createComponentImplementation(DatePickerApi, ({ props }) => {
  const id = useId();
  return (
    <FieldShell id={id} label={props.label} description={props.description} required={props.required} errors={props.validationErrors}>
      <input
        id={id}
        className="va2-control"
        type={props.precision ?? "date"}
        value={props.value ?? ""}
        min={props.min}
        max={props.max}
        disabled={props.disabled}
        required={props.required}
        {...fieldAccessibility(id, props.description, props.validationErrors, props.isValid)}
        onChange={(event) => props.setValue(event.target.value)}
      />
    </FieldShell>
  );
});

export const SwitchGroup = createComponentImplementation(SwitchGroupApi, ({ props }) => {
  const selected = Array.isArray(props.value) ? props.value : [];
  const toggle = (value: string) => {
    props.setValue(selected.includes(value) ? selected.filter((item) => item !== value) : [...selected, value]);
  };
  return (
    <GroupShell label={props.label} description={props.description} required={props.required} errors={props.validationErrors}>
      <div className="va2-switch-group">
        {(props.options ?? []).map((option) => (
          <label className="va2-switch" key={option.value}>
            <span><strong>{asString(option.label)}</strong>{option.description && <small>{asString(option.description)}</small>}</span>
            <input
              type="checkbox"
              role="switch"
              checked={selected.includes(option.value)}
              disabled={props.disabled || asBoolean(option.disabled)}
              onChange={() => toggle(option.value)}
            />
            <span className="va2-switch__track" aria-hidden="true"><i /></span>
          </label>
        ))}
      </div>
    </GroupShell>
  );
});

export const ToggleGroup = createComponentImplementation(ToggleGroupApi, ({ props }) => {
  const selected = Array.isArray(props.value) ? props.value : [];
  const toggle = (value: string) => {
    if (props.multiple) {
      props.setValue(selected.includes(value) ? selected.filter((item) => item !== value) : [...selected, value]);
    } else {
      props.setValue(selected.includes(value) ? [] : [value]);
    }
  };
  return (
    <GroupShell label={props.label} description={props.description} required={props.required} errors={props.validationErrors}>
      <div className="va2-toggle-group" data-full-width={props.fullWidth ? "true" : "false"}>
        {(props.options ?? []).map((option) => (
          <button
            type="button"
            aria-pressed={selected.includes(option.value)}
            disabled={props.disabled || asBoolean(option.disabled)}
            key={option.value}
            onClick={() => toggle(option.value)}
          >
            {asString(option.label)}
          </button>
        ))}
      </div>
    </GroupShell>
  );
});

export const formComponents = [
  Form,
  Input,
  TextArea,
  Select,
  RadioGroup,
  CheckboxGroup,
  Slider,
  DatePicker,
  SwitchGroup,
  ToggleGroup,
];
