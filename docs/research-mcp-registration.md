# MCP server registration vs. tools: how providers design it

Research note, 2026-09-24. Scope: how first-party AI chat/agent products register MCP servers and how that relates to tools, plus a recommendation for our orchestrator.

Every claim below links to a primary source. Claims I couldn't verify, because a page was blocked, returned 404, or research stopped early, are marked **unverified**.

## Sources fetched

| Key | URL |
| --- | --- |
| SPEC-TOOLS | https://modelcontextprotocol.io/specification/2025-06-18/server/tools |
| SPEC-SCHEMA | https://modelcontextprotocol.io/specification/2025-06-18/schema |
| A-API | https://docs.claude.com/en/docs/agents-and-tools/mcp-connector |
| A-CODE | https://code.claude.com/docs/en/mcp |
| A-CONN | https://support.claude.com/en/articles/11175166-getting-started-with-custom-connectors-using-remote-mcp |
| O-RESP | https://platform.openai.com/docs/guides/tools-connectors-mcp |
| O-DEV | https://platform.openai.com/docs/guides/developer-mode |
| O-SDK | https://openai.github.io/openai-agents-python/mcp/ |
| G-CLI | https://raw.githubusercontent.com/google-gemini/gemini-cli/main/docs/tools/mcp-server.md |
| G-ADK | https://google.github.io/adk-docs/tools-custom/mcp-tools/ |
| MS-VSC | https://code.visualstudio.com/docs/copilot/customization/mcp-servers |
| MS-VSC-REF | https://code.visualstudio.com/docs/copilot/reference/mcp-configuration |
| MS-VSC-TOOLS | https://code.visualstudio.com/docs/copilot/agents/agent-tools |
| MS-CS | https://learn.microsoft.com/en-us/microsoft-copilot-studio/agent-extend-action-mcp |
| CUR | https://cursor.com/docs/context/mcp |
| MIS | https://docs.mistral.ai/agents/tools/mcp |

The following pages failed to load:

- OpenAI help center "developer mode" article: returned 403.
- Claude help center "Manage Claude's tool access" article: fetch was cancelled.
- VS Code "approvals" page: returned 404.
- Mistral "managing connectors" and "human-in-the-loop" pages: returned 404 or were cancelled.

---

## 0. MCP spec baseline

- **Tool discovery.** Tools are discovered with `tools/list`, which is paginated. Each tool has `name`, `title`, `description`, `inputSchema`, an optional `outputSchema`, and optional `annotations` ([SPEC-TOOLS]).
- **List changes.** A server declares `capabilities.tools.listChanged`. If it does, it SHOULD send `notifications/tools/list_changed` when its tool list changes ([SPEC-TOOLS]).
- **Annotation fields and defaults** ([SPEC-SCHEMA]):
  - `readOnlyHint` defaults to false.
  - `destructiveHint` defaults to true and only matters when `readOnlyHint` is false.
  - `idempotentHint` defaults to false.
  - `openWorldHint` defaults to true.
- **Annotations are untrusted.** They are hints only. Clients MUST treat them as untrusted unless the server is trusted, and "should never make tool use decisions based on ToolAnnotations received from untrusted servers" ([SPEC-TOOLS], [SPEC-SCHEMA]).
- **Human in the loop.** There SHOULD always be a human able to deny tool invocations. Clients SHOULD:
  - show which tools are exposed to the model;
  - ask for confirmation on sensitive operations;
  - validate structured results against `outputSchema` ([SPEC-TOOLS]).

---

## 1. Anthropic

### 1a. Claude.ai, Desktop, Cowork: custom connectors

