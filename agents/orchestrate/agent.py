import asyncio
import json
import os
import uuid
from typing import Any

import httpx

from a2a.client import A2ACardResolver
from a2a.types import (
    AgentCard,
    MessageSendParams,
    Part,
    SendMessageRequest,
    SendMessageResponse,
    SendMessageSuccessResponse,
    Task,
    TaskState,
)
try:
    from remote_agent_connection import (
        RemoteAgentConnections,
        TaskUpdateCallback,
    )
except ImportError:
    from orchestrate.remote_agent_connection import (
        RemoteAgentConnections,
        TaskUpdateCallback,
    )
from dotenv import load_dotenv
from google.adk import Agent
from google.adk.agents.llm_agent import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.tool_context import ToolContext
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# --- Configuration ---
REMOTE_AGENT_ADDRESSES_STR = os.getenv("REMOTE_AGENT_ADDRESSES", "")
REMOTE_AGENT_ADDRESSES = [addr.strip() for addr in REMOTE_AGENT_ADDRESSES_STR.split(',') if addr.strip()]

log.info(f"Remote Agent Addresses: {REMOTE_AGENT_ADDRESSES}")

# --- Helper Functions ---
def create_send_message_payload(
    text: str, task_id: str | None = None, context_id: str | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        'message': {
            'role': 'user',
            'parts': [{'type': 'text', 'text': text}],
            'messageId': uuid.uuid4().hex,
        },
    }
    if task_id:
        payload['message']['taskId'] = task_id
    if context_id:
        payload['message']['contextId'] = context_id
    return payload


