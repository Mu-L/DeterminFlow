import type {
  ScriptLibraryGroup,
  ScriptLibraryScript,
  WorkflowVariable,
} from "../../types";
import { CodeEditor } from "../shared";
import { VarInput } from "./NodeConfigVariableInputs";

interface NodeConfigScriptFieldsProps {
  variables: WorkflowVariable[];
  libGroups: ScriptLibraryGroup[];
  libScripts: ScriptLibraryScript[];
  scriptSource: string;
  setScriptSource: (value: string) => void;
  scriptType: string;
  setScriptType: (value: string) => void;
  scriptName: string;
  setScriptName: (value: string) => void;
  scriptGroup: string;
  setScriptGroup: (value: string) => void;
  scriptArgs: string;
  setScriptArgs: (value: string) => void;
  scriptArgv: string[];
  setScriptArgv: (value: string[]) => void;
  useScriptArgv: boolean;
  timeout: string;
  setTimeout: (value: string) => void;
  scriptContent: string;
  setScriptContent: (value: string) => void;
  scriptLoaded: boolean;
  enableRejectUpstream: boolean;
  setEnableRejectUpstream: (value: boolean) => void;
  maxRejectCount: string;
  setMaxRejectCount: (value: string) => void;
  isReadOnly: boolean;
  readOnlyInput: string;
  baseInputClass: string;
  onMarkUnsaved: () => void;
}