- **Registered entity.** A "custom connector", registered by its remote MCP server URL. Advanced settings take an optional OAuth Client ID and Secret ([A-CONN]).
- **Who can add one.** On Team and Enterprise plans only Owners can add connectors, under Organization settings > Connectors. Each member then connects and authenticates individually, "so Claude can only access tools and data that the individual user has access to." Pro and Max users add connectors themselves ([A-CONN]).
- **Auth.** The user clicks "Connect", which runs an OAuth flow. Access is revoked by disconnecting. The connection goes from Anthropic's cloud, not from the user's device, so the server must be publicly reachable ([A-CONN]).
- **Tool relationship.**
  - Connectors are toggled per conversation.
  - Individual tools can be disabled from the "Search and tools" menu.
  - Tool approval prompts include "Allow always" ([A-CONN]).
  - Admins can disable specific tool calls that render interactive UI ([A-CONN]).
  - Org admins can set per-tool `ask` or `blocked` controls on connectors, and Claude Code enforces them too ([A-CODE]).
- **Discovery and editing.** Editing a connector means removing it and re-adding it ([A-CONN]). How the tool list is snapshotted or refreshed in claude.ai: **unverified**.

### 1b. Claude Code

- **Registration.** `claude mcp add --transport http|sse|stdio <name> <url|-- cmd>` with `--header` and `--env` options. Also `claude mcp add-json` and `.mcp.json` ([A-CODE]).
- **Scopes** ([A-CODE]):
  - `local` (the default): stored in `~/.claude.json` under the project path.
  - `project`: stored in `.mcp.json` and checked in.
  - `user`: available across all projects.
  - Plugin servers and claude.ai connectors are lower-precedence sources.
  - Org-managed `managedMcpServers`, `allowedMcpServers`, and `deniedMcpServers` rank above all of these.
- **Project trust.** Project `.mcp.json` servers need interactive approval. A cloned repository cannot approve its own servers ([A-CODE]).
- **Auth.**
  - `/mcp` or `claude mcp login <name>` runs OAuth. Discovery tries RFC 9728 protected-resource metadata first, then falls back to RFC 8414. Supported client registration methods: DCR, CIMD, or a pre-registered `--client-id` / `--client-secret` ([A-CODE]).
  - Tokens are stored securely and refreshed automatically.
  - On a 401 the client refreshes the token and retries once.
  - A rejected refresh token marks the server "needs authentication" and shows a notice pointing to `/mcp`.
  - Removing a server deletes its tokens and client registration ([A-CODE]).
  - `oauth.scopes` pins the requested scopes. A 403 `insufficient_scope` is surfaced to the user ([A-CODE]).
- **Tool relationship.**
  - Tools are discovered live.
  - A `list_changed` notification triggers a refresh. If the refresh fails, the previous tools are kept ([A-CODE]).
  - An optional discovery cache loads tools from a previous session, shown as `cached ... 5 tools`, and connects on first use ([A-CODE]).
  - Tools with invalid schemas are excluded individually ([A-CODE]).
  - Tool names follow the pattern `mcp__<server>__<tool>`, which is used in permission rules ([A-CODE]).
- **Approval.**
  - The normal permission prompts apply.
  - A server can set `_meta["anthropic/requiresUserInteraction"]: true` to force a prompt on every call, even in bypass mode ([A-CODE]).
  - Whether Claude Code uses `readOnlyHint` for approval: **unverified**.

### 1c. Messages API MCP connector (beta)

- **Two parts** ([A-API]):
  - `mcp_servers[]` holds the connection: `{type:"url", url, name, authorization_token}`.
  - `tools[]` holds an `mcp_toolset` with `mcp_server_name`, `default_config`, and per-tool `configs` (`enabled`, `defer_loading`).
  - The old `tool_configuration.allowed_tools` field is deprecated in favour of this split ([A-API]).
- **Allowlist and denylist.**
  - Allowlist: set `default_config.enabled: false` and enable named tools.
  - Denylist: disable named tools.
  - An unknown tool name only logs a warning, "MCP servers may have dynamic tool availability" ([A-API]).
- **Auth.** The caller runs OAuth and passes an access token, and must refresh it itself ([A-API]).
- **Discovery and pinning.**
  - Tools are listed live by default.
  - Beta `mcp-client-2026-09-15` records an `mcp_tool_listing` block per server. Copying its `tools` into the toolset **pins** the list, and the API then does not query the server ([A-API]).
