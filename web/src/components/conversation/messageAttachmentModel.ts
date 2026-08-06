import type { MessageAttachment } from "../../types";

export type UserMessageContentPart =
  | { type: "text"; value: string }
  | { type: "file"; name: string; absolutePath: string };

function fileNameFromPath(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  return normalized.slice(normalized.lastIndexOf("/") + 1) || path;
}

function splitStructuredAttachments(
  content: string,
  attachments: MessageAttachment[],
): UserMessageContentPart[] | null {
  const parts: UserMessageContentPart[] = [];
  let cursor = 0;
  let matched = false;

  for (const attachment of attachments) {
    const path = attachment.absolute_path;
    const index = path ? content.indexOf(path, cursor) : -1;
    if (index < 0) continue;
    if (index > cursor) {
      parts.push({ type: "text", value: content.slice(cursor, index) });
    }
    parts.push({
      type: "file",
      name: attachment.name || fileNameFromPath(path),
      absolutePath: path,
    });
    cursor = index + path.length;
    matched = true;
  }

  if (!matched) return null;
  if (cursor < content.length) {
    parts.push({ type: "text", value: content.slice(cursor) });
  }
  return parts;
}

function legacyWorkspaceAttachment(line: string): UserMessageContentPart | null {
  const path = line.trim();
  const normalized = path.replace(/\\/g, "/");
  const absolute = normalized.startsWith("/") || /^[A-Za-z]:\//.test(normalized);
  const hasFileExtension = /\.[^./\s]+$/.test(normalized);
  if (!absolute || !normalized.includes("/attachments/") || !hasFileExtension) {
    return null;
  }
  return {
    type: "file",
    name: fileNameFromPath(path),
    absolutePath: path,
  };
}

function splitLegacyInlineAttachments(line: string): UserMessageContentPart[] {
  const pathPattern = /(?:[A-Za-z]:[\\/]|\/)[^\s\r\n]*[\\/]attachments[\\/][^\s\r\n]+/g;
  const parts: UserMessageContentPart[] = [];
  let cursor = 0;

  for (const match of line.matchAll(pathPattern)) {
    const path = match[0];
    const index = match.index;
    if (index > cursor) {
      parts.push({ type: "text", value: line.slice(cursor, index) });
    }
    parts.push({
      type: "file",
      name: fileNameFromPath(path),
      absolutePath: path,
    });
    cursor = index + path.length;
  }

  if (parts.length === 0) return [{ type: "text", value: line }];
  if (cursor < line.length) {
    parts.push({ type: "text", value: line.slice(cursor) });
  }
  return parts;
}

function splitLegacyWorkspaceAttachments(content: string): UserMessageContentPart[] {
  const parts = content.split(/(\r?\n)/).flatMap((segment) => {
    if (/^\r?\n$/.test(segment)) {
      return [{ type: "text", value: segment } as const];
    }
    const wholeLineAttachment = legacyWorkspaceAttachment(segment);
    return wholeLineAttachment
      ? [wholeLineAttachment]
      : splitLegacyInlineAttachments(segment);
  });
  return parts.some((part) => part.type === "file")
    ? parts
    : [{ type: "text", value: content }];
}

export function splitUserMessageAttachments(
  content: string,
  attachments: MessageAttachment[] | undefined,
): UserMessageContentPart[] {
  if (attachments?.length) {
    const structured = splitStructuredAttachments(content, attachments);
    if (structured) return structured;
  }
  return splitLegacyWorkspaceAttachments(content);
}
