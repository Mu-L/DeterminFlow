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

export function shouldShowModelSwitcher(
  session: { type: string; runtime_scope?: string | null } | null | undefined,
): boolean {
  return session?.type === "main" && session.runtime_scope !== "workflow";
}