- **Approval.** No per-tool approval hook is documented. The docs recommend denylisting destructive tools ([A-API]).

---

## 2. OpenAI

### 2a. Responses API `mcp` tool

- **Registration shape.** `{type:"mcp", server_label, server_description, server_url | tunnel_id, allowed_tools, require_approval, authorization, defer_loading}` ([O-RESP]). `connector_id` is deprecated for models released after 2026-09-01 ([O-RESP]).
- **Discovery.**
  - The API lists tools and emits an `mcp_list_tools` item.
  - While that item stays in context, the tools are **not re-fetched** on later turns. Snapshot-in-context acts as the cache ([O-RESP]).
  - Listed tools include an `annotations` field ([O-RESP]).
- **Filtering.** `allowed_tools` imports only the named tools ([O-RESP]).
- **Approval.**
  - Approval is required by default.
  - `require_approval` accepts `"always"`, `"never"`, or `{never:{tool_names:[...]}}`.
  - The flow uses an `mcp_approval_request` item answered by an `mcp_approval_response` ([O-RESP]).
- **Auth.** OAuth is handled by the app. The `authorization` value is **not stored** and must be sent on every request ([O-RESP]).

### 2b. ChatGPT developer mode (apps from MCP servers)

- **Registered entity.** An "app" created from a remote MCP server over SSE or streaming HTTP ([O-DEV]).
- **Auth.** OAuth, no auth, or mixed. Client registration can use static credentials, CIMD, or DCR ([O-DEV]).
- **Tools.**
  - A per-app details page toggles tools on or off.
  - "Refresh" pulls new tools, descriptions, and instructions. This implies a **snapshot refreshed manually** ([O-DEV]).
- **Approval.**
  - "We respect the `readOnlyHint` ... Tools without this hint are treated as write actions."
  - Write actions require confirmation by default.
  - An approve or deny choice can be remembered per conversation only ([O-DEV]).
- **Not verified.** Apps SDK specifics and workspace admin controls are **unverified**; the help center returned 403.

### 2c. Agents SDK (Python)

- **Server types.** Hosted `HostedMCPTool`, which the Responses API executes, versus local `MCPServerStreamableHttp`, `MCPServerSse`, and `MCPServerStdio` ([O-SDK]).
- **Filtering.**
  - `tool_filter=create_static_tool_filter(allowed_tool_names, blocked_tool_names)`. The allowlist is applied first, then the blocklist.
  - A dynamic callable filter receives `run_context`, `agent`, and `server_name` ([O-SDK]).
- **Caching.**
  - `list_tools()` runs on every agent run.
  - `cache_tools_list=True` enables caching, and `invalidate_tools_cache()` clears it ([O-SDK]).
  - Handling of `list_changed`: **unverified**.
- **Approval.** `require_approval` accepts `always`/`never`, a per-tool map, or `{always:{tool_names}, never:{tool_names}}`. Hosted tools can also use an `on_approval_request` callback ([O-SDK]).

---

## 3. Google

### 3a. Gemini CLI

- **Registration.** `settings.json` → `mcpServers.<name>` with `command`/`url`/`httpUrl`, `headers`, `env`, `timeout`, `trust`, `includeTools`, and `excludeTools`. `excludeTools` wins over `includeTools` ([G-CLI]).
- **CLI and scopes.** `gemini mcp add|list|remove|enable|disable`, with scope `user` or `project` ([G-CLI]).
- **Global controls.** `mcp.allowed` and `mcp.excluded` list server names ([G-CLI]).
- **Auth.**
  - On a 401: OAuth discovery, then DCR, then a browser flow.
  - Tokens are stored in `~/.gemini/mcp-oauth-tokens.json` and refreshed automatically.
  - `/mcp auth <server>` re-authenticates.
  - RFC 9207 `iss` validation.
  - `authProviderType` also supports Google ADC and service-account impersonation ([G-CLI]).
