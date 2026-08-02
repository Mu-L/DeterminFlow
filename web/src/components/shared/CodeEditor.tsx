/**
 * CodeEditor — 基于 CodeMirror 6 的简易代码编辑器
 *
 * 支持语法高亮、行号、自动缩进，按需加载语言包。
 *
 * Props:
 * - value: 代码内容
 * - onChange: 内容变更回调
 * - language: 编程语言（"shell" | "python"），默认 "shell"
 * - readOnly: 是否只读，默认 false
 * - height: 编辑器高度，默认 "280px"
 * - placeholder: 占位文本
 */
import { useEffect, useRef, useCallback } from "react";
import { EditorView, keymap, lineNumbers, highlightActiveLine, placeholder as cmPlaceholder } from "@codemirror/view";
import { EditorState } from "@codemirror/state";
import { defaultKeymap, indentWithTab } from "@codemirror/commands";
import { python } from "@codemirror/lang-python";
import { javascript } from "@codemirror/lang-javascript";
import { LanguageSupport } from "@codemirror/language";

// 使用设计令牌的暗色主题
const darkTheme = EditorView.theme(
  {
    "&": {
      backgroundColor: "hsl(var(--background))",
      color: "hsl(var(--foreground))",
      borderRadius: "8px",
      border: "1px solid hsl(var(--border))",
    },
    "&.cm-focused": {
      outline: "none",
      borderColor: "hsl(var(--ring))",
    },
    ".cm-content": {
      fontFamily: "'Fira Code', 'Cascadia Code', 'JetBrains Mono', monospace",
      fontSize: "13px",
      lineHeight: "1.6",
      padding: "12px 0",
      caretColor: "hsl(var(--primary))",
    },
    ".cm-line": {
      padding: "0 12px",
    },
    ".cm-activeLine": {
      backgroundColor: "hsl(var(--primary) / 0.08)",
    },
    ".cm-gutters": {
      backgroundColor: "hsl(var(--background))",
      color: "hsl(var(--muted-foreground))",
      border: "none",
      borderRight: "1px solid hsl(var(--border))",
    },
    ".cm-cursor": {
      borderLeftColor: "hsl(var(--primary))",
    },
    ".cm-selectionBackground": {
      backgroundColor: "hsl(var(--primary) / 0.25)",
    },
    ".cm-selectionMatch": {
      backgroundColor: "hsl(var(--primary) / 0.1)",
    },
    "&.cm-editor.cm-focused .cm-selectionBackground": {
      backgroundColor: "hsl(var(--primary) / 0.3)",
    },
    ".cm-tooltip": {
      backgroundColor: "hsl(var(--card))",
      border: "1px solid hsl(var(--border))",
      color: "hsl(var(--foreground))",
    },
  },
  { dark: true },
);

// shell 语法高亮（基于 JavaScript mode，添加 shell 关键词）
const shellHighlight = javascript({
  typescript: false,
});

function getLanguageSupport(lang: string): LanguageSupport {
  switch (lang) {
    case "python":
      return python();
    case "shell":
    default:
      // 使用 JavaScript mode 模拟 shell 语法高亮
      // 注意：对于复杂的 shell 脚本，建议安装专门的 shell 语法包
      return shellHighlight;
  }
}

interface CodeEditorProps {
  value: string;
  onChange: (value: string) => void;
  language?: "shell" | "python";
  readOnly?: boolean;
  height?: string;
  placeholder?: string;
}

export default function CodeEditor({
  value,
  onChange,
  language = "shell",
  readOnly = false,
  height = "280px",
  placeholder: ph = "输入脚本内容...",
}: CodeEditorProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const valueRef = useRef(value);
  valueRef.current = value;

  // 受控更新（外部 value 变更 → 同步到编辑器）
  const syncValue = useCallback((newVal: string) => {
    if (!viewRef.current) return;
    const currentVal = viewRef.current.state.doc.toString();
    if (newVal !== currentVal) {
      viewRef.current.dispatch({
        changes: { from: 0, to: currentVal.length, insert: newVal },
      });
    }
  }, []);

  // 语言切换时重建编辑器
  useEffect(() => {
    if (!containerRef.current) return;

    // 清理旧编辑器
    if (viewRef.current) {
      viewRef.current.destroy();
      viewRef.current = null;
    }

    const extensions = [
      lineNumbers(),
      highlightActiveLine(),
      darkTheme,
      getLanguageSupport(language),
      keymap.of([...defaultKeymap, indentWithTab]),
      EditorView.updateListener.of((update) => {
        if (update.docChanged) {
          const newVal = update.state.doc.toString();
          if (newVal !== valueRef.current) {
            onChange(newVal);
          }
        }
      }),
    ];

    if (readOnly) {
      extensions.push(EditorState.readOnly.of(true));
    }

    if (ph) {
      extensions.push(cmPlaceholder(ph));
    }

    const state = EditorState.create({
      doc: valueRef.current,
      extensions,
    });

    const view = new EditorView({
      state,
      parent: containerRef.current,
    });

    viewRef.current = view;

    return () => {
      if (viewRef.current) {
        viewRef.current.destroy();
        viewRef.current = null;
      }
    };
  }, [language, readOnly, onChange, ph]);

  // 外部 value 变更时同步
  useEffect(() => {
    syncValue(value);
  }, [value, syncValue]);

  // 高度样式
  useEffect(() => {
    if (viewRef.current?.dom) {
      viewRef.current.dom.style.height = height;
    }
  }, [height]);

  return (
    <div
      ref={containerRef}
      className="overflow-hidden rounded-lg"
      style={{ height }}
      role="textbox"
      aria-label={`代码编辑器 - ${language}语言`}
      aria-multiline="true"
      aria-readonly={readOnly}
    />
  );
}
