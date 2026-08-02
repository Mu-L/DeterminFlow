import assert from "node:assert/strict";
import test from "node:test";

import {
  buildScriptNodeParams,
  nodeParamString,
  scriptArgvParam,
} from "./nodeConfigUtils";

test("script node editor round-trips structured argv without shell splitting", () => {
  const original = {
    script_argv: ["--title", "Hero's Return", "--json", '{"label":"a b"}'],
    custom_extension_param: { keep: true },
  };

  const result = buildScriptNodeParams(original, {
    scriptSource: "inline",
    scriptType: "python",
    scriptName: "run",
    scriptGroup: "",
    scriptArgs: "",
    scriptArgv: scriptArgvParam(original) || [],
    useScriptArgv: true,
    timeout: "600",
    enableRejectUpstream: true,
    maxRejectCount: "4",
  });

  assert.deepEqual(result.script_argv, original.script_argv);
  assert.equal("script_args" in result, false);
  assert.deepEqual(result.custom_extension_param, { keep: true });
});

test("legacy script args remain compatible and malformed argv is ignored", () => {
  const original = { script_args: "--verbose --env dev", script_argv: ["ok", 3] };

  assert.equal(scriptArgvParam(original), null);
  assert.equal(nodeParamString(original, "script_args"), "--verbose --env dev");

  const result = buildScriptNodeParams(original, {
    scriptSource: "library",
    scriptType: "shell",
    scriptName: "deploy",
    scriptGroup: "ops",
    scriptArgs: "--verbose --env dev",
    scriptArgv: [],
    useScriptArgv: false,
    timeout: "300",
    enableRejectUpstream: false,
    maxRejectCount: "3",
  });

  assert.equal(result.script_args, "--verbose --env dev");
  assert.equal("script_argv" in result, false);
  assert.equal(result.script_group, "ops");
});
