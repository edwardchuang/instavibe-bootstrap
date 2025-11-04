from a2a.server.apps import A2AStarletteApplication
from a2a.types import AgentCard, AgentCapabilities, AgentSkill
from a2a.server.tasks import InMemoryTaskStore
from a2a.server.request_handlers import DefaultRequestHandler
from google.adk.agents import LoopAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.artifacts import InMemoryArtifactService
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
import os
import logging
from dotenv import load_dotenv
from social.agent_executor import (
    SocialAgentExecutor, 
)
import uvicorn
from social import agent

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

host=os.environ.get("A2A_HOST", "localhost")
port=int(os.environ.get("A2A_PORT",10001))
PUBLIC_URL=os.environ.get("PUBLIC_URL")

class SocialAgent:
  """An agent that handles social profile analysis."""

  SUPPORTED_CONTENT_TYPES = ["text", "text/plain"]

  def __init__(self):
    self._agent = self._build_agent()
    self.runner = Runner(
        app_name=self._agent.name,
        agent=self._agent,
        artifact_service=InMemoryArtifactService(),
        session_service=InMemorySessionService(),
        memory_service=InMemoryMemoryService(),
    )
    capabilities = AgentCapabilities(streaming=True)
    skill = AgentSkill(
                id="social_profile_analysis",
                name="Analyze Instavibe social profile",
                description="""
                Using a provided list of names, this agent synthesizes Instavibe social profile information by analyzing posts, friends, and events.
                It delivers a comprehensive single-paragraph summary for individuals, and for groups, identifies commonalities in their social activities
                and connections based on profile data.
                """,
                tags=["instavibe"],
                examples=["Can you tell me about Bob and Alice?"],
    )
    self.agent_card = AgentCard(
                name="Social Profile Agent",
                description="""
                Using a provided list of names, this agent synthesizes Instavibe social profile information by analyzing posts, friends, and events.
                It delivers a comprehensive single-paragraph summary for individuals, and for groups, identifies commonalities in their social activities
                and connections based on profile data.
                """,
                url=f"{PUBLIC_URL}",
                version="1.0.0",
                defaultInputModes=self.SUPPORTED_CONTENT_TYPES,
                defaultOutputModes=self.SUPPORTED_CONTENT_TYPES,
                capabilities=capabilities,
                skills=[skill],
    )

  def get_processing_message(self) -> str:
      return "Processing the social profile analysis request..."

  def _build_agent(self) -> LoopAgent:
    """Builds the LLM agent for the social profile analysis agent."""
    return agent.root_agent

if __name__ == '__main__':
    try:
        socialAgent = SocialAgent()

        request_handler = DefaultRequestHandler(
            agent_executor=SocialAgentExecutor(socialAgent.runner,socialAgent.agent_card),
            task_store=InMemoryTaskStore(),
        )

        server = A2AStarletteApplication(
            agent_card=socialAgent.agent_card,
            http_handler=request_handler,
        )

        uvicorn.run(server.build(), host='0.0.0.0', port=port)
    except Exception as e:
        logger.error(f"An error occurred during server startup: {e}")
        exit(1)