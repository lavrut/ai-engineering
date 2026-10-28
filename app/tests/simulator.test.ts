import { describe, expect, it } from "vitest";
import {
  announce,
  createSimulator,
  estimateTokens,
  runFakeTool,
  validateTranscript,
  type ApiMessage,
  type Scenario,
  type ToolUse,
} from "../src/lib/simulator";

const scenario: Scenario = {
  id: "t",
  title: "t",
  task: "Fix the typo",
  workspace: { "a.md": "recieve", "dir/b.md": "b" },
  turns: [
    {
      blocks: [
        { type: "text", text: "Writing." },
        { type: "tool_use", id: "tu_1", name: "write_file", input: { path: "a.md", content: "receive" } },
      ],
      stop_reason: "tool_use",
      branches: {
        denied: [{ blocks: [{ type: "text", text: "Understood, leaving it." }], stop_reason: "end_turn" }],
      },
    },
    { blocks: [{ type: "text", text: "Done." }], stop_reason: "end_turn" },
  ],
};

describe("createSimulator", () => {
  it("steps one event at a time: 1 -> 2 -> 3 -> 4, then done", () => {
    const sim = createSimulator(scenario, "allow-all");
    expect(sim.state().messages).toHaveLength(1);
    expect(sim.state().messages[0]).toEqual({ role: "user", content: "Fix the typo" });
    expect(sim.step().messages).toHaveLength(2);
    expect(sim.step().messages).toHaveLength(3);
    const last = sim.step();
    expect(last.messages).toHaveLength(4);
    expect(last.done).toBe(true);
    expect(last.stopReason).toBe("end_turn");
    expect(last.turn).toBe(2);
    expect(validateTranscript(last.messages)).toEqual([]);
    expect(sim.step()).toEqual(last);
  });

  it("returns tool results in one user message and mutates only its own workspace copy", () => {
    const sim = createSimulator(scenario, "allow-all");
    sim.step();
    const s = sim.step();
    expect(s.messages[2]).toEqual({
      role: "user",
      content: [{ type: "tool_result", tool_use_id: "tu_1", content: "Wrote 7 characters to a.md", is_error: false }],
    });
    expect(s.workspace["a.md"]).toBe("receive");
    expect(scenario.workspace["a.md"]).toBe("recieve");
  });

  it("allow-all never sets pending", () => {
    const sim = createSimulator(scenario, "allow-all");
    sim.step();
    expect(sim.step().pending).toBeNull();
  });

  it("ask-writes sets pending; step() and runToEnd() do not move past it", () => {
    const sim = createSimulator(scenario, "ask-writes");
    sim.step();
    const pendingState = sim.step();
    expect(pendingState.pending?.id).toBe("tu_1");
    expect(pendingState.messages).toHaveLength(2);
    expect(sim.step()).toEqual(pendingState);
    const after = sim.runToEnd();
    expect(after.pending?.id).toBe("tu_1");
    expect(after.messages).toHaveLength(2);
    expect(after.done).toBe(false);
    expect(after.workspace["a.md"]).toBe("recieve");
  });

  it("approve() runs the call and clears pending", () => {
    const sim = createSimulator(scenario, "ask-writes");
    sim.runToEnd();
    const s = sim.approve();
    expect(s.pending).toBeNull();
    expect(s.messages).toHaveLength(3);
    expect(s.workspace["a.md"]).toBe("receive");
    expect(sim.runToEnd().done).toBe(true);
  });

  it("deny() gives the denial result and follows branches.denied", () => {
    const sim = createSimulator(scenario, "ask-writes");
    sim.runToEnd();
    const s = sim.deny();
    expect(s.pending).toBeNull();
    expect(s.workspace["a.md"]).toBe("recieve");
    expect(s.messages[2]).toEqual({
      role: "user",
      content: [{ type: "tool_result", tool_use_id: "tu_1", content: "The user denied this action.", is_error: true }],
    });
    const end = sim.runToEnd();
    expect(end.done).toBe(true);
    expect(end.messages).toHaveLength(4);
    expect(JSON.stringify(end.messages[3])).toContain("leaving it");
    expect(validateTranscript(end.messages)).toEqual([]);
  });

  it("approve()/deny() with nothing pending are no-ops", () => {
    const sim = createSimulator(scenario, "ask-writes");
    const before = sim.state();
    expect(sim.approve()).toEqual(before);
    expect(sim.deny()).toEqual(before);
  });

  it("reset() clears a pending approval and restores the workspace", () => {
    const sim = createSimulator(scenario, "allow-all");
    sim.runToEnd();
    expect(sim.state().workspace["a.md"]).toBe("receive");
    const s = sim.reset("ask-writes");
    expect(s.messages).toHaveLength(1);
    expect(s.workspace["a.md"]).toBe("recieve");
    sim.runToEnd();
    expect(sim.state().pending).not.toBeNull();
    const r = sim.reset();
    expect(r.pending).toBeNull();
    expect(r.messages).toHaveLength(1);
    expect(r.done).toBe(false);
    expect(r.turn).toBe(0);
    // policy persists across a bare reset
    sim.runToEnd();
    expect(sim.state().pending).not.toBeNull();
  });

  it("returns fresh state objects", () => {
    const sim = createSimulator(scenario, "allow-all");
    const a = sim.state();
    sim.step();
    expect(a.messages).toHaveLength(1);
    expect(sim.state()).not.toBe(a);
  });

  it("tokens follow estimateTokens", () => {
    const sim = createSimulator(scenario, "allow-all");
    const s = sim.step();
    expect(s.tokens).toBe(estimateTokens(s.messages));
    expect(estimateTokens([{ role: "user", content: "x" }])).toBe(
      Math.ceil(JSON.stringify([{ role: "user", content: "x" }]).length / 4),
    );
  });
});

