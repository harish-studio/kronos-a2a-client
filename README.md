# kronos-a2a-client

A separate agent that finds [kronos-agent](https://github.com/harish-studio/kronos-agent)'s obligation-impact agent by its A2A Agent Card and delegates a question to it.

It stands in for another organisation's agent. It depends only on the public A2A SDKs, imports nothing from kronos-agent, and knows the agent only by a URL. CI checks that it installs and imports with no kronos-agent package present.

> The same author wrote both sides. The boundary is real in code and simulated in organisation; kronos-agent's [ADR-0002](https://github.com/harish-studio/kronos-agent/blob/main/docs/adr/0002-a2a-scoped-to-one-agent-and-one-client.md) says so.

## What it shows

1. **Discovery.** The client reads the Agent Card from the base URL. No API docs, no shared client library.
2. **`input-required`.** The agent pauses mid-task to ask which legal entity the question is about — but only when the answer differs between entities. That rule is a database query in kronos-agent, not a model judgement.
3. **Resume on the same task.** The answer goes back with the same task and context ids, and the agent continues. A plain REST call has no equivalent.

## Run

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install --pre .          # agent-framework-a2a has only pre-release builds

export A2A_URL=https://<a2a-fqdn>/          # kronos-agent: terraform -chdir=infra output -raw a2a_fqdn
export KRONOS_API_KEY=<key>                 # kronos-agent: terraform -chdir=infra output -raw kronos_api_key
python client.py
```

The first request after idle takes 10–30 seconds while a replica starts.

Expected shape of the output:

```
   [discovered <agent name>: <skill id>]

Q: Which DORA obligations apply to onboarding a critical ICT provider, ...

A: <the agent asks which legal entity>

   [task <task id> is input-required; answering 'KB-DE']

A: <the obligations and deadlines for KB-DE>
```

Other cases worth running:

| Command | Expected |
|---|---|
| `python client.py "<a DORA question>" KB-DE` | asks which entity, then answers for KB-DE |
| `python client.py "<a GDPR question>"` | answers without asking — GDPR obligations are identical for every entity, so asking would be theatre |

## Dependencies

`a2a-sdk` and `agent-framework-a2a`, pinned to the versions the kronos-agent server is built against. A protocol upgrade on either side should be a deliberate, matched bump.
