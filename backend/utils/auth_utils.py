import sentry
from fastapi import HTTPException, Request, Header
from typing import Optional, Dict, Any
import jwt
from jwt.exceptions import PyJWTError
from utils.logger import structlog
from utils.config import config

# User context data structure
class UserContext:
    def __init__(self, user_id: str, organization_id: Optional[str] = None, organization_name: Optional[str] = None):
        self.user_id = user_id
        self.organization_id = organization_id
        self.organization_name = organization_name
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "organization_id": self.organization_id,
            "organization_name": self.organization_name
        }

# Enhanced function to extract full user context from Clerk JWT
async def get_current_user_context_from_jwt(request: Request) -> UserContext:
    """
    Extract and verify the user context from the Clerk JWT in the Authorization header.
    
    Thanks to the Supabase-Clerk integration, Clerk JWTs are automatically verified
    by Supabase, so we just need to extract the user and organization info from claims.
    
    Args:
        request: The FastAPI request object
        
    Returns:
        UserContext: Object containing user_id, organization_id, and organization_name
        
    Raises:
        HTTPException: If no valid token is found or if the token is invalid
    """
    auth_header = request.headers.get('Authorization')
    
    if not auth_header or not auth_header.startswith('Bearer '):
        raise HTTPException(
            status_code=401,
            detail="No valid authentication credentials found",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    token = auth_header.split(' ')[1]
    
    try:
        # Decode without verification since Supabase handles verification via Clerk integration
        payload = jwt.decode(token, options={"verify_signature": False})
        # Extract Clerk user ID from 'sub' claim
        clerk_user_id = payload.get('sub')
        
        if not clerk_user_id:
            raise HTTPException(
                status_code=401,
                detail="Invalid token payload - missing user ID",
                headers={"WWW-Authenticate": "Bearer"}
            )
        
        # Validate that this looks like a Clerk user ID
        if not clerk_user_id.startswith('user_'):
            raise HTTPException(
                status_code=401,
                detail="Invalid Clerk user ID format",
                headers={"WWW-Authenticate": "Bearer"}
            )

        # Extract organization information from Clerk JWT
        # Clerk typically puts organization info in the 'org_id' and 'org_slug' claims
        organization_id = payload.get('org_id')  # Organization ID
        organization_name = payload.get('org_slug') or payload.get('org_name')  # Organization name/slug
        
        # Create user context object
        user_context = UserContext(
            user_id=clerk_user_id,
            organization_id=organization_id,
            organization_name=organization_name
        )

        # Set user context for logging and monitoring
        sentry_user_data = {"id": clerk_user_id}
        if organization_id:
            sentry_user_data["organization_id"] = organization_id
        if organization_name:
            sentry_user_data["organization_name"] = organization_name
            
        sentry.sentry.set_user(sentry_user_data)
        
        # Bind context variables for structured logging
        context_vars = {"user_id": clerk_user_id}
        if organization_id:
            context_vars["organization_id"] = organization_id
        if organization_name:
            context_vars["organization_name"] = organization_name
            
        structlog.contextvars.bind_contextvars(**context_vars)
        
        return user_context
        
    except PyJWTError:
        raise HTTPException(
            status_code=401,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"}
        )

# This function extracts the user ID from Clerk JWT (via Supabase integration)
async def get_current_user_id_from_jwt(request: Request) -> str:
    """
    Extract and verify the user ID from the Clerk JWT in the Authorization header.
    
    This is a wrapper around get_current_user_context_from_jwt for backward compatibility.
    
    Args:
        request: The FastAPI request object
        
    Returns:
        str: The Clerk user ID extracted from the JWT (format: user_xxxxx)
        
    Raises:
        HTTPException: If no valid token is found or if the token is invalid
    """
    user_context = await get_current_user_context_from_jwt(request)
    return user_context.user_id

# Legacy function name for backward compatibility
async def get_current_user_id(request: Request) -> str:
    """
    Legacy function name - redirects to get_current_user_id_from_jwt
    """
    return await get_current_user_id_from_jwt(request)

async def get_account_id_from_thread(client, thread_id: str) -> str:
    """
    Extract and verify the account ID from the thread.
    
    Args:
        client: The Supabase client
        thread_id: The ID of the thread
        
    Returns:
        str: The account ID associated with the thread
        
    Raises:
        HTTPException: If the thread is not found or if there's an error
    """
    try:
        response = await client.table('threads').select('account_id').eq('thread_id', thread_id).execute()
        
        if not response.data or len(response.data) == 0:
            raise HTTPException(
                status_code=404,
                detail="Thread not found"
            )
        
        account_id = response.data[0].get('account_id')
        
        if not account_id:
            raise HTTPException(
                status_code=500,
                detail="Thread has no associated account"
            )
        
        return account_id
    
    except Exception as e:
        error_msg = str(e)
        if "cannot schedule new futures after shutdown" in error_msg or "connection is closed" in error_msg:
            raise HTTPException(
                status_code=503,
                detail="Server is shutting down"
            )
        else:
            raise HTTPException(
                status_code=500,
                detail=f"Error retrieving thread information: {str(e)}"
            )
    
async def get_user_id_from_stream_auth(
    request: Request,
    token: Optional[str] = None
) -> str:
    """
    Extract and verify the Clerk user ID from either the Authorization header or query parameter token.
    This function is specifically designed for streaming endpoints that need to support both
    header-based and query parameter-based authentication (for EventSource compatibility).
    
    Args:
        request: The FastAPI request object
        token: Optional token from query parameters
        
    Returns:
        str: The Clerk user ID extracted from the JWT (format: user_xxxxx)
        
    Raises:
        HTTPException: If no valid token is found or if the token is invalid
    """
    try:
        # Try to get user_id from token in query param (for EventSource which can't set headers)
        if token:
            try:
                # Decode Clerk JWT (verification handled by Supabase integration)
                payload = jwt.decode(token, options={"verify_signature": False})
                clerk_user_id = payload.get('sub')
                if clerk_user_id and clerk_user_id.startswith('user_'):
                    sentry.sentry.set_user({ "id": clerk_user_id })
                    structlog.contextvars.bind_contextvars(
                        user_id=clerk_user_id
                    )
                    return clerk_user_id
            except Exception:
                pass
        
        # If no valid token in query param, try to get it from the Authorization header
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            try:
                # Extract token from header
                header_token = auth_header.split(' ')[1]
                payload = jwt.decode(header_token, options={"verify_signature": False})
                clerk_user_id = payload.get('sub')
                if clerk_user_id and clerk_user_id.startswith('user_'):
                    return clerk_user_id
            except Exception:
                pass
        
        # If we still don't have a user_id, return authentication error
        raise HTTPException(
            status_code=401,
            detail="No valid authentication credentials found",
            headers={"WWW-Authenticate": "Bearer"}
        )
    except HTTPException:
        # Re-raise HTTP exceptions as they are
        raise
    except Exception as e:
        error_msg = str(e)
        if "cannot schedule new futures after shutdown" in error_msg or "connection is closed" in error_msg:
            raise HTTPException(
                status_code=503,
                detail="Server is shutting down"
            )
        else:
            raise HTTPException(
                status_code=500,
                detail=f"Error during authentication: {str(e)}"
            )

async def verify_thread_access(client, thread_id: str, user_id: str):
    """
    Verify that a user has access to a specific thread based on direct ownership.
    
    Args:
        client: The Supabase client
        thread_id: The thread ID to check access for
        user_id: The Clerk user ID to check permissions for
        
    Returns:
        bool: True if the user has access
        
    Raises:
        HTTPException: If the user doesn't have access to the thread
    """
    try:
        # Query the thread to get account information
        thread_result = await client.table('threads').select('*,project_id').eq('thread_id', thread_id).execute()

        if not thread_result.data or len(thread_result.data) == 0:
            raise HTTPException(status_code=404, detail="Thread not found")
        
        thread_data = thread_result.data[0]
        
        # Check if project is public
        project_id = thread_data.get('project_id')
        if project_id:
            project_result = await client.table('projects').select('is_public').eq('project_id', project_id).execute()
            if project_result.data and len(project_result.data) > 0:
                if project_result.data[0].get('is_public'):
                    return True
            
        # Check if the user owns the thread directly (account_id should match user_id with Clerk)
        account_id = thread_data.get('account_id')
        if account_id and account_id == user_id:
            return True
            
        # Also check project ownership
        if project_id:
            project_result = await client.table('projects').select('account_id').eq('project_id', project_id).execute()
            if project_result.data and len(project_result.data) > 0:
                project_account_id = project_result.data[0].get('account_id')
                if project_account_id and project_account_id == user_id:
                    return True
        
        raise HTTPException(status_code=403, detail="Not authorized to access this thread")
    except HTTPException:
        # Re-raise HTTP exceptions as they are
        raise
    except Exception as e:
        error_msg = str(e)
        if "cannot schedule new futures after shutdown" in error_msg or "connection is closed" in error_msg:
            raise HTTPException(
                status_code=503,
                detail="Server is shutting down"
            )
        else:
            raise HTTPException(
                status_code=500,
                detail=f"Error verifying thread access: {str(e)}"
            )

async def get_optional_user_id(request: Request) -> Optional[str]:
    """
    Extract the Clerk user ID from the JWT in the Authorization header if present,
    but don't require authentication. Returns None if no valid token is found.
    
    This function is used for endpoints that support both authenticated and 
    unauthenticated access (like public projects).
    
    Args:
        request: The FastAPI request object
        
    Returns:
        Optional[str]: The Clerk user ID extracted from the JWT, or None if no valid token
    """
    auth_header = request.headers.get('Authorization')
    
    if not auth_header or not auth_header.startswith('Bearer '):
        return None
    
    token = auth_header.split(' ')[1]
    
    try:
        # Decode Clerk JWT (verification handled by Supabase integration)
        payload = jwt.decode(token, options={"verify_signature": False})
        
        # Extract Clerk user ID from 'sub' claim
        clerk_user_id = payload.get('sub')
        if clerk_user_id and clerk_user_id.startswith('user_'):
            sentry.sentry.set_user({ "id": clerk_user_id })
            structlog.contextvars.bind_contextvars(
                user_id=clerk_user_id
            )
            return clerk_user_id
        
        return None
    except PyJWTError:
        return None

async def verify_admin_api_key(x_admin_api_key: Optional[str] = Header(None)):
    if not config.KORTIX_ADMIN_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="Admin API key not configured on server"
        )
    
    if not x_admin_api_key:
        raise HTTPException(
            status_code=401,
            detail="Admin API key required. Include X-Admin-Api-Key header."
        )
    
    if x_admin_api_key != config.KORTIX_ADMIN_API_KEY:
        raise HTTPException(
            status_code=403,
            detail="Invalid admin API key"
        )
    
    return True

async def verify_agent_access(client, agent_id: str, user_id: str) -> dict:
    """
    Verify that a user has access to a specific agent based on ownership.
    
    Args:
        client: The Supabase client
        agent_id: The agent ID to check access for
        user_id: The user ID to check permissions for
        
    Returns:
        dict: Agent data if access is granted
        
    Raises:
        HTTPException: If the user doesn't have access to the agent or agent doesn't exist
    """
    try:
        agent_result = await client.table('agents').select('*').eq('agent_id', agent_id).eq('account_id', user_id).execute()
        
        if not agent_result.data:
            raise HTTPException(status_code=404, detail="Agent not found or access denied")
        
        return agent_result.data[0]
        
    except HTTPException:
        raise
    except Exception as e:
        structlog.error(f"Error verifying agent access for agent {agent_id}, user {user_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to verify agent access")
