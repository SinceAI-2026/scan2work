# Public MCP and ElevenLabs voice

The source includes two independent integrations: the MCP equipment-data server
and the browser ElevenLabs widget. Ilkka is managed in Wael's ElevenLabs workspace.
The original public demo disabled the widget together with offline model inference;
this branch separates an explicit voice opt-in from backend model policy.

## Connection for Wael

| Setting | Value |
| --- | --- |
| MCP URL | `https://scan2work.app/mcp` |
| Transport | Streamable HTTP (JSON responses; no SSE stream) |
| Authorization | `Authorization: Bearer <workspace secret>` |
| Existing agent | `agent_5501m6v02k69e2r8fwc0hznmfda5` (Ilkka) |
| Main widget hostname | `scan2work.app` |
| Optional review hostname | `review.scan2work.app` |
| Dynamic variable | `selected_equipment`, default `none` |

The MCP credential belongs only in the ElevenLabs workspace secret and the private
server configuration. Never put it in the widget, repository, a URL, screenshot
or public README. The deployed endpoint requires it even for loopback calls.

1. In the agent configuration used for the public site, replace the old tunnel
   MCP connection with this URL, HTTP transport and private bearer credential.
   Refresh its available tools. If the original Ilkka must retain local job-writing
   behavior, make a separate public agent configuration and supply its agent ID
   when enabling the widget.
2. In the default read-only mode, use the six read tools: `find_equipment`, `get_equipment_status`,
   `list_open_problems`, `get_procedure`, `search_service_knowledge`, `get_job`.
   It does not expose `start_job`, `complete_check`, `add_note` or `close_job`;
   attempts are rejected. If shared inspection jobs are explicitly enabled as
   described below, refresh the agent's tools to include those four writing
   tools too. Match its instructions to the chosen mode; read-only assistance
   must not claim to have recorded maintenance actions.
3. Keep the existing browser client tool `show_equipment`, and configure the
   `selected_equipment` variable. Its JSON parameter schema is:

   ```json
   {
     "type": "object",
     "properties": {
       "equipment": {"type": "string", "description": "Registered equipment tag, e.g. CV-003"}
     },
     "required": ["equipment"]
   }
   ```

   The client selects/focuses the equipment and returns a spoken result string.
   Unmapped scene objects pass `none` instead of borrowing a workbook tag.
4. Allow the intended hostname in the agent's widget configuration. Test a
   question about CV-003 and a request to show it. Verify actual MCP tool calls
   in the ElevenLabs conversation, then activate the widget on the main site.

[ElevenLabs MCP setup](https://elevenlabs.io/docs/eleven-agents/customization/tools/mcp)
and [widget configuration](https://elevenlabs.io/docs/eleven-agents/customization/widget)
describe their side of this connection. No ElevenLabs API key is sent to visitors.

## Run the service

Use the prepared baseline dataset and its installed Python environment, with
this branch's code. Set `FIELD_ATLAS_MCP_PUBLIC=1`, `VALMET_OFFLINE=1`,
`VALMET_DATA_DIR=/absolute/path/to/data/baseline`, and a private
`FIELD_ATLAS_MCP_TOKEN` of at least 32 characters. Run `python mcp_server.py 9090`.
It binds loopback; put the dedicated encrypted transport in front of it.

Public startup selects offline policy before configuration or retrieval imports.
By default it ignores operator job history and cannot persist jobs. To preserve
Wael's hosted assistant workflow, set `VALMET_PUBLIC_ASSISTANT=1` on the MCP
process and start the viewer with `--assistant`, using the same baseline data.
That explicit mode exposes all ten tools and shares the inspection job log;
write fields are limited to 1,000 characters. Enable Worker `ASSISTANT="1"` for
browser job POSTs. Scene/library/hardware writes remain blocked. The supplied
host wrappers read the same flag from `deploy/assistant.env` so both processes
agree. Normal installed MCP retains its existing ten-tool behavior. HTTP requests are bounded to 16 KiB,
eight batch entries and four concurrent batches in public mode.

The public site's Cloudflare Worker forwards `/mcp` only to `MCP_ORIGIN`, which
must use HTTPS. The demo uses a separate supervised quick tunnel. Its hostname
can change on restart, requiring a Worker configuration update; the public URL
remains `https://scan2work.app/mcp`. Management services are separate.

## Enable the widget after connecting Ilkka

With this branch deployed, configure `VALMET_VOICE_AGENT_ID` and optionally
`VALMET_VOICE_AGENT_NAME=Ilkka` / `VALMET_VOICE_MCP_URL=https://scan2work.app/mcp`.
The server projects a validated `runtime.voice` object with `service_mode=read_only`
or `shared_jobs`, matching the configured assistant mode. It does not enable
backend model calls or scene/library/hardware writes. Wael's `--assistant`
without an explicit agent ID also retains the original Ilkka widget through
`runtime.voice_assistant`; no disabled override masks that opt-in. Loading the
widget does not start a conversation or request microphone access.

Malformed explicit configuration disables the widget. Without either explicit
voice configuration or `--assistant`, public/offline voice stays disabled. CDN
loading failure displays a short fallback to workbook guidance. Unmarked legacy
online scenes retain their original widget behavior.

The 0.1.1 downloadable archives remain immutable. This integration is a later
source change; its cloud agent binding and widget activation are not included
in the offline 0.1.1 installer.

## Verified deployment — 7 November 2026

The public endpoint is deployed and tested through `https://scan2work.app/mcp`.
Unauthenticated requests return 401. Authenticated initialization and all six
read tools passed against the 504-object baseline; all four job-writing tools
were denied. The main room and separate 1,933-region review remain unchanged.
No operator history or lock file was created.

The service runs from immutable source snapshot `c37efcc` in its own supervised
process and encrypted tunnel, using the existing 0.1.1 environment/data. The
existing web, proxy and management processes were not changed. The quick-tunnel
origin is recorded as `MCP_ORIGIN` in the edge configuration; the private bearer
credential is stored separately and is not part of this repository.

Validation: 41 focused backend tests, 42 frontend tests and four edge proxy tests
passed. Both viewer patch scripts reported zero edits. Local browser QA showed
the explicitly enabled widget and verified selected-tag changes without
starting a call. The current public site's widget remains disabled: Wael must
configure Ilkka's MCP connection, then the voice opt-in source must be deployed
and enabled. No end-to-end ElevenLabs conversation has been claimed or tested.

## Compatibility follow-up after PR #8

PR #8 was squash-merged before the MCP follow-up commits. The new fix branch is
based on upstream `49ae804`, preserving its navigation, fault-panel, optional
voice and shared inspection jobs. It does not reintroduce the installer changes
already merged in PR #8. The live endpoint described above still runs the
read-only `c37efcc` snapshot; this compatibility follow-up has not been deployed.

Validation of the reconciled source: 46 focused backend tests, 17 frontend tests
and six edge tests passed. Temporary fixtures verified shared browser/MCP job
writes, the 1,000-character limit and other mutation denials. The default six-tool
MCP mode and upstream ten-tool shared mode are both covered. No production data,
services or agent settings were changed while preparing this fix PR.
