from agentpress.tool import Tool, ToolResult, openapi_schema, usage_example
from utils.logger import logger
from utils.config import config
from supabase import create_async_client

class SupabaseTools(Tool):
    """Tools for interacting with Supabase storage and database."""

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
            "description": "Fetch a template from Supabase storage using a full URL. Returns the template content as a string.",
            "parameters": {
                "type": "object",
                "properties": {
                    "template_url": {
                        "type": "string",
                        "description": "Full URL to the template in Supabase storage (e.g., 'https://example.supabase.co/storage/v1/object/public/templates/newsletters/market-intelligence/template.html')"
                    }
                },
                "required": ["template_url"]
            }
        }
    })
    @usage_example('''
        <function_calls>
        <invoke name="fetch_template">
        <parameter name="template_url">https://example.supabase.co/storage/v1/object/public/templates/newsletters/market-intelligence/template.html</parameter>
        </invoke>
        </function_calls>
        ''')
    async def fetch_template(self, template_url: str) -> ToolResult:
        """
        Fetch a template from Supabase storage using a full URL.
        Returns the content for the agent to save using create_file tool.
        
        Args:
            template_url: Full URL to the template (e.g., 'https://example.supabase.co/storage/v1/object/public/templates/newsletters/market-intelligence/template.html')
            
        Returns:
            ToolResult containing the template content and suggested filename
        """
        try:
            # Parse the URL to extract bucket name and path
            from urllib.parse import urlparse
            
            parsed_url = urlparse(template_url)
            
            # Extract path components
            # Expected format: /storage/v1/object/public/{bucket_name}/{path}
            # or /storage/v1/object/private/{bucket_name}/{path}
            path_parts = parsed_url.path.strip('/').split('/')
            
            if len(path_parts) < 5 or path_parts[0] != 'storage':
                return self.fail_response(f"Invalid Supabase storage URL format: {template_url}")
            
            # Extract bucket name and template path
            access_type = path_parts[3]  # 'public' or 'private'
            bucket_name = path_parts[4]
            template_path = '/'.join(path_parts[5:]) if len(path_parts) > 5 else ''
            
            # Get Supabase client
            supabase = await self._get_supabase_client()
            
            # Fetch the template from storage
            logger.info(f"Fetching template from URL: {template_url}")
            logger.info(f"Extracted - Bucket: '{bucket_name}', Path: '{template_path}', Access: '{access_type}'")
            
            # Try to download the file based on access type
            response = None
            
            if access_type == 'private':
                try:
                    # Try private storage
                    response = await supabase.storage.from_(bucket_name).download(template_path)
                except Exception as private_error:
                    logger.debug(f"Private storage failed: {private_error}")
            
            # If not private or private failed, try direct URL access
            if not response:
                try:
                    import httpx
                    logger.info(f"Fetching directly from URL: {template_url}")
                    
                    async with httpx.AsyncClient() as client:
                        direct_response = await client.get(template_url)
                        if direct_response.status_code == 200:
                            response = direct_response.content
                        else:
                            logger.debug(f"Direct URL fetch failed with status: {direct_response.status_code}")
                except Exception as url_error:
                    logger.debug(f"Direct URL fetch failed: {url_error}")
            
            if not response:
                return self.fail_response(f"Template not found at URL: {template_url}")
            
            # Decode the content (assuming it's text-based template)
            try:
                template_content = response.decode('utf-8')
            except UnicodeDecodeError:
                # If it's not UTF-8, try with different encodings or return as base64
                template_content = response.decode('latin-1', errors='ignore')
            
            logger.info(f"Successfully fetched template from URL ({len(template_content)} characters)")
            
            # Extract filename from the template path or use a default
            if template_path:
                # Get just the filename from the path
                filename = template_path.split('/')[-1] if '/' in template_path else template_path
            else:
                filename = "template.html"
            
            # Ensure the filename has .html extension
            if not filename.endswith('.html'):
                filename = filename + '.html'
            
            return self.success_response({
                "template_content": template_content,
                "template_url": template_url,
                "template_filename": filename,
                "size": len(template_content),
                "message": f"Template fetched successfully. Use 'create_file' tool with filename '{filename}' to save it in the sandbox."
            })
            
        except Exception as e:
            error_msg = f"Error fetching template from URL '{template_url}': {str(e)}"
            logger.error(error_msg)
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

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "fetch_customer_information",
            "description": "Fetch customer information from the platform_customer table by customer ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "The ID of the customer to fetch information for"
                    }
                },
                "required": ["customer_id"]
            }
        }
    })
    @usage_example('''
        <function_calls>
        <invoke name="fetch_customer_information">
        <parameter name="customer_id">cust_123456</parameter>
        </invoke>
        </function_calls>
        ''')
    async def fetch_customer_information(self, customer_id: str) -> ToolResult:
        """
        Fetch customer information from the platform_customer table.
        
        Args:
            customer_id: The ID of the customer to fetch
            
        Returns:
            ToolResult containing the customer data or error message
        """
        try:
            # Get Supabase client
            supabase = await self._get_supabase_client()
            
            logger.info(f"Fetching customer information for ID: {customer_id}")
            
            # Query the platform_customer table
            response = await supabase.table('platform_customer').select("*").eq('id', customer_id).execute()
            
            if not response.data:
                return self.fail_response(f"Customer not found with ID: {customer_id}")
            
            # Get the first (and should be only) customer record
            customer_data = response.data[0] if response.data else None
            
            if not customer_data:
                return self.fail_response(f"No customer data found for ID: {customer_id}")
            
            logger.info(f"Successfully fetched customer information for ID: {customer_id}")
            
            return self.success_response({
                "customer": customer_data,
                "customer_id": customer_id
            })
            
        except Exception as e:
            error_msg = f"Error fetching customer information for ID '{customer_id}': {str(e)}"
            logger.error(error_msg)
            return self.fail_response(error_msg)

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "list_customers",
            "description": "List customers from the platform_customer table with optional filters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of customers to return",
                        "default": 100
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Number of customers to skip (for pagination)",
                        "default": 0
                    },
                    "filters": {
                        "type": "object",
                        "description": "Optional filters to apply (e.g., {'status': 'active', 'plan': 'premium'})"
                    }
                },
                "required": []
            }
        }
    })
    @usage_example('''
        <function_calls>
        <invoke name="list_customers">
        <parameter name="limit">50</parameter>
        <parameter name="filters">{"status": "active"}</parameter>
        </invoke>
        </function_calls>
        ''')
    async def list_customers(self, limit: int = 100, offset: int = 0, filters: dict = None) -> ToolResult:
        """
        List customers from the platform_customer table with optional filters.
        
        Args:
            limit: Maximum number of customers to return
            offset: Number of customers to skip (for pagination)
            filters: Optional dictionary of filters to apply
            
        Returns:
            ToolResult containing the list of customers or error message
        """
        try:
            # Get Supabase client
            supabase = await self._get_supabase_client()
            
            logger.info(f"Listing customers with limit: {limit}, offset: {offset}, filters: {filters}")
            
            # Build the query
            query = supabase.table('platform_customer').select("*")
            
            # Apply filters if provided
            if filters:
                for key, value in filters.items():
                    query = query.eq(key, value)
            
            # Apply pagination
            query = query.range(offset, offset + limit - 1)
            
            # Execute the query
            response = await query.execute()
            
            if not response.data:
                return self.success_response({
                    "customers": [],
                    "count": 0,
                    "limit": limit,
                    "offset": offset,
                    "filters": filters or {}
                })
            
            logger.info(f"Successfully fetched {len(response.data)} customers")
            
            return self.success_response({
                "customers": response.data,
                "count": len(response.data),
                "limit": limit,
                "offset": offset,
                "filters": filters or {}
            })
            
        except Exception as e:
            error_msg = f"Error listing customers: {str(e)}"
            logger.error(error_msg)
            return self.fail_response(error_msg)