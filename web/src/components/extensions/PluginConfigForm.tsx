import {
  useEffect,
  useMemo,
  useState,
  type FormEvent,
} from "react";
import { Loader2, RotateCcw, Save, TriangleAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import {
  coerceSettingsFieldValue,
  getSettingsValue,
  parsePluginSettingsSchema,
  schemaHasConfigurableFields,
  setSettingsValue,
  settingsFieldDisplayValue,
  validatePluginSettings,
} from "@/extensions/plugin-model";
import type {
  PluginObjectSchema,
  PluginSettings,
  PluginSettingsSchemaNode,
} from "@/extensions/plugin-types";

interface PluginConfigFormProps {
  pluginId: string;
  schemaValue: unknown;
  settings: PluginSettings;
  configPresent: boolean;
  busy: boolean;
  onSave: (settings: PluginSettings) => Promise<boolean>;
  onReset: () => Promise<boolean>;
}

interface SettingsFieldsProps {
  schema: PluginObjectSchema;
  settings: PluginSettings;
  path?: string[];
  errors: Record<string, string>;
  disabled: boolean;
  onChange: (path: string[], value: unknown) => void;
}

function cloneSettings(settings: PluginSettings): PluginSettings {
  return JSON.parse(JSON.stringify(settings)) as PluginSettings;
}

function SettingsLeafField({
  name,
  schema,
  path,
  value,
  required,
  error,
  disabled,
  onChange,
}: {
  name: string;
  schema: Exclude<PluginSettingsSchemaNode, PluginObjectSchema>;
  path: string[];
  value: unknown;
  required: boolean;
  error?: string;
  disabled: boolean;
  onChange: (path: string[], value: unknown) => void;
}) {
  const fieldId = `plugin-setting-${path.join("-")}`;
  const label = schema.title || name;
  const descriptionId = schema.description ? `${fieldId}-description` : undefined;
  const errorId = error ? `${fieldId}-error` : undefined;
  const describedBy = [descriptionId, errorId].filter(Boolean).join(" ") || undefined;
  const displayValue = settingsFieldDisplayValue(schema, value ?? schema.default);

  const labelNode = (
    <Label htmlFor={fieldId}>
      {label}
      {required ? <span className="text-destructive" aria-label="必填"> *</span> : null}
    </Label>
  );
  let control: React.ReactNode;

  if (schema.type === "boolean") {
    control = (
      <div className="flex items-center gap-3">
        <Switch
          id={fieldId}
          checked={Boolean(displayValue)}
          onCheckedChange={(checked) => onChange(path, checked)}
          disabled={disabled}
          aria-describedby={describedBy}
        />
        <span className="text-xs text-muted-foreground">
          {displayValue ? "已开启" : "已关闭"}
        </span>
      </div>
    );
  } else if (
    schema.type === "array"
    || (schema.type === "string" && schema.format === "multiline")
  ) {
    control = (
      <Textarea
        id={fieldId}
        value={String(displayValue)}
        onChange={(event) => onChange(
          path,
          coerceSettingsFieldValue(schema, event.target.value),
        )}
        placeholder={schema.type === "array" ? "每行一个值" : undefined}
        disabled={disabled}
        required={required}
        aria-invalid={Boolean(error)}
        aria-describedby={describedBy}
      />
    );
  } else if ("enum" in schema && schema.enum) {
    control = (
      <Select
        value={String(displayValue)}
        onValueChange={(next) => onChange(path, coerceSettingsFieldValue(schema, next))}
        disabled={disabled}
      >
        <SelectTrigger id={fieldId} aria-label={label} className="w-full">
          <SelectValue placeholder="请选择" />
        </SelectTrigger>
        <SelectContent>
          {schema.enum.map((option) => (
            <SelectItem key={String(option)} value={String(option)}>
              {String(option)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    );
  } else {
    control = (
      <Input
        id={fieldId}
        type={
          schema.type === "number" || schema.type === "integer"
            ? "number"
            : schema.type === "string" && schema.format === "password"
              ? "password"
              : "text"
        }
        value={String(displayValue)}
        onChange={(event) => onChange(
          path,
          coerceSettingsFieldValue(schema, event.target.value),
        )}
        min={schema.type === "number" || schema.type === "integer" ? schema.minimum : undefined}
        max={schema.type === "number" || schema.type === "integer" ? schema.maximum : undefined}
        step={schema.type === "integer" ? 1 : schema.type === "number" ? "any" : undefined}
        disabled={disabled}
        required={required}
        aria-invalid={Boolean(error)}
        aria-describedby={describedBy}
      />
    );
  }

  return (
    <div className="flex flex-col gap-2" data-invalid={error ? true : undefined}>
      {labelNode}
      {control}
      {schema.description ? (
        <p id={descriptionId} className="text-xs text-muted-foreground">
          {schema.description}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} className="text-xs text-destructive" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function SettingsFields({
  schema,
  settings,
  path = [],
  errors,
  disabled,
  onChange,
}: SettingsFieldsProps) {
  return (
    <div className="grid gap-5 md:grid-cols-2">
      {Object.entries(schema.properties).map(([name, childSchema]) => {
        const childPath = [...path, name];
        const childKey = childPath.join(".");
        const childValue = getSettingsValue(settings, childPath);

        if (childSchema.type === "object") {
          return (
            <fieldset key={childKey} className="flex flex-col gap-4 rounded-md border p-4 md:col-span-2">
              <legend className="px-1 text-sm font-medium">
                {childSchema.title || name}
              </legend>
              {childSchema.description ? (
                <p className="text-xs text-muted-foreground">{childSchema.description}</p>
              ) : null}
              <SettingsFields
                schema={childSchema}
                settings={settings}
                path={childPath}
                errors={errors}
                disabled={disabled}
                onChange={onChange}
              />
            </fieldset>
          );
        }

        return (
          <SettingsLeafField
            key={childKey}
            name={name}
            schema={childSchema}
            path={childPath}
            value={childValue}
            required={Boolean(schema.required?.includes(name))}
            error={errors[childKey]}
            disabled={disabled}
            onChange={onChange}
          />
        );
      })}
    </div>
  );
}

export function PluginConfigForm({
  pluginId,
  schemaValue,
  settings,
  configPresent,
  busy,
  onSave,
  onReset,
}: PluginConfigFormProps) {
  const parsedSchema = useMemo(
    () => parsePluginSettingsSchema(schemaValue),
    [schemaValue],
  );
  const [draft, setDraft] = useState<PluginSettings>(() => cloneSettings(settings));
  const [showErrors, setShowErrors] = useState(false);

  useEffect(() => {
    setDraft(cloneSettings(settings));
    setShowErrors(false);
  }, [pluginId, settings]);

  const validationErrors = useMemo(
    () => parsedSchema.ok ? validatePluginSettings(parsedSchema.schema, draft) : {},
    [draft, parsedSchema],
  );
  const dirty = useMemo(
    () => JSON.stringify(draft) !== JSON.stringify(settings),
    [draft, settings],
  );

  if (!parsedSchema.ok) {
    return (
      <Card role="alert" className="border-destructive/40">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base text-destructive">
            <TriangleAlert aria-hidden="true" />
            配置 Schema 不受支持
          </CardTitle>
          <CardDescription>{parsedSchema.error}</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  if (!schemaHasConfigurableFields(parsedSchema.schema)) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">配置</CardTitle>
          <CardDescription>该插件没有可配置字段。</CardDescription>
        </CardHeader>
        <CardFooter className="justify-end">
          <Button
            type="button"
            variant="outline"
            onClick={() => void onReset()}
            disabled={busy || !configPresent}
          >
            <RotateCcw data-icon="inline-start" aria-hidden="true" />
            清空旧配置
          </Button>
        </CardFooter>
      </Card>
    );
  }

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setShowErrors(true);
    if (Object.keys(validationErrors).length > 0) return;
    const saved = await onSave(draft);
    if (saved) setShowErrors(false);
  };

  return (
    <Card>
      <form onSubmit={submit}>
        <CardHeader>
          <CardTitle className="text-base">{parsedSchema.schema.title || "插件配置"}</CardTitle>
          <CardDescription>
            {parsedSchema.schema.description || "保存后需要重启 DeterminFlow 主进程生效。"}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <SettingsFields
            schema={parsedSchema.schema}
            settings={draft}
            errors={showErrors ? validationErrors : {}}
            disabled={busy}
            onChange={(path, value) => setDraft((current) => setSettingsValue(current, path, value))}
          />
        </CardContent>
        <CardFooter className="justify-end gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => void onReset()}
            disabled={busy || !configPresent}
          >
            清空已保存配置
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              setDraft(cloneSettings(settings));
              setShowErrors(false);
            }}
            disabled={busy || !dirty}
          >
            <RotateCcw data-icon="inline-start" aria-hidden="true" />
            撤销修改
          </Button>
          <Button type="submit" disabled={busy || !dirty}>
            {busy
              ? <Loader2 data-icon="inline-start" className="animate-spin" aria-hidden="true" />
              : <Save data-icon="inline-start" aria-hidden="true" />}
            保存配置
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}
