/**
 * WorkspaceExplorer - Workspace 文件浏览器
 *
 * 显示当前选中 session 的 workspace 文件树：
 * - 文件/文件夹图标 + 缩进层级
 * - 点击文件可预览内容（调用 workspace REST API）
 * - 显示 workspace 路径
 */
import { useState, useEffect, useCallback } from "react";
import {
  FolderOpen, File, ChevronRight, ChevronDown,
  RefreshCw, Code, X, FolderClosed,
} from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import { fetchWorkspaceTree, fetchWorkspaceFile } from "../lib/api";

interface WorkspaceExplorerProps {
  sessionId: string | null;
  workspacePath?: string;
}

interface TreeEntry {
  path: string;
  type: "file" | "directory";
}

interface TreeNode {
  name: string;
  path: string;
  type: "file" | "directory";
  children: TreeNode[];
}

function buildTree(entries: TreeEntry[]): TreeNode[] {
  const root: TreeNode[] = [];
  const nodeMap = new Map<string, TreeNode>();

  // Sort: directories first, then alphabetical
  const sorted = [...entries].sort((a, b) => {
    if (a.type !== b.type) return a.type === "directory" ? -1 : 1;
    return a.path.localeCompare(b.path);
  });

  for (const entry of sorted) {
    const parts = entry.path.replace(/\\/g, "/").split("/");
    let currentLevel = root;
    let currentPath = "";

    for (let i = 0; i < parts.length; i++) {
      const part = parts[i];
      currentPath = currentPath ? `${currentPath}/${part}` : part;
      const isLast = i === parts.length - 1;

      let existing = nodeMap.get(currentPath);
      if (!existing) {
        existing = {
          name: part,
          path: currentPath,
          type: isLast ? entry.type : "directory",
          children: [],
        };
        nodeMap.set(currentPath, existing);
        currentLevel.push(existing);
      }
      currentLevel = existing.children;
    }
  }

  return root;
}

