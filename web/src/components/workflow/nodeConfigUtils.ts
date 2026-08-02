export function generateVarKey(
  nodeId: string,
  field: string,
  existingKeys: string[],
): string {
  let key = `${nodeId}_${field}`;
  let counter = 1;
  while (existingKeys.includes(key)) {
    key = `${nodeId}_${field}_${counter}`;
    counter++;
  }
  return key;
}

export function nodeParamString(
  params: Record<string, unknown> | undefined,
  key: string,
  fallback = "",
): string {
  const value = params?.[key];
  return typeof value === "string" ? value : fallback;
}

export function scriptArgvParam(
  params: Record<string, unknown> | undefined,
): string[] | null {
  const value = params?.script_argv;
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    return null;
  }
  return [...value];
}

interface ScriptNodeParamInput {
  scriptSource: string;
  scriptType: string;
  scriptName: string;
  scriptGroup: string;
  scriptArgs: string;
  scriptArgv: string[];
  useScriptArgv: boolean;
  timeout: string;
  enableRejectUpstream: boolean;
  maxRejectCount: string;
}

export function buildScriptNodeParams(
  existing: Record<string, unknown> | undefined,
  input: ScriptNodeParamInput,
): Record<string, unknown> {
  const params: Record<string, unknown> = {
    ...existing,
    script_source: input.scriptSource,
    script_type: input.scriptType,
    script_name: input.scriptName,
    timeout: input.timeout,
    enable_reject_upstream: String(input.enableRejectUpstream),
    max_reject_count: String(parseInt(input.maxRejectCount, 10) || 3),
  };
  if (input.useScriptArgv) {
    params.script_argv = [...input.scriptArgv];
    delete params.script_args;
  } else {
    params.script_args = input.scriptArgs;
    delete params.script_argv;
  }
  if (input.scriptSource === "library") {
    params.script_group = input.scriptGroup;
  } else {
    delete params.script_group;
  }
  return params;
}
