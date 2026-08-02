import * as React from "react";
import { ChevronDown, Check } from "lucide-react";

import { cn } from "@/lib/utils";

export interface SelectProps {
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  disabled?: boolean;
  className?: string;
  children: React.ReactNode;
}

export interface SelectTriggerProps {
  id?: string;
  className?: string;
  "aria-label"?: string;
  children: React.ReactNode;
}

export interface SelectContentProps {
  className?: string;
  children: React.ReactNode;
}

export interface SelectItemProps {
  value: string;
  className?: string;
  children: React.ReactNode;
}

export interface SelectValueProps {
  placeholder?: string;
  className?: string;
}

const SelectContext = React.createContext<{
  value?: string;
  onValueChange?: (value: string) => void;
  disabled: boolean;
  isOpen: boolean;
  setIsOpen: (open: boolean) => void;
}>({ disabled: false, isOpen: false, setIsOpen: () => {} });

export const Select: React.FC<SelectProps> = ({
  value,
  defaultValue,
  onValueChange,
  disabled = false,
  className = "",
  children,
}) => {
  const [isOpen, setIsOpen] = React.useState(false);
  const [selectedValue, setSelectedValue] = React.useState(
    value || defaultValue || ""
  );
  const containerRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (value !== undefined) {
      setSelectedValue(value);
    }
  }, [value]);

  // Close on outside click
  React.useEffect(() => {
    if (!isOpen) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setIsOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [isOpen]);

  // Close on Escape key
  React.useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setIsOpen(false);
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [isOpen]);

  const handleValueChange = (newValue: string) => {
    setSelectedValue(newValue);
    onValueChange?.(newValue);
    setIsOpen(false);
  };

  return (
    <SelectContext.Provider
      value={{
        value: selectedValue,
        onValueChange: handleValueChange,
        disabled,
        isOpen,
        setIsOpen,
      }}
    >
      <div ref={containerRef} className={cn("relative", className)}>
        {children}
      </div>
    </SelectContext.Provider>
  );
};

export const SelectTrigger: React.FC<SelectTriggerProps> = ({
  id,
  className = "",
  "aria-label": ariaLabel,
  children,
}) => {
  const { disabled, isOpen, setIsOpen } = React.useContext(SelectContext);

  return (
    <button
      id={id}
      type="button"
      role="combobox"
      aria-expanded={isOpen}
      aria-haspopup="listbox"
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={() => setIsOpen(!isOpen)}
      className={cn(
        "flex h-10 w-full items-center justify-between rounded-md border border-input bg-background",
        "px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground",
        "focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className
      )}
    >
      {children}
      <ChevronDown
        className={cn(
          "h-4 w-4 shrink-0 opacity-50 transition-transform duration-200",
          isOpen && "rotate-180"
        )}
        aria-hidden="true"
      />
    </button>
  );
};

export const SelectContent: React.FC<SelectContentProps> = ({
  className = "",
  children,
}) => {
  const { isOpen } = React.useContext(SelectContext);

  if (!isOpen) return null;

  return (
    <div
      role="listbox"
      className={cn(
        "absolute z-50 max-h-96 min-w-[8rem] overflow-hidden rounded-md border bg-popover p-1",
        "text-popover-foreground shadow-md animate-in fade-in-80",
        className
      )}
    >
      {children}
    </div>
  );
};

export const SelectItem: React.FC<SelectItemProps> = ({
  value,
  className = "",
  children,
}) => {
  const { value: selectedValue, onValueChange } =
    React.useContext(SelectContext);
  const isSelected = selectedValue === value;

  return (
    <div
      role="option"
      aria-selected={isSelected}
      onClick={() => onValueChange?.(value)}
      className={cn(
        "relative flex w-full cursor-default select-none items-center rounded-sm py-1.5 pl-8 pr-2 text-sm",
        "outline-none hover:bg-accent hover:text-accent-foreground focus:bg-accent focus:text-accent-foreground",
        isSelected && "bg-accent text-accent-foreground",
        className
      )}
    >
      {isSelected && (
        <span
          className="absolute left-2 flex h-3.5 w-3.5 items-center justify-center"
          aria-hidden="true"
        >
          <Check className="h-4 w-4" />
        </span>
      )}
      {children}
    </div>
  );
};

export const SelectValue: React.FC<SelectValueProps> = ({
  placeholder,
  className = "",
}) => {
  const { value } = React.useContext(SelectContext);

  return (
    <span className={cn("block truncate", className)}>
      {value || placeholder}
    </span>
  );
};
