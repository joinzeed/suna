import asyncio
from agentpress.tool import Tool, ToolResult, openapi_schema, usage_example

class WaitTool(Tool):
    """A tool to wait for a specified number of seconds."""

    def __init__(self):
        super().__init__()

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "wait",
            "description": "Wait for a specified number of seconds before proceeding.",
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {"type": "number", "description": "The number of seconds to wait."}
                },
                "required": ["seconds"]
            }
        }
    })
    @usage_example('''
        <function_calls>
        <invoke name="wait">
        <parameter name="seconds">10</parameter>
        </invoke>
        </function_calls>
        ''')
    async def wait(self, seconds: int) -> ToolResult:
        """Waits for a specified number of seconds."""
        try:
            await asyncio.sleep(seconds)
            return self.success_response(f"Waited for {seconds} seconds.")
        except Exception as e:
            return self.fail_response(f"Error while waiting: {str(e)}")
