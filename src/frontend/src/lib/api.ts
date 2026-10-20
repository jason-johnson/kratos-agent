import { getApiUrl, getAuthConfig } from "@/lib/config";
import { getMcpAccessToken } from "@/lib/auth";
import type {
  Attachment,
  EvalScenario,
  EvalRun,
  EvalMode,
  TraceList,
  TraceOperation,
  OperationalContext,
} from "@/types";

/**
 * Send a message to the agent and receive streaming SSE events.
 */
export async function streamAgentChat(
  conversationId: string,
  message: string,
  onEvent: (event: { type: string; data: unknown }) => void,
  onError: (error: Error) => void,
  onDone: () => void,
  attachments?: Attachment[]
): Promise<void> {
  try {
    const payload: Record<string, unknown> = { conversationId, message };
    if (attachments && attachments.length > 0) {
      payload.attachments = attachments;
    }
    // When OBO sign-in is configured, attach the user's MCP-scoped access token
    // so the in-process agent can call the OBO MCP server On-Behalf-Of the user.
    // Silent-only (never interactive): an interactive acquisition here opens an
    // MSAL popup, and a popup that is blocked or ignored leaves its promise
    // pending forever, which would hang the send instead of failing. Signing in
    // is the explicit job of the OBO sign-in button; until the user does that,
    // the chat proceeds without a token.
    const authCfg = getAuthConfig();
    if (authCfg) {
      try {
        const token = await getMcpAccessToken(false);
        if (token) {
          payload.mcpAccessTokens = { [authCfg.mcpServerName]: token };
        }
      } catch (err) {
        console.warn("OBO token acquisition failed; continuing without it", err);
      }
    }

    const response = await fetch(`${getApiUrl()}/api/agent/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      throw new Error(`Agent request failed: ${response.status}`);
    }

    const reader = response.body?.getReader();
    if (!reader) {
      throw new Error("No response body");
    }

    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (line.startsWith("event: ")) {
          // Ignore — we extract type from the data
          continue;
        }
        if (line.startsWith("data: ")) {
          const jsonStr = line.slice(6);
          try {
            const parsed = JSON.parse(jsonStr);
            onEvent({ type: parsed.type, data: parsed });

            if (parsed.type === "done") {
              onDone();
              return;
            }
          } catch {
            // Skip malformed JSON
          }
        }
      }
    }

    onDone();
  } catch (err) {
    onError(err instanceof Error ? err : new Error(String(err)));
  }
}

/**
 * Create a new conversation.
 */
export async function createConversation(
  title: string = "New Conversation"
): Promise<{ id: string; title: string }> {
  const response = await fetch(`${getApiUrl()}/api/conversations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });

  if (!response.ok) {
    throw new Error(`Failed to create conversation: ${response.status}`);
  }

  return response.json();
}

/**
 * Respond to a user input request from the agent.
 */
export async function respondToUserInput(
  conversationId: string,
  requestId: string,
  answer: string
): Promise<void> {
  const response = await fetch(`${getApiUrl()}/api/agent/user-input`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ conversationId, requestId, answer }),
  });
  if (!response.ok) {
    throw new Error(`Failed to respond to user input: ${response.status}`);
  }
}

export async function getOperationalContext(): Promise<OperationalContext> {
  const response = await fetch(`${getApiUrl()}/api/context`);
  if (!response.ok) {
    throw new Error(`Failed to load operational context: ${response.status}`);
  }
  return response.json();
}

/**
 * Update a conversation's title.
 */
