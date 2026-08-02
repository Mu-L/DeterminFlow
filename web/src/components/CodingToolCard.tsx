/**
 * CodingToolCard - 编码工具 Rich 展示组件
 *
 * 根据编码工具类型，提供差异化的 Rich 渲染：
 * - read_file: 代码高亮 + 行号
 * - write_to_file: 代码高亮 + 成功提示
 * - replace_in_file: Inline diff 视图（红删绿增）
 * - execute_command: 终端风格输出
 * - search_files: 搜索结果列表
 * - list_files: 文件树视图
 * - list_code_definitions: 定义列表
 * - ask_user: 问答展示
 */

import { memo } from "react";
import {
  FileText,
  FolderOpen,
  FolderTree,
  MessageSquare,
  CheckCircle2,
  XCircle,
  Loader2,
  Pencil,
  Terminal,
  Search,
  Code2,
  FileCode2,
} from "lucide-react";

interface CodingToolCardProps {
  name: string;
  args: string;
  result?: string;
  status: "building" | "running" | "completed";
}

function tryParseJSON(str: string): Record<string, unknown> | null {
  try {
    return JSON.parse(str);
  } catch {
    return null;
  }
}

// ============ Args 展示子组件 ============

function FilePathBadge({ path }: { path: string }) {
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-slate-800 text-xs font-mono text-cyan-400 border border-cyan-500/20">
      <FileText className="w-3 h-3 flex-shrink-0" aria-hidden="true" />
      {path}
    </span>
  );
}

function CommandBadge({ command }: { command: string }) {
  return (
    <div className="mt-1 px-3 py-1.5 rounded bg-slate-900 border border-slate-700 font-mono text-xs text-amber-400 flex items-center gap-1.5">
      <Terminal className="w-3 h-3 flex-shrink-0" aria-hidden="true" />
      <span>$ {command}</span>
    </div>
  );
}

// ============ Result 展示子组件 ============

function CodeBlock({ content, showLineNumbers = false }: { content: string; showLineNumbers?: boolean }) {
  const lines = content.split("\n");
  return (
    <div className="mt-1.5 bg-slate-900/80 rounded border border-slate-700/50 overflow-hidden">
      <pre className="p-2 text-xs overflow-x-auto max-h-64 overflow-y-auto">
        <code>
          {lines.map((line, i) => (
            <div key={i} className="flex">
              {showLineNumbers && (
                <span className="select-none text-slate-600 w-10 text-right pr-2 flex-shrink-0">{i + 1}</span>
              )}
              <span className="text-slate-300">{line}</span>
            </div>
          ))}
        </code>
      </pre>
    </div>
  );
}

function TerminalOutput({ output, exitCode }: { output: string; exitCode?: number }) {
  return (
    <div className="mt-1.5 bg-slate-900 rounded border border-slate-700/50 overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-1.5 bg-slate-900/50 border-b border-slate-700/30">
        <div className="flex gap-1" aria-hidden="true">
          <span className="w-2.5 h-2.5 rounded-full bg-red-500/70" />
          <span className="w-2.5 h-2.5 rounded-full bg-yellow-500/70" />
          <span className="w-2.5 h-2.5 rounded-full bg-green-500/70" />
        </div>
        <span className="text-xs text-slate-500 font-mono">terminal</span>
        {exitCode !== undefined && (
          <span className={`ml-auto text-xs font-mono ${exitCode === 0 ? "text-green-400" : "text-red-400"}`}>
            exit: {exitCode}
          </span>
        )}
      </div>
      <pre className="p-2 text-xs text-green-300/90 font-mono overflow-x-auto max-h-48 overflow-y-auto whitespace-pre-wrap">
        {output || "(no output)"}
      </pre>
    </div>
  );
}

function DiffView({ oldStr, newStr }: { oldStr: string; newStr: string }) {
  const oldLines = oldStr.split("\n");
  const newLines = newStr.split("\n");
  return (
    <div className="mt-1.5 bg-slate-900/80 rounded border border-slate-700/50 overflow-hidden">
      <pre className="p-2 text-xs overflow-x-auto max-h-48 overflow-y-auto">
        {oldLines.map((line, i) => (
          <div key={`old-${i}`} className="bg-red-500/10 text-red-300">
            <span className="select-none text-red-500/50 mr-2" aria-hidden="true">-</span>{line}
          </div>
        ))}
        {newLines.map((line, i) => (
          <div key={`new-${i}`} className="bg-green-500/10 text-green-300">
            <span className="select-none text-green-500/50 mr-2" aria-hidden="true">+</span>{line}
          </div>
        ))}
      </pre>
    </div>
  );
}

function SearchResults({ matches }: { matches: { file: string; line: number; content: string }[] }) {
  return (
    <div className="mt-1.5 space-y-1 max-h-48 overflow-y-auto" role="list" aria-label="搜索结果">
      {matches.slice(0, 20).map((m, i) => (
        <div key={i} role="listitem" className="flex gap-2 text-xs px-2 py-1 rounded bg-slate-900/60 border border-slate-700/30">
          <span className="text-cyan-400 font-mono flex-shrink-0">{m.file}:{m.line}</span>
          <span className="text-slate-400 truncate">{m.content}</span>
        </div>
      ))}
      {matches.length > 20 && (
        <div className="text-xs text-slate-500 px-2">... 还有 {matches.length - 20} 条结果</div>
      )}
    </div>
  );
}

function FileTree({ entries }: { entries: string[] }) {
  return (
    <div className="mt-1.5 bg-slate-900/60 rounded border border-slate-700/30 p-2 max-h-48 overflow-y-auto" role="list" aria-label="文件列表">
      {entries.slice(0, 100).map((entry, i) => {
        const isDir = entry.endsWith("/");
        const depth = (entry.match(/\//g) || []).length - (isDir ? 1 : 0);
        return (
          <div key={i} role="listitem" className="text-xs font-mono" style={{ paddingLeft: `${depth * 12}px` }}>
            <span className="inline-flex items-center gap-1">
              {isDir ? (
                <FolderOpen className="w-3 h-3 text-cyan-400 flex-shrink-0" aria-hidden="true" />
              ) : (
                <FileText className="w-3 h-3 text-slate-400 flex-shrink-0" aria-hidden="true" />
              )}
              <span className={isDir ? "text-cyan-400" : "text-slate-400"}>
                {entry.split("/").pop()}
              </span>
            </span>
          </div>
        );
      })}
      {entries.length > 100 && (
        <div className="text-xs text-slate-500 mt-1">... 还有 {entries.length - 100} 个条目</div>
      )}
    </div>
  );
}

function DefinitionsList({ definitions }: { definitions: { line: number; kind: string; name: string; text: string }[] }) {
  const kindColors: Record<string, string> = {
    function: "text-amber-400",
    class: "text-purple-400",
    method: "text-green-400",
    interface: "text-cyan-400",
    type: "text-indigo-400",
    struct: "text-rose-400",
    enum: "text-amber-400",
    trait: "text-cyan-400",
    impl: "text-slate-400",
    namespace: "text-slate-400",
  };

  const kindIcons: Record<string, typeof Code2> = {
    function: Code2,
    class: FileCode2,
    method: Code2,
    interface: FileCode2,
    type: FileCode2,
    struct: FileCode2,
    enum: FileCode2,
    trait: FileCode2,
    impl: Code2,
    namespace: FolderTree,
  };

  return (
    <div className="mt-1.5 space-y-0.5 max-h-48 overflow-y-auto" role="list" aria-label="代码定义列表">
      {definitions.map((d, i) => {
        const KindIcon = kindIcons[d.kind] || Code2;
        return (
          <div key={i} role="listitem" className="flex items-center gap-2 text-xs px-2 py-1 rounded bg-slate-900/60">
            <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-mono ${kindColors[d.kind] || "text-slate-400"} bg-slate-800`}>
              <KindIcon className="w-3 h-3" aria-hidden="true" />
              {d.kind}
            </span>
            <span className="text-slate-300 font-medium">{d.name}</span>
            <span className="text-slate-600 font-mono ml-auto">L{d.line}</span>
          </div>
        );
      })}
    </div>
  );
}

// ============ 主组件 ============

function CodingToolCard({ name, args, result, status }: CodingToolCardProps) {
  const parsedArgs = tryParseJSON(args) || {};
  const parsed = result ? tryParseJSON(result) : null;

  // 状态颜色
  const statusLabel = status === "building" ? "生成参数..." : status === "running" ? "执行中..." : "完成";
  const statusBadgeBg = status === "completed" ? "bg-green-500/20 text-green-400" : "bg-amber-500/20 text-amber-400";
  return (
    <div className="mb-2 ml-10">
      <div
        role="article"
        aria-label={`编码工具 ${name} - ${statusLabel}`}
        className={`px-3 py-2 bg-slate-800/50 border border-slate-700/40 rounded-lg transition-colors duration-200 ${status === "running" ? "animate-pulse-slow motion-reduce:animate-none" : ""}`}
      >
        {/* 头部 */}
        <div className="flex items-center gap-2">
          {status === "running" ? (
            <Loader2 className="w-4 h-4 animate-spin motion-reduce:animate-none text-amber-400" aria-hidden="true" />
          ) : status === "building" ? (
            <Pencil className="w-4 h-4 text-amber-400 animate-pulse motion-reduce:animate-none" aria-hidden="true" />
          ) : (
            <CheckCircle2 className="w-4 h-4 text-green-400" aria-hidden="true" />
          )}
          <span className="text-sm font-medium text-amber-400">{name}</span>
          <span className={`text-xs px-2 py-0.5 rounded-full ${statusBadgeBg}`} role="status" aria-label={statusLabel}>
            {statusLabel}
          </span>
        </div>

        {/* Args 展示 */}
        {renderArgs(name, parsedArgs)}

        {/* Result 展示 */}
        {result && status === "completed" && renderResult(name, result, parsed)}
      </div>
    </div>
  );
}

export default memo(CodingToolCard);

function renderArgs(name: string, args: Record<string, unknown>) {
  switch (name) {
    case "read_file":
    case "write_to_file":
    case "list_code_definitions":
      return (
        <div className="mt-1.5 flex items-center gap-2 flex-wrap">
          {!!args.path && <FilePathBadge path={String(args.path)} />}
          {!!args.offset && <span className="text-xs text-slate-500">offset: {String(args.offset)}</span>}
          {!!args.limit && <span className="text-xs text-slate-500">limit: {String(args.limit)}</span>}
        </div>
      );

    case "replace_in_file":
      return (
        <div className="mt-1.5">
          {!!args.path && <FilePathBadge path={String(args.path)} />}
          {!!args.old_str && !!args.new_str && (
            <DiffView oldStr={String(args.old_str)} newStr={String(args.new_str)} />
          )}
        </div>
      );

    case "execute_command":
      return args.command ? <CommandBadge command={String(args.command)} /> : null;

    case "search_files":
      return (
        <div className="mt-1.5 flex items-center gap-2 flex-wrap">
          {!!args.path && <FilePathBadge path={String(args.path)} />}
          {!!args.regex && (
            <span className="px-2 py-0.5 rounded bg-slate-800 text-xs font-mono text-purple-400 border border-purple-500/20 inline-flex items-center gap-1">
              <Search className="w-3 h-3" aria-hidden="true" />
              /{String(args.regex)}/
            </span>
          )}
          {!!args.file_pattern && (
            <span className="text-xs text-slate-500">glob: {String(args.file_pattern)}</span>
          )}
        </div>
      );

    case "list_files":
      return (
        <div className="mt-1.5 flex items-center gap-2">
          {!!args.path && <FilePathBadge path={String(args.path)} />}
          <span className="text-xs text-slate-500">{args.recursive !== false ? "递归" : "非递归"}</span>
        </div>
      );

    case "ask_user":
      return args.question ? (
        <div className="mt-1.5 px-3 py-2 rounded bg-indigo-500/10 border border-indigo-500/20 text-sm text-slate-200 flex items-start gap-2">
          <MessageSquare className="w-4 h-4 text-indigo-400 flex-shrink-0 mt-0.5" aria-hidden="true" />
          <span>{String(args.question)}</span>
        </div>
      ) : null;

    default:
      return Object.keys(args).length > 0 ? (
        <div className="mt-1.5 text-xs">
          <pre className="bg-slate-900/60 rounded p-1.5 overflow-x-auto text-slate-400">
            {JSON.stringify(args, null, 2)}
          </pre>
        </div>
      ) : null;
  }
}

function renderResult(name: string, result: string, parsed: Record<string, unknown> | null) {
  if (!parsed) {
    // JSON 解析失败，直接展示
    return (
      <div className="mt-1.5 text-xs text-slate-400">
        <div className="bg-slate-900/60 rounded p-1.5 overflow-x-auto max-h-32 overflow-y-auto">
          {result.length > 500 ? result.slice(0, 500) + "..." : result}
        </div>
      </div>
    );
  }

  // 错误处理
  if (parsed.error) {
    return (
      <div className="mt-1.5 px-2 py-1.5 rounded bg-red-500/10 border border-red-500/20 text-xs text-red-300 flex items-center gap-1.5">
        <XCircle className="w-3.5 h-3.5 flex-shrink-0" aria-hidden="true" />
        <span>{String(parsed.error)}</span>
      </div>
    );
  }

  switch (name) {
    case "read_file": {
      const content = String(parsed.content || "");
      const totalLines = parsed.total_lines as number | undefined;
      return (
        <div>
          {totalLines && (
            <div className="mt-1 text-xs text-slate-500">
              共 {totalLines} 行 {parsed.showing ? `· 显示 ${parsed.showing}` : ""}
            </div>
          )}
          <CodeBlock content={content} />
        </div>
      );
    }

    case "write_to_file":
      return (
        <div className="mt-1.5 px-2 py-1.5 rounded bg-green-500/10 border border-green-500/20 text-xs text-green-400 flex items-center gap-1.5">
          <CheckCircle2 className="w-3.5 h-3.5 flex-shrink-0" aria-hidden="true" />
          <span>{String(parsed.message || "文件已写入")}</span>
          {!!parsed.bytes_written && <span className="ml-2 text-slate-500">({String(parsed.bytes_written)} bytes)</span>}
        </div>
      );

    case "replace_in_file":
      return (
        <div className="mt-1.5 px-2 py-1.5 rounded bg-green-500/10 border border-green-500/20 text-xs text-green-400 flex items-center gap-1.5">
          <CheckCircle2 className="w-3.5 h-3.5 flex-shrink-0" aria-hidden="true" />
          <span>{String(parsed.message || "文件已更新")}</span>
        </div>
      );

    case "execute_command": {
      const output = String(parsed.output || "");
      const exitCode = parsed.exit_code as number | undefined;
      return <TerminalOutput output={output} exitCode={exitCode} />;
    }

    case "search_files": {
      const matches = (parsed.matches || []) as { file: string; line: number; content: string }[];
      const total = parsed.total as number | undefined;
      return (
        <div>
          <div className="mt-1 text-xs text-slate-500">
            {total || matches.length} 条匹配 · {String(parsed.engine || "python")}
          </div>
          <SearchResults matches={matches} />
        </div>
      );
    }

    case "list_files": {
      const entries = (parsed.entries || []) as string[];
      return (
        <div>
          <div className="mt-1 text-xs text-slate-500">
            {Number(parsed.total || entries.length)} 个条目 {parsed.truncated ? "(已截断)" : ""}
          </div>
          <FileTree entries={entries} />
        </div>
      );
    }

    case "list_code_definitions": {
      const definitions = (parsed.definitions || []) as { line: number; kind: string; name: string; text: string }[];
      return (
        <div>
          <div className="mt-1 text-xs text-slate-500">
            {definitions.length} 个定义 · {String(parsed.file || "")}
          </div>
          <DefinitionsList definitions={definitions} />
        </div>
      );
    }

    case "ask_user":
      return parsed.needs_user_input ? (
        <div className="mt-1.5 px-2 py-1.5 rounded bg-indigo-500/10 border border-indigo-500/20 text-xs text-indigo-400 flex items-center gap-1.5">
          <Loader2 className="w-3.5 h-3.5 animate-spin motion-reduce:animate-none flex-shrink-0" aria-hidden="true" />
          <span>等待用户输入...</span>
        </div>
      ) : null;

    default:
      return (
        <div className="mt-1.5 text-xs text-slate-400">
          <div className="bg-slate-900/60 rounded p-1.5 overflow-x-auto max-h-32 overflow-y-auto">
            {result.length > 500 ? result.slice(0, 500) + "..." : result}
          </div>
        </div>
      );
  }
}
