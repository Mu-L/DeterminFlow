import type { MessageAttachment } from "../../types";

export type ComposerPart =
  | { type: "text"; value: string }
  | { type: "file"; name: string; path: string | null };

export interface ComposerMessage {
  content: string;
  attachments: MessageAttachment[];
}

export function shouldOfferComposerExpansion(
  scrollHeight: number,
  clientHeight: number,
): boolean {
  return scrollHeight > clientHeight + 1;
}

const ZERO_WIDTH_SPACE = /\u200b/g;

export function getDroppedFileName(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  return normalized.slice(normalized.lastIndexOf("/") + 1) || path;
}

export function formatComposerParts(parts: ComposerPart[]): string {
  let content = "";

  for (let index = 0; index < parts.length; index += 1) {
    const part = parts[index];
    if (part.type === "text") {
      content += part.value.replace(ZERO_WIDTH_SPACE, "");
      continue;
    }
    if (!part.path) continue;

    if (content && !/\s$/.test(content)) content += " ";
    content += part.path;

    const nextText = parts.slice(index + 1).find(
      (candidate): candidate is Extract<ComposerPart, { type: "text" }> =>
        candidate.type === "text" && candidate.value.replace(ZERO_WIDTH_SPACE, "").length > 0,
    );
    if (nextText && !/^\s/.test(nextText.value.replace(ZERO_WIDTH_SPACE, ""))) {
      content += " ";
    }
  }

  return content;
}

export function formatComposerMessage(parts: ComposerPart[]): ComposerMessage {
  return {
    content: formatComposerParts(parts),
    attachments: parts.flatMap((part) =>
      part.type === "file" && part.path
        ? [{ name: part.name, absolute_path: part.path }]
        : []
    ),
  };
}
