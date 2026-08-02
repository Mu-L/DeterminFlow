/**
 * ScriptLibraryPanel - 脚本库管理面板
 *
 * - 左侧：分组列表（可增删）
 * - 右侧：选中分组下的脚本列表（可增删改）
 * - 点击脚本：展开内联编辑器（代码 + SCRIPT.md 元信息）
 */
import { useState, useEffect, useCallback } from "react";
import { Plus, Trash2, Folder, FileCode } from "lucide-react";
import { CodeEditor } from "../shared";
import {
  fetchScriptLibraryGroups,
  fetchScriptLibraryScripts,
  getLibraryScript,
  saveLibraryScript,
  deleteLibraryScript,
  getLibraryScriptMeta,
  saveLibraryScriptMeta,
  deleteLibraryGroup,
} from "../../lib/api";
import type { ScriptLibraryGroup, ScriptLibraryScript } from "../../types";

const DIALOG_BASE = "p-6 w-full max-w-md rounded-2xl bg-slate-900 border border-indigo-500/20 shadow-2xl";

export default function ScriptLibraryPanel() {
  const [groups, setGroups] = useState<ScriptLibraryGroup[]>([]);
  const [scripts, setScripts] = useState<ScriptLibraryScript[]>([]);
  const [selectedGroup, setSelectedGroup] = useState<string>("");
  const [selectedScript, setSelectedScript] = useState<ScriptLibraryScript | null>(null);
  const [scriptContent, setScriptContent] = useState("");
  const [metaContent, setMetaContent] = useState("");
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState(false);

  // Dialogs
  const [showNewGroup, setShowNewGroup] = useState(false);
  const [newGroupName, setNewGroupName] = useState("");
  const [showNewScript, setShowNewScript] = useState(false);
  const [newScriptName, setNewScriptName] = useState("");
  const [newScriptType, setNewScriptType] = useState<"shell" | "python">("shell");

  const loadGroups = useCallback(async () => {
    try {
      const gs = await fetchScriptLibraryGroups();
      setGroups(gs);
    } catch (e) { console.error("加载分组失败:", e); }
  }, []);

  const loadScripts = useCallback(async (group: string) => {
    if (!group) return;
    try {
      const ss = await fetchScriptLibraryScripts(group);
      setScripts(ss);
    } catch (e) { console.error("加载脚本列表失败:", e); }
  }, []);

  useEffect(() => { loadGroups(); }, [loadGroups]);

  useEffect(() => {
    if (selectedGroup) loadScripts(selectedGroup);
    else setScripts([]);
  }, [selectedGroup, loadScripts]);

  const handleSelectGroup = (group: string) => {
    setSelectedGroup(group);
    setSelectedScript(null);
    setScriptContent("");
  };

  const handleSelectScript = async (s: ScriptLibraryScript) => {
    setSelectedScript(s);
    setEditing(false);
    setLoading(true);
    try {
      const [scriptRes, metaRes] = await Promise.all([
        getLibraryScript(s.group, s.name, s.script_type),
        getLibraryScriptMeta(s.group, s.name),
      ]);
      setScriptContent(scriptRes.content || "");
      setMetaContent(metaRes.content || "");
    } catch {
      setScriptContent("");
      setMetaContent("");
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    if (!selectedScript) return;
    try {
      await saveLibraryScript(selectedScript.group, selectedScript.name, selectedScript.script_type, scriptContent);
      await saveLibraryScriptMeta(selectedScript.group, selectedScript.name, metaContent);
      setEditing(false);
    } catch (e) { console.error("保存失败:", e); }
  };

  const handleDeleteScript = async () => {
    if (!selectedScript) return;
    if (!window.confirm(`确认删除脚本 "${selectedScript.group}/${selectedScript.name}"？`)) return;
    try {
      await deleteLibraryScript(selectedScript.group, selectedScript.name);
      setSelectedScript(null);
      loadGroups();
      loadScripts(selectedGroup);
    } catch (e) { console.error("删除失败:", e); }
  };

  const handleCreateGroup = async () => {
    const name = newGroupName.trim();
    if (!name) return;
    try {
      // 创建一个初始脚本，用于持久化分组目录
      await saveLibraryScript(name, "_init", "shell", "# 分组初始化脚本\n# 可删除此脚本，分组在所有脚本删除后自动清理\n");
      setShowNewGroup(false);
      setNewGroupName("");
      await loadGroups();
      // 自动选择新分组
      setSelectedGroup(name);
      loadScripts(name);
    } catch (e: unknown) {
      alert("创建分组失败: " + (e instanceof Error ? e.message : "未知错误"));
    }
  };

  const handleDeleteGroup = async (group: string) => {
    if (!window.confirm(`确认删除分组 "${group}"？分组内必须无脚本。`)) return;
    try {
      await deleteLibraryGroup(group);
      if (selectedGroup === group) {
        setSelectedGroup("");
        setSelectedScript(null);
      }
      loadGroups();
    } catch (e: unknown) {
      alert("删除失败: " + (e instanceof Error ? e.message : "未知错误"));
    }
  };

  const handleCreateScript = async () => {
    const name = newScriptName.trim();
    if (!name || !selectedGroup) return;
    try {
      const content = newScriptType === "shell"
        ? "#!/bin/bash\n# 脚本库脚本: " + name + "\n\necho 'Hello World'"
        : "#!/usr/bin/env python3\n# 脚本库脚本: " + name + "\n\nprint('Hello World')";
      const meta = "---\nname: " + name + "\ndescription: 描述待补充\n---\n\n## 用法\n";
      await saveLibraryScript(selectedGroup, name, newScriptType, content);
      await saveLibraryScriptMeta(selectedGroup, name, meta);
      setShowNewScript(false);
      setNewScriptName("");
      setNewScriptType("shell");
      loadGroups();
      loadScripts(selectedGroup);
      // Auto-select the new script
      const s: ScriptLibraryScript = { group: selectedGroup, name, script_type: newScriptType };
      setSelectedScript(s);
      setScriptContent(content);
      setMetaContent(meta);
      setEditing(false);
    } catch (e) { console.error("创建脚本失败:", e); }
  };

  return (
    <div className="flex-1 flex min-h-0">
      {/* Group Sidebar */}
      <div className="w-56 shrink-0 border-r border-indigo-500/10 flex flex-col">
        <div className="flex items-center justify-between px-4 py-3 border-b border-indigo-500/10">
          <h3 className="text-xs font-semibold text-indigo-400 uppercase tracking-wider">分组</h3>
          <button
            onClick={() => setShowNewGroup(true)}
            aria-label="新建分组"
            className="p-1 rounded text-slate-400 hover:text-slate-100 hover:bg-slate-800 transition-colors cursor-pointer"
          >
            <Plus size={14} />
          </button>
        </div>
        <div className="flex-1 overflow-auto py-1">
          {groups.length === 0 ? (
            <p className="px-4 py-6 text-xs text-slate-500 text-center">暂无分组，点击 + 新建</p>
          ) : (
            groups.map((g) => (
              <button
                key={g.name}
                onClick={() => handleSelectGroup(g.name)}
                className={`w-full flex items-center justify-between px-4 py-2 text-sm transition-colors cursor-pointer ${
                  selectedGroup === g.name
                    ? "bg-indigo-500/10 text-indigo-300 border-l-2 border-indigo-500"
                    : "text-slate-400 hover:text-slate-100 hover:bg-slate-800/50 border-l-2 border-transparent"
                }`}
              >
                <span className="flex items-center gap-2 truncate">
                  <Folder size={14} className="shrink-0" />
                  {g.name}
                </span>
                <button
                  onClick={(e) => { e.stopPropagation(); handleDeleteGroup(g.name); }}
                  aria-label={`删除分组 ${g.name}`}
                  className="opacity-0 group-hover:opacity-100 p-0.5 rounded hover:bg-red-500/20 text-slate-500 hover:text-red-400 transition-all cursor-pointer"
                  style={{ opacity: selectedGroup === g.name ? 1 : undefined }}
                >
                  <Trash2 size={12} />
                </button>
              </button>
            ))
          )}
        </div>
      </div>

      {/* Script List */}
      <div className="w-56 shrink-0 border-r border-indigo-500/10 flex flex-col">
        <div className="flex items-center justify-between px-4 py-3 border-b border-indigo-500/10">
          <h3 className="text-xs font-semibold text-indigo-400 uppercase tracking-wider">
            {selectedGroup ? `${selectedGroup} 脚本` : "脚本"}
          </h3>
          {selectedGroup && (
            <button
              onClick={() => setShowNewScript(true)}
              aria-label="新建脚本"
              className="p-1 rounded text-slate-400 hover:text-slate-100 hover:bg-slate-800 transition-colors cursor-pointer"
            >
              <Plus size={14} />
            </button>
          )}
        </div>
        <div className="flex-1 overflow-auto py-1">
          {!selectedGroup ? (
            <p className="px-4 py-6 text-xs text-slate-500 text-center">请先选择一个分组</p>
          ) : scripts.length === 0 ? (
            <p className="px-4 py-6 text-xs text-slate-500 text-center">该分组暂无脚本</p>
          ) : (
            scripts.map((s) => (
              <button
                key={s.name}
                onClick={() => handleSelectScript(s)}
                className={`w-full flex items-center gap-2 px-4 py-2 text-sm transition-colors cursor-pointer ${
                  selectedScript?.name === s.name && selectedScript?.group === s.group
                    ? "bg-indigo-500/10 text-indigo-300 border-l-2 border-indigo-500"
                    : "text-slate-400 hover:text-slate-100 hover:bg-slate-800/50 border-l-2 border-transparent"
                }`}
              >
                <FileCode size={14} className="shrink-0" />
                <span className="truncate">{s.name}</span>
                <span className="ml-auto text-[10px] text-slate-600 uppercase">{s.script_type === "shell" ? "sh" : "py"}</span>
              </button>
            ))
          )}
        </div>
      </div>

      {/* Script Editor */}
      <div className="flex-1 flex flex-col min-w-0">
        {!selectedScript ? (
          <div className="flex-1 flex items-center justify-center text-slate-500">
            <p className="text-sm">选择一个脚本查看或编辑</p>
          </div>
        ) : (
          <div className="flex-1 flex flex-col min-h-0">
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-indigo-500/10 shrink-0">
              <div>
                <h3 className="text-sm font-medium text-slate-100">
                  {selectedScript.group} / {selectedScript.name}
                  <span className="ml-2 text-xs text-slate-500">.{selectedScript.script_type === "shell" ? "sh" : "py"}</span>
                </h3>
                <p className="text-xs text-slate-500 mt-0.5">
                  脚本库路径: data/script-library/{selectedScript.group}/{selectedScript.name}/
                </p>
              </div>
              <div className="flex items-center gap-2">
                {editing ? (
                  <>
                    <button
                      onClick={handleSave}
                      className="px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-medium transition-colors cursor-pointer"
                    >
                      保存
                    </button>
                    <button
                      onClick={() => setEditing(false)}
                      className="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs transition-colors cursor-pointer"
                    >
                      取消
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      onClick={() => setEditing(true)}
                      className="px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-medium transition-colors cursor-pointer"
                    >
                      编辑
                    </button>
                    <button
                      onClick={handleDeleteScript}
                      className="px-3 py-1.5 rounded-lg bg-red-500/10 hover:bg-red-500/20 text-red-400 text-xs transition-colors cursor-pointer"
                    >
                      <Trash2 size={14} className="inline mr-1" />
                      删除
                    </button>
                  </>
                )}
              </div>
            </div>

            {/* Content */}
            {loading ? (
              <div className="flex-1 flex items-center justify-center">
                <span className="text-sm text-slate-500">加载中...</span>
              </div>
            ) : (
              <div className="flex-1 flex flex-col min-h-0 overflow-auto p-4 space-y-4">
                {/* Script Code */}
                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-2">
                    脚本内容 ({selectedScript.script_type === "shell" ? "Shell" : "Python"})
                  </label>
                  <CodeEditor
                    value={scriptContent}
                    onChange={setScriptContent}
                    language={selectedScript.script_type as "shell" | "python"}
                    readOnly={!editing}
                    height="280px"
                  />
                </div>

                {/* SCRIPT.md Meta */}
                <div>
                  <label className="block text-xs font-medium text-slate-400 mb-2">
                    SCRIPT.md（元信息）
                  </label>
                  <textarea
                    value={metaContent}
                    onChange={(e) => setMetaContent(e.target.value)}
                    readOnly={!editing}
                    rows={8}
                    className={`w-full px-3 py-2 rounded-lg bg-slate-950 border border-indigo-500/20 text-slate-100 text-sm font-mono focus:outline-none focus:border-indigo-500/50 transition-colors resize-none ${
                      !editing ? "pointer-events-none opacity-60" : ""
                    }`}
                    placeholder="---\nname: 脚本名称\ndescription: 脚本用途描述\n---\n\n## 用法"
                  />
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* New Group Dialog */}
      {showNewGroup && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setShowNewGroup(false)}>
          <div className={DIALOG_BASE} onClick={(e) => e.stopPropagation()}>
            <h3 className="text-sm font-medium text-slate-100 mb-4">新建分组</h3>
            <input
              type="text"
              value={newGroupName}
              onChange={(e) => setNewGroupName(e.target.value)}
              placeholder="分组名（小写字母+连字符）"
              autoFocus
              className="w-full px-3 py-2 rounded-lg bg-slate-950 border border-indigo-500/20 text-slate-100 text-sm focus:outline-none focus:border-indigo-500/50 mb-4"
              onKeyDown={(e) => { if (e.key === "Enter") handleCreateGroup(); }}
            />
            <div className="flex justify-end gap-2">
              <button onClick={() => setShowNewGroup(false)} className="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs transition-colors cursor-pointer">取消</button>
              <button onClick={handleCreateGroup} className="px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs transition-colors cursor-pointer">创建</button>
            </div>
          </div>
        </div>
      )}

      {/* New Script Dialog */}
      {showNewScript && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" onClick={() => setShowNewScript(false)}>
          <div className={DIALOG_BASE} onClick={(e) => e.stopPropagation()}>
            <h3 className="text-sm font-medium text-slate-100 mb-4">
              在 {selectedGroup} 中新建脚本
            </h3>
            <div className="space-y-3 mb-4">
              <input
                type="text"
                value={newScriptName}
                onChange={(e) => setNewScriptName(e.target.value)}
                placeholder="脚本名（不含扩展名）"
                autoFocus
                className="w-full px-3 py-2 rounded-lg bg-slate-950 border border-indigo-500/20 text-slate-100 text-sm focus:outline-none focus:border-indigo-500/50"
                onKeyDown={(e) => { if (e.key === "Enter") handleCreateScript(); }}
              />
              <select
                value={newScriptType}
                onChange={(e) => setNewScriptType(e.target.value as "shell" | "python")}
                className="w-full px-3 py-2 rounded-lg bg-slate-950 border border-indigo-500/20 text-slate-100 text-sm focus:outline-none focus:border-indigo-500/50 appearance-none"
              >
                <option value="shell">Shell</option>
                <option value="python">Python</option>
              </select>
            </div>
            <div className="flex justify-end gap-2">
              <button onClick={() => setShowNewScript(false)} className="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs transition-colors cursor-pointer">取消</button>
              <button onClick={handleCreateScript} className="px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs transition-colors cursor-pointer">创建</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
