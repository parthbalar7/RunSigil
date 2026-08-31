# Framework adapters

Install the desired optional dependency with `pip install .[langgraph-adapter]` or
`pip install .[openai-agents-adapter]`. Both adapters use a scoped RunSigil API key and
fixed project, environment, and agent identifiers through `AdapterSettings`; tenant
identity is never accepted from framework state or model tool arguments.

`LangGraphRunSigilAdapter.node` expects `runsigil_action` state containing recipient,
amount, description, and a stable idempotency key. Compile the graph with a
checkpointer and invoke it with a thread ID. A pending approval appears as a native
interrupt. Resume with an object containing the exact `content_digest`, `decision`,
and `reason`.

`OpenAIAgentsRunSigilAdapter.tools()` returns a typed `runsigil_send_invoice`
`FunctionTool`. Add it to an Agents SDK `Agent`. The tool declares
`needs_approval=True`; approve or reject the returned SDK interruption through its
`RunState`. On approved invocation the adapter derives a stable idempotency key from
the tool-call ID, creates the RunSigil intent, and consumes RunSigil's exact-content
approval under the same authenticated API actor.

The adapters return only run IDs, states, digests, approval/evidence states, and error
codes. They do not record model prompts or outputs and do not make RunSigil an agent
process host. Wrap a framework runner in `runsigil_sdk.agent_invocation(...)` when a
GenAI `invoke_agent` span and duration metric are desired.
