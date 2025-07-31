from agentpress.tool import ToolResult, openapi_schema, usage_example
from sandbox.tool_base import SandboxToolsBase
from utils.files_utils import should_exclude_file, clean_path
from agentpress.thread_manager import ThreadManager
from utils.logger import logger
from utils.config import config
import os
import json
import litellm
import openai
import asyncio
from typing import Optional

class SandboxFilesTool(SandboxToolsBase):
    """Tool for executing file system operations in a Daytona sandbox. All operations are performed relative to the /workspace directory."""

    def __init__(self, project_id: str, thread_manager: ThreadManager):
        super().__init__(project_id, thread_manager)
        self.SNIPPET_LINES = 4  # Number of context lines to show around edits
        self.workspace_path = "/workspace"  # Ensure we're always operating in /workspace

    def clean_path(self, path: str) -> str:
        """Clean and normalize a path to be relative to /workspace"""
        return clean_path(path, self.workspace_path)

    def _should_exclude_file(self, rel_path: str) -> bool:
        """Check if a file should be excluded based on path, name, or extension"""
        return should_exclude_file(rel_path)

    async def _file_exists(self, path: str) -> bool:
        """Check if a file exists in the sandbox"""
        try:
            await self.sandbox.fs.get_file_info(path)
            return True
        except Exception:
            return False

    async def get_workspace_state(self) -> dict:
        """Get the current workspace state by reading all files"""
        files_state = {}
        try:
            # Ensure sandbox is initialized
            await self._ensure_sandbox()
            
            files = await self.sandbox.fs.list_files(self.workspace_path)
            for file_info in files:
                rel_path = file_info.name
                
                # Skip excluded files and directories
                if self._should_exclude_file(rel_path) or file_info.is_dir:
                    continue

                try:
                    full_path = f"{self.workspace_path}/{rel_path}"
                    content = (await self.sandbox.fs.download_file(full_path)).decode()
                    files_state[rel_path] = {
                        "content": content,
                        "is_dir": file_info.is_dir,
                        "size": file_info.size,
                        "modified": file_info.mod_time
                    }
                except Exception as e:
                    print(f"Error reading file {rel_path}: {e}")
                except UnicodeDecodeError:
                    print(f"Skipping binary file: {rel_path}")

            return files_state
        
        except Exception as e:
            print(f"Error getting workspace state: {str(e)}")
            return {}


    # def _get_preview_url(self, file_path: str) -> Optional[str]:
    #     """Get the preview URL for a file if it's an HTML file."""
    #     if file_path.lower().endswith('.html') and self._sandbox_url:
    #         return f"{self._sandbox_url}/{(file_path.replace('/workspace/', ''))}"
    #     return None

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "create_file",
            "description": "Create a new file with the provided contents at a given path in the workspace. The path must be relative to /workspace (e.g., 'src/main.py' for /workspace/src/main.py)",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to be created, relative to /workspace (e.g., 'src/main.py')"
                    },
                    "file_contents": {
                        "type": "string",
                        "description": "The content to write to the file"
                    },
                    "permissions": {
                        "type": "string",
                        "description": "File permissions in octal format (e.g., '644')",
                        "default": "644"
                    }
                },
                "required": ["file_path", "file_contents"]
            }
        }
    })
    @usage_example('''
        <function_calls>
        <invoke name="create_file">
        <parameter name="file_path">src/main.py</parameter>
        <parameter name="file_contents">
        # This is the file content
        def main():
            print("Hello, World!")
        
        if __name__ == "__main__":
            main()
        </parameter>
        </invoke>
        </function_calls>
        ''')
    async def create_file(self, file_path: str, file_contents: str, permissions: str = "644") -> ToolResult:
        try:
            # Ensure sandbox is initialized
            await self._ensure_sandbox()
            
            file_path = self.clean_path(file_path)
            full_path = f"{self.workspace_path}/{file_path}"
            if await self._file_exists(full_path):
                return self.fail_response(f"File '{file_path}' already exists. Use update_file to modify existing files.")
            
            # Create parent directories if needed
            parent_dir = '/'.join(full_path.split('/')[:-1])
            if parent_dir:
                await self.sandbox.fs.create_folder(parent_dir, "755")
            
            # convert to json string if file_contents is a dict
            if isinstance(file_contents, dict):
                file_contents = json.dumps(file_contents, indent=4)
            
            # Write the file content
            await self.sandbox.fs.upload_file(file_contents.encode(), full_path)
            await self.sandbox.fs.set_file_permissions(full_path, permissions)
            
            message = f"File '{file_path}' created successfully."
            
            # Check if index.html was created and add 8080 server info (only in root workspace)
            if file_path.lower() == 'index.html':
                try:
                    website_link = await self.sandbox.get_preview_link(8080)
                    website_url = website_link.url if hasattr(website_link, 'url') else str(website_link).split("url='")[1].split("'")[0]
                    message += f"\n\n[Auto-detected index.html - HTTP server available at: {website_url}]"
                    message += "\n[Note: Use the provided HTTP server URL above instead of starting a new server]"
                except Exception as e:
                    logger.warning(f"Failed to get website URL for index.html: {str(e)}")
            
            return self.success_response(message)
        except Exception as e:
            return self.fail_response(f"Error creating file: {str(e)}")

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "str_replace",
            "description": "Replace specific text in a file. The file path must be relative to /workspace (e.g., 'src/main.py' for /workspace/src/main.py). IMPORTANT: Prefer using edit_file for faster, shorter edits to avoid repetition. Only use this tool when you need to replace a unique string that appears exactly once in the file and edit_file is not suitable.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the target file, relative to /workspace (e.g., 'src/main.py')"
                    },
                    "old_str": {
                        "type": "string",
                        "description": "Text to be replaced (must appear exactly once)"
                    },
                    "new_str": {
                        "type": "string",
                        "description": "Replacement text"
                    }
                },
                "required": ["file_path", "old_str", "new_str"]
            }
        }
    })
    @usage_example('''
        <function_calls>
        <invoke name="str_replace">
        <parameter name="file_path">src/main.py</parameter>
        <parameter name="old_str">text to replace (must appear exactly once in the file)</parameter>
        <parameter name="new_str">replacement text that will be inserted instead</parameter>
        </invoke>
        </function_calls>
        ''')
    async def str_replace(self, file_path: str, old_str: str, new_str: str) -> ToolResult:
        try:
            # Ensure sandbox is initialized
            await self._ensure_sandbox()
            
            file_path = self.clean_path(file_path)
            full_path = f"{self.workspace_path}/{file_path}"
            if not await self._file_exists(full_path):
                return self.fail_response(f"File '{file_path}' does not exist")
            
            content = (await self.sandbox.fs.download_file(full_path)).decode()
            old_str = old_str.expandtabs()
            new_str = new_str.expandtabs()
            
            occurrences = content.count(old_str)
            if occurrences == 0:
                return self.fail_response(f"String '{old_str}' not found in file")
            if occurrences > 1:
                lines = [i+1 for i, line in enumerate(content.split('\n')) if old_str in line]
                return self.fail_response(f"Multiple occurrences found in lines {lines}. Please ensure string is unique")
            
            # Perform replacement
            new_content = content.replace(old_str, new_str)
            await self.sandbox.fs.upload_file(new_content.encode(), full_path)
            
            # Show snippet around the edit
            replacement_line = content.split(old_str)[0].count('\n')
            start_line = max(0, replacement_line - self.SNIPPET_LINES)
            end_line = replacement_line + self.SNIPPET_LINES + new_str.count('\n')
            snippet = '\n'.join(new_content.split('\n')[start_line:end_line + 1])
            
            # Get preview URL if it's an HTML file
            # preview_url = self._get_preview_url(file_path)
            message = f"Replacement successful."
            # if preview_url:
            #     message += f"\n\nYou can preview this HTML file at: {preview_url}"
            
            return self.success_response(message)
            
        except Exception as e:
            return self.fail_response(f"Error replacing string: {str(e)}")

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "full_file_rewrite",
            "description": "Completely rewrite an existing file with new content. The file path must be relative to /workspace (e.g., 'src/main.py' for /workspace/src/main.py). IMPORTANT: Always prefer using edit_file for making changes to code. Only use this tool when edit_file fails or when you need to replace the entire file content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to be rewritten, relative to /workspace (e.g., 'src/main.py')"
                    },
                    "file_contents": {
                        "type": "string",
                        "description": "The new content to write to the file, replacing all existing content"
                    },
                    "permissions": {
                        "type": "string",
                        "description": "File permissions in octal format (e.g., '644')",
                        "default": "644"
                    }
                },
                "required": ["file_path", "file_contents"]
            }
        }
    })
    @usage_example('''
        <function_calls>
        <invoke name="full_file_rewrite">
        <parameter name="file_path">src/main.py</parameter>
        <parameter name="file_contents">
        This completely replaces the entire file content.
        Use when making major changes to a file or when the changes
        are too extensive for str-replace.
        All previous content will be lost and replaced with this text.
        </parameter>
        </invoke>
        </function_calls>
        ''')
    async def full_file_rewrite(self, file_path: str, file_contents: str, permissions: str = "644") -> ToolResult:
        try:
            # Ensure sandbox is initialized
            await self._ensure_sandbox()
            
            file_path = self.clean_path(file_path)
            full_path = f"{self.workspace_path}/{file_path}"
            if not await self._file_exists(full_path):
                return self.fail_response(f"File '{file_path}' does not exist. Use create_file to create a new file.")
            
            await self.sandbox.fs.upload_file(file_contents.encode(), full_path)
            await self.sandbox.fs.set_file_permissions(full_path, permissions)
            
            message = f"File '{file_path}' completely rewritten successfully."
            
            # Check if index.html was rewritten and add 8080 server info (only in root workspace)
            if file_path.lower() == 'index.html':
                try:
                    website_link = await self.sandbox.get_preview_link(8080)
                    website_url = website_link.url if hasattr(website_link, 'url') else str(website_link).split("url='")[1].split("'")[0]
                    message += f"\n\n[Auto-detected index.html - HTTP server available at: {website_url}]"
                    message += "\n[Note: Use the provided HTTP server URL above instead of starting a new server]"
                except Exception as e:
                    logger.warning(f"Failed to get website URL for index.html: {str(e)}")
            
            return self.success_response(message)
        except Exception as e:
            return self.fail_response(f"Error rewriting file: {str(e)}")

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Delete a file at the given path. The path must be relative to /workspace (e.g., 'src/main.py' for /workspace/src/main.py)",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to be deleted, relative to /workspace (e.g., 'src/main.py')"
                    }
                },
                "required": ["file_path"]
            }
        }
    })
    @usage_example('''
        <function_calls>
        <invoke name="delete_file">
        <parameter name="file_path">src/main.py</parameter>
        </invoke>
        </function_calls>
        ''')
    async def delete_file(self, file_path: str) -> ToolResult:
        try:
            # Ensure sandbox is initialized
            await self._ensure_sandbox()
            
            file_path = self.clean_path(file_path)
            full_path = f"{self.workspace_path}/{file_path}"
            if not await self._file_exists(full_path):
                return self.fail_response(f"File '{file_path}' does not exist")
            
            await self.sandbox.fs.delete_file(full_path)
            return self.success_response(f"File '{file_path}' deleted successfully.")
        except Exception as e:
            return self.fail_response(f"Error deleting file: {str(e)}")

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "copy_supabase_field_to_file",
            "description": "Copy the value of a single field from a single row (by primary key) of a Supabase table in the job Supabase database into a file in the sandbox.",
            "parameters": {
                "type": "object",
                "properties": {
                    "table_name": {
                        "type": "string",
                        "description": "Name of the Supabase table to query."
                    },
                    "field_name": {
                        "type": "string",
                        "description": "Name of the field/column to extract."
                    },
                    "primary_key": {
                        "type": "string",
                        "description": "Name of the primary key column."
                    },
                    "primary_key_value": {
                        "type": "string",
                        "description": "Value of the primary key for the row to fetch."
                    },
                    "output_file_path": {
                        "type": "string",
                        "description": "Path to the output file in the sandbox, relative to /workspace."
                    }
                },
                "required": ["table_name", "field_name", "primary_key", "primary_key_value", "output_file_path"]
            }
        }
    })
    @usage_example('''
        <function_calls>
        <invoke name="copy_supabase_field_to_file">
        <parameter name="table_name">users</parameter>
        <parameter name="field_name">email</parameter>
        <parameter name="primary_key">id</parameter>
        <parameter name="primary_key_value">1</parameter>
        <parameter name="output_file_path">output/email.txt</parameter>
        </invoke>
        </function_calls>
        ''')
    async def copy_supabase_field_to_file(self, table_name: str, field_name: str, primary_key: str, primary_key_value: str, output_file_path: str) -> ToolResult:
        """Copy the value of a single field from a single row (by primary key) of a Supabase table in the job Supabase database into a file in the sandbox."""
        try:
            # Ensure sandbox is initialized
            await self._ensure_sandbox()

            # Get job Supabase client from config
            from utils.config import config
            from supabase import create_async_client

            supabase_url = config.JOB_SUPABASE_URL
            supabase_key = config.JOB_SUPABASE_SERVICE_ROLE_KEY
            if not supabase_url or not supabase_key:
                return self.fail_response("Job Supabase URL or key not set in config.")
            supabase = await create_async_client(supabase_url, supabase_key)

            # Build query for single row by primary key
            query = supabase.table(table_name).select(field_name).eq(primary_key, primary_key_value).single()
            response = await query.execute()
            if not response.data or field_name not in response.data:
                return self.fail_response(f"No data found in table '{table_name}' for {primary_key}={primary_key_value} or field '{field_name}' missing.")

            # Extract the field value
            field_value = str(response.data[field_name])

            # Write to file in sandbox
            full_path = f"/workspace/{output_file_path}"
            parent_dir = '/'.join(full_path.split('/')[:-1])
            if parent_dir:
                await self.sandbox.fs.create_folder(parent_dir, "755")
            await self.sandbox.fs.upload_file(field_value.encode(), full_path)
            await self.sandbox.fs.set_file_permissions(full_path, "644")

            return self.success_response(f"Copied field '{field_name}' from table '{table_name}' (row {primary_key}={primary_key_value}) to '{output_file_path}'.")
        except Exception as e:
            return self.fail_response(f"Error copying Supabase field: {str(e)}")

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Edit a file using AI-powered intelligent editing with fallback to string replacement. The file path must be relative to /workspace (e.g., 'src/main.py' for /workspace/src/main.py).",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_file": {
                        "type": "string",
                        "description": "Path to the file to be edited, relative to /workspace (e.g., 'src/main.py')"
                    },
                    "instructions": {
                        "type": "string",
                        "description": "Instructions for the edit operation"
                    },
                    "code_edit": {
                        "type": "string",
                        "description": "The new content to write to the file"
                    }
                },
                "required": ["target_file", "instructions", "code_edit"]
            }
        }
    })
    @usage_example('''
        <!-- Example: Mark multiple scattered tasks as complete in a todo list -->
        <function_calls>
        <invoke name="edit_file">
        <parameter name="target_file">todo.md</parameter>
        <parameter name="instructions">I am marking the research and setup tasks as complete in my todo list.</parameter>
        <parameter name="code_edit">
        - [x] Research topic A
        - [ ] Research topic B
        - [x] Research topic C
        - [x] Setup database
        - [x] Configure server
        </parameter>
        </invoke>
        </function_calls>
        ''')
    async def edit_file(self, target_file: str, instructions: str, code_edit: str) -> ToolResult:
        """Edit a file using AI-powered intelligent editing with fallback to string replacement"""
        try:
            await self._ensure_sandbox()
            
            # Clean the file path
            target_file = self.clean_path(target_file)
            full_path = f"/workspace/{target_file}"
            
            # Check if file exists
            if not await self._file_exists(full_path):
                return self.fail_response(f"File '{target_file}' does not exist")
            
            # For now, use full_file_rewrite as a fallback
            # In the future, this could use AI-powered editing
            await self.sandbox.fs.upload_file(code_edit.encode(), full_path)
            await self.sandbox.fs.set_file_permissions(full_path, "644")
            
            return self.success_response(f"Successfully edited file '{target_file}' with the provided content")
        except Exception as e:
            return self.fail_response(f"Error editing file: {str(e)}")

    # @openapi_schema({
    #     "type": "function",
    #     "function": {
    #         "name": "read_file",
    #         "description": "Read the contents of a file at the given path. The path must be relative to /workspace (e.g., 'src/main.py' for /workspace/src/main.py)",
    #         "parameters": {
    #             "type": "object",
    #             "properties": {
    #                 "file_path": {
    #                     "type": "string",
    #                     "description": "Path to the file to be read, relative to /workspace (e.g., 'src/main.py')"
    #                 }
    #             },
    #             "required": ["file_path"]
    #         }
    #     }
    # })
    # @usage_example('''
    #     <function_calls>
    #     <invoke name="read_file">
    #     <parameter name="file_path">src/main.py</parameter>
    #     </invoke>
    #     </function_calls>
    #     ''')
    # async def read_file(self, file_path: str) -> ToolResult:
    #     """Read the contents of a file"""
    #     try:
    #         await self._ensure_sandbox()
    #         
    #         full_path = f"/workspace/{self.clean_path(file_path)}"
    #         
    #         if not await self._file_exists(full_path):
    #             return self.fail_response(f"File '{file_path}' does not exist")
    #         
    #         file_content = await self.sandbox.fs.read_file(full_path)
    #         
    #         # Check if file is binary
    #         try:
    #             file_content.decode('utf-8')
    #         except UnicodeDecodeError:
    #             return self.fail_response(f"File '{file_path}' appears to be binary and cannot be read as text")
    #     except Exception as e:
    #         return self.fail_response(f"Error reading file: {str(e)}")