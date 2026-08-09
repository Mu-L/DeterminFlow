export function mergeUniqueModels(
  current: string[],
  incoming: string[],
): string[] {
  const seen = new Set<string>();
  const models: string[] = [];

  for (const value of [...current, ...incoming]) {
    const model = value.trim();
    if (!model || seen.has(model)) continue;
    seen.add(model);
    models.push(model);
  }

  return models;
}

export function sanitizeProviderId(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

export function nextProviderId(
  providerType: string,
  existingProviderIds: string[],
): string {
  const base = sanitizeProviderId(providerType) || "provider";
  const existing = new Set(existingProviderIds);
  if (!existing.has(base)) return base;

  let suffix = 2;
  while (existing.has(`${base}-${suffix}`)) suffix += 1;
  return `${base}-${suffix}`;
}

export function shouldShowModelSwitcher(
  session: { type: string; runtime_scope?: string | null } | null | undefined,
): boolean {
  return session?.type === "main" && session.runtime_scope !== "workflow";
}
