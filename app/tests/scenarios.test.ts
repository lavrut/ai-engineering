import { describe, expect, it } from "vitest";
import { scenarios } from "../src/data/scenarios";
import { createSimulator, validateTranscript, type Policy, type ToolResult } from "../src/lib/simulator";

const ids = ["parallel-read", "approve-write", "recover-error"] as const;
const policies: Policy[] = ["ask-writes", "allow-all"];

function drive(id: (typeof ids)[number], policy: Policy, decision: "approve" | "deny") {
  const sim = createSimulator(scenarios[id], policy);
  for (let i = 0; i < 50 && !sim.state().done; i++) {
    const s = sim.step();
    if (s.pending) decision === "approve" ? sim.approve() : sim.deny();
  }
  return sim.state();
}

const results = (messages: ReturnType<typeof drive>["messages"]) =>
  messages.flatMap((m) => (m.role === "user" && typeof m.content !== "string" ? (m.content as ToolResult[]) : []));

describe("scenarios", () => {
  it("exports exactly the three scenarios, keyed by their id", () => {
    expect(Object.keys(scenarios).sort()).toEqual([...ids].sort());
    for (const id of ids) expect(scenarios[id].id).toBe(id);
  });

  for (const id of ids) {
    for (const policy of policies) {
      for (const decision of ["approve", "deny"] as const) {
        it(`${id} / ${policy} / ${decision} finishes with a valid transcript`, () => {
          const s = drive(id, policy, decision);
          expect(s.done).toBe(true);
          expect(s.stopReason).toBe("end_turn");
          expect(validateTranscript(s.messages)).toEqual([]);
        });
      }
    }
  }

  it("parallel-read has a turn with two tool_use blocks", () => {
    const counts = scenarios["parallel-read"].turns.map((t) => t.blocks.filter((b) => b.type === "tool_use").length);
    expect(Math.max(...counts)).toBe(2);
  });

  it("approve-write: README typo fixed when approved, unchanged when denied", () => {
    const original = scenarios["approve-write"].workspace["README.md"];
    expect(original).toContain("recieve");
    expect(drive("approve-write", "ask-writes", "approve").workspace["README.md"]).not.toContain("recieve");
    expect(drive("approve-write", "allow-all", "approve").workspace["README.md"]).not.toContain("recieve");
    expect(drive("approve-write", "ask-writes", "deny").workspace["README.md"]).toBe(original);
  });

  it("approve-write asks for approval under ask-writes only", () => {
    const sim = createSimulator(scenarios["approve-write"], "ask-writes");
    sim.runToEnd();
    expect(sim.state().pending?.name).toBe("write_file");
    expect(createSimulator(scenarios["approve-write"], "allow-all").runToEnd().pending).toBeNull();
  });

  it("approve-write denial surfaces the denied result to the model", () => {
    const r = results(drive("approve-write", "ask-writes", "deny").messages);
    expect(r.at(-1)).toMatchObject({ content: "The user denied this action.", is_error: true });
  });

  it("recover-error: first tool result is an error, a later one succeeds", () => {
    const r = results(drive("recover-error", "allow-all", "approve").messages);
    expect(r[0].is_error).toBe(true);
    expect(r.some((x) => !x.is_error)).toBe(true);
    expect(scenarios["recover-error"].workspace["config.yaml"]).toBeUndefined();
    expect(scenarios["recover-error"].workspace["config.example.yaml"]).toBeDefined();
  });
});
