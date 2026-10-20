# Microsoft 365 Graph Mock

This workspace contains the synthetic Microsoft 365 Graph stdio MCP server used
for local Agent Reliability Engineer development. Production uses the delegated
OBO MCP service instead.

The mock uses curated, synthetic fixtures and communicates with the Copilot SDK
over stdio. It does not listen on a network port or require credentials.

## Build

```bash
cd mocks
npm install
npm run build
```

The AKS backend image installs `m365-graph-mcp-server` on `PATH`. For native
development, run `npm link` in the package after building it.

The ARE configuration launches the mock from
`use-cases/agent-reliability-engineer/.mcp.json`:

```json
{
  "m365-graph": {
    "type": "local",
    "command": "m365-graph-mcp-server",
    "args": [],
    "tools": ["*"]
  }
}
```

## Testing the mock standalone

```bash
npx @modelcontextprotocol/inspector node packages/m365-graph-mcp-server/dist/server.js
```
