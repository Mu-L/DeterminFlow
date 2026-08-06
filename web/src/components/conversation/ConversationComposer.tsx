import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ClipboardEvent,
  type DragEvent,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { Loader2, Maximize2, Minimize2, Paperclip, Send, Square } from "lucide-react";

import { isDesktopRuntime } from "../../lib/desktop-update";
import { uploadWorkspaceAttachment } from "../../lib/api";
import type { MessageAttachment } from "../../types";
import {
  formatComposerMessage,
  getDroppedFileName,
  shouldOfferComposerExpansion,
  type ComposerPart,
} from "./conversationComposerModel";

export interface ConversationComposerProps {
  sessionId: string | null;
  onSendMessage: (
    content: string,
    attachments?: MessageAttachment[],
  ) => boolean | void;
  onAbort?: () => void;
  isStreaming?: boolean;
  editable?: boolean;
  sendEnabled?: boolean;
  placeholder?: string;
  trailingControls?: ReactNode;
  variant?: "primary" | "compact";
  className?: string;
}

type CaretDocument = Document & {
  caretRangeFromPoint?: (x: number, y: number) => Range | null;
  caretPositionFromPoint?: (x: number, y: number) => {
    offsetNode: Node;
    offset: number;
  } | null;
};

function nodeElement(node: Node): Element | null {
  return node.nodeType === Node.ELEMENT_NODE
    ? node as Element
    : node.parentElement;
}

function rangeAtPoint(editor: HTMLElement, x: number, y: number): Range | null {
  const pointElement = document.elementFromPoint(x, y);
  const fileToken = pointElement?.closest<HTMLElement>("[data-file-token]");
  if (fileToken && editor.contains(fileToken)) {
    const range = document.createRange();
    const bounds = fileToken.getBoundingClientRect();
    if (x < bounds.left + bounds.width / 2) range.setStartBefore(fileToken);
    else range.setStartAfter(fileToken);
    range.collapse(true);
    return range;
  }

  const caretDocument = document as CaretDocument;
  let range = caretDocument.caretRangeFromPoint?.(x, y) ?? null;
  if (!range) {
    const position = caretDocument.caretPositionFromPoint?.(x, y);
    if (position) {
      range = document.createRange();
      range.setStart(position.offsetNode, position.offset);
      range.collapse(true);
    }
  }
  if (range && editor.contains(nodeElement(range.startContainer))) return range;

  range = document.createRange();
  range.selectNodeContents(editor);
  range.collapse(false);
  return range;
}

function selectionRange(editor: HTMLElement): Range {
  const selection = window.getSelection();
  const current = selection?.rangeCount ? selection.getRangeAt(0) : null;
  if (current && editor.contains(nodeElement(current.startContainer))) {
    return current.cloneRange();
  }
  const range = document.createRange();
  range.selectNodeContents(editor);
  range.collapse(false);
  return range;
}

function placeCaretAfter(node: Node) {
  const selection = window.getSelection();
  if (!selection) return;
  const range = document.createRange();
  range.setStartAfter(node);
  range.collapse(true);
  selection.removeAllRanges();
  selection.addRange(range);
}

function insertText(editor: HTMLElement, text: string) {
  const range = selectionRange(editor);
  range.deleteContents();
  const node = document.createTextNode(text);
  range.insertNode(node);
  placeCaretAfter(node);
}

function collectComposerParts(node: Node, parts: ComposerPart[]) {
  if (node.nodeType === Node.TEXT_NODE) {
    parts.push({ type: "text", value: node.textContent ?? "" });
    return;
  }
  if (!(node instanceof HTMLElement)) return;

  if (node.dataset.fileToken) {
    const name = node.querySelector<HTMLElement>("[data-file-name]")?.textContent ?? "文件";
    parts.push({
      type: "file",
      name,
      path: node.dataset.attachmentStatus === "ready"
        ? node.dataset.absolutePath ?? null
        : null,
    });
    return;
  }
  if (node.tagName === "BR") {
    parts.push({ type: "text", value: "\n" });
    return;
  }

  Array.from(node.childNodes).forEach((child) => collectComposerParts(child, parts));
  if ((node.tagName === "DIV" || node.tagName === "P") && node.nextSibling) {
    parts.push({ type: "text", value: "\n" });
  }
}

