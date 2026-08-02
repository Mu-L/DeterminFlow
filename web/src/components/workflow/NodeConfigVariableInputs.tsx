import { useCallback, useEffect, useRef, useState } from "react";
import type { ChangeEvent, KeyboardEvent } from "react";
import { Link2 } from "lucide-react";
import type { WorkflowVariable } from "../../types";

const PLACEHOLDER_RE = /\{\{([\w-]+)\}\}/g;

function extractPlaceholders(text: string): string[] {
  const keys: string[] = [];
  let match: RegExpExecArray | null;
  PLACEHOLDER_RE.lastIndex = 0;
  while ((match = PLACEHOLDER_RE.exec(text)) !== null) {
    if (!keys.includes(match[1])) keys.push(match[1]);
  }
  return keys;
}

function detectTrigger(
  text: string,
  cursorPos: number,
): { triggered: boolean; prefix: string } {
  const before = text.slice(0, cursorPos);
  const match = before.match(/\{\{([\w-]*)$/);
  if (match) {
    return { triggered: true, prefix: match[1] || "" };
  }
  return { triggered: false, prefix: "" };
}

function variableTypeLabel(type: WorkflowVariable["type"]): string {
  if (type === "select") return "选择器";
  if (type === "file") return "文件";
  if (type === "textarea") return "文本段";
  if (type === "list") return "列表";
  if (type === "dict") return "字典";
  return "文本";
}

interface VariableDropdownProps {
  variables: WorkflowVariable[];
  selectedIndex: number;
  onSelect: (variableKey: string) => void;
  dropdownRef: React.RefObject<HTMLDivElement>;
}

function VariableDropdown({
  variables,
  selectedIndex,
  onSelect,
  dropdownRef,
}: VariableDropdownProps) {
  return (
    <div
      ref={dropdownRef}
      className="absolute left-0 right-0 top-full mt-1 z-20 bg-slate-900 border border-indigo-500/30 rounded-lg shadow-xl max-h-36 overflow-y-auto"
      role="listbox"
      aria-label="变量选择列表"
    >
      {variables.map((variable, index) => (
        <button
          key={variable.key}
          onClick={() => onSelect(variable.key)}
          className={`w-full flex items-center gap-2 px-3 py-2 text-left text-xs transition-colors ${
            index === selectedIndex
              ? "bg-indigo-500/20 text-slate-100"
              : "text-slate-400 hover:bg-indigo-500/10"
          }`}
          role="option"
          aria-selected={index === selectedIndex}
        >
          <span className="text-green-500 font-mono">{`{{${variable.key}}}`}</span>
          <span className="text-slate-500 truncate">{variable.name}</span>
          <span className="text-xs text-slate-500 ml-auto">
            {variableTypeLabel(variable.type)}
          </span>
        </button>
      ))}
    </div>
  );
}

function PlaceholderTags({
  placeholders,
  variables,
}: {
  placeholders: string[];
  variables: WorkflowVariable[];
}) {
  if (placeholders.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1 mt-1.5">
      {placeholders.map((key) => {
        const variable = variables.find((item) => item.key === key);
        return (
          <span
            key={key}
            className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-xs bg-green-500/10 text-green-500 border border-green-500/20 font-mono"
          >
            {`{{${key}}}`}
            {variable && (
              <span className="text-green-500/60">{variable.name}</span>
            )}
          </span>
        );
      })}
    </div>
  );
}

interface VariableInputBaseProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  readOnly?: boolean;
  readOnlyClass: string;
  inputClass: string;
  variables: WorkflowVariable[];
}

export function VarInput({
  value,
  onChange,
  placeholder,
  readOnly,
  readOnlyClass,
  inputClass,
  variables,
}: VariableInputBaseProps) {
  const [showDropdown, setShowDropdown] = useState(false);
  const [filteredVars, setFilteredVars] = useState<WorkflowVariable[]>([]);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [triggerStart, setTriggerStart] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const placeholders = extractPlaceholders(value);

  const handleInput = useCallback((event: ChangeEvent<HTMLInputElement>) => {
    const newValue = event.target.value;
    onChange(newValue);
    const cursorPos = event.target.selectionStart || 0;
    const { triggered, prefix } = detectTrigger(newValue, cursorPos);
    if (!triggered) {
      setShowDropdown(false);
      return;
    }
    const filtered = variables.filter((variable) =>
      variable.key.toLowerCase().startsWith(prefix.toLowerCase()),
    );
    setFilteredVars(filtered);
    setSelectedIndex(0);
    setTriggerStart(cursorPos - prefix.length - 2);
    setShowDropdown(filtered.length > 0);
  }, [onChange, variables]);

  const insertVariable = useCallback((variableKey: string) => {
    const before = value.slice(0, triggerStart);
    const after = value.slice(inputRef.current?.selectionStart || triggerStart);
    onChange(`${before}{{${variableKey}}}${after}`);
    setShowDropdown(false);
    setTimeout(() => {
      if (!inputRef.current) return;
      const cursorPos = before.length + variableKey.length + 4;
      inputRef.current.setSelectionRange(cursorPos, cursorPos);
      inputRef.current.focus();
    }, 0);
  }, [value, triggerStart, onChange]);

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (!showDropdown) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setSelectedIndex((previous) => Math.min(previous + 1, filteredVars.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setSelectedIndex((previous) => Math.max(previous - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      const variable = filteredVars[selectedIndex];
      if (variable) insertVariable(variable.key);
    } else if (event.key === "Escape") {
      setShowDropdown(false);
    }
  };

  useEffect(() => {
    if (!showDropdown) return;
    const handleOutsideClick = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setShowDropdown(false);
      }
    };
    document.addEventListener("mousedown", handleOutsideClick);
    return () => document.removeEventListener("mousedown", handleOutsideClick);
  }, [showDropdown]);

  return (
    <div className="relative">
      <input
        ref={inputRef}
        type="text"
        value={value}
        onChange={handleInput}
        onKeyDown={handleKeyDown}
        readOnly={readOnly}
        className={`${inputClass} ${readOnly ? readOnlyClass : ""}`}
        placeholder={placeholder}
        aria-label={placeholder || "输入变量"}
      />
      {showDropdown && (
        <VariableDropdown
          variables={filteredVars}
          selectedIndex={selectedIndex}
          onSelect={insertVariable}
          dropdownRef={dropdownRef}
        />
      )}
      <PlaceholderTags placeholders={placeholders} variables={variables} />
    </div>
  );
}

interface VarTextareaProps extends VariableInputBaseProps {
  rows: number;
}

export function VarTextarea({
  value,
  onChange,
  placeholder,
  readOnly,
  readOnlyClass,
  inputClass,
  rows,
  variables,
}: VarTextareaProps) {
  const [showDropdown, setShowDropdown] = useState(false);
  const [filteredVars, setFilteredVars] = useState<WorkflowVariable[]>([]);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const [triggerStart, setTriggerStart] = useState(0);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const placeholders = extractPlaceholders(value);

  const handleInput = useCallback((event: ChangeEvent<HTMLTextAreaElement>) => {
    const newValue = event.target.value;
    onChange(newValue);
    const cursorPos = event.target.selectionStart || 0;
    const { triggered, prefix } = detectTrigger(newValue, cursorPos);
    if (!triggered) {
      setShowDropdown(false);
      return;
    }
    const filtered = variables.filter((variable) =>
      variable.key.toLowerCase().startsWith(prefix.toLowerCase()),
    );
    setFilteredVars(filtered);
    setSelectedIndex(0);
    setTriggerStart(cursorPos - prefix.length - 2);
    setShowDropdown(filtered.length > 0);
  }, [onChange, variables]);

  const insertVariable = useCallback((variableKey: string) => {
    const before = value.slice(0, triggerStart);
    const after = value.slice(textareaRef.current?.selectionStart || triggerStart);
    onChange(`${before}{{${variableKey}}}${after}`);
    setShowDropdown(false);
    setTimeout(() => {
      if (!textareaRef.current) return;
      const cursorPos = before.length + variableKey.length + 4;
      textareaRef.current.setSelectionRange(cursorPos, cursorPos);
      textareaRef.current.focus();
    }, 0);
  }, [value, triggerStart, onChange]);

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (!showDropdown) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setSelectedIndex((previous) => Math.min(previous + 1, filteredVars.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setSelectedIndex((previous) => Math.max(previous - 1, 0));
    } else if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      const variable = filteredVars[selectedIndex];
      if (variable) insertVariable(variable.key);
    } else if (event.key === "Escape") {
      setShowDropdown(false);
    }
  };

  useEffect(() => {
    if (!showDropdown) return;
    const handleOutsideClick = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setShowDropdown(false);
      }
    };
    document.addEventListener("mousedown", handleOutsideClick);
    return () => document.removeEventListener("mousedown", handleOutsideClick);
  }, [showDropdown]);

  return (
    <div className="relative">
      <textarea
        ref={textareaRef}
        value={value}
        onChange={handleInput}
        onKeyDown={handleKeyDown}
        readOnly={readOnly}
        rows={rows}
        className={`${inputClass} ${readOnly ? readOnlyClass : ""} resize-none`}
        placeholder={placeholder}
        aria-label={placeholder || "输入变量"}
      />
      {showDropdown && (
        <VariableDropdown
          variables={filteredVars}
          selectedIndex={selectedIndex}
          onSelect={insertVariable}
          dropdownRef={dropdownRef}
        />
      )}
      <PlaceholderTags placeholders={placeholders} variables={variables} />
    </div>
  );
}

interface FieldHookButtonProps {
  field: string;
  currentValue: string;
  isHooked: boolean;
  isReadOnly: boolean;
  onToggle: (field: string, currentValue: string) => void;
}

export function FieldHookButton({
  field,
  currentValue,
  isHooked,
  isReadOnly,
  onToggle,
}: FieldHookButtonProps) {
  return (
    <button
      type="button"
      onClick={() => onToggle(field, currentValue)}
      disabled={isReadOnly}
      title={isHooked ? "取消变量，节点内维护" : "转为变量"}
      aria-label={isHooked ? "取消变量" : "转为变量"}
      className={`absolute right-2 top-1/2 -translate-y-1/2 z-10 p-1 rounded transition-all duration-150 ${
        isHooked
          ? "text-green-500 hover:text-red-500"
          : "text-slate-500 hover:text-indigo-500"
      } ${isReadOnly ? "opacity-30 cursor-not-allowed" : "cursor-pointer hover:scale-110"}`}
    >
      <Link2 size={14} className={isHooked ? "" : "opacity-60"} />
    </button>
  );
}