- **Tools.**
  - Discovered at startup.
  - Schemas are sanitized.
  - Names are namespaced as `mcp_{server}_{tool}` ([G-CLI]).
  - Handling of `list_changed`: **unverified**.
- **Approval.**
  - `trust: true` bypasses all confirmations.
  - Otherwise the prompt offers once, always allow this tool, or always allow this server ([G-CLI]).
  - When an extension config and a user config are merged, the most restrictive tool list wins ([G-CLI]).

### 3b. ADK `McpToolset`

- **Discovery.** `McpToolset(connection_params=Stdio|SSE|StreamableHTTPConnectionParams(url, headers), tool_filter=[...])`. It calls `list_tools` on connect and adapts each tool into an ADK `BaseTool` ([G-ADK]).
- **Filtering.** `tool_filter` limits which tools are exposed. The docs recommend read-only filters for production ([G-ADK]).
- **Auth.** Examples use static headers. ADK OAuth flow: **unverified**.
- **Gemini API native MCP** (outside ADK): **unverified**.

---

## 4. Microsoft

### 4a. VS Code / GitHub Copilot

- **Registration.**
  - `.vscode/mcp.json` uses a `servers` object. `.mcp.json` and `~/.copilot/mcp-config.json` use `mcpServers`.
  - Servers can also live in the user profile ([MS-VSC], [MS-VSC-REF]).
  - Fields: `type`, `url`, `headers`, `oauth.clientId`, `inputs` for secret prompts, and `sandboxEnabled` ([MS-VSC-REF]).
- **Trust.**
  - Workspace servers inherit Workspace Trust.
  - Servers from other sources get their own trust dialog on first start or on configuration change.
  - `MCP: Reset Trust` clears these decisions ([MS-VSC]).
- **Tools.**
  - The "Configure Tools" picker toggles individual tools per request or per profile ([MS-VSC], [MS-VSC-TOOLS]).
  - "Tool availability is separate from tool approval" ([MS-VSC-TOOLS]).
  - `MCP: Reset Cached Tools` implies a cached tool list ([MS-VSC-REF]).
  - There is a limit of 128 tools per request ([MS-VSC-TOOLS]).
- **Approval.**
  - Tool calls may prompt for confirmation, and parameters are editable before approval ([MS-VSC-TOOLS]).
  - Sandboxed servers are auto-approved ([MS-VSC]).
  - Whether `readOnlyHint` drives auto-approval: **unverified**.
- **Admin.** GitHub policies govern MCP server access, via `chat.mcp.access` ([MS-VSC], [MS-VSC-REF]).

### 4b. Copilot Studio

- **Live discovery.** Tools and resources from a connected MCP server are available automatically. When the server changes, "Copilot Studio dynamically reflects these changes" ([MS-CS]).
- **Requirement.** Generative orchestration must be enabled ([MS-CS]).
- **Auth and approval details:** **unverified**.

---

## 5. Cursor

- **Registration.** `.cursor/mcp.json` for a project or `~/.cursor/mcp.json` globally, plus a marketplace and an extension API ([CUR]).
- **Auth.** OAuth through DCR, or static `auth.CLIENT_ID` / `CLIENT_SECRET` with fixed redirect URLs ([CUR]).
- **Tool relationship.**
  - Servers are toggled on or off in Customize.
  - Approval is required by default before MCP tool use ([CUR]).
  - The admin allowlist approves servers by command or URL pattern. Per-server **tool allowlists** decide which tools "can run automatically" ([CUR]).
  - Per-tool user toggles: **unverified**; only server toggles are documented.

## 6. Mistral

- **Registered entity.** "Connectors are registered MCP servers that you can use as tools in conversations and Agents." They have a visibility scope, private or Workspace-shared, and API keys can be scoped to one or the other ([MIS]).
- **Capabilities.** Tools are discovered on demand. Direct tool calling and a human-in-the-loop confirmation feature exist ([MIS]).
- **Field-level details:** **unverified**; the pages returned 404.

