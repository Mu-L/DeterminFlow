interface NodeFailurePolicyFieldsProps {
  autoRetryCount: number;
  autoRetryIntervalSeconds: number;
  failAutoSkip: boolean;
  isReadOnly: boolean;
  onAutoRetryCountChange: (value: number) => void;
  onAutoRetryIntervalSecondsChange: (value: number) => void;
  onFailAutoSkipChange: (value: boolean) => void;
  onMarkUnsaved: () => void;
}

function boundedNonNegativeInteger(value: string, maximum: number): number {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? Math.min(maximum, Math.max(0, parsed)) : 0;
}

export default function NodeFailurePolicyFields({
  autoRetryCount,
  autoRetryIntervalSeconds,
  failAutoSkip,
  isReadOnly,
  onAutoRetryCountChange,
  onAutoRetryIntervalSecondsChange,
  onFailAutoSkipChange,
  onMarkUnsaved,
}: NodeFailurePolicyFieldsProps) {
  const inputClass = `w-full px-3 py-2 rounded-lg bg-slate-950 border border-indigo-500/20 text-slate-100 text-sm focus:outline-none focus:border-indigo-500/50 transition-colors ${
    isReadOnly ? "pointer-events-none opacity-60" : ""
  }`;

  return (
    <fieldset className="space-y-3 pt-3 border-t border-indigo-500/10">
      <legend className="text-xs font-semibold text-slate-300">失败处理</legend>
      <p className="text-xs text-slate-500 leading-relaxed">
        对 Agent、脚本、审批和子流程统一生效，与“驳回上游节点（reject_upstream）”的计数彼此独立。
      </p>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label htmlFor="node-auto-retry-count" className="block text-xs font-medium text-slate-400 mb-1.5">
            自动重试次数
          </label>
          <input
            id="node-auto-retry-count"
            type="number"
            min={0}
            max={20}
            value={autoRetryCount}
            disabled={isReadOnly}
            onChange={(event) => {
              onAutoRetryCountChange(boundedNonNegativeInteger(event.target.value, 20));
              onMarkUnsaved();
            }}
            className={inputClass}
          />
          <p className="text-xs text-slate-500 mt-1">0 表示关闭</p>
        </div>

        <div>
          <label htmlFor="node-auto-retry-interval" className="block text-xs font-medium text-slate-400 mb-1.5">
            重试间隔（秒）
          </label>
          <input
            id="node-auto-retry-interval"
            type="number"
            min={0}
            max={86400}
            value={autoRetryIntervalSeconds}
            disabled={isReadOnly || autoRetryCount === 0}
            onChange={(event) => {
              onAutoRetryIntervalSecondsChange(boundedNonNegativeInteger(event.target.value, 86400));
              onMarkUnsaved();
            }}
            className={inputClass}
          />
        </div>
      </div>

      <label className="flex items-start gap-3 cursor-pointer">
        <input
          type="checkbox"
          checked={failAutoSkip}
          onChange={(event) => {
            onFailAutoSkipChange(event.target.checked);
            onMarkUnsaved();
          }}
          disabled={isReadOnly}
          className="mt-0.5 w-4 h-4 rounded border-indigo-500/30 bg-slate-950 text-indigo-500 focus:ring-indigo-500/30"
        />
        <div>
          <span className="text-sm text-slate-100">重试耗尽后自动跳过</span>
          <p className="text-xs text-slate-500 mt-0.5 leading-relaxed">
            跳过节点不会传播失败时的部分输出，下游引用缺失输出时仍可能失败。
          </p>
        </div>
      </label>
    </fieldset>
  );
}
