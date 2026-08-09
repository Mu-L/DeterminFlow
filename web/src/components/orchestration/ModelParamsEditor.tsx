import { Bot } from "lucide-react";
import type { ModelParamFieldSchema } from "../../types";

interface Props {
  agentType: string;
  fields: Record<string, ModelParamFieldSchema>;
  modelParams: Record<string, unknown> | null | undefined;
  onChange: (modelParams: Record<string, unknown>) => void;
}

function fieldValue(
  modelParams: Record<string, unknown> | null | undefined,
  name: string,
  field: ModelParamFieldSchema,
) {
  return modelParams && Object.prototype.hasOwnProperty.call(modelParams, name)
    ? modelParams[name]
    : field.default;
}

export default function ModelParamsEditor({
  agentType,
  fields,
  modelParams,
  onChange,
}: Props) {
  const entries = Object.entries(fields);
  if (entries.length === 0) return null;

  const update = (name: string, value: unknown) => {
    onChange({ ...(modelParams || {}), [name]: value });
  };

  return (
    <section className="border-t border-border/30 pt-3">
      <h4 className="mb-3 flex items-center gap-1 text-xs font-semibold text-slate-300">
        <Bot size={12} className="text-indigo-500" aria-hidden="true" />
        模型参数
      </h4>

      <div className="space-y-3">
        {entries.map(([name, field]) => {
          const value = fieldValue(modelParams, name, field);
          const inputId = `model-param-${agentType}-${name}`;

          if (field.type === "boolean" || field.type === "json_mode") {
            const checked = field.type === "json_mode" ? Boolean(value) : value === true;
            return (
              <div key={name} className="flex items-center justify-between gap-4">
                <span id={`${inputId}-label`} className="text-xs text-slate-300">
                  {field.label}
                </span>
                <button
                  type="button"
                  role="switch"
                  aria-checked={checked}
                  aria-labelledby={`${inputId}-label`}
                  onClick={() => update(
                    name,
                    field.type === "json_mode"
                      ? (checked ? null : { type: "json_object" })
                      : !checked,
                  )}
                  className={`relative h-5 w-10 rounded-full border transition-colors ${
                    checked
                      ? "border-indigo-500/60 bg-indigo-500/30"
                      : "border-slate-600 bg-slate-800"
                  }`}
                >
                  <span
                    className={`absolute top-0.5 h-4 w-4 rounded-full transition-all ${
                      checked ? "left-5 bg-indigo-400" : "left-0.5 bg-slate-500"
                    }`}
                  />
                </button>
              </div>
            );
          }

          if (field.type === "select") {
            const selected = typeof value === "string" ? value : String(field.default ?? "");
            return (
              <label key={name} htmlFor={inputId} className="block text-xs text-slate-300">
                {field.label}
                <select
                  id={inputId}
                  value={selected}
                  onChange={(event) => update(name, event.target.value)}
                  className="mt-1 w-full rounded-md border border-border/50 bg-slate-800/60 px-2 py-1.5 text-xs text-slate-300 outline-none focus:border-indigo-500/50"
                >
                  {(field.options || []).map((option) => (
                    <option key={option} value={option}>
                      {field.option_labels?.[option] || option}
                    </option>
                  ))}
                </select>
              </label>
            );
          }

          if (field.type === "range") {
            const numericValue = typeof value === "number"
              ? value
              : Number(field.default ?? field.min ?? 0);
            return (
              <div key={name}>
                <div className="mb-1 flex items-center justify-between gap-3 text-xs">
                  <label htmlFor={inputId} className="text-slate-300">{field.label}</label>
                  <span className="text-muted-foreground">{numericValue}</span>
                </div>
                <input
                  id={inputId}
                  type="range"
                  min={field.min}
                  max={field.max}
                  step={field.step}
                  value={numericValue}
                  onChange={(event) => update(name, Number(event.target.value))}
                  className="h-1.5 w-full cursor-pointer appearance-none rounded-full bg-slate-800 [&::-webkit-slider-thumb]:h-3.5 [&::-webkit-slider-thumb]:w-3.5 [&::-webkit-slider-thumb]:cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-indigo-500"
                />
              </div>
            );
          }

          return (
            <label
              key={name}
              htmlFor={inputId}
              className="flex items-center justify-between gap-4 text-xs text-slate-300"
            >
              {field.label}
              <input
                id={inputId}
                type="number"
                min={field.min}
                max={field.max}
                step={field.step}
                placeholder={field.nullable ? "自动" : undefined}
                value={typeof value === "number" ? value : ""}
                onChange={(event) => update(
                  name,
                  event.target.value === "" ? null : Number(event.target.value),
                )}
                className="min-h-9 w-36 rounded-md border border-border/50 bg-slate-800/60 px-2 text-right text-xs text-slate-300 outline-none focus:border-indigo-500/50"
              />
            </label>
          );
        })}
      </div>
    </section>
  );
}