export async function updateConversation(
  conversationId: string,
  updates: { title?: string }
): Promise<void> {
  const response = await fetch(
    `${getApiUrl()}/api/conversations/${encodeURIComponent(conversationId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updates),
    }
  );
  if (!response.ok) {
    throw new Error(`Failed to update conversation: ${response.status}`);
  }
}

/**
 * Delete a conversation.
 */
export async function deleteConversation(conversationId: string): Promise<void> {
  const response = await fetch(
    `${getApiUrl()}/api/conversations/${encodeURIComponent(conversationId)}`,
    { method: "DELETE" }
  );
  if (!response.ok) {
    throw new Error(`Failed to delete conversation: ${response.status}`);
  }
}

/**
 * List all conversations.
 */
export async function listConversations(): Promise<{ conversations: unknown[] }> {
  const response = await fetch(`${getApiUrl()}/api/conversations`);
  if (!response.ok) {
    throw new Error(`Failed to list conversations: ${response.status}`);
  }
  return response.json();
}

/**
 * Get messages for a conversation.
 */
export async function getConversationMessages(conversationId: string): Promise<unknown[]> {
  const response = await fetch(`${getApiUrl()}/api/conversations/${encodeURIComponent(conversationId)}/messages`);
  if (!response.ok) {
    throw new Error(`Failed to get messages: ${response.status}`);
  }
  return response.json();
}

// ─── Skills Admin API ───

import type { Skill, SkillCreate, SkillUpdate } from "@/types";

export async function listSkills(): Promise<Skill[]> {
  const response = await fetch(`${getApiUrl()}/api/admin/skills`);
  if (!response.ok) throw new Error(`Failed to list skills: ${response.status}`);
  const data = await response.json();
  return data.skills;
}

export async function getSkill(name: string): Promise<Skill> {
  const response = await fetch(`${getApiUrl()}/api/admin/skills/${encodeURIComponent(name)}`);
  if (!response.ok) throw new Error(`Failed to get skill: ${response.status}`);
  return response.json();
}

export async function createSkill(skill: SkillCreate): Promise<Skill> {
  const response = await fetch(`${getApiUrl()}/api/admin/skills`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(skill),
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to create skill: ${response.status}`);
  }
  return response.json();
}

export async function updateSkill(
  name: string,
  updates: SkillUpdate
): Promise<Skill> {
  const response = await fetch(`${getApiUrl()}/api/admin/skills/${encodeURIComponent(name)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to update skill: ${response.status}`);
  }
  return response.json();
}

export async function deleteSkill(name: string): Promise<void> {
  const response = await fetch(`${getApiUrl()}/api/admin/skills/${encodeURIComponent(name)}`, {
    method: "DELETE",
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to delete skill: ${response.status}`);
  }
}

// ─── Skill Files API ───

import type { SkillFile } from "@/types";

export async function listSkillFiles(
  skillName: string
): Promise<{ files: SkillFile[] }> {
  const response = await fetch(
    `${getApiUrl()}/api/admin/skills/${encodeURIComponent(skillName)}/files`
  );
  if (!response.ok) throw new Error(`Failed to list skill files: ${response.status}`);
  return response.json();
}

export async function upsertSkillFile(
  skillName: string,
  filePath: string,
  content: string
): Promise<void> {
  const response = await fetch(
    `${getApiUrl()}/api/admin/skills/${encodeURIComponent(skillName)}/files/${filePath}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }
  );
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to upload file: ${response.status}`);
  }
}

export async function deleteSkillFile(
  skillName: string,
  filePath: string
): Promise<void> {
  const response = await fetch(
    `${getApiUrl()}/api/admin/skills/${encodeURIComponent(skillName)}/files/${filePath}`,
    { method: "DELETE" }
  );
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to delete file: ${response.status}`);
  }
}

// ─── System Prompt Admin API ───

import type { SystemPrompt } from "@/types";

export async function getSystemPrompt(): Promise<SystemPrompt> {
  const response = await fetch(`${getApiUrl()}/api/admin/system-prompt`);
  if (!response.ok) throw new Error(`Failed to get system prompt: ${response.status}`);
  return response.json();
}

export async function updateSystemPrompt(content: string): Promise<SystemPrompt> {
  const response = await fetch(`${getApiUrl()}/api/admin/system-prompt`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to update system prompt: ${response.status}`);
  }
  return response.json();
}

export async function resetSystemPrompt(): Promise<void> {
  const response = await fetch(`${getApiUrl()}/api/admin/system-prompt`, {
    method: "DELETE",
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to reset system prompt: ${response.status}`);
  }
}

// ─── MCP Servers Admin API ───

import type { MCPConfig } from "@/types";

export async function getMCPConfig(): Promise<MCPConfig> {
  const response = await fetch(`${getApiUrl()}/api/admin/mcp-servers`);
  if (!response.ok) throw new Error(`Failed to get MCP config: ${response.status}`);
  return response.json();
}

export async function updateMCPConfig(servers: MCPConfig["servers"]): Promise<MCPConfig> {
  const response = await fetch(`${getApiUrl()}/api/admin/mcp-servers`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ servers }),
  });
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || `Failed to update MCP config: ${response.status}`);
  }
  return response.json();
}

// ─── Consistency Analysis API ───

import type { AnalysisResult } from "@/types";

