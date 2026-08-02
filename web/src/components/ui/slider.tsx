import * as React from "react";

import { cn } from "@/lib/utils";

export interface SliderProps {
  id?: string;
  value?: number[];
  defaultValue?: number[];
  onValueChange?: (value: number[]) => void;
  min?: number;
  max?: number;
  step?: number;
  disabled?: boolean;
  className?: string;
}

export const Slider = React.forwardRef<HTMLDivElement, SliderProps>(
  (
    {
      id,
      value = [0],
      defaultValue,
      onValueChange,
      min = 0,
      max = 100,
      step = 1,
      disabled = false,
      className,
    },
    ref
  ) => {
    const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
      const newValue = parseFloat(e.target.value);
      onValueChange?.([newValue]);
    };

    const currentValue = value[0] ?? defaultValue?.[0] ?? min;
    const percentage = ((currentValue - min) / (max - min)) * 100;

    return (
      <div
        ref={ref}
        id={id}
        className={cn(
          "relative flex w-full touch-none select-none items-center",
          className
        )}
      >
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={currentValue}
          onChange={handleChange}
          disabled={disabled}
          aria-valuemin={min}
          aria-valuemax={max}
          aria-valuenow={currentValue}
          className={cn(
            "h-2 w-full cursor-pointer appearance-none rounded-full bg-secondary",
            "[&::-webkit-slider-thumb]:h-5 [&::-webkit-slider-thumb]:w-5",
            "[&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:cursor-pointer",
            "[&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-primary [&::-webkit-slider-thumb]:shadow-md",
            "[&::-webkit-slider-thumb]:transition-all [&::-webkit-slider-thumb]:hover:scale-110",
            "disabled:cursor-not-allowed disabled:opacity-50"
          )}
          style={{
            background: `linear-gradient(to right, hsl(var(--primary)) 0%, hsl(var(--primary)) ${percentage}%, hsl(var(--secondary)) ${percentage}%, hsl(var(--secondary)) 100%)`,
          }}
        />
      </div>
    );
  }
);

Slider.displayName = "Slider";
