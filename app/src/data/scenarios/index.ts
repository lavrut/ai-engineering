import type { Scenario } from "../../lib/simulator";

const README_TYPO = "# Tidy\n\nA tiny CLI that sorts your notes.\n\nSend feedback and you will recieve a reply within a day.\n";
const README_FIXED = README_TYPO.replace("recieve", "receive");

const parallelRead: Scenario = {
  id: "parallel-read",
  title: "Two reads, one turn",
  task: "Summarize notes.md and todo.md.",
  workspace: {
    "notes.md": "# Notes\n\n- Kickoff is Monday.\n- Budget draft due Friday.\n",
    "todo.md": "# Todo\n\n- [ ] Book the room\n- [x] Send the agenda\n",
  },
  turns: [
    {
      blocks: [
        { type: "text", text: "I'll read both files at once." },
        { type: "tool_use", id: "toolu_01", name: "read_file", input: { path: "notes.md" } },
        { type: "tool_use", id: "toolu_02", name: "read_file", input: { path: "todo.md" } },
      ],
      stop_reason: "tool_use",
    },
    {
      blocks: [
        {
          type: "text",
          text: "Kickoff is Monday and the budget draft is due Friday. Still to do: book the room; the agenda is already sent.",
        },
      ],
      stop_reason: "end_turn",
    },
  ],
};

const approveWrite: Scenario = {
  id: "approve-write",
  title: "A write that needs approval",
  task: "Fix the spelling mistake in README.md.",
  workspace: { "README.md": README_TYPO, "notes.md": "# Notes\n\n- Ship v1 soon.\n" },
  turns: [
    {
      blocks: [
        { type: "text", text: "Let me read the README first." },
        { type: "tool_use", id: "toolu_01", name: "read_file", input: { path: "README.md" } },
      ],
      stop_reason: "tool_use",
    },
    {
      blocks: [
        { type: "text", text: "Found it: \"recieve\" should be \"receive\". I'll write the corrected file." },
        { type: "tool_use", id: "toolu_02", name: "write_file", input: { path: "README.md", content: README_FIXED } },
      ],
      stop_reason: "tool_use",
      branches: {
        denied: [
          {
            blocks: [
              {
                type: "text",
                text: "Understood, I won't change README.md. The typo is \"recieve\" (should be \"receive\") if you want to fix it yourself.",
              },
            ],
            stop_reason: "end_turn",
          },
        ],
      },
    },
    {
      blocks: [{ type: "text", text: "Fixed: \"recieve\" is now \"receive\" in README.md." }],
      stop_reason: "end_turn",
    },
  ],
};

const recoverError: Scenario = {
  id: "recover-error",
  title: "Recovering from an error",
  task: "Read config.yaml and tell me the port.",
  workspace: {
    "config.example.yaml": "name: demo\nport: 8080\ndebug: false\n",
    "README.md": "# Demo\n\nCopy config.example.yaml to config.yaml to get started.\n",
  },
  turns: [
    {
      blocks: [
        { type: "text", text: "I'll read the config file." },
        { type: "tool_use", id: "toolu_01", name: "read_file", input: { path: "config.yaml" } },
      ],
      stop_reason: "tool_use",
    },
    {
      blocks: [
        { type: "text", text: "That file doesn't exist. Let me look at what is in the workspace." },
        { type: "tool_use", id: "toolu_02", name: "list_files", input: { path: "." } },
      ],
      stop_reason: "tool_use",
    },
    {
      blocks: [
        { type: "text", text: "There is a config.example.yaml. I'll read that instead." },
        { type: "tool_use", id: "toolu_03", name: "read_file", input: { path: "config.example.yaml" } },
      ],
      stop_reason: "tool_use",
    },
    {
      blocks: [
        {
          type: "text",
          text: "config.yaml doesn't exist, but config.example.yaml sets port 8080. That is the default to use.",
        },
      ],
      stop_reason: "end_turn",
    },
  ],
};

export const scenarios: Record<"parallel-read" | "approve-write" | "recover-error", Scenario> = {
  "parallel-read": parallelRead,
  "approve-write": approveWrite,
  "recover-error": recoverError,
};