export async function analyzeConsistency(
  includeDisabled: boolean = true
): Promise<AnalysisResult> {
  const response = await fetch(
    `${getApiUrl()}/api/admin/analysis/consistency`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ includeDisabled }),
    }
  );
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || `Analysis failed: ${response.status}`);
  }
  return response.json();
}

import type { AnalysisIssue, ApplyFixResult } from "@/types";

export async function applyAnalysisFix(
  issue: AnalysisIssue
): Promise<ApplyFixResult> {
  const response = await fetch(
    `${getApiUrl()}/api/admin/analysis/apply-fix`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        category: issue.category,
        title: issue.title,
        description: issue.description,
        recommendation: issue.recommendation,
        affectedSkills: issue.affectedSkills,
      }),
    }
  );
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || `Apply fix failed: ${response.status}`);
  }
  return response.json();
}

// ─── Evals ─────────────────────────────────────────────────────────────────

async function readJson<T>(response: Response, errPrefix: string): Promise<T> {
  if (!response.ok) {
    const body = await response.text().catch(() => "");
    throw new Error(`${errPrefix} (${response.status})${body ? `: ${body}` : ""}`);
  }
  return response.json();
}

export async function listEvalScenarios(): Promise<EvalScenario[]> {
  const r = await fetch(`${getApiUrl()}/api/evals/scenarios`);
  const data = await readJson<{ scenarios: EvalScenario[] }>(r, "List scenarios failed");
  return data.scenarios ?? [];
}

export async function generateEvalScenarios(
  body: { count?: number; persist?: boolean; instructions?: string } = {},
): Promise<{ scenarios: EvalScenario[]; persisted: boolean }> {
  const r = await fetch(
    `${getApiUrl()}/api/evals/scenarios/generate`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ count: 10, persist: false, instructions: "", ...body }),
    },
  );
  return readJson(r, "Generate scenarios failed");
}

export async function upsertEvalScenario(
  scenario: EvalScenario,
): Promise<EvalScenario> {
  const r = await fetch(
    `${getApiUrl()}/api/evals/scenarios/${encodeURIComponent(scenario.name)}`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(scenario),
    },
  );
  return readJson(r, "Save scenario failed");
}

export async function deleteEvalScenario(name: string): Promise<void> {
  const r = await fetch(
    `${getApiUrl()}/api/evals/scenarios/${encodeURIComponent(name)}`,
    { method: "DELETE" },
  );
  if (!r.ok && r.status !== 404) {
    throw new Error(`Delete scenario failed: ${r.status}`);
  }
}

export async function startEvalRun(
  body: { mode: EvalMode; scenarios?: string[] },
): Promise<EvalRun> {
  const r = await fetch(
    `${getApiUrl()}/api/evals/run`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
  );
  return readJson(r, "Start eval run failed");
}

export async function listEvalRuns(): Promise<EvalRun[]> {
  const r = await fetch(`${getApiUrl()}/api/evals/runs`);
  const data = await readJson<{ runs: EvalRun[] }>(r, "List runs failed");
  return data.runs ?? [];
}

export async function getEvalRun(runId: string): Promise<EvalRun> {
  const r = await fetch(
    `${getApiUrl()}/api/evals/runs/${encodeURIComponent(runId)}`,
  );
  return readJson(r, "Get run failed");
}

// ─── Traces ────────────────────────────────────────────────────────────────

export async function listTraceOperations(filters: {
  conversationId?: string;
  evalRunId?: string;
  lookbackHours?: number;
  maxOperations?: number;
}): Promise<TraceList> {
  const params = new URLSearchParams();
  if (filters.conversationId) params.set("conversation_id", filters.conversationId);
  if (filters.evalRunId) params.set("eval_run_id", filters.evalRunId);
  if (filters.lookbackHours) params.set("hours", String(filters.lookbackHours));
  if (filters.maxOperations) params.set("limit", String(filters.maxOperations));
  const qs = params.toString();
  const r = await fetch(`${getApiUrl()}/api/traces/operations${qs ? `?${qs}` : ""}`);
  return readJson(r, "List trace operations failed");
}

export async function getTraceOperation(
  operationId: string,
  lookbackHours = 168,
): Promise<TraceOperation> {
  const r = await fetch(
    `${getApiUrl()}/api/traces/operations/${encodeURIComponent(operationId)}?hours=${lookbackHours}`,
  );
  return readJson(r, "Get trace operation failed");
}
