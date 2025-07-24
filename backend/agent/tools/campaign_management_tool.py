import aioboto3
import json
from typing import Dict, Any, Optional, List, Union
from agentpress.tool import Tool, ToolResult, openapi_schema, xml_schema
from utils.config import config
import uuid
import datetime
from supabase import create_async_client
from utils.logger import logger

class CampaignManagementTool(Tool):
    """Tool for managing campaigns via AWS Lambda SDK (aioboto3)."""

    def __init__(self):
        super().__init__()
        self.aws_access_key_id = getattr(config, 'AWS_ACCESS_KEY_ID', None)
        self.aws_secret_access_key = getattr(config, 'AWS_SECRET_ACCESS_KEY', None)
        self.aws_region_name = getattr(config, 'AWS_REGION_NAME', None)
        self.lambda_function_name = getattr(config, 'CAMPAIGN_MANAGEMENT_LAMBDA_FUNCTION_NAME', None) or 'campaign-management-function'
        self.session = aioboto3.Session()

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
            "path": "/campaign_manage",
            "httpMethod": "POST",
            "body": {
                "action": "build",
                "campaign_id": campaign_id,
                "user_id": user_id,
                "configuration_name": configuration_name,
                "organization_id": organization_id,
                "organization_name": organization_name
            }
        }
        try:
            async with self.session.client(
                'lambda',
                aws_access_key_id=self.aws_access_key_id,
                aws_secret_access_key=self.aws_secret_access_key,
                region_name=self.aws_region_name
            ) as lambda_client:
                response = await lambda_client.invoke(
                    FunctionName=self.lambda_function_name,
                    InvocationType='RequestResponse',
                    Payload=bytes(json.dumps(payload), encoding='utf-8')
                )
                result_payload = await response['Payload'].read()
                result = json.loads(result_payload)
                return self.success_response(result)
        except Exception as e:
            return self.fail_response(f"Error calling campaign build via Lambda SDK: {str(e)}")

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
            async with self.session.client(
                'lambda',
                aws_access_key_id=self.aws_access_key_id,
                aws_secret_access_key=self.aws_secret_access_key,
                region_name=self.aws_region_name
            ) as lambda_client:
                response = await lambda_client.invoke(
                    FunctionName=self.lambda_function_name,
                    InvocationType='RequestResponse',
                    Payload=bytes(json.dumps(payload), encoding='utf-8')
                )
                result_payload = await response['Payload'].read()
                result = json.loads(result_payload)
                return self.success_response(result)
        except Exception as e:
            return self.fail_response(f"Error calling campaign remove via Lambda SDK: {str(e)}")

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "send_prelimilary_job",
            "description": "Submit a batch of research jobs to SQS. For 'ticker' jobs, 'name' and 'ticker' are required. For 'topic' jobs, 'topic' is required.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_list": {
                        "type": "array",
                        "description": "List of job objects. For 'ticker' jobs, 'name' and 'ticker' are required. For 'topic' jobs, 'topic' is required. Optional fields: 'performance', 'picked_reason', etc.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "enum": ["ticker", "topic"], "description": "Type of job: 'ticker' or 'topic'."},
                                "name": {"type": "string", "description": "Name of the job (required for ticker jobs)."},
                                "ticker": {"type": "string", "description": "Ticker symbol (required if type is 'ticker')."},
                                "topic": {"type": "string", "description": "Topic text (required if type is 'topic')."},
                                "performance": {"type": "object", "description": "Performance data (optional)."},
                                "picked_reason": {"type": "string", "description": "Reason for picking (optional)."}
                            },
                            "required": ["type"]
                        }
                    },
                    "batch_id": {"type": "string"}
                },
                "required": ["job_list", "batch_id"]
            }
        }
    })
    @xml_schema(
        tag_name="send-prelimilary-job",
        mappings=[
            {"param_name": "job_list", "node_type": "content", "path": "."},
            {"param_name": "batch_id", "node_type": "attribute", "path": "."}
        ],
        example='''
        <function_calls>
        <invoke name="send_prelimilary_job">
        <parameter name="job_list">[{"name": "BioXcel Therapeutics Inc", "type": "ticker", "ticker": "BTAI"}, {"type": "topic", "topic": "FDA Approves KEYTRUDA for PD-L1+ Resectable Locally Advanced Head & Neck Squamous Cell Carcinoma"}]</parameter>
        <parameter name="batch_id">batch-123</parameter>
        </invoke>
        </function_calls>
        '''
    )
    async def send_prelimilary_job(self, job_list, batch_id):
        """
        Submits research jobs for a list of jobs using batch operations to SQS and a secondary Supabase DB.
        For 'ticker' jobs, 'name' and 'ticker' are required. For 'topic' jobs, 'topic' is required.
        Jobs missing required fields are skipped and reported as failed.
        Args:
            job_list (list): List of job dicts (each with type-specific required fields)
            batch_id (str): Batch identifier for grouping messages
        Returns:
            dict: Results containing successful and failed jobs
        """
        # SQS setup
        sqs_queue_url = getattr(config, 'SQS_QUEUE_URL', None)
        if not sqs_queue_url:
            return self.fail_response("SQS_QUEUE_URL not configured in config.")
        # Create a new Supabase client for the secondary DB
        try:
            supabase = await create_async_client(config.JOB_SUPABASE_URL, config.JOB_SUPABASE_SERVICE_ROLE_KEY)
        except Exception as e:
            return self.fail_response(f"Failed to connect to secondary Supabase: {str(e)}")
        async with self.session.client(
            'sqs',
            aws_access_key_id=self.aws_access_key_id,
            aws_secret_access_key=self.aws_secret_access_key,
            region_name=self.aws_region_name
        ) as sqs:
            successful_jobs = []
            failed_jobs = []
            chunk_size = 10
            for i in range(0, len(job_list), chunk_size):
                job_chunk = job_list[i:i+chunk_size]
                supabase_entries = []
                sqs_entries = []
                for job in job_chunk:
                    job_type = job.get('type')
                    if not job_type:
                        failed_jobs.append({"job": job, "error": "Missing required field: 'type'"})
                        continue
                    if job_type == 'ticker':
                        name = job.get('name')
                        ticker = job.get('ticker')
                        if not name:
                            failed_jobs.append({"job": job, "error": "Missing required field: 'name' for ticker job"})
                            continue
                        if not ticker:
                            failed_jobs.append({"job": job, "error": "Missing required field: 'ticker' for ticker job"})
                            continue
                    elif job_type == 'topic':
                        topic = job.get('topic')
                        if not topic:
                            failed_jobs.append({"job": job, "error": "Missing required field: 'topic' for topic job"})
                            continue
                    else:
                        failed_jobs.append({"job": job, "error": f"Unknown job type: {job_type}"})
                        continue
                    try:
                        content_id = str(uuid.uuid4())
                        timestamp = datetime.datetime.now().isoformat()
                        if job_type == 'ticker':
                            message = f"Ticker {job['ticker']} research started"
                            supabase_entry = {
                                'content_id': content_id,
                                'batch_id': batch_id,
                                'status': 'queued',
                                'message': message,
                                'created_at': timestamp,
                                'updated_at': timestamp,
                                'is_regeneration': False,
                                'version_number': 1
                            }
                            sqs_message = {
                                'jobId': content_id,
                                'batchId': batch_id,
                                'job': job,
                                'type': job_type,
                                'name': name
                            }
                        else:  # topic
                            message = f"Topic {job.get('topic')} research started"
                            supabase_entry = {
                                'content_id': content_id,
                                'batch_id': batch_id,
                                'status': 'queued',
                                'message': message,
                                'created_at': timestamp,
                                'updated_at': timestamp,
                                'is_regeneration': False,
                                'version_number': 1
                            }
                            sqs_message = {
                                'jobId': content_id,
                                'batchId': batch_id,
                                'job': job,
                                'type': job_type
                            }
                        supabase_entries.append(supabase_entry)
                        sqs_entries.append({
                            'Id': content_id,
                            'MessageBody': json.dumps(sqs_message)
                        })
                        successful_jobs.append(content_id)
                    except Exception as e:
                        failed_jobs.append({"job": job, "error": str(e)})
                # Insert into Supabase
                if supabase_entries:
                    try:
                        await supabase.table('content_jobs').insert(supabase_entries).execute()
                    except Exception as e:
                        for entry in supabase_entries:
                            job = next((j for j in job_chunk if f"Ticker {j.get('ticker')}" in entry.get('message', '')), "unknown")
                            failed_jobs.append({"job": job, "error": f"Database insert failed: {str(e)}"})
                            if entry['content_id'] in successful_jobs:
                                successful_jobs.remove(entry['content_id'])
                        continue
                # Send to SQS
                if sqs_entries:
                    try:
                        response = await sqs.send_message_batch(
                            QueueUrl=sqs_queue_url,
                            Entries=sqs_entries
                        )
                        if 'Failed' in response and response['Failed']:
                            for failed in response['Failed']:
                                failed_id = failed['Id']
                                job = next((json.loads(entry['MessageBody'])['job'] for entry in sqs_entries if entry['Id'] == failed_id), "unknown")
                                failed_jobs.append({"job": job, "error": failed.get('Message')})
                                if failed_id in successful_jobs:
                                    successful_jobs.remove(failed_id)
                    except Exception as e:
                        for entry in sqs_entries:
                            job = json.loads(entry['MessageBody'])['job']
                            failed_jobs.append({"job": job, "error": f"SQS send failed: {str(e)}"})
                            if entry['Id'] in successful_jobs:
                                successful_jobs.remove(entry['Id'])
            return {
                "successful_number": len(successful_jobs),
                "successful_jobs": successful_jobs,
                "failed_number": len(failed_jobs),
                "failed_jobs": failed_jobs
            }

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "send_deep_research_job",
            "description": "Submit a batch of deep research jobs to SQS. Requires a list of selections and a batch_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "selections": {
                        "type": "array",
                        "description": "List of selection dictionaries containing content_id, follow_up_queries, and optional sqs_message and preliminary_research_result.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content_id": {"type": "string", "description": "The content_id of the job to follow up on."},
                                "follow_up_queries": {"type": "array", "description": "List of follow-up queries to send to the deep research queue.", "items": {"type": "string"}},
                                "sqs_message": {"type": "object", "description": "Original SQS message for the job (optional)."},
                                "preliminary_research_result": {"type": "object", "description": "Preliminary research result for the job (optional)."}
                            },
                            "required": ["content_id", "follow_up_queries", "sqs_message", "preliminary_research_result"]
                        }
                    },
                    "batch_id": {"type": "string", "description": "Batch identifier for grouping messages."}
                },
                "required": ["selections", "batch_id"]
            }
        }
    })
    @xml_schema(
        tag_name="send-deep-research-job",
        mappings=[
            {"param_name": "selections", "node_type": "content", "path": "."},
            {"param_name": "batch_id", "node_type": "attribute", "path": "."}
        ],
        example='''
        <function_calls>
        <invoke name="send_deep_research_job">
        <parameter name="selections">[{"content_id": "your-content-id-1", "follow_up_queries": ["query1", "query2"], "sqs_message": {"example": "value"}, "preliminary_research_result": {"example": "value"}}, {"content_id": "your-content-id-2", "follow_up_queries": ["query3"]}]</parameter>
        <parameter name="batch_id">batch-123</parameter>
        </invoke>
        </function_calls>
        '''
    )
    async def send_deep_research_job(self, selections, batch_id):
        """
        Submits deep research jobs for a list of selections using batch operations to SQS.
        Args:
            selections (list): List of selection dictionaries containing content_id and follow_up_queries
            batch_id (str): Batch identifier for grouping messages
        Returns:
            dict: Results containing successful and failed jobs
        """
        # SQS setup
        sqs_queue_url = getattr(config, 'SQS_QUEUE_URL', None)
        if not sqs_queue_url:
            return self.fail_response("SQS_QUEUE_URL not configured in config.")
        is_fifo_queue = sqs_queue_url.endswith('.fifo')
        chunk_size = 10
        successful_jobs = []
        failed_jobs = []
        async with self.session.client(
            'sqs',
            aws_access_key_id=self.aws_access_key_id,
            aws_secret_access_key=self.aws_secret_access_key,
            region_name=self.aws_region_name
        ) as sqs:
            for i in range(0, len(selections), chunk_size):
                selection_chunk = selections[i:i+chunk_size]
                sqs_entries = []
                for selection in selection_chunk:
                    # Validate all required fields are present and not None
                    required_fields = ['content_id', 'follow_up_queries', 'sqs_message', 'preliminary_research_result']
                    missing_or_none = [field for field in required_fields if field not in selection or selection[field] is None]
                    if missing_or_none:
                        failed_jobs.append({
                            "content_id": selection.get('content_id', 'unknown'),
                            "error": f"Missing or None fields: {', '.join(missing_or_none)}"
                        })
                        continue
                    try:
                        job_id = selection['content_id']
                        message_id = str(uuid.uuid4())
                        entry = {
                            'Id': job_id,
                            'MessageBody': json.dumps({
                                'jobId': job_id,
                                'batchId': batch_id,
                                'followUpQueries': selection['follow_up_queries'],
                                'originalMessage': selection['sqs_message'],
                                'preliminaryResearchResult': selection['preliminary_research_result']
                            })
                        }
                        if is_fifo_queue:
                            entry['MessageGroupId'] = batch_id
                            entry['MessageDeduplicationId'] = message_id
                        sqs_entries.append(entry)
                        successful_jobs.append(job_id)
                    except Exception as e:
                        logger.error(f"Error preparing selection {selection.get('content_id', 'unknown')}: {str(e)}")
                        failed_jobs.append({"content_id": selection.get('content_id', 'unknown'), "error": str(e)})
                logger.info(f"Deep research job batch {batch_id} submitted with {len(sqs_entries)} jobs: {sqs_entries}")
                if sqs_entries:
                    try:
                        response = await sqs.send_message_batch(
                            QueueUrl=sqs_queue_url,
                            Entries=sqs_entries
                        )
                        if 'Failed' in response and response['Failed']:
                            for failed in response['Failed']:
                                failed_id = failed['Id']
                                message_body = next((json.loads(entry['MessageBody']) for entry in sqs_entries if entry['Id'] == failed_id), {})
                                content_id = message_body.get('jobId', failed_id)
                                logger.error(f"Failed to send message for content_id {content_id}: {failed.get('Message')}")
                                failed_jobs.append({"content_id": content_id, "error": failed.get('Message')})
                                if failed_id in successful_jobs:
                                    successful_jobs.remove(failed_id)
                    except Exception as e:
                        logger.error(f"Error sending batch to SQS: {str(e)}")
                        for entry in sqs_entries:
                            message_body = json.loads(entry['MessageBody'])
                            content_id = message_body['jobId']
                            failed_jobs.append({"content_id": content_id, "error": f"SQS send failed: {str(e)}"})
                            if entry['Id'] in successful_jobs:
                                successful_jobs.remove(entry['Id'])
        return {
            "successful_jobs": successful_jobs,
            "failed_jobs": failed_jobs
        }

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "build_batch",
            "description": "Build a batch via Lambda. Requires batch_id, user_id, campaign_id, config_id, and select_all.",
            "parameters": {
                "type": "object",
                "properties": {
                    "batch_id": {"type": "string", "description": "Batch ID."},
                    "user_id": {"type": "string", "description": "User ID."},
                    "campaign_id": {"type": "string", "description": "Campaign ID."},
                    "config_id": {"type": "string", "description": "Config ID."},
                    "select_all": {"type": "boolean", "description": "Whether to select all (default: true).", "default": True}
                },
                "required": ["batch_id", "user_id", "campaign_id", "config_id"]
            }
        }
    })
    @xml_schema(
        tag_name="build-batch",
        mappings=[
            {"param_name": "batch_id", "node_type": "attribute", "path": "."},
            {"param_name": "user_id", "node_type": "attribute", "path": "."},
            {"param_name": "campaign_id", "node_type": "attribute", "path": "."},
            {"param_name": "config_id", "node_type": "attribute", "path": "."},
            {"param_name": "select_all", "node_type": "attribute", "path": "."}
        ],
        example='''
        <function_calls>
        <invoke name="build_batch">
        <parameter name="batch_id">batch-123</parameter>
        <parameter name="user_id">user-456</parameter>
        <parameter name="campaign_id">campaign-789</parameter>
        <parameter name="config_id">config-abc</parameter>
        <parameter name="select_all">true</parameter>
        </invoke>
        </function_calls>
        '''
    )
    async def build_batch(self, batch_id: str, user_id: str, campaign_id: str, config_id: str, select_all: bool = True) -> ToolResult:
        """
        Build a batch via Lambda.
        """
        payload = {
            "path": "/manage_batch",
            "httpMethod": "POST",
            "body": {
                "action": "build",
                "batch_id": batch_id,
                "user_id": user_id,
                "campaign_id": campaign_id,
                "config_id": config_id,
                "select_all": select_all
            }
        }
        try:
            async with self.session.client(
                'lambda',
                aws_access_key_id=self.aws_access_key_id,
                aws_secret_access_key=self.aws_secret_access_key,
                region_name=self.aws_region_name
            ) as lambda_client:
                response = await lambda_client.invoke(
                    FunctionName=self.lambda_function_name,
                    InvocationType='RequestResponse',
                    Payload=bytes(json.dumps(payload), encoding='utf-8')
                )
                result_payload = await response['Payload'].read()
                result = json.loads(result_payload)
                return self.success_response(result)
        except Exception as e:
            return self.fail_response(f"Error calling build_batch via Lambda SDK: {str(e)}")

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "remove_batch",
            "description": "Remove a batch via Lambda. Requires batch_id and user_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "batch_id": {"type": "string", "description": "Batch ID."},
                    "user_id": {"type": "string", "description": "User ID."}
                },
                "required": ["batch_id", "user_id"]
            }
        }
    })
    @xml_schema(
        tag_name="remove-batch",
        mappings=[
            {"param_name": "batch_id", "node_type": "attribute", "path": "."},
            {"param_name": "user_id", "node_type": "attribute", "path": "."}
        ],
        example='''
        <function_calls>
        <invoke name="remove_batch">
        <parameter name="batch_id">batch-123</parameter>
        <parameter name="user_id">user-456</parameter>
        </invoke>
        </function_calls>
        '''
    )
    async def remove_batch(self, batch_id: str, user_id: str) -> ToolResult:
        """
        Remove a batch via Lambda.
        """
        payload = {
            "path": "/manage_batch",
            "httpMethod": "POST",
            "body": {
                "action": "remove",
                "batch_id": batch_id,
                "user_id": user_id
            }
        }
        try:
            async with self.session.client(
                'lambda',
                aws_access_key_id=self.aws_access_key_id,
                aws_secret_access_key=self.aws_secret_access_key,
                region_name=self.aws_region_name
            ) as lambda_client:
                response = await lambda_client.invoke(
                    FunctionName=self.lambda_function_name,
                    InvocationType='RequestResponse',
                    Payload=bytes(json.dumps(payload), encoding='utf-8')
                )
                result_payload = await response['Payload'].read()
                result = json.loads(result_payload)
                return self.success_response(result)
        except Exception as e:
            return self.fail_response(f"Error calling remove_batch via Lambda SDK: {str(e)}")

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "get_job_status",
            "description": "Get the status of one or more jobs from the content_jobs table.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content_ids": {
                        "type": "array",
                        "description": "A list of content job IDs to check.",
                        "items": {
                            "type": "string"
                        }
                    }
                },
                "required": ["content_ids"]
            }
        }
    })
    @xml_schema(
        tag_name="get-job-status",
        mappings=[
            {"param_name": "content_ids", "node_type": "attribute", "path": "."}
        ],
        example='''
        <function_calls>
        <invoke name="get_job_status">
        <parameter name="content_ids">["your-content-id-1", "your-content-id-2"]</parameter>
        </invoke>
        </function_calls>
        '''
    )
    async def get_job_status(self, content_ids: Union[str, List[str]]) -> ToolResult:
        """
        Retrieves the status of one or more jobs from the content_jobs table.
        Args:
            content_ids (Union[str, List[str]]): A single content ID or a list of content IDs to check.
        Returns:
            ToolResult: The result of the operation.
        """
        if isinstance(content_ids, str):
            content_ids = [content_ids]

        try:
            supabase = await create_async_client(config.JOB_SUPABASE_URL, config.JOB_SUPABASE_SERVICE_ROLE_KEY)
            response = await supabase.table('content_jobs').select('*').in_('content_id', content_ids).execute()
            if response.data:
                return self.success_response(response.data)
            else:
                return self.fail_response(f"No jobs found with the provided content_ids.")
        except Exception as e:
            return self.fail_response(f"Failed to get job status: {str(e)}") 