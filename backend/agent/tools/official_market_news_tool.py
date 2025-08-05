import os
import json
import datetime
import urllib.parse
import aiohttp
import asyncio
import logging
from typing import List, Dict, Any
from dotenv import load_dotenv
from agentpress.tool import Tool, ToolResult, openapi_schema
from utils.config import config
from sandbox.tool_base import SandboxToolsBase
from agentpress.thread_manager import ThreadManager


class SandboxOfficialMarketNewsTool(SandboxToolsBase):
    """Tool for fetching official market news from Nordic, LSEG, and Euronext exchanges."""

    def __init__(self, project_id: str, thread_manager: ThreadManager):
        super().__init__(project_id, thread_manager)
        # Load environment variables
        load_dotenv()
        # Use API keys from config
        self.firecrawl_api_key = config.FIRECRAWL_API_KEY
        
        if not self.firecrawl_api_key:
            raise ValueError("FIRECRAWL_API_KEY not found in configuration")

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "get_nordic_rns_placement_list",
            "description": "Fetches news from the Nasdaq Nordic API based on a search term. Retrieves placement and fundraising announcements from Nordic markets (Sweden, Norway, Denmark, Finland).",
            "parameters": {
                "type": "object",
                "properties": {
                    "free_text": {
                        "type": "string",
                        "description": "Search term to filter results. Common terms: 'placement', 'rights issue', 'fundraising', 'equity'",
                        "default": "placement"
                    }
                },
                "required": []
            }
        }
    })
    async def get_nordic_rns_placement_list(self, free_text: str = "placement") -> ToolResult:
        """
        Fetches news from the Nasdaq Nordic API based on a search term.
        """
        try:
            # Calculate yesterday and today's dates in milliseconds since epoch
            today = datetime.datetime.now()
            yesterday = today - datetime.timedelta(days=1)

            # Convert to milliseconds and ensure full day coverage
            from_date = int(yesterday.replace(hour=0, minute=0,
                            second=0, microsecond=0).timestamp() * 1000)
            to_date = int(today.replace(hour=23, minute=59, second=59,
                          microsecond=999999).timestamp() * 1000)

            # Base URL for the Nasdaq Nordic news API
            base_url = "https://api.news.eu.nasdaq.com/news/query.action"

            # Query parameters
            params = {
                "countResults": "true",
                "globalGroup": "exchangeNotice",
                "displayLanguage": "en",
                "timeZone": "CET",
                "dateMask": "yyyy-MM-dd+HH:mm:ss",
                "limit": "100",
                "start": "0",
                "dir": "DESC",
                "globalName": "NordicAllMarkets",
                "freeText": free_text,
                "fromDate": str(from_date),
                "toDate": str(to_date),
            }

            # Build URL with parameters
            query_string = "&".join(
                [f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items()])
            url = f"{base_url}?{query_string}"

            logging.info(f"Fetching Nordic news with query: {free_text}")

            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=30) as response:
                    if response.status != 200:
                        logging.error(f"Error fetching Nordic RNS: HTTP {response.status}")
                        return self.fail_response(f"HTTP {response.status} error from Nordic API")

                    # Get the response text
                    content = await response.text()
                    # Remove the 'handleResponse(' prefix and ')' suffix if present
                    if content.startswith("handleResponse(") and content.endswith(")"):
                        content = content[15:-1]

                    try:
                        # Parse the JSON response
                        data = json.loads(content)
                    except json.JSONDecodeError as e:
                        logging.error(f"Failed to parse JSON response: {e}")
                        # Log first 200 chars for debugging
                        logging.debug(f"Response content: {content[:200]}...")
                        return self.fail_response(f"Failed to parse Nordic API response: {e}")

                    # Extract and format the news items
                    results = []
                    if "results" in data and data["results"]:
                        for item in data["results"]['item']:
                            news_item = {
                                "disclosure_id": item.get("disclosureId", ""),
                                "date": item.get("releaseTime", ""),
                                "headline": item.get("headline", ""),
                                "link": item.get("messageUrl", ""),
                                "type": "topic",
                                "picked_reason": "Nordic Nasdaq Fundraises"
                            }
                            results.append(news_item)

                    logging.info(f"Found {len(results)} Nordic news items matching '{free_text}'")
                    
                    return ToolResult(
                        success=True,
                        output=json.dumps(results, ensure_ascii=False, indent=2)
                    )

        except Exception as e:
            error_message = str(e)
            logging.error(f"Unexpected error in get_nordic_rns_placement_list: {error_message}")
            return self.fail_response(f"Error fetching Nordic news: {error_message}")

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "get_lseg_rns_placement_list",
            "description": "Fetches news from the LSEG (London Stock Exchange Group) via Investegate using Firecrawl to scrape filtered content. Focuses on placement and fundraising announcements.",
            "parameters": {
                "type": "object",
                "properties": {
                    "free_text": {
                        "type": "string",
                        "description": "Search term to filter results. Common terms: 'placement', 'rights issue', 'fundraising', 'equity'",
                        "default": "placement"
                    }
                },
                "required": []
            }
        }
    })
    async def get_lseg_rns_placement_list(self, free_text: str = "placement") -> ToolResult:
        """
        Fetches news from the LSEG via Investegate using Firecrawl to scrape filtered content.
        """
        try:
            # Calculate yesterday and today's dates for URL formatting
            today = datetime.datetime.now()
            yesterday = today - datetime.timedelta(days=1)

            # Format dates for Investegate URL (e.g., "16+July+2025")
            from_date = yesterday.strftime("%d+%B+%Y")
            to_date = today.strftime("%d+%B+%Y")

            # Build the Investegate URL with dynamic dates
            base_url = "https://www.investegate.co.uk/advanced-search"
            params = {
                "search_for": "1",
                "date_from": from_date,
                "date_to": to_date,
                "exclude_navs": "true",
                "key_word": free_text
            }

            # Build query string
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{base_url}?{query_string}"
            logging.info(f"Fetching LSEG news from Investegate with query: {free_text}")
            logging.info(f"URL: {url}")

            # Firecrawl API configuration
            api_url = "https://api.firecrawl.dev/v1/scrape"
            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {self.firecrawl_api_key}'
            }

            # JSON schema for structured data extraction
            json_schema = {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "company": {
                            "type": "string",
                            "description": "The name of the company"
                        },
                        "date": {
                            "type": "string",
                            "description": "The date when the news was released"
                        },
                        "time": {
                            "type": "string",
                            "description": "The time when the news was released"
                        },
                        "headline": {
                            "type": "string",
                            "description": "The headline/title of the news announcement"
                        },
                        "link": {
                            "type": "string",
                            "description": "The link to the full news article"
                        },
                        "category": {
                            "type": "string",
                            "description": "The category or type of announcement"
                        }
                    },
                    "required": ["company", "date", "headline", "link"]
                }
            }

            # Firecrawl payload
            payload = {
                "url": url,
                "formats": ["json"],
                "timeout": 90000,
                "jsonOptions": {
                    "schema": json_schema,
                    "systemPrompt": "You are an expert financial news analyst specializing in LSEG regulatory announcements.",
                    "prompt": f"""
                    Extract structured information ONLY from the table within the div element with id="advanced-table-div".
                    
                    Look specifically for the HTML table with class="table-investegate" inside the advanced-table-div.
                    IGNORE all other content on the page that is outside this specific table.
                    
                    IMPORTANT TIME FILTER: Only extract announcements published after 11:30 AM of {yesterday.strftime('%d %B %Y')}.
                    This includes announcements from yesterday after 11:30 AM and all announcements from today.
                    If you can see the time of publication, exclude any items published before 11:30 AM on {yesterday.strftime('%d %B %Y')}.
                    
                    For each row in the table (excluding the header row), extract:
                    - company: The exact company name from the "Company" column
                    - date: The release date from the "Date" column
                    - time: The release time (if available in the date/time information)
                    - headline: The full headline/title from the "Announcement" column
                    - link: The full URL link to the news article (if present in the announcement cell)
                    - category: The type of announcement from the "Source" column (e.g., "RNS", "Regulatory News", etc.)
                    
                    Focus on announcements related to '{free_text}' and similar financial activities like equity issues, placements, fundraising, etc.
                    
                    EXCLUDE the following types of entities:
                    - Investment trusts (companies with "Trust" in the name)
                    - Investment funds (companies with "Fund" in the name)
                    - Investment companies with "Limited" or "Plc" that appear to be investment vehicles
                    - Any company names containing patterns like "Income Trust", "Bond Income", "Equity Income Trust", etc.
                    - REITs and other investment vehicles
                    
                    Focus only on operating companies and businesses, not investment trusts or funds.
                    
                    IMPORTANT: If the table body is empty (no data rows) or no announcements match the criteria, return an empty array: []
                    
                    Process only the data within the table structure:
                    <table class="table-investegate">
                        <thead>
                            <tr>
                                <th>Date</th>
                                <th>Company</th>
                                <th>Source</th>
                                <th>Announcement</th>
                            </tr>
                        </thead>
                        <tbody>
                            <!-- Extract data from rows here -->
                        </tbody>
                    </table>
                    """
                }
            }


            # Make the API request
            async with aiohttp.ClientSession() as session:
                async with session.post(api_url, json=payload, headers=headers) as response:
                    if response.status != 200:
                        logging.error(f"Error fetching LSEG RNS: HTTP {response.status}")
                        return self.fail_response(f"HTTP {response.status} error from Firecrawl API")

                    result = await response.json()

                    if result.get('success') and result.get('data', {}):
                        raw_data = result.get('data', {}).get('json', [])
                        
                        # Check if raw_data is empty or None
                        if not raw_data:
                            logging.info(f"No data found in LSEG RNS response for '{free_text}'")
                            return ToolResult(
                                success=True,
                                output=json.dumps([], ensure_ascii=False, indent=2)
                            )

                        # Format the data to match the expected structure
                        formatted_results = []
                        for item in raw_data:
                            news_item = {
                                "company": item.get("company", ""),
                                "date": item.get("date", ""),
                                "time": item.get("time", ""),
                                "headline": item.get("headline", ""),
                                "link": item.get("link", ""),
                                "category": item.get("category", ""),
                                "type": "topic",
                                "picked_reason": "LSE Fundraises"
                            }
                            formatted_results.append(news_item)

                        logging.info(f"Found {len(formatted_results)} LSEG news items matching '{free_text}'")
                        
                        return ToolResult(
                            success=True,
                            output=json.dumps(formatted_results, ensure_ascii=False, indent=2)
                        )
                    else:
                        error_msg = result.get('error', 'Unknown error')
                        logging.error("❌ Scraping failed:")
                        logging.error(error_msg)
                        return self.fail_response(f"Scraping failed: {error_msg}")

        except aiohttp.ClientError as e:
            logging.error(f"❌ HTTP request failed: {e}")
            return self.fail_response(f"HTTP request failed: {e}")
        except asyncio.TimeoutError:
            logging.error("❌ Request timed out")
            return self.fail_response("Request timed out")
        except Exception as e:
            error_message = str(e)
            logging.error(f"❌ Unexpected error in get_lseg_rns_placement_list: {error_message}")
            return self.fail_response(f"Error fetching LSEG news: {error_message}")

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "get_euronext_rns_placement_list",
            "description": "Fetches news from Euronext using Firecrawl to scrape filtered content. Specifically targets financial transaction and share introduction announcements from European markets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "free_text": {
                        "type": "string",
                        "description": "Search term to filter results. Common terms: 'placement', 'rights issue', 'fundraising', 'equity'",
                        "default": "placement"
                    }
                },
                "required": []
            }
        }
    })
    async def get_euronext_rns_placement_list(self, free_text: str = "placement") -> ToolResult:
        """
        Fetches news from Euronext using Firecrawl to scrape filtered content.
        Specifically targets "Other financial transaction" and "Share introduction and issues" categories.
        """
        url = "https://api.firecrawl.dev/v1/scrape"

        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.firecrawl_api_key}'
        }
        
        # Get today and yesterday dates for filtering
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.datetime.now() -
                     datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        
        payload = {
            "url": "https://live.euronext.com/en/products/equities/company-news#regulated-news",
            "formats": ["json"],
            "timeout": 90000,
            "actions": [
                {"type": "wait", "milliseconds": 2000},

                {"type": "wait", "milliseconds": 2000},
                {"type": "click", "selector": "#edit-combine"},
                {"type": "write", "selector": "#edit-combine", "text": free_text},

                # Apply the filter using the more specific selector
                {"type": "click", "selector": "input[value='Apply']"},
                {"type": "wait", "milliseconds": 3000}
            ],
            "jsonOptions": {
                "prompt": f"""
                Extract structured information from the financial news announcements displayed on this page.
                IMPORTANT: Only include news items that were released on {today} or {yesterday}.

                For each relevant news item, extract:
                - company: The exact company name
                - released_date: The release date in YYYY-MM-DD format
                - title: The full title of the announcement
                - topic: Categorize the announcement
                - industry: The industry sector of the company
                - link: The link to the news article

                Return the results as a JSON array of objects. Each object should contain the fields: company, released_date, title, topic, industry, and link.
                Only include items from {yesterday} and {today}.

                Example format:
                [
                {{
                    'company': 'Example Corp',
                    'released_date': '2024-01-15',
                    'title': 'Example Corp Reports Q4 Earnings',
                    'topic': 'Earnings',
                    'industry': 'Technology',
                    'link': 'https://example.com/news/123'
                }}
                ]
                """
            }
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers) as response:
                    response.raise_for_status()
                    result = await response.json()

                    if result.get('success'):
                        logging.info("✅ Successfully scraped Euronext RNS placement news")
                        logging.info(f"\n📄 Content length: {len(result['data']['json'])}")
                        raw_data = result['data']['json']
                        
                        formatted_results = [{
                            "date": item.get("released_date", ""),
                            "headline": item.get("title", ""),
                            "link": item.get("link", ""),
                            "type": "topic",
                            "company": item.get("company", ""),
                            "industry": item.get("industry", ""),
                            "category": item.get("topic", ""),
                            "picked_reason": "Euronext Fundraises"
                        } for item in raw_data]
                        
                        return ToolResult(
                            success=True,
                            output=json.dumps(formatted_results, ensure_ascii=False, indent=2)
                        )
                    else:
                        logging.error("❌ Scraping failed:")
                        logging.error(result.get('error', 'Unknown error'))
                        return self.fail_response(f"Scraping failed: {result.get('error', 'Unknown error')}")

        except aiohttp.ClientError as e:
            logging.error(f"❌ HTTP request failed: {e}")
            return self.fail_response(f"HTTP request failed: {e}")
        except asyncio.TimeoutError:
            logging.error("❌ Request timed out")
            return self.fail_response("Request timed out")
        except Exception as e:
            error_message = str(e)
            logging.error(f"❌ Unexpected error: {error_message}")
            return self.fail_response(f"Error fetching Euronext news: {error_message}")


if __name__ == "__main__":
    async def test_nordic_news():
        """Test function for the Nordic news tool"""
        print("Test function needs to be updated for sandbox version")
    
    async def test_lseg_news():
        """Test function for the LSEG news tool"""
        print("Test function needs to be updated for sandbox version")
    
    async def test_euronext_news():
        """Test function for the Euronext news tool"""
        print("Test function needs to be updated for sandbox version")
    
    async def run_tests():
        """Run all test functions"""
        await test_nordic_news()
        await test_lseg_news()
        await test_euronext_news()
        
    asyncio.run(run_tests())