function readComposerMessage(editor: HTMLElement) {
  const parts: ComposerPart[] = [];
  Array.from(editor.childNodes).forEach((node) => collectComposerParts(node, parts));
  return formatComposerMessage(parts);
}

function hasEditorContent(editor: HTMLElement): boolean {
  return Boolean(
    editor.querySelector("[data-file-token]") ||
    editor.textContent?.replace(/\u200b/g, "").trim(),
  );
}

function pointInside(element: HTMLElement, x: number, y: number): boolean {
  const bounds = element.getBoundingClientRect();
  return x >= bounds.left && x <= bounds.right && y >= bounds.top && y <= bounds.bottom;
}

function insertTokenAtRange(range: Range, token: HTMLElement): Range {
  range.insertNode(token);
  range.setStartAfter(token);
  range.collapse(true);
  return range;
}

function moveTokenByKeyboard(token: HTMLElement, direction: "left" | "right") {
  const sibling = direction === "left" ? token.previousSibling : token.nextSibling;
  if (!sibling) return;
  if (direction === "left") sibling.before(token);
  else sibling.after(token);
  token.focus();
}

export default function ConversationComposer({
  sessionId,
  onSendMessage,
  onAbort,
  isStreaming = false,
  editable = true,
  sendEnabled = true,
  placeholder = "输入消息...",
  trailingControls,
  variant = "primary",
  className = "",
}: ConversationComposerProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const editorRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const editableRef = useRef(editable);
  const expandedRef = useRef(false);
  const dragDepthRef = useRef(0);
  const [hasContent, setHasContent] = useState(false);
  const [pendingUploads, setPendingUploads] = useState(0);
  const [dragActive, setDragActive] = useState(false);
  const [canExpand, setCanExpand] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const errorId = useId();

  editableRef.current = editable;
  expandedRef.current = expanded;

  const refreshContentState = useCallback(() => {
    const editor = editorRef.current;
    if (!editor) return;
    setHasContent(hasEditorContent(editor));
    if (!expandedRef.current) {
      setCanExpand(shouldOfferComposerExpansion(editor.scrollHeight, editor.clientHeight));
    }
  }, []);

  const collapseComposer = useCallback(() => {
    expandedRef.current = false;
    setExpanded(false);
    window.requestAnimationFrame(refreshContentState);
  }, [refreshContentState]);

  const removeToken = useCallback((token: HTMLElement) => {
    token.remove();
    refreshContentState();
    editorRef.current?.focus();
  }, [refreshContentState]);

  const moveTokenAtPoint = useCallback((token: HTMLElement, x: number, y: number) => {
    const editor = editorRef.current;
    if (!editor || !pointInside(editor, x, y)) return;
    token.style.pointerEvents = "none";
    const range = rangeAtPoint(editor, x, y);
    token.style.pointerEvents = "";
    if (!range) return;
    insertTokenAtRange(range, token);
    placeCaretAfter(token);
    refreshContentState();
  }, [refreshContentState]);

  const createFileToken = useCallback((name: string, absolutePath?: string) => {
    const token = document.createElement("span");
    token.contentEditable = "false";
    token.tabIndex = 0;
    token.dataset.fileToken = crypto.randomUUID();
    token.dataset.attachmentStatus = absolutePath ? "ready" : "uploading";
    if (absolutePath) token.dataset.absolutePath = absolutePath;
    token.className = [
      "mx-1 inline-flex max-w-[min(18rem,75vw)] items-center gap-1 rounded-full",
      "border border-indigo-400/35 bg-indigo-500/15 px-2 py-0.5 align-baseline",
      "text-xs leading-5 text-indigo-100 outline-none transition-colors",
      "focus-visible:ring-2 focus-visible:ring-indigo-400/60",
    ].join(" ");
    token.setAttribute(
      "aria-label",
      `文件 ${name}。可拖动调整位置，按 Delete 删除`,
    );
    if (absolutePath) token.title = absolutePath;

    const status = document.createElement("span");
    status.dataset.fileStatus = "";
    status.className = absolutePath
      ? "h-1.5 w-1.5 shrink-0 rounded-full bg-indigo-300"
      : "h-3 w-3 shrink-0 animate-spin rounded-full border border-indigo-200/30 border-t-indigo-200 motion-reduce:animate-none";
    status.setAttribute("aria-hidden", "true");

    const label = document.createElement("span");
    label.dataset.fileName = "";
    label.className = "truncate";
    label.textContent = name;

    const remove = document.createElement("button");
    remove.type = "button";
    remove.tabIndex = -1;
    remove.className = "ml-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-indigo-200/70 hover:bg-indigo-200/15 hover:text-white";
    remove.setAttribute("aria-label", `移除文件 ${name}`);
    remove.textContent = "×";
    remove.addEventListener("pointerdown", (event) => event.stopPropagation());
    remove.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      removeToken(token);
    });

    token.append(status, label, remove);
    token.addEventListener("keydown", (event) => {
      event.stopPropagation();
      if (event.key === "Delete" || event.key === "Backspace") {
        event.preventDefault();
        removeToken(token);
      } else if (event.altKey && event.key === "ArrowLeft") {
        event.preventDefault();
        moveTokenByKeyboard(token, "left");
        refreshContentState();
      } else if (event.altKey && event.key === "ArrowRight") {
        event.preventDefault();
        moveTokenByKeyboard(token, "right");
        refreshContentState();
      }
    });
    token.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || !editableRef.current) return;
      event.preventDefault();
      const startX = event.clientX;
      const startY = event.clientY;
      let moved = false;

      const handleMove = (moveEvent: PointerEvent) => {
        if (Math.hypot(moveEvent.clientX - startX, moveEvent.clientY - startY) < 4) return;
        moved = true;
        token.classList.add("opacity-50", "cursor-grabbing");
      };
      const handleUp = (upEvent: PointerEvent) => {
        window.removeEventListener("pointermove", handleMove);
        window.removeEventListener("pointerup", handleUp);
        token.classList.remove("opacity-50", "cursor-grabbing");
        if (moved) moveTokenAtPoint(token, upEvent.clientX, upEvent.clientY);
        else token.focus();
      };
      window.addEventListener("pointermove", handleMove);
      window.addEventListener("pointerup", handleUp, { once: true });
    });

    return token;
  }, [moveTokenAtPoint, refreshContentState, removeToken]);

  const insertNativePaths = useCallback((paths: string[], x: number, y: number) => {
    const editor = editorRef.current;
    if (!editor || !editableRef.current || !pointInside(editor, x, y)) return;
    let range = rangeAtPoint(editor, x, y);
    if (!range) return;
    for (const path of paths) {
      range = insertTokenAtRange(range, createFileToken(getDroppedFileName(path), path));
    }
    editor.focus();
    refreshContentState();
    setAttachmentError(null);
  }, [createFileToken, refreshContentState]);

  useEffect(() => {
    if (!isDesktopRuntime()) return undefined;
    let disposed = false;
    let unlisten: (() => void) | undefined;

    void import("@tauri-apps/api/webview")
      .then(({ getCurrentWebview }) => getCurrentWebview().onDragDropEvent((event) => {
        const editor = editorRef.current;
        if (!editor || !editableRef.current) return;
        if (event.payload.type === "leave") {
          setDragActive(false);
          return;
        }
        const scale = window.devicePixelRatio || 1;
        const x = event.payload.position.x / scale;
        const y = event.payload.position.y / scale;
        const inside = pointInside(editor, x, y);
        setDragActive(inside);
        if (event.payload.type === "drop") {
          setDragActive(false);
          if (inside) insertNativePaths(event.payload.paths, x, y);
        }
      }))
      .then((cleanup) => {
        if (disposed) cleanup();
        else unlisten = cleanup;
      })
      .catch(() => {
        if (!disposed) setAttachmentError("桌面文件拖入暂不可用");
      });

    return () => {
      disposed = true;
      unlisten?.();
    };
  }, [insertNativePaths]);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    editor.replaceChildren();
    expandedRef.current = false;
    setHasContent(false);
    setPendingUploads(0);
    setCanExpand(false);
    setExpanded(false);
    setAttachmentError(null);
  }, [sessionId]);

  useEffect(() => {
    const editor = editorRef.current;
    if (!editor) return undefined;
    const observer = new ResizeObserver(() => {
      if (!expandedRef.current) refreshContentState();
    });
    observer.observe(editor);
    return () => observer.disconnect();
  }, [refreshContentState]);

  useEffect(() => {
    if (!expanded) return undefined;
    const closeOnOutside = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) collapseComposer();
    };
    document.addEventListener("pointerdown", closeOnOutside, true);
    return () => document.removeEventListener("pointerdown", closeOnOutside, true);
  }, [collapseComposer, expanded]);

  const insertBrowserFiles = useCallback((files: File[], initialRange: Range) => {
    const editor = editorRef.current;
    if (!editor || !editable || !sessionId || files.length === 0) return;

    let range = initialRange;
    setAttachmentError(null);
    setPendingUploads((count) => count + files.length);

    for (const file of files) {
      const token = createFileToken(file.name);
      range = insertTokenAtRange(range, token);
      void uploadWorkspaceAttachment(sessionId, file)
        .then((attachment) => {
          token.dataset.attachmentStatus = "ready";
          token.dataset.absolutePath = attachment.absolute_path;
          token.title = attachment.absolute_path;
          const status = token.querySelector<HTMLElement>("[data-file-status]");
          if (status) {
            status.className = "h-1.5 w-1.5 shrink-0 rounded-full bg-indigo-300";
          }
          const label = token.querySelector<HTMLElement>("[data-file-name]");
          if (label) label.textContent = attachment.name;
          token.setAttribute(
            "aria-label",
            `文件 ${attachment.name}。可拖动调整位置，按 Delete 删除`,
          );
        })
        .catch(() => {
          token.remove();
          setAttachmentError(`文件 ${file.name} 添加失败，请重试`);
        })
        .finally(() => {
          setPendingUploads((count) => Math.max(0, count - 1));
          refreshContentState();
        });
    }
    editor.focus();
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(range);
    refreshContentState();
  }, [createFileToken, editable, refreshContentState, sessionId]);

  const handleBrowserDrop = useCallback((event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    dragDepthRef.current = 0;
    setDragActive(false);
    const files = Array.from(event.dataTransfer.files);
    const editor = editorRef.current;
    if (!editor) return;
    const range = rangeAtPoint(editor, event.clientX, event.clientY);
    if (!range) return;
    insertBrowserFiles(files, range);
  }, [insertBrowserFiles]);

  const handleSend = useCallback(() => {
    const editor = editorRef.current;
    if (!editor || !sendEnabled || pendingUploads > 0) return;
    const message = readComposerMessage(editor);
    const content = message.content.trim();
    if (!content) return;
    const sent = onSendMessage(
      content,
      message.attachments.length > 0 ? message.attachments : undefined,
    );
    if (sent === false) return;
    editor.replaceChildren();
    expandedRef.current = false;
    setHasContent(false);
    setCanExpand(false);
    setExpanded(false);
    setAttachmentError(null);
  }, [onSendMessage, pendingUploads, sendEnabled]);

  const handleKeyDown = useCallback((event: KeyboardEvent<HTMLDivElement>) => {
    if (event.nativeEvent.isComposing) return;
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      handleSend();
    } else if (event.key === "Enter" && event.shiftKey) {
      event.preventDefault();
      if (editorRef.current) {
        insertText(editorRef.current, "\n");
        refreshContentState();
      }
    }
  }, [handleSend, refreshContentState]);

  const handlePaste = useCallback((event: ClipboardEvent<HTMLDivElement>) => {
    event.preventDefault();
    if (!editorRef.current) return;
    insertText(editorRef.current, event.clipboardData.getData("text/plain"));
    refreshContentState();
  }, [refreshContentState]);

  const isCompact = variant === "compact";
  const canSubmit = sendEnabled && hasContent && pendingUploads === 0;

  return (
    <div ref={rootRef} className={className}>
      <div
        className={`${isCompact ? "rounded-lg bg-slate-950" : "rounded-2xl bg-slate-800/80"} border p-2.5 transition-[border-color,box-shadow] duration-200 focus-within:border-indigo-500/60 focus-within:ring-2 focus-within:ring-indigo-500/35 ${
          dragActive
            ? "border-indigo-400 ring-2 ring-indigo-400/25"
            : "border-border/60"
        } ${editable ? "" : "opacity-50"}`}
      >
        <div className="relative">
          <div
            ref={editorRef}
            role="textbox"
            aria-label="聊天消息输入"
            aria-multiline="true"
            aria-disabled={!editable}
            aria-describedby={attachmentError ? errorId : undefined}
            contentEditable={editable}
            suppressContentEditableWarning
            data-placeholder={placeholder}
            onInput={refreshContentState}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            onDragEnter={(event) => {
              if (!editable || !event.dataTransfer.types.includes("Files")) return;
              event.preventDefault();
              dragDepthRef.current += 1;
              setDragActive(true);
            }}
            onDragOver={(event) => {
              if (!editable || !event.dataTransfer.types.includes("Files")) return;
              event.preventDefault();
              event.dataTransfer.dropEffect = "copy";
            }}
            onDragLeave={() => {
              dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
              if (dragDepthRef.current === 0) setDragActive(false);
            }}
            onDrop={handleBrowserDrop}
            className={`conversation-composer-editor w-full overflow-y-auto whitespace-pre-wrap break-words rounded-lg border-none bg-transparent px-2 py-1 text-sm text-foreground outline-none ${
              expanded
                ? "h-[calc(50vh-4.25rem)] min-h-48 max-h-none"
                : isCompact
                  ? "max-h-[200px] min-h-11"
                  : "max-h-32 min-h-12"
            } ${canExpand || expanded ? "pr-11" : ""} ${editable ? "" : "cursor-not-allowed"}`}
          />
          {canExpand || expanded ? (
            <button
              type="button"
              onPointerDown={(event) => event.preventDefault()}
              onClick={() => {
                if (expanded) collapseComposer();
                else setExpanded(true);
                window.requestAnimationFrame(() => editorRef.current?.focus());
              }}
              aria-label={expanded ? "收起输入框" : "展开输入框"}
              aria-pressed={expanded}
              title={expanded ? "收起输入框" : "展开输入框"}
              className="absolute right-1 top-1 flex h-8 w-8 items-center justify-center rounded-lg bg-slate-700/70 text-slate-400 transition-colors hover:bg-slate-700 hover:text-slate-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500/50"
            >
              {expanded ? <Minimize2 size={15} aria-hidden="true" /> : <Maximize2 size={15} aria-hidden="true" />}
            </button>
          ) : null}
        </div>
        <div className="mt-2 flex min-h-10 items-center justify-between gap-2">
          <div>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              tabIndex={-1}
              aria-hidden="true"
              className="hidden"
              onChange={(event) => {
                const files = Array.from(event.target.files ?? []);
                const editor = editorRef.current;
                if (editor && files.length > 0) {
                  insertBrowserFiles(files, selectionRange(editor));
                }
                event.target.value = "";
              }}
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={!editable || !sessionId}
              title="添加文件"
              aria-label="添加文件"
              className="flex h-10 w-10 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-indigo-500/10 hover:text-indigo-300 disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Paperclip size={17} aria-hidden="true" />
            </button>
          </div>
          <div className="flex items-center justify-end gap-2">
            {trailingControls}
            {isStreaming && onAbort ? (
            <button
              type="button"
              onClick={onAbort}
              title="中止输出"
              aria-label="中止输出"
              className="flex h-10 w-10 items-center justify-center rounded-full bg-red-500/20 text-red-400 transition-colors duration-200 hover:bg-red-500/40"
            >
              <Square size={17} className="fill-current" aria-hidden="true" />
            </button>
            ) : (
            <button
              type="button"
              onClick={handleSend}
              disabled={!canSubmit}
              aria-label={pendingUploads > 0 ? "正在添加文件" : "发送消息"}
              className={`flex h-10 w-10 items-center justify-center rounded-full transition-colors duration-200 ${
                canSubmit
                  ? "bg-indigo-500 text-white hover:bg-indigo-400"
                  : "cursor-not-allowed bg-slate-700 text-muted-foreground"
              }`}
            >
              {pendingUploads > 0 ? (
                <Loader2 size={17} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
              ) : (
                <Send size={17} aria-hidden="true" />
              )}
            </button>
            )}
          </div>
        </div>
      </div>
      {attachmentError ? (
        <p id={errorId} className="mt-1 text-xs text-red-400" role="alert">
          {attachmentError}
        </p>
      ) : null}
    </div>
  );
}
