"""A2A client: discovers kronos-agent's obligation-impact agent and delegates one query.

The other half of the A2A boundary. This repository depends only on the
public A2A SDKs. It imports nothing from kronos-agent, shares no process
or database with it, and knows the agent only by a URL and the Agent
Card served there.

The same author wrote both sides, so the boundary is real in code and
simulated in organisation. kronos-agent's ADR-0002 says so plainly.

WHAT THIS DEMONSTRATES, and what a plain REST call could not:

    1. Discovery      - the card is read from the base URL; no API docs.
    2. input-required - the agent pauses mid-task to ask which legal
                        entity, because the answer genuinely differs
                        between them.
    3. Resume         - the answer goes back on the SAME task, and the
                        agent continues with it as a hard constraint.

Step 3 is the one REST has no equivalent for. A mid-task clarification
that resumes the same task is a session protocol, and building one ad
hoc over REST is reimplementing part of A2A badly.

`background=True` is required on the first call, not optional: the MAF
A2A client only attaches a continuation token to an input-required
response in background mode (see A2AAgent._build_continuation_token).
Without it the caller receives the question and has no way to answer it.

Run:
    export A2A_URL=https://<a2a-fqdn>/        # default http://localhost:9000/
    export KRONOS_API_KEY=<key issued by the operator>
    python client.py
    python client.py "<question>" <ENTITY_CODE>
"""

import asyncio
import os
import sys

import httpx
from agent_framework_a2a import A2AAgent
from a2a.client.card_resolver import A2ACardResolver

import uuid
from a2a.types import GetTaskRequest, Message, Part, Role, SendMessageRequest, TaskState

# The ADR's own example question. Scoped to DORA deliberately: the
# register holds different DORA obligations per entity, so this is a
# question the agent cannot answer correctly without asking.
DEFAULT_QUESTION = (
    "Which DORA obligations apply to onboarding a critical ICT provider, "
    "and which deadlines fall in the next 90 days?"
)

# The answer given when the agent asks which entity. Overridable so the
# demo can show a wrong answer being rejected rather than guessed at.
DEFAULT_ENTITY = "KB-DE"


def _text(response) -> str:
    """Text from an AgentResponse, wherever this SDK put it.

    A background or input-required response carries no `.text`: the
    agent's message hangs off the underlying Task's status instead, so
    the question would otherwise print blank.
    """
    direct = getattr(response, "text", None)
    if direct:
        return direct

    raw = getattr(response, "raw_representation", None)
    status = getattr(raw, "status", None)
    message = getattr(status, "message", None)
    if message is not None:
        return "".join(p.text for p in message.parts if p.text)

    return ""


async def main() -> None:
    base = os.environ.get("A2A_URL", "http://localhost:9000/")
    question = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUESTION
    entity = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_ENTITY

    async with httpx.AsyncClient(
        headers={"X-API-Key": os.environ["KRONOS_API_KEY"]},
        timeout=httpx.Timeout(30.0, read=300.0),
    ) as http_client:
        # Resolve the Agent Card ourselves rather than passing `url`.
        # A2AAgent given only a url builds a MINIMAL card locally and
        # never fetches the real one — which silently drops the server's
        # advertised capabilities, streaming included, and means the
        # demo would not actually be performing discovery at all.
        card = await A2ACardResolver(http_client, base.rstrip("/")).get_agent_card()
        print(f"   [discovered {card.name}: {card.skills[0].id}]\n")

        client_agent = A2AAgent(
            name="demo-client",
            agent_card=card,
            http_client=http_client,
        )

        print(f"Q: {question}\n")
        first = await client_agent.run(question,stream=False, background=True)
        
        token = getattr(first, "continuation_token", None)
        if token is None:
            # The agent answered outright. Correct whenever the question
            # is unambiguous — a clarification firing here would be the
            # theatre ADR-0002 rejects.
            print(f"A: {_text(first)}\n")
            return

        parked = await client_agent.client.get_task(
            GetTaskRequest(id=token["task_id"])
        )
        # Fall back to the response's own text: the task's status message
        # is written asynchronously and is occasionally not yet readable
        # on the first get_task.
        question_text = "".join(
            p.text for p in parked.status.message.parts if p.text
        ) or _text(first)
        print(f"A: {question_text}\n")

        # The task is parked in input-required. `A2AAgent.run(
        # continuation_token=...)` cannot be used here: it always
        # RESUBSCRIBES to an SSE stream, which requires the card to
        # advertise streaming — and this agent genuinely does not stream,
        # so advertising it would be a lie that fails on the first call.
        #
        # So the answer goes back over the raw protocol instead: a message
        # carrying the SAME task_id and context_id. That is what makes this
        # a resumption rather than a new question, and it is the capability
        # a REST endpoint has no equivalent for.
        print(f"   [task {token['task_id']} is input-required; answering {entity!r}]\n")

        reply = Message(
            message_id=str(uuid.uuid4()),
            role=Role.ROLE_USER,
            task_id=token["task_id"],
            context_id=token["context_id"],
            parts=[Part(text=entity)],
        )

        # Send the answer, then poll. `send_message` returns a SNAPSHOT of
        # the task as it stands when the call returns — for anything that
        # takes real work the artifacts are not there yet, and the stream
        # yields one `task` event with an empty artifact list. Polling to a
        # terminal state is the non-streaming half of the task lifecycle,
        # and it is the thing a REST caller would have to build by hand.
        async for _ in client_agent.client.send_message(
            SendMessageRequest(message=reply)
        ):
            pass

        # Poll the task itself rather than AgentResponse. `poll_task`
        # maps a Task onto an AgentResponse whose `.text` is the last
        # MESSAGE on the task — which, after answering a clarification,
        # is the caller's own answer echoed back, and on a failed task is
        # not the rejection at all. Reading the Task directly gives the
        # state and the agent's own message, which is what the A2A task
        # lifecycle is for.
        for _ in range(60):
            await asyncio.sleep(2)
            task = await client_agent.client.get_task(
                GetTaskRequest(id=token["task_id"])
            )
            state = task.status.state

            if state == TaskState.TASK_STATE_COMPLETED:
                for artifact in task.artifacts:
                    print("A:", "".join(p.text for p in artifact.parts if p.text))
                break

            if state == TaskState.TASK_STATE_FAILED:
                # The entity did not resolve. The agent refuses rather
                # than guessing: a wrong entity produces a confidently
                # wrong answer, which is the failure this whole
                # clarification mechanism exists to prevent.
                print("REJECTED:", "".join(
                    p.text for p in task.status.message.parts if p.text
                ))
                break
        else:
            print("   [task did not reach a terminal state within 2 minutes]")

if __name__ == "__main__":
    asyncio.run(main())
