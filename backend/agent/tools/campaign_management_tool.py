import httpx
from typing import Dict, Any, Optional
from agentpress.tool import Tool, ToolResult, openapi_schema, xml_schema
from utils.config import config

class CampaignManagementTool(Tool):
    """Tool for managing campaigns via AWS Lambda endpoint."""

    def __init__(self):
        super().__init__()
        self.lambda_url = getattr(config, 'CAMPAIGN_MANAGEMENT_LAMBDA_URL', None) or \
            'https://ramdrbygbyxwgcrckkjhvmcokq0sddyv.lambda-url.eu-west-2.on.aws/'

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "campaign_build",
            "description": "Build a campaign configuration via Lambda.",
            "parameters": {
                "type": "object",
                "properties": {
                    "campaign_id": {"type": "string", "description": "Campaign ID."},
                    "user_id": {"type": "string", "description": "User ID."},
                    "configuration_name": {"type": "string", "description": "Configuration name."},
                    "organization_id": {"type": "string", "description": "Organization ID."},
                    "organization_name": {"type": "string", "description": "Organization name."}
                },
                "required": ["campaign_id", "user_id", "configuration_name", "organization_id", "organization_name"]
            }
        }
    })
    @xml_schema(
        tag_name="campaign-build",
        mappings=[
            {"param_name": "campaign_id", "node_type": "attribute", "path": "."},
            {"param_name": "user_id", "node_type": "attribute", "path": "."},
            {"param_name": "configuration_name", "node_type": "attribute", "path": "."},
            {"param_name": "organization_id", "node_type": "attribute", "path": "."},
            {"param_name": "organization_name", "node_type": "attribute", "path": "."}
        ],
        example='''
        <function_calls>
        <invoke name="campaign_build">
        <parameter name="campaign_id">your-campaign-id</parameter>
        <parameter name="user_id">your-user-id</parameter>
        <parameter name="configuration_name">your-config-name</parameter>
        <parameter name="organization_id">your-org-id</parameter>
        <parameter name="organization_name">your-org-name</parameter>
        </invoke>
        </function_calls>
        '''
    )
    async def campaign_build(self, campaign_id: str, user_id: str, configuration_name: str, organization_id: str, organization_name: str) -> ToolResult:
        payload = {
                "action": "build",
                "campaign_id": campaign_id,
                "user_id": user_id,
                "configuration_name": configuration_name,
                "organization_id": organization_id,
                "organization_name": organization_name
        }
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    self.lambda_url,
                    path="/campaign_manage",
                    httpMethod="POST",
                    body=payload,
                    headers={"Content-Type": "application/json"}
                )
                resp.raise_for_status()
                return self.success_response(resp.json())
        except Exception as e:
            return self.fail_response(f"Error calling campaign build: {str(e)}")

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "campaign_remove",
            "description": "Remove a campaign via Lambda.",
            "parameters": {
                "type": "object",
                "properties": {
                    "campaign_id": {"type": "string", "description": "Campaign ID."},
                    "user_id": {"type": "string", "description": "User ID."}
                },
                "required": ["campaign_id", "user_id"]
            }
        }
    })
    @xml_schema(
        tag_name="campaign-remove",
        mappings=[
            {"param_name": "campaign_id", "node_type": "attribute", "path": "."},
            {"param_name": "user_id", "node_type": "attribute", "path": "."}
        ],
        example='''
        <function_calls>
        <invoke name="campaign_remove">
        <parameter name="campaign_id">your-campaign-id</parameter>
        <parameter name="user_id">your-user-id</parameter>
        </invoke>
        </function_calls>
        '''
    )
    async def campaign_remove(self, campaign_id: str, user_id: str) -> ToolResult:
        payload = {
            "path": "/campaign_manage",
            "httpMethod": "POST",
            "body": {
                "action": "remove",
                "campaign_id": campaign_id,
                "user_id": user_id
            }
        }
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    self.lambda_url,
                    json=payload,
                    headers={"Content-Type": "application/json"}
                )
                resp.raise_for_status()
                return self.success_response(resp.json())
        except Exception as e:
            return self.fail_response(f"Error calling campaign remove: {str(e)}") 