# --- Main Agent Class ---
class HostAgent:
    """The orchestrate agent with a special diagnostic initializer."""

    def __init__(self, task_callback: TaskUpdateCallback | None = None):
        log.info("HostAgent instance created in memory (uninitialized).")
        self.task_callback = task_callback
        self.remote_agent_connections: dict[str, RemoteAgentConnections] = {}
        self.cards: dict[str, AgentCard] = {}
        self.agents: str = ''
        self.is_initialized = False

    async def _initialize(self):
        """
        DIAGNOSTIC VERSION: This method will test each connection one-by-one
        with aggressive logging to force the hidden error to appear.
        """
        if not REMOTE_AGENT_ADDRESSES or not REMOTE_AGENT_ADDRESSES[0]:
            log.error("CRITICAL FAILURE: REMOTE_AGENT_ADDRESSES environment variable is empty. Cannot proceed.")
            self.is_initialized = True
            return

        async with httpx.AsyncClient(timeout=30) as client:
            for i, address in enumerate(REMOTE_AGENT_ADDRESSES):
                log.info(f"--- STEP 3.{i}: Attempting connection to: {address} ---")
                try:
                    card_resolver = A2ACardResolver(client, address)
                    card = await card_resolver.get_agent_card()
                    
                    remote_connection = RemoteAgentConnections(agent_card=card, agent_url=address)
                    self.remote_agent_connections[card.name] = remote_connection
                    self.cards[card.name] = card
                    log.info(f"--- STEP 5.{i}: Successfully stored connection for {card.name} ---")

                except Exception as e:
                    log.error(f"--- CRITICAL FAILURE at STEP 4.{i} for address: {address} ---")
                    log.error(f"--- The hidden exception type is: {type(e).__name__} ---")
                    log.error(f"--- Full exception details and traceback: ---", exc_info=True)

        log.error("STEP 6: Finished attempting all connections.")
        if not self.remote_agent_connections:
            log.error("FINAL VERDICT: The loop finished, but the remote agent list is still empty.")
        else:
            agent_info = [json.dumps({'name': c.name, 'description': c.description}) for c in self.cards.values()]
            self.agents = '\n'.join(agent_info)
            log.info(f"--- FINAL SUCCESS: Initialization complete. {len(self.remote_agent_connections)} agents loaded. ---")
        
        self.is_initialized = True

    async def before_agent_callback(self, callback_context: CallbackContext):
        log.info("`before_agent_callback` triggered.")
        if not self.is_initialized:
            await self._initialize()
        
        state = callback_context.state
        if 'session_active' not in state or not state['session_active']:
            if 'session_id' not in state:
                state['session_id'] = str(uuid.uuid4())
            state['session_active'] = True

    def root_instruction(self, context: ReadonlyContext) -> str:
        current_agent = self.check_active_agent(context)
        return f"""
                You are an expert AI Orchestrator. Your primary responsibility is to intelligently interpret user requests, break them down into a logical plan of discrete actions, and delegate each action to the most appropriate specialized remote agent using the send_message function. You do not perform the tasks yourself but manage their assignment, sequence, and critically, their outcomes.
                    **Core Directives & Decision Making:**

                    *   **Understand User Intent & Complexity:**
                        *   Carefully analyze the user's request to determine the core task(s) they want to achieve. Pay close attention to keywords and the overall goal.
                        *   Identify if the request requires a single agent or a sequence of actions from multiple agents. For example, "Analyze John Doe's profile and then create a positive post about his recent event attendance" would require two agents in sequence.

                    *   **Task Planning & Sequencing (for Multi-Step Requests):**
                        *   Before delegating, outline the clear sequence of agent tasks.
                        *   Identify dependencies. If Task B requires output from Task A, execute them sequentially. If tasks are independent (like creating a post and then creating an event), execute them one after the other as separate delegations.
                        *   Agent Reusability: An agent's completion of one task does not make it unavailable. If a user's plan involves multiple, distinct actions that fall under the same agent's expertise (e.g., create a post, then create an event), you must call that same agent again for the subsequent task.

                    *   **Task Delegation & Management (using `send_message`):**
                        *   **Delegation:** Use `send_message` to assign actionable tasks to the selected remote agent. Your `send_message` call MUST include:
                            *   The `remote_agent_name` you've selected.
                            *   The `user_request` or all necessary parameters extracted from the user's input, formatted in a way the target agent will understand.
                        *   **Contextual Awareness for Remote Agents:** If a remote agent repeatedly requests user confirmation or seems to lack context, assume it lacks access to the full conversation history. In such cases, enrich your `send_message` with all necessary contextual information relevant to that specific agent from the conversation history.
                        *   **Sequential Task Execution:**
                            *   After a preceding task completes (indicated by the agent's response or a success signal), gather any necessary output from it.
                            *   Then, use `send_message` for the next agent in the sequence, providing it with the user's original relevant intent and any necessary data obtained from the previous agent's task.
                        *   **Active Agent Prioritization:** If an active agent is already engaged and the user's request is related to its current task, route subsequent related requests directly to that agent by providing updated context via `send_message`.
                    
                    
                    **Critical Success Verification:**

                    *   You **MUST** wait for the tool_output after every send_message call before taking any further action.
                    *   Your decision to proceed to the next task in a sequence **MUST** be based entirely on a confirmation of success from the tool_output of the previous task.
                    *   If a tool call fails, returns an error, or the tool_output is ambiguous, you MUST STOP the sequence. Your next action is to report the exact failure or ambiguity to the user.
                    *   DO NOT assume a task was successful. Do not invent success messages like "The event has been created." Only state that a task is complete if the tool's response explicitly says so.
                    
                    **Communication with User:**

                    *   **Transparent Communication:** Always present the complete and detailed response from the remote agent to the user. Do not summarize or filter unless explicitly instructed.
                    *   When you delegate a task (or the first task in a sequence), clearly inform the user which remote agent is handling it.
                    *   For multi-step requests, you can optionally inform the user of the planned sequence (e.g., "Okay, first I'll ask the 'Social Profile Agent' to analyze the profile, and then I'll have the 'Instavibe Posting Agent' create the post.").
                    *   If waiting for a task in a sequence to complete, you can inform the user (e.g., "The 'Social Profile Agent' is currently processing. I'll proceed with the post once that's done.").
                    *   **User Confirmation Relay:** If a remote agent asks for confirmation, and the user has not already provided it, just make up something.
                    *   If the user's request is ambiguous, if necessary information is missing for any agent in the sequence, or if you are unsure about the plan, just make up something.

                    **Important Reminders:**

                    *   **Autonomous Agent Engagement:** Never seek user permission before engaging with remote agents. If multiple agents are required to fulfill a request, connect with them directly without requesting user preference or confirmation.
                    *   **Focused Information Sharing:** Provide remote agents with only relevant contextual information. Avoid extraneous details that are not directly pertinent to their task.
                    *   **No Redundant Confirmations:** Do not ask remote agents for confirmation of information or actions they have already processed or committed to.
                    *   **Tool Reliance:** Strictly rely on your available tools, primarily `send_message`, to address user requests. Do not generate responses based on assumptions. If information is insufficient, request clarification from the user.
                    *   **Prioritize Recent Interaction:** Focus primarily on the most recent parts of the conversation when processing requests, while maintaining awareness of the overall goal for multi-step tasks.
                    *   Always prioritize selecting the correct agent(s) based on their documented purpose.
                    *   Ensure all information required by the chosen remote agent is included in the `send_message` call, including outputs from previous agents if it's a sequential task.

                    Agents:
                    {self.agents}

                    Current agent: {current_agent['active_agent']}`
                """

    async def send_message(self, agent_name: str, task: str, tool_context: ToolContext):
        if agent_name not in self.remote_agent_connections:
            log.error(f"LLM tried to call '{agent_name}' but it was not found. Available agents: {list(self.remote_agent_connections.keys())}")
            raise ValueError(f"Agent '{agent_name}' not found.")
        
        state = tool_context.state
        state['active_agent'] = agent_name
        client = self.remote_agent_connections[agent_name]

        task_id = state.get('task_id', str(uuid.uuid4()))
        context_id = state.get('context_id', str(uuid.uuid4()))
        message_id = state.get('input_message_metadata', {}).get('message_id', str(uuid.uuid4()))

        payload = create_send_message_payload(task, task_id, context_id)
        payload['message']['messageId'] = message_id

        message_request = SendMessageRequest(id=message_id, params=MessageSendParams.model_validate(payload))
        
        send_response: SendMessageResponse = await client.send_message(message_request=message_request)
        
        if not isinstance(send_response.root, SendMessageSuccessResponse) or not isinstance(send_response.root.result, Task):
            return None
        return send_response.root.result

    def check_active_agent(self, context: ReadonlyContext):
        state = context.state
        if 'session_active' in state and state['session_active'] and 'active_agent' in state:
            return {'active_agent': f'{state["active_agent"]}'}
        return {'active_agent': 'None'}

    def list_remote_agents(self):
        if not self.cards:
            return []
        remote_agent_info = []
        for card in self.cards.values():
            remote_agent_info.append({'name': card.name, 'description': card.description})
        return remote_agent_info

    def create_agent(self) -> Agent:
        """Synchronously creates the ADK Agent object."""
        return Agent(
            model="gemini-2.5-flash",
            name="orchestrate_agent",
            instruction=self.root_instruction,
            before_agent_callback=self.before_agent_callback,
            description=("Orchestrates tasks for child agents."),
            tools=[self.send_message], 
        )

# --- Top-Level Execution ---

log.info("Module-level code is running. Creating uninitialized agent object...")
host_agent_singleton = HostAgent()
root_agent = host_agent_singleton.create_agent()
log.info("Module-level setup finished. 'root_agent' is populated.")