---

## Comparison table

| Provider | Registered entity | Discovery | Tool filtering | Approval model | Auth | Scope |
| --- | --- | --- | --- | --- | --- | --- |
| Claude.ai connectors | Connector (server URL) | Live (refresh behavior unverified) | Per-conversation connector toggle, per-tool disable; admin per-tool `ask`/`blocked` | Per-call prompt, "Allow always" | OAuth per user; org adds, members connect | User / org |
| Claude Code | Server (`mcp add`) | Live + `list_changed`; optional discovery cache | Permission rules on `mcp__srv__tool`; managed allow/deny | Permission prompts; `_meta` force-prompt | OAuth (PRM→AS metadata, DCR/CIMD/static), auto-refresh, needs-auth state | local / project / user / managed |
| Messages API | `mcp_servers` + `mcp_toolset` | Live, or **pinned** list (beta) | allow/deny via `configs.enabled` | None built in (denylist) | Caller-supplied bearer | Per request |
| OpenAI Responses | `mcp` tool (server_label/url) | Listed once, reused while `mcp_list_tools` is in context | `allowed_tools` | Default always; per-tool `never` list | Caller-supplied, not stored | Per request |
| ChatGPT dev mode | App from MCP server | Snapshot + manual Refresh | Per-tool toggles | `readOnlyHint` honoured; writes confirm | OAuth (static/CIMD/DCR), none, mixed | User (admin unverified) |
| OpenAI Agents SDK | Server object | `list_tools` per run; opt-in cache | static allow/block, dynamic callable | always/never/per-tool map | Headers/httpx auth | Code |
| Gemini CLI | `mcpServers` entry | At startup (list_changed unverified) | include/excludeTools; global allowed/excluded | Confirm; always tool/server; `trust` bypass | OAuth auto-discovery+DCR, ADC, SA impersonation | user / project |
| Google ADK | `McpToolset` | On connect | `tool_filter` | Unverified | Headers (OAuth unverified) | Code |
| VS Code Copilot | `servers` in mcp.json | Live, cached (reset command) | Tool picker toggles | Confirm; sandbox auto-approve | OAuth clientId, inputs | workspace / user / org policy |
| Copilot Studio | MCP server connection | Live, reflects changes | Unverified | Unverified | Unverified | Agent |
| Cursor | mcp.json server | Live (details unverified) | Server toggle; admin tool allowlist | Approve by default | OAuth DCR or static | project / global / team |
| Mistral | Connector | On demand | Unverified | HITL confirmation (details unverified) | Unverified | private / workspace |

## Common pattern

1. **The server is the registered entity, not the individual tool.** Nobody asks the user to create a record per tool. The connection holds the URL, transport, auth, and scope; tools hang off it.
2. **The tool list is discovered from `tools/list`.** A filter is applied at the point of use, either an allowlist, a denylist, or both, with deny winning ([G-CLI], [O-SDK]).
3. **Discovery is live by default, with some caching.** Examples:
   - OpenAI reuses the tool list while it stays in context ([O-RESP]).
   - The Agents SDK and Claude Code have opt-in caches ([O-SDK], [A-CODE]).
   - Anthropic now offers explicit **pinning** ([A-API]).
   - ChatGPT uses a snapshot plus a Refresh button ([O-DEV]).
4. **Auth lives on the server connection, per user.** OAuth tokens are refreshed automatically, and a "needs auth / re-authenticate" state is surfaced ([A-CODE], [G-CLI], [A-CONN]).
5. **Approval defaults to asking**, with per-tool "never ask" overrides and a server-level trust bypass ([O-RESP], [G-CLI], [CUR]). Only ChatGPT documents using `readOnlyHint` to skip confirmation ([O-DEV]), and the spec says annotations are untrusted unless the server is trusted ([SPEC-SCHEMA]).
6. **Availability and approval are separate settings** ([MS-VSC-TOOLS], [CUR] tool allowlists = auto-run).

