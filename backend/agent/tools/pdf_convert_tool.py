import traceback
import json
from agentpress.tool import ToolResult, openapi_schema, usage_example
from agentpress.thread_manager import ThreadManager
from sandbox.tool_base import SandboxToolsBase
from utils.logger import logger
import datetime


class SandboxPDFConvertTool(SandboxToolsBase):
    """Tool for converting HTML content to PDF using AWS Lambda Splat service via sandbox curl commands."""
    
    def __init__(self, project_id: str, thread_id: str, thread_manager: ThreadManager):
        super().__init__(project_id, thread_manager)
        self.thread_id = thread_id
        self.lambda_url = "https://fchxghd4q3qazrjhtaz2f5nnmm0jehjq.lambda-url.eu-west-2.on.aws/"

    async def _execute_pdf_conversion(self, payload: dict, output_filename: str = None) -> ToolResult:
        """Execute PDF conversion through the Lambda API using sandbox curl
        
        Args:
            payload (dict): The conversion parameters
            output_filename (str, optional): Custom output filename
            
        Returns:
            ToolResult: Result of the conversion
        """
        try:
            # Ensure sandbox is initialized
            await self._ensure_sandbox()
            
            # Generate filename
            if not output_filename:
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                output_filename = f"converted_pdf_{timestamp}.pdf"
            
            # Ensure .pdf extension
            if not output_filename.endswith('.pdf'):
                output_filename += '.pdf'
            
            pdf_file_path = f"/workspace/{output_filename}"
            
            logger.debug("\033[95mExecuting PDF conversion request:\033[0m")
            logger.debug(f"Payload: {json.dumps(payload, indent=2)}")
            
            # Create JSON payload file in sandbox
            payload_json = json.dumps(payload)
            
            # Escape single quotes in JSON for shell
            escaped_payload = payload_json.replace("'", "'\"'\"'")
            
            # Build curl command
            curl_cmd = f"""curl -X POST '{self.lambda_url}' \\
  -H 'Content-Type: application/json' \\
  -d '{escaped_payload}' \\
  --output '{pdf_file_path}' \\
  --max-time 300 \\
  --silent \\
  --show-error"""
            
            logger.debug(f"Executing curl command: {curl_cmd}")
            
            # Execute curl command in sandbox
            response = await self.sandbox.process.exec(curl_cmd, timeout=320)
            
            if response.exit_code == 0:
                # Check if PDF file was created and has content
                try:
                    file_info_cmd = f"ls -la '{pdf_file_path}'"
                    file_info = await self.sandbox.process.exec(file_info_cmd)
                    
                    if file_info.exit_code == 0:
                        logger.info(f"PDF conversion completed successfully: {pdf_file_path}")
                        
                        # Add message to thread
                        added_message = await self.thread_manager.add_message(
                            thread_id=self.thread_id,
                            type="pdf_conversion",
                            content={
                                "success": True,
                                "message": "PDF conversion completed successfully",
                                "file_path": pdf_file_path,
                                "curl_output": response.result if response.result else "Success"
                            },
                            is_llm_message=False
                        )
                        
                        success_response = {
                            "success": True,
                            "message": "PDF conversion completed successfully",
                            "file_path": pdf_file_path
                        }
                        
                        if added_message and 'message_id' in added_message:
                            success_response['message_id'] = added_message['message_id']
                        
                        return self.success_response(success_response)
                    else:
                        error_msg = f"PDF file was not created: {pdf_file_path}"
                        logger.error(error_msg)
                        return self.fail_response(error_msg)
                        
                except Exception as e:
                    logger.error(f"Error checking PDF file: {e}")
                    return self.fail_response(f"Error checking PDF file: {e}")
                    
            else:
                error_msg = f"PDF conversion failed: {response.result}"
                logger.error(error_msg)
                return self.fail_response(error_msg)

        except Exception as e:
            logger.error(f"Error executing PDF conversion: {e}")
            logger.debug(traceback.format_exc())
            return self.fail_response(f"Error executing PDF conversion: {e}")

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "convert_html_to_pdf",
            "description": "Convert HTML content to PDF using PrinceXML. Supports HTML content, external CSS, and various rendering options.",
            "parameters": {
                "type": "object",
                "properties": {
                    "document_content": {
                        "type": "string",
                        "description": "HTML content as string to convert to PDF"
                    },
                    "external_css": {
                        "type": "string",
                        "description": "Additional CSS styles to apply to the document"
                    },
                    "javascript": {
                        "type": "boolean",  
                        "description": "Enable JavaScript execution (PrinceXML only)",
                        "default": False
                    },
                    "renderer": {
                        "type": "string",
                        "description": "PDF renderer to use: 'princexml' (default) or 'playwright'",
                        "default": "princexml"
                    }
                },
                "required": ["document_content"]
            }
        }
    })
    @usage_example('''
        <function_calls>
        <invoke name="convert_html_to_pdf">
        <parameter name="document_content"><h1>Invoice #12345</h1><p>Total: $100.00</p></parameter>
        <parameter name="external_css">h1 { color: navy; } body { font-family: Arial; }</parameter>
        </invoke>
        </function_calls>
        ''')
    async def convert_html_to_pdf(
        self, 
        document_content: str,
        external_css: str = None,
        javascript: bool = False,
        renderer: str = "princexml"
    ) -> ToolResult:
        """Convert HTML content to PDF
        
        Args:
            document_content (str): HTML content to convert
            external_css (str, optional): Additional CSS styles
            javascript (bool, optional): Enable JavaScript execution
            renderer (str, optional): PDF renderer to use
            
        Returns:
            ToolResult: Result of the conversion
        """
        payload = {
            "document_content": document_content
        }
        
        if external_css:
            payload["external_css"] = external_css
        if javascript:
            payload["javascript"] = javascript
        if renderer and renderer != "princexml":
            payload["renderer"] = renderer
            
        logger.debug(f"\033[95mConverting HTML to PDF with renderer: {renderer}\033[0m")
        return await self._execute_pdf_conversion(payload)

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "convert_html_file_to_pdf",
            "description": "Convert an HTML file to PDF by reading the file content and converting it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "html_file_path": {
                        "type": "string",
                        "description": "Path to the HTML file to convert to PDF (relative to /workspace/)"
                    },
                    "pdf_file_path": {
                        "type": "string",
                        "description": "Output PDF file path (relative to /workspace/). If not provided, will auto-generate filename."
                    },
                    "external_css": {
                        "type": "string",
                        "description": "Additional CSS styles to apply to the document"
                    },
                    "javascript": {
                        "type": "boolean",
                        "description": "Enable JavaScript execution (PrinceXML only)",
                        "default": False
                    },
                    "renderer": {
                        "type": "string",
                        "description": "PDF renderer to use: 'princexml' (default) or 'playwright'",
                        "default": "princexml"
                    }
                },
                "required": ["html_file_path"]
            }
        }
    })
    @usage_example('''
        <function_calls>
        <invoke name="convert_html_file_to_pdf">
        <parameter name="html_file_path">lawsuit_research_report.html</parameter>
        <parameter name="pdf_file_path">lawsuit_research_report.pdf</parameter>
        <parameter name="external_css">@page { size: A4; margin: 2cm; }</parameter>
        </invoke>
        </function_calls>
        ''')
    async def convert_html_file_to_pdf(
        self,
        html_file_path: str,
        pdf_file_path: str = None,
        external_css: str = None,
        javascript: bool = False,
        renderer: str = "princexml"
    ) -> ToolResult:
        """Convert an HTML file to PDF by reading the file and converting its content
        
        Args:
            html_file_path (str): Path to HTML file to convert (relative to /workspace/)
            pdf_file_path (str, optional): Output PDF file path
            external_css (str, optional): Additional CSS styles
            javascript (bool, optional): Enable JavaScript execution
            renderer (str, optional): PDF renderer to use
            
        Returns:
            ToolResult: Result of the conversion
        """
        try:
            # Ensure sandbox is initialized
            await self._ensure_sandbox()
            
            # Read the HTML file using cat command
            full_html_path = f"/workspace/{html_file_path}"
            read_cmd = f"cat '{full_html_path}'"
            read_response = await self.sandbox.process.exec(read_cmd)
            
            if read_response.exit_code != 0:
                error_msg = f"Failed to read HTML file {html_file_path}: {read_response.result}"
                logger.error(error_msg)
                return self.fail_response(error_msg)
            
            html_content = read_response.result
            logger.debug(f"\033[95mRead HTML file: {html_file_path} ({len(html_content)} chars)\033[0m")
            
            # Build payload
            payload = {
                "document_content": html_content
            }
            
            if external_css:
                payload["external_css"] = external_css
            if javascript:
                payload["javascript"] = javascript
            if renderer and renderer != "princexml":
                payload["renderer"] = renderer
            
            # Use provided output filename or generate one
            output_filename = pdf_file_path if pdf_file_path else f"{html_file_path.rsplit('.', 1)[0]}.pdf"
            
            logger.debug(f"\033[95mConverting HTML file to PDF: {html_file_path} -> {output_filename}\033[0m")
            return await self._execute_pdf_conversion(payload, output_filename)
            
        except Exception as e:
            logger.error(f"Error converting HTML file {html_file_path}: {e}")
            return self.fail_response(f"Error converting HTML file {html_file_path}: {e}")

