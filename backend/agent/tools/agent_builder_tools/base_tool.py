import structlog
from typing import Optional
from agentpress.tool import Tool
from agentpress.thread_manager import ThreadManager
from utils.logger import logger


class AgentBuilderBaseTool(Tool):
    def __init__(self, thread_manager: ThreadManager, db_connection, agent_id: str):
        super().__init__()
        self.thread_manager = thread_manager
        self.db = db_connection
        self.agent_id = agent_id
    
    async def _get_current_account_id(self) -> str:
        try:
            context_vars = structlog.contextvars.get_contextvars()
            thread_id = context_vars.get('thread_id')
            
            if not thread_id:
                raise ValueError("No thread_id available from execution context")
            
            client = await self.db.client
            
            thread_result = await client.table('threads').select('account_id').eq('thread_id', thread_id).limit(1).execute()
            if not thread_result.data:
                raise ValueError(f"Could not find thread with ID: {thread_id}")
            
            account_id = thread_result.data[0]['account_id']
            if not account_id:
                raise ValueError("Thread has no associated account_id")
            
            return account_id
            
        except Exception as e:
            logger.error(f"Error getting current account_id: {e}")
            raise

    async def _get_agent_data(self) -> Optional[dict]:
        try:
            logger.info(f"[BASE_TOOL] Getting agent data for agent_id: {self.agent_id}")
            client = await self.db.client
            
            # Get the current account ID to ensure we're querying the right agent
            account_id = await self._get_current_account_id()
            logger.info(f"[BASE_TOOL] Current account_id: {account_id}")
            
            # Query for the agent with both agent_id AND account_id
            result = await client.table('agents').select('*').eq('agent_id', self.agent_id).eq('account_id', account_id).execute()
            logger.info(f"[BASE_TOOL] Query result: {len(result.data) if result.data else 0} agents found")
            
            if not result.data:
                # Let's check if the agent exists but belongs to a different user
                any_agent = await client.table('agents').select('agent_id, account_id').eq('agent_id', self.agent_id).execute()
                if any_agent.data:
                    logger.error(f"[BASE_TOOL] Agent {self.agent_id} exists but belongs to account {any_agent.data[0]['account_id']}, not {account_id}")
                else:
                    logger.error(f"[BASE_TOOL] Agent {self.agent_id} does not exist at all")
                return None
                
            agent_data = result.data[0]
            logger.info(f"[BASE_TOOL] Found agent: {agent_data.get('name', 'Unknown')}")
            return agent_data
            
        except Exception as e:
            logger.error(f"[BASE_TOOL] Error getting agent data: {e}")
            return None 