function TreeItem({
  node, depth, onFileClick, selectedFile,
}: {
  node: TreeNode;
  depth: number;
  onFileClick: (path: string) => void;
  selectedFile: string | null;
}) {
  const [expanded, setExpanded] = useState(depth < 1);
  const isDir = node.type === "directory";
  const isSelected = selectedFile === node.path;

  return (
    <div role="treeitem" aria-expanded={isDir ? expanded : undefined} aria-selected={isSelected}>
      <button
        onClick={() => {
          if (isDir) {
            setExpanded(!expanded);
          } else {
            onFileClick(node.path);
          }
        }}
        className={`w-full flex items-center gap-1 py-0.5 px-1 rounded text-xs hover:bg-white/5 transition-colors cursor-pointer min-h-[44px] ${
          isSelected ? "bg-indigo-500/10 text-indigo-500" : "text-slate-300"
        }`}
        style={{ paddingLeft: `${depth * 14 + 4}px` }}
        aria-label={isDir ? `${expanded ? '折叠' : '展开'}文件夹 ${node.name}` : `打开文件 ${node.name}`}
      >
        {isDir ? (
          expanded ? (
            <ChevronDown size={14} className="text-slate-500 flex-shrink-0" aria-hidden="true" />
          ) : (
            <ChevronRight size={14} className="text-slate-500 flex-shrink-0" aria-hidden="true" />
          )
        ) : (
          <span className="w-3 flex-shrink-0" aria-hidden="true" />
        )}
        {isDir ? (
          expanded ? (
            <FolderOpen size={14} className="text-cyan-500 flex-shrink-0" aria-hidden="true" />
          ) : (
            <FolderClosed size={14} className="text-cyan-500/70 flex-shrink-0" aria-hidden="true" />
          )
        ) : (
          <File size={14} className="text-slate-500 flex-shrink-0" aria-hidden="true" />
        )}
        <span className="truncate">{node.name}</span>
      </button>
      {isDir && expanded && node.children.length > 0 && (
        <div role="group">
          {node.children.map((child) => (
            <TreeItem
              key={child.path}
              node={child}
              depth={depth + 1}
              onFileClick={onFileClick}
              selectedFile={selectedFile}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default function WorkspaceExplorer({ sessionId, workspacePath }: WorkspaceExplorerProps) {
  const [entries, setEntries] = useState<TreeEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<string | null>(null);
  const [fileLoading, setFileLoading] = useState(false);

  const loadTree = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    setError(null);
    try {
      const data = await fetchWorkspaceTree(sessionId);
      setEntries(data.entries || []);
    } catch (e) {
      setError((e as Error).message || "加载失败");
      setEntries([]);
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    setSelectedFile(null);
    setFileContent(null);
    if (sessionId) {
      loadTree();
    } else {
      setEntries([]);
    }
  }, [sessionId, loadTree]);

  const handleFileClick = useCallback(async (path: string) => {
    if (!sessionId) return;
    if (selectedFile === path) {
      // Toggle off
      setSelectedFile(null);
      setFileContent(null);
      return;
    }
    setSelectedFile(path);
    setFileLoading(true);
    try {
      const data = await fetchWorkspaceFile(sessionId, path);
      setFileContent(data.content);
    } catch {
      setFileContent("// 无法加载文件内容");
    } finally {
      setFileLoading(false);
    }
  }, [sessionId, selectedFile]);

  if (!sessionId) {
    return (
      <div className="h-full flex items-center justify-center p-4">
        <div className="text-center text-muted-foreground text-sm" role="status">
          <Code size={24} className="mx-auto mb-2 opacity-40" aria-hidden="true" />
          <p>选择一个会话以查看其工作空间</p>
        </div>
      </div>
    );
  }

  if (!workspacePath) {
    return (
      <div className="h-full flex items-center justify-center p-4">
        <div className="text-center text-muted-foreground text-sm" role="status">
          <Code size={24} className="mx-auto mb-2 opacity-40" aria-hidden="true" />
          <p>该会话没有工作空间</p>
          <p className="text-xs mt-1">请确保 server 已正确初始化 workspace</p>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="px-3 py-2 border-b border-border/50 flex-shrink-0">
        <div className="flex items-center justify-between mb-1">
          <div className="flex items-center gap-1.5">
            <Code size={14} className="text-cyan-500" aria-hidden="true" />
            <span className="text-xs font-medium text-slate-200">工作空间</span>
            <Badge variant="outline" className="text-xs text-muted-foreground border-muted-foreground/30">
              {entries.length}
            </Badge>
          </div>
          <button
            onClick={loadTree}
            disabled={loading}
            className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-slate-800/60 transition-colors cursor-pointer disabled:opacity-40 min-h-[44px] min-w-[44px] flex items-center justify-center"
            aria-label="刷新工作空间文件树"
          >
            <RefreshCw size={12} className={loading ? "animate-spin" : ""} aria-hidden="true" />
          </button>
        </div>
        <div className="text-xs text-muted-foreground font-mono truncate" title={workspacePath}>
          {workspacePath}
        </div>
      </div>

      {loading && entries.length === 0 && (
        <div className="flex items-center justify-center p-4 text-muted-foreground text-sm gap-2" role="status" aria-label="加载中">
          <RefreshCw size={14} className="animate-spin" aria-hidden="true" />
          加载中...
        </div>
      )}

      {error && (
        <div className="px-3 py-2 text-xs text-red-500" role="alert">{error}</div>
      )}

      {/* File Tree */}
      {!selectedFile ? (
        <ScrollArea className="flex-1">
          <div className="py-1" role="tree" aria-label="工作空间文件树">
            {buildTree(entries).map((node) => (
              <TreeItem
                key={node.path}
                node={node}
                depth={0}
                onFileClick={handleFileClick}
                selectedFile={selectedFile}
              />
            ))}
            {entries.length === 0 && !loading && !error && (
              <div className="text-center text-muted-foreground text-xs py-4" role="status">空目录</div>
            )}
          </div>
        </ScrollArea>
      ) : (
        /* File Preview */
        <div className="flex-1 flex flex-col overflow-hidden">
          <div className="px-3 py-1.5 border-b border-border/50 flex items-center gap-2 flex-shrink-0">
            <File size={12} className="text-slate-500" aria-hidden="true" />
            <span className="text-xs font-mono text-cyan-500 truncate flex-1">{selectedFile}</span>
            <button
              onClick={() => { setSelectedFile(null); setFileContent(null); }}
              className="p-0.5 rounded text-muted-foreground hover:text-foreground cursor-pointer min-h-[44px] min-w-[44px] flex items-center justify-center"
              aria-label="关闭文件预览"
            >
              <X size={12} aria-hidden="true" />
            </button>
          </div>
          <ScrollArea className="flex-1">
            {fileLoading ? (
              <div className="flex items-center justify-center p-4 text-muted-foreground text-sm gap-2" role="status" aria-label="加载文件内容中">
                <RefreshCw size={12} className="animate-spin" aria-hidden="true" />
                加载中...
              </div>
            ) : (
              <pre className="p-2 text-xs font-mono text-slate-300 leading-relaxed whitespace-pre-wrap">
                {fileContent ?? ""}
              </pre>
            )}
          </ScrollArea>
        </div>
      )}
    </div>
  );
}
