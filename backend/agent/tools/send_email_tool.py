from typing import List, Optional, Union
from agentpress.tool import Tool, ToolResult, openapi_schema, usage_example
from utils.logger import logger
import os
import datetime
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email, To, Content

class SendEmailTool(Tool):
    """Tool for sending emails via SendGrid API.
    
    This tool provides methods for sending HTML emails to recipients
    using SendGrid as the email service provider.
    """
    
    def __init__(self):
        super().__init__()
        self.sender_email = "hello@zeed.ai"
        self.sendgrid_api_key = os.getenv('SENDGRID_API_KEY')
        if self.sendgrid_api_key:
            logger.info(f"SendGrid API key loaded successfully (length: {len(self.sendgrid_api_key)})")
        else:
            logger.warning("SendGrid API key not found in environment variables during initialization")
        
    def modify_html_content(self, html_content: str) -> str:
        """
        Modifies HTML content by adding base URL to deep research links and removing html tags.
        
        Args:
            html_content (str): Original HTML content
            
        Returns:
            str: Modified HTML content
        """
        # Remove ```html if present at start
        html_content = html_content.replace("```html", "")
        html_content = html_content.replace("```", "")
        
        # Add base URL to deep research links
        html_content = html_content.replace('href="/deep_research/', 'href="https://demo.zeed.ai/deep_research/')
        
        return html_content
    
    @openapi_schema({
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Send HTML email to recipients via SendGrid. Use for: 1) Sending newsletters or updates to users, 2) Sending notification emails, 3) Sending formatted HTML reports via email, 4) Email campaign management, 5) Automated email communications. IMPORTANT: Ensure SendGrid API key is configured. The email will be sent individually to each recipient. All emails include the current date in the subject line.",
            "parameters": {
                "type": "object",
                "properties": {
                    "html_content": {
                        "type": "string",
                        "description": "The HTML content of the email. Can include full HTML structure with styling, images, links, etc. Deep research links will be automatically converted to full URLs."
                    },
                    "recipients": {
                        "anyOf": [
                            {"type": "string"},
                            {"items": {"type": "string"}, "type": "array"}
                        ],
                        "description": "Email address(es) to send to. Can be a single email string or array of email addresses. Each recipient receives an individual email."
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject line. If not provided, defaults to 'Research Update [current date]'. The current date will be appended automatically."
                    }
                },
                "required": ["html_content", "recipients"]
            }
        }
    })
    @usage_example('''
        <function_calls>
        <invoke name="send_email">
        <parameter name="html_content"><html>
<head>
    <style>
        body { font-family: Arial, sans-serif; }
        .header { background-color: #f0f0f0; padding: 20px; }
        .content { padding: 20px; }
    </style>
</head>
<body>
    <div class="header">
        <h1>Weekly Market Update</h1>
    </div>
    <div class="content">
        <p>Here's your weekly market analysis...</p>
        <ul>
            <li>S&P 500: +2.3%</li>
            <li>NASDAQ: +3.1%</li>
        </ul>
        <p>For detailed analysis, visit <a href="/deep_research/market-analysis">our research portal</a>.</p>
    </div>
</body>
</html></parameter>
        <parameter name="recipients">["investor1@example.com", "investor2@example.com"]</parameter>
        <parameter name="subject">Weekly Market Analysis</parameter>
        </invoke>
        </function_calls>
        ''')
    async def send_email(self, html_content: str, recipients: Union[str, List[str]], subject: Optional[str] = None) -> ToolResult:
        """Send HTML email to recipients.
        
        Args:
            html_content: The HTML content of the email
            recipients: Email address(es) to send to
            subject: Optional subject line (defaults to Research Update with date)
            
        Returns:
            ToolResult indicating success or failure of email sending
        """
        try:
            # Check if SendGrid API key is configured
            if not self.sendgrid_api_key:
                logger.error("SendGrid API key not found in environment variables")
                return self.fail_response("SendGrid API key not configured. Please set SENDGRID_API_KEY environment variable.")
            
            # Convert single recipient to list
            if isinstance(recipients, str):
                recipients = [recipients]
            
            # Initialize SendGrid client
            sg = SendGridAPIClient(self.sendgrid_api_key)
            
            # Generate subject with date if not provided
            today_date = datetime.datetime.now().strftime("%Y-%m-%d")
            if not subject:
                subject = f'Research Update {today_date}'
            elif today_date not in subject:
                subject = f'{subject} {today_date}'
            
            # Modify HTML content
            modified_html = self.modify_html_content(html_content)
            
            success_count = 0
            failed_recipients = []
            
            # Send individual email to each recipient
            for recipient in recipients:
                try:
                    # Create message for individual recipient
                    message = Mail(
                        from_email=Email(self.sender_email),
                        to_emails=[To(recipient)],
                        subject=subject,
                        html_content=Content("text/html", modified_html)
                    )
                    
                    # Send email
                    response = sg.send(message)
                    
                    if response.status_code in [200, 201, 202]:
                        logger.info(f"Email sent successfully to {recipient}")
                        success_count += 1
                    else:
                        logger.error(f"Failed to send email to {recipient}. Status code: {response.status_code}")
                        failed_recipients.append(recipient)
                
                except Exception as e:
                    logger.error(f"Failed to send email to {recipient}: {str(e)}")
                    failed_recipients.append(recipient)
            
            # Return result based on success
            if success_count == len(recipients):
                return self.success_response({
                    "status": "All emails sent successfully",
                    "recipients_count": len(recipients),
                    "subject": subject
                })
            elif success_count > 0:
                return self.success_response({
                    "status": "Some emails sent successfully",
                    "successful": success_count,
                    "failed": failed_recipients,
                    "subject": subject
                })
            else:
                return self.fail_response(f"Failed to send emails to all recipients: {failed_recipients}")
                
        except Exception as e:
            logger.error(f"Error in send_email: {str(e)}", exc_info=True)
            return self.fail_response(f"Error sending email: {str(e)}")


if __name__ == "__main__":
    import asyncio
    
    async def test_send_email():
        email_tool = SendEmailTool()
        
        # Test HTML content
        test_html = """
        <html>
        <body>
            <h1>Test Email</h1>
            <p>This is a test email from the SendEmailTool.</p>
            <p>Visit <a href="/deep_research/test">our research</a>.</p>
        </body>
        </html>
        """
        
        # Test sending
        result = await email_tool.send_email(
            html_content=test_html,
            recipients="test@example.com",
            subject="Test Email"
        )
        print("Email result:", result)
    
    asyncio.run(test_send_email())