export default function NodeConfigScriptFields({
  variables,
  libGroups,
  libScripts,
  scriptSource,
  setScriptSource,
  scriptType,
  setScriptType,
  scriptName,
  setScriptName,
  scriptGroup,
  setScriptGroup,
  scriptArgs,
  setScriptArgs,
  scriptArgv,
  setScriptArgv,
  useScriptArgv,
  timeout,
  setTimeout,
  scriptContent,
  setScriptContent,
  scriptLoaded,
  enableRejectUpstream,
  setEnableRejectUpstream,
  maxRejectCount,
  setMaxRejectCount,
  isReadOnly,
  readOnlyInput,
  baseInputClass,
  onMarkUnsaved,
}: NodeConfigScriptFieldsProps) {
  return (
    <>
      <div>
        <label htmlFor="script-source" className="block text-xs font-medium text-slate-400 mb-1.5">
          脚本来源
        </label>
        <select
          id="script-source"
          value={scriptSource}
          onChange={(event) => {
            setScriptSource(event.target.value);
            if (event.target.value === "library") setScriptContent("");
          }}
          disabled={isReadOnly}
          aria-label="选择脚本来源"
          className={`w-full px-3 py-2 rounded-lg bg-slate-950 border border-indigo-500/20 text-slate-100 text-sm focus:outline-none focus:border-indigo-500/50 transition-colors appearance-none ${
            isReadOnly ? "pointer-events-none opacity-60" : ""
          }`}
        >
          <option value="inline">直接编辑</option>
          <option value="library">脚本库</option>
        </select>
      </div>

      <div>
        <label htmlFor="script-type" className="block text-xs font-medium text-slate-400 mb-1.5">
          脚本类型 {!isReadOnly && <span className="text-red-400">*</span>}
        </label>
        <select
          id="script-type"
          value={scriptType}
          onChange={(event) => setScriptType(event.target.value)}
          disabled={isReadOnly}
          aria-label="选择脚本类型"
          className={`w-full px-3 py-2 rounded-lg bg-slate-950 border border-indigo-500/20 text-slate-100 text-sm focus:outline-none focus:border-indigo-500/50 transition-colors appearance-none ${
            isReadOnly ? "pointer-events-none opacity-60" : ""
          }`}
        >
          <option value="shell">Shell</option>
          <option value="python">Python</option>
        </select>
      </div>

      {scriptSource === "library" && (
        <>
          <div>
            <label htmlFor="script-group" className="block text-xs font-medium text-slate-400 mb-1.5">
              分组 {!isReadOnly && <span className="text-red-400">*</span>}
            </label>
            <select
              id="script-group"
              value={scriptGroup}
              onChange={(event) => {
                setScriptGroup(event.target.value);
                setScriptName("");
              }}
              disabled={isReadOnly}
              aria-label="选择脚本库分组"
              className={`w-full px-3 py-2 rounded-lg bg-slate-950 border border-indigo-500/20 text-slate-100 text-sm focus:outline-none focus:border-indigo-500/50 transition-colors appearance-none ${
                isReadOnly ? "pointer-events-none opacity-60" : ""
              }`}
            >
              <option value="">-- 选择分组 --</option>
              {libGroups.map((group) => (
                <option key={group.name} value={group.name}>
                  {group.name} ({group.script_count} 个脚本)
                </option>
              ))}
            </select>
          </div>

          {scriptGroup && (
            <div>
              <label htmlFor="script-select" className="block text-xs font-medium text-slate-400 mb-1.5">
                脚本 {!isReadOnly && <span className="text-red-400">*</span>}
              </label>
              <select
                id="script-select"
                value={scriptName}
                onChange={(event) => {
                  const selected = libScripts.find(
                    (script) => script.group === scriptGroup && script.name === event.target.value,
                  );
                  if (selected) {
                    setScriptName(selected.name);
                    setScriptType(selected.script_type);
                  } else {
                    setScriptName(event.target.value);
                  }
                }}
                disabled={isReadOnly}
                aria-label="选择脚本库中的脚本"
                className={`w-full px-3 py-2 rounded-lg bg-slate-950 border border-indigo-500/20 text-slate-100 text-sm focus:outline-none focus:border-indigo-500/50 transition-colors appearance-none ${
                  isReadOnly ? "pointer-events-none opacity-60" : ""
                }`}
              >
                <option value="">-- 选择脚本 --</option>
                {libScripts
                  .filter((script) => script.group === scriptGroup)
                  .map((script) => (
                    <option key={script.name} value={script.name}>
                      {script.name}.{script.script_type === "shell" ? "sh" : "py"}
                    </option>
                  ))}
              </select>
              <p className="text-xs text-slate-500 mt-1">运行时将从脚本库拉取最新版本执行</p>
            </div>
          )}
        </>
      )}

      {scriptSource !== "library" && (
        <div>
          <label className="block text-xs font-medium text-slate-400 mb-1.5">
            脚本名 {!isReadOnly && <span className="text-red-400">*</span>}
          </label>
          <VarInput
            value={scriptName}
            onChange={setScriptName}
            placeholder="例如: deploy"
            readOnly={isReadOnly}
            readOnlyClass={readOnlyInput}
            inputClass={baseInputClass}
            variables={variables}
          />
          <p className="text-xs text-slate-500 mt-1">
            脚本文件名（不含扩展名），存储为 data/workflows/脚本名.{scriptType === "shell" ? "sh" : "py"}
          </p>
        </div>
      )}

      <div>
        <label className="block text-xs font-medium text-slate-400 mb-1.5">
          脚本参数 <span className="text-slate-500">(可选)</span>
        </label>
        {useScriptArgv ? (
          <div className="space-y-2">
            {scriptArgv.map((argument, index) => (
              <div key={index} className="flex items-center gap-2">
                <VarInput
                  value={argument}
                  onChange={(value) => setScriptArgv(
                    scriptArgv.map((item, itemIndex) => itemIndex === index ? value : item),
                  )}
                  placeholder={`参数 ${index + 1}`}
                  readOnly={isReadOnly}
                  readOnlyClass={readOnlyInput}
                  inputClass={baseInputClass}
                  variables={variables}
                />
                {!isReadOnly && (
                  <button
                    type="button"
                    onClick={() => setScriptArgv(scriptArgv.filter((_, itemIndex) => itemIndex !== index))}
                    className="shrink-0 rounded border border-slate-700 px-2 py-2 text-xs text-slate-400 hover:border-red-500/50 hover:text-red-300"
                    aria-label={`删除参数 ${index + 1}`}
                  >
                    删除
                  </button>
                )}
              </div>
            ))}
            {!isReadOnly && (
              <button
                type="button"
                onClick={() => setScriptArgv([...scriptArgv, ""])}
                className="rounded border border-indigo-500/30 px-2.5 py-1.5 text-xs text-indigo-300 hover:border-indigo-400/60"
              >
                添加参数
              </button>
            )}
            <p className="text-xs text-slate-500">
              安全参数列表：每项作为一个完整参数传递，空格和 {"{{key}}"} 占位符不会被 Shell 重新拆分。
            </p>
          </div>
        ) : (
          <>
            <VarInput
              value={scriptArgs}
              onChange={setScriptArgs}
              placeholder="--verbose --env dev"
              readOnly={isReadOnly}
              readOnlyClass={readOnlyInput}
              inputClass={baseInputClass}
              variables={variables}
            />
            <p className="text-xs text-amber-500/80 mt-1">
              兼容旧定义的参数字符串；新工作流应使用安全参数列表。
            </p>
          </>
        )}
      </div>

      <div>
        <label htmlFor="script-timeout" className="block text-xs font-medium text-slate-400 mb-1.5">
          超时时间（秒）
        </label>
        <input
          type="number"
          id="script-timeout"
          value={timeout}
          onChange={(event) => setTimeout(event.target.value)}
          disabled={isReadOnly}
          min={1}
          max={86400}
          aria-label="脚本超时时间（秒）"
          className={`${baseInputClass} ${isReadOnly ? "pointer-events-none opacity-60" : ""}`}
          placeholder="300"
        />
      </div>

      <div className="space-y-3 pt-3 border-t border-indigo-500/10">
        <label className="flex items-start gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={enableRejectUpstream}
            onChange={(event) => {
              setEnableRejectUpstream(event.target.checked);
              onMarkUnsaved();
            }}
            disabled={isReadOnly}
            className="mt-0.5 w-4 h-4 rounded border-indigo-500/30 bg-slate-950 text-indigo-500 focus:ring-indigo-500/30"
          />
          <div>
            <span className="text-sm text-slate-100">允许脚本打回上游</span>
            <p className="text-xs text-slate-500 mt-0.5 leading-relaxed">
              开启后脚本可输出 {"<WF_REJECT_UPSTREAM>"} 反馈，要求上游节点重试。
            </p>
          </div>
        </label>

        {enableRejectUpstream && (
          <div className="ml-7">
            <label htmlFor="script-max-reject-count" className="block text-xs font-medium text-slate-400 mb-1">
              最大打回次数
            </label>
            <input
              type="number"
              id="script-max-reject-count"
              min="1"
              max="100"
              value={maxRejectCount}
              onChange={(event) => {
                setMaxRejectCount(event.target.value);
                onMarkUnsaved();
              }}
              disabled={isReadOnly}
              className={`w-full px-3 py-2 rounded-lg bg-slate-950 border border-indigo-500/20 text-slate-100 text-sm focus:outline-none focus:border-indigo-500/50 transition-colors ${
                isReadOnly ? "pointer-events-none opacity-60" : ""
              }`}
              placeholder="3"
            />
          </div>
        )}
      </div>

      {scriptSource !== "library" && (
        <div>
          <label className="block text-xs font-medium text-slate-400 mb-1.5">
            脚本内容 {!isReadOnly && <span className="text-red-400">*</span>}
          </label>
          {scriptLoaded ? (
            <CodeEditor
              value={scriptContent}
              onChange={setScriptContent}
              language={scriptType as "shell" | "python"}
              readOnly={isReadOnly}
              height="300px"
              placeholder={
                scriptType === "shell"
                  ? "#!/bin/bash\necho 'Hello World'"
                  : "#!/usr/bin/env python3\nprint('Hello World')"
              }
            />
          ) : (
            <div className="flex items-center justify-center h-[100px] rounded-lg bg-slate-950 border border-indigo-500/10">
              <span className="text-xs text-slate-500">正在加载脚本内容...</span>
            </div>
          )}
          <p className="text-xs text-slate-500 mt-1">
            点击"保存"按钮将脚本内容持久化到文件，并更新节点配置
          </p>
        </div>
      )}
    </>
  );
}
