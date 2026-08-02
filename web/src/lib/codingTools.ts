export const CODING_TOOL_NAMES = new Set([
  "read_file", "write_to_file", "replace_in_file",
  "search_files", "list_files", "list_code_definitions",
  "execute_command", "ask_user",
]);

export function isCodingTool(name: string): boolean {
  return CODING_TOOL_NAMES.has(name);
}