---

## Recommendation for our orchestrator

### Data model

```text
McpServer                      -- the connection (today's "MCP connection", promoted to first-class)
  id, owner_scope (user|workspace), name, url, transport
  auth_kind (none|headers|oauth), oauth_client (DCR/static), trusted: bool (admin-set)
  status (ok|needs_reauth|error), capabilities_list_changed: bool

McpCredential                  -- per-user token for a server (unchanged encryption/refresh)
  server_id, user_id, enc_access, enc_refresh, expires_at

McpToolSnapshot                -- immutable, content-addressed result of tools/list
  id, server_id, fetched_at, tools_hash
  tools: [{name, title, description, input_schema, output_schema, annotations}]

AgentRevision.mcp_bindings[]   -- part of the immutable published agent revision
  server_id, snapshot_id,
  tools: [{name, approval: always|never|inherit}]   -- explicit allowlist, no "all"
```

Drop the current one-`Tool`-record-per-remote-tool flow. The allowlist inside the binding replaces it. Keep `Tool` for native tools only. If existing code needs a uniform list, project the bindings into it at runtime.

### Server-level allowlist, not individually pinned tools

- An agent references **one server with an allowlist**, which matches the Anthropic `mcp_toolset`, OpenAI `allowed_tools`, and Gemini `includeTools` designs.
- The allowlist is explicit. "All tools" is a draft-time convenience only, never saved into a published revision. Otherwise a server adding a destructive tool silently widens what the agent can do.

### Reproducibility with live discovery

- **Drafts** track the server's latest snapshot. The UI shows the current `tools/list` with checkboxes.
- **Publishing** freezes the `snapshot_id` into the revision. The model is given the **pinned** schemas and descriptions, not the live ones. This is the same idea as Anthropic's pinned toolset ([A-API]).
- **Before each call at runtime:**
  - Look the tool up in the current listing, or in the cache when `list_changed` hasn't fired.
  - If it's missing, return a tool error.
  - If its schema hash differs from the pinned one, apply policy: `fail` by default, or `warn` if the owner opted in.
  - Validate `structuredContent` against the pinned `output_schema` ([SPEC-TOOLS]).

### Approval policy from annotations

- **Default derivation:**
  - If the server is `trusted` and the tool has `readOnlyHint == true`, default to `never` (no prompt).
  - Otherwise default to `always`.
  - A missing hint means write, as ChatGPT does ([O-DEV]).
  - `destructiveHint` true, or unset on a non-read-only tool, gets a red badge.
- **Why only trusted servers:** the spec treats annotations from untrusted servers as untrusted ([SPEC-SCHEMA]).
- **Storing the policy:** the derived default is stored on the binding at publish time. The user can override it per tool, and a server-level "trust" skips prompts (as Gemini's `trust` does).

### Handling `list_changed` and drift

- On a notification, or on a periodic or manual refresh, fetch `tools/list`, hash it, and store a new `McpToolSnapshot` only if the hash changed. Same idea as Claude Code: keep the old snapshot if the refresh fails ([A-CODE]).
- Mark agents whose pinned tools are missing or changed as "drift detected". Offer "review & republish" with a diff of added, removed, and schema-changed tools, and flag any annotation that moved from read-only to write.
- `needs_reauth` stays on `McpCredential`/`McpServer`. Affected agents show it in the UI, and runs fail fast with a reauth link.

### UX

1. **Integrations → MCP servers.** Add a URL. OAuth starts automatically on a 401 or discovered metadata. The page shows status and tool count, with Refresh, Reconnect, and Remove actions.
2. **Agent editor → Tools → Add MCP server.** Pick a server, tick the tools you want, and set the approval column. It's pre-filled from annotations and badged Read, Write, or Destructive.
3. **Publish.** A snapshot diff shows the tool changes since the last publish.

Skipped for now: per-workspace shared credentials, and sandboxing. Add shared credentials when team servers need one service account. The model already allows for it through `owner_scope`.
