import { useCallback, useEffect, useState } from "react";

export type SearchParamPatch = Record<string, string | null | undefined>;

export function readSearchParam(search: string, key: string): string | null {
  const value = new URLSearchParams(search).get(key)?.trim();
  return value || null;
}

export function patchSearchParams(search: string, patch: SearchParamPatch): string {
  const params = new URLSearchParams(search);

  for (const [key, value] of Object.entries(patch)) {
    if (value == null || value.trim() === "") {
      params.delete(key);
    } else {
      params.set(key, value);
    }
  }

  const serialized = params.toString();
  return serialized ? `?${serialized}` : "";
}

interface SetUrlParamOptions {
  replace?: boolean;
}

export function useUrlParam(
  key: string,
): readonly [string | null, (value: string | null, options?: SetUrlParamOptions) => void] {
  const readCurrentValue = useCallback(
    () => readSearchParam(window.location.search, key),
    [key],
  );
  const [value, setValue] = useState<string | null>(readCurrentValue);

  useEffect(() => {
    const handlePopState = () => setValue(readCurrentValue());
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, [readCurrentValue]);

  const updateValue = useCallback(
    (nextValue: string | null, options?: SetUrlParamOptions) => {
      const normalizedValue = nextValue?.trim() || null;
      if (readSearchParam(window.location.search, key) === normalizedValue) {
        setValue(normalizedValue);
        return;
      }
      const nextSearch = patchSearchParams(window.location.search, {
        [key]: normalizedValue,
      });
      const nextUrl = `${window.location.pathname}${nextSearch}${window.location.hash}`;
      const method = options?.replace ? "replaceState" : "pushState";
      window.history[method](window.history.state, "", nextUrl);
      setValue(normalizedValue);
    },
    [key],
  );

  return [value, updateValue] as const;
}
