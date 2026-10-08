# acp-client

Talk to an ACP (Agent Client Protocol) agent from asyncio, such as
Claude's [`claude-agent-acp`](https://www.npmjs.com/package/@agentclientprotocol/claude-agent-acp):
it runs the agent as a child process and talks to it in JSON-RPC, one
message per line, as micro-claude does in micro. hill-ops's strip asks Claude
for tips and answers with it, and palace's Claude pane talks to Claude
about a note.

```python
from acp_client import Agent, AgentError

async def permission(request):            # the agent wants to use a tool
    title = request["toolCall"]["title"]  # "Edit notes.md"
    return "allow" if await ask_yes_no(title) else None   # an optionId, or None

agent = Agent(["claude-agent-acp"], Path("~/projects/hill").expanduser(), permission=permission)
try:
    answer = await agent.ask("What does README.md say?", on_text=show, on_update=log)
except AgentError as e:
    print(e)                              # "Claude needs a login: run claude, then /login"
await agent.stop()
```

- **Agent(command, cwd, meta=None, permission=None, session=None)** is one
  agent and one session, started by the first question and kept until
  **stop()**, so it remembers what was said. `command` is the agent's
  command line, `cwd` the folder its session works in. `meta` goes with
  session/new: claude-agent-acp takes a system prompt and options there,
  such as its tools and model; without it, the agent works with its own
  settings, as Claude Code does (your CLAUDE.md, permissions and tools).
- `session` is an earlier session to carry on, by its id: the agent loads
  it (session/load) rather than starting a new one, so it remembers what
  was said in it, also after the app restarted. If the agent can't load
  sessions, or turns that one down (gone, say), it starts a new one.
  Once started, **session** is the session's id, for the app to keep.
- **start(on_update=None)** starts the agent and its session now, rather
  than at the first question. Carrying on a session, the agent tells what
  was said in it again, and `on_update` hears it: each question as
  `user_message_chunk` updates, then the answer's updates, as below.
- **ask(text, on_text=None, on_update=None)** returns the answer in one
  piece, once the questions before it are answered: one at a time, in
  turn. `text` may be a function that makes it, told whether the session
  is new; `on_text` hears the answer as it grows, all of it so far each
  time, and `on_update` each update the agent sends meanwhile, as ACP has
  it (`{"sessionUpdate": "tool_call", "title": ..., "status": ...}`).
  Cancelling the wait stops the agent's turn. `busy` says whether a
  question is being answered or waits its turn.
- **cancel()** stops the turn under way; `ask` returns the answer so far.
- **permission(request)** hears each permission the agent asks for: the
  request's params, with its `toolCall` and its `options`, each
  `{optionId, name, kind}` (`allow_once`, `allow_always`, `reject_once`,
  ...). It returns the optionId chosen, or None to turn it down; without
  it, every permission is turned down.
- The client offers the agent nothing of its own: no files, no terminal.
- The agent runs without `$HILL_SOCKET`, so that nothing it starts, such
  as palace or its tests, joins the hill-ops it runs in: its hello would take
  that hill-ops's panes over.
- **AgentError** says what went wrong in the client's own words (the agent
  can't start, has stopped, needs a login, turned a request down), never
  with the agent's message, which can name a file, so an app can put it in
  a log.