describe("runFakeTool", () => {
  const ws = { "a.md": "hello", "dir/b.md": "b" };
  const call = (name: ToolUse["name"], input: Record<string, string>): ToolUse => ({ type: "tool_use", id: "x", name, input });

  it("lists sorted names, marks directories, reports empty", () => {
    expect(runFakeTool(ws, call("list_files", { path: "." }))).toEqual({ content: "a.md\ndir/", is_error: false });
    expect(runFakeTool(ws, call("list_files", { path: "dir" })).content).toBe("b.md");
    expect(runFakeTool({}, call("list_files", { path: "." })).content).toBe("(empty directory)");
  });

  it("reads files and reports missing ones", () => {
    expect(runFakeTool(ws, call("read_file", { path: "a.md" }))).toEqual({ content: "hello", is_error: false });
    expect(runFakeTool(ws, call("read_file", { path: "nope.md" }))).toEqual({
      content: "FileNotFoundError: nope.md",
      is_error: true,
    });
  });

  it("blocks path traversal and absolute paths", () => {
    for (const name of ["list_files", "read_file", "write_file"] as const) {
      expect(runFakeTool(ws, call(name, { path: "../x", content: "c" }))).toEqual({
        content: "PermissionError: ../x is outside the workspace",
        is_error: true,
      });
    }
    expect(runFakeTool(ws, call("read_file", { path: "/etc/passwd" })).content).toBe(
      "PermissionError: /etc/passwd is outside the workspace",
    );
  });

  it("write_file mutates the given workspace", () => {
    const w: Record<string, string> = { ...ws };
    expect(runFakeTool(w, call("write_file", { path: "n.md", content: "abc" })).content).toBe("Wrote 3 characters to n.md");
    expect(w["n.md"]).toBe("abc");
  });
});

describe("validateTranscript", () => {
  const use = (id: string) => ({ type: "tool_use" as const, id, name: "read_file" as const, input: { path: "a" } });
  const res = (id: string) => ({ type: "tool_result" as const, tool_use_id: id, content: "x", is_error: false });
  const good: ApiMessage[] = [
    { role: "user", content: "go" },
    { role: "assistant", content: [use("1"), use("2")] },
    { role: "user", content: [res("1"), res("2")] },
    { role: "assistant", content: [{ type: "text", text: "done" }] },
  ];

  it("accepts a good transcript", () => expect(validateTranscript(good)).toEqual([]));

  it("flags consecutive assistants", () => {
    const bad = [good[0], good[3], good[3]];
    expect(validateTranscript(bad).length).toBeGreaterThan(0);
  });
  it("flags a first message that is not user", () => {
    expect(validateTranscript([good[3]]).length).toBeGreaterThan(0);
  });
  it("flags a missing tool_result", () => {
    const bad = [good[0], good[1], { role: "user" as const, content: [res("1")] }];
    expect(validateTranscript(bad).length).toBeGreaterThan(0);
  });
  it("flags a tool_use with no following user message", () => {
    expect(validateTranscript([good[0], good[1]]).length).toBeGreaterThan(0);
  });
  it("flags a stray tool_result", () => {
    const bad = [good[0], { role: "assistant" as const, content: [{ type: "text" as const, text: "hi" }] }, { role: "user" as const, content: [res("9")] }];
    expect(validateTranscript(bad).length).toBeGreaterThan(0);
  });
  it("flags a result message that mixes in text", () => {
    const bad = [good[0], good[1], { role: "user" as const, content: [res("1"), res("2"), { type: "text" as const, text: "hm" }] }];
    expect(validateTranscript(bad).length).toBeGreaterThan(0);
  });
});

describe("announce", () => {
  it("names a reply with neither text nor tool calls", () => {
    const state = { messages: [{ role: "user" as const, content: "t" }, { role: "assistant" as const, content: [] }],
      workspace: {}, pending: null, done: true, stopReason: "end_turn" as const, turn: 1, tokens: 0 };
    expect(announce(state)).toBe("Turn 1: the assistant replies with no text; stop_reason end_turn. Done.");
  });
});
