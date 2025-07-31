from agentpress.tool import Tool, ToolResult, openapi_schema, usage_example
from utils.logger import logger
from utils.config import config
from supabase import create_async_client

class TemplateFetchTool(Tool):
    """Tool for fetching templates from Supabase storage."""

    def __init__(self):
        super().__init__()
        self.supabase_url = config.JOB_SUPABASE_URL
        self.supabase_key = config.JOB_SUPABASE_SERVICE_ROLE_KEY

    async def _get_supabase_client(self):
        """Get Supabase client instance."""
        if not self.supabase_url or not self.supabase_key:
            raise RuntimeError("Supabase URL and key must be configured")
        return await create_async_client(self.supabase_url, self.supabase_key)

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "fetch_template",
            "description": "Fetch a template from Supabase storage using the template path. Returns the template content as a string.",
            "parameters": {
                "type": "object",
                "properties": {
                    "template_path": {
                        "type": "string",
                        "description": "Path to the template in Supabase storage (e.g., '/templates/newsletters/market-intelligence/template.html')"
                    },
                    "bucket_name": {
                        "type": "string",
                        "description": "Name of the Supabase storage bucket",
                        "default": "templates"
                    }
                },
                "required": ["template_path"]
            }
        }
    })
    @usage_example('''
        <function_calls>
        <invoke name="fetch_template">
        <parameter name="template_path">/templates/newsletters/market-intelligence/template.html</parameter>
        <parameter name="bucket_name">templates</parameter>
        </invoke>
        </function_calls>
        ''')
    async def fetch_template(self, template_path: str, bucket_name: str = "templates") -> ToolResult:
        """
        Fetch a template from Supabase storage.
        
        Args:
            template_path: Path to the template in storage (e.g., '/templates/newsletters/market-intelligence/template.html')
            bucket_name: Name of the storage bucket (defaults to 'templates')
            
        Returns:
            ToolResult containing the template content or error message
        """
        try:
            # Get Supabase client
            supabase = await self._get_supabase_client()
            
            # Clean the template path (remove leading slash and bucket name if present)
            clean_path = template_path.lstrip('/')
            # If path starts with bucket name, remove it to avoid duplication
            if clean_path.startswith(f"{bucket_name}/"):
                clean_path = clean_path[len(f"{bucket_name}/"):]
            
            # Fetch the template from storage
            logger.info(f"Fetching template from bucket '{bucket_name}' at path '{clean_path}'")
            
            # Try to download the file from storage (private first, then public)
            response = None
            try:
                # First try private storage
                response = await supabase.storage.from_(bucket_name).download(clean_path)
            except Exception as private_error:
                logger.debug(f"Private storage failed: {private_error}, trying public storage")
                try:
                    # If private fails, try public storage URL
                    import httpx
                    public_url = f"{self.supabase_url}/storage/v1/object/public/{bucket_name}/{clean_path}"
                    logger.info(f"Trying public storage URL: {public_url}")
                    
                    async with httpx.AsyncClient() as client:
                        public_response = await client.get(public_url)
                        if public_response.status_code == 200:
                            response = public_response.content
                        else:
                            logger.debug(f"Public storage also failed: {public_response.status_code}")
                except Exception as public_error:
                    logger.debug(f"Public storage failed: {public_error}")
            
            if not response:
                return self.fail_response(f"Template not found at path '{template_path}' in bucket '{bucket_name}' (tried both private and public storage)")
            
            # Decode the content (assuming it's text-based template)
            try:
                template_content = response.decode('utf-8')
            except UnicodeDecodeError:
                # If it's not UTF-8, try with different encodings or return as base64
                template_content = response.decode('latin-1', errors='ignore')
            
            logger.info(f"Successfully fetched template from '{template_path}' ({len(template_content)} characters)")
            
            return self.success_response({
                "template_content": template_content,
                "template_path": template_path,
                "bucket_name": bucket_name,
                "size": len(template_content)
            })
            
        except Exception as e:
            error_msg = f"Error fetching template from '{template_path}': {str(e)}"
            logger.error(error_msg)
            # Try to suggest similar templates if exact path fails
            try:
                logger.info("Attempting to find similar templates...")
                similar_templates = await self._find_similar_templates(template_path, bucket_name)
                if similar_templates:
                    return self.fail_response(f"{error_msg}\n\nSimilar templates found: {similar_templates[:3]}")
            except:
                pass
            return self.fail_response(error_msg)

    async def _find_similar_templates(self, template_path: str, bucket_name: str = "templates"):
        """Helper method to find similar template paths when exact match fails."""
        try:
            supabase = await self._get_supabase_client()
            
            # Get all templates in the bucket
            all_templates = []
            
            # Try different folder paths that might contain templates
            possible_folders = ["", "templates", "newsletters", "reports", "marketing"]
            
            for folder in possible_folders:
                try:
                    response = await supabase.storage.from_(bucket_name).list(folder)
                    if response:
                        for item in response:
                            if item.get("name", "").endswith(('.html', '.htm')):
                                full_path = f"{folder}/{item['name']}" if folder else item['name']
                                all_templates.append(full_path)
                except:
                    continue
            
            # Find templates with similar names or paths
            template_name = template_path.split('/')[-1]  # Get just the filename
            similar = []
            
            for tmpl in all_templates:
                if template_name.lower() in tmpl.lower() or tmpl.split('/')[-1].lower() in template_name.lower():
                    similar.append(tmpl)
                elif 'template.html' in tmpl.lower():
                    similar.append(tmpl)
            
            return similar[:5]  # Return up to 5 similar templates
        except:
            return []

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "list_templates",
            "description": "List available templates in a Supabase storage bucket or folder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder_path": {
                        "type": "string",
                        "description": "Folder path to list templates from (e.g., 'templates/newsletters')",
                        "default": ""
                    },
                    "bucket_name": {
                        "type": "string",
                        "description": "Name of the Supabase storage bucket",
                        "default": "templates"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of templates to return",
                        "default": 100
                    }
                },
                "required": []
            }
        }
    })
    @usage_example('''
        <function_calls>
        <invoke name="list_templates">
        <parameter name="folder_path">templates/newsletters</parameter>
        <parameter name="bucket_name">templates</parameter>
        <parameter name="limit">50</parameter>
        </invoke>
        </function_calls>
        ''')
    async def list_templates(self, folder_path: str = "", bucket_name: str = "templates", limit: int = 100) -> ToolResult:
        """
        List available templates in a storage bucket or folder.
        
        Args:
            folder_path: Folder path to search in (optional)
            bucket_name: Name of the storage bucket
            limit: Maximum number of results to return
            
        Returns:
            ToolResult containing list of template paths
        """
        try:
            # Get Supabase client
            supabase = await self._get_supabase_client()
            
            logger.info(f"Listing templates in bucket '{bucket_name}', folder '{folder_path}'")
            
            # List files in the storage bucket
            response = await supabase.storage.from_(bucket_name).list(folder_path, {"limit": limit})
            
            if not response:
                return self.success_response({
                    "templates": [],
                    "count": 0,
                    "folder_path": folder_path,
                    "bucket_name": bucket_name
                })
            
            # Extract file information
            templates = []
            for item in response:
                template_info = {
                    "name": item.get("name", ""),
                    "path": f"{folder_path}/{item.get('name', '')}" if folder_path else item.get("name", ""),
                    "size": item.get("metadata", {}).get("size", 0),
                    "last_modified": item.get("updated_at", ""),
                    "content_type": item.get("metadata", {}).get("mimetype", "")
                }
                templates.append(template_info)
            
            logger.info(f"Found {len(templates)} templates")
            
            return self.success_response({
                "templates": templates,
                "count": len(templates),
                "folder_path": folder_path,
                "bucket_name": bucket_name
            })
            
        except Exception as e:
            error_msg = f"Error listing templates in '{folder_path}': {str(e)}"
            logger.error(error_msg)
            return self.fail_response(error_msg)