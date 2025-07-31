import os
import logging
from typing import Dict, Any, Optional, List, Tuple
import httpx
from urllib.parse import urlencode
from bs4 import BeautifulSoup
import re
from dotenv import load_dotenv
from agentpress.tool import Tool, ToolResult, openapi_schema, usage_example
from utils.config import config
from sandbox.tool_base import SandboxToolsBase
from agentpress.thread_manager import ThreadManager


class FinvizClient:
    def __init__(self, email: str = "", password: str = "", use_elite: bool = False, page_row_number: int = 100):
        self.email = email
        self.password = password
        self.use_elite = use_elite
        self.session = None  # Will be an httpx.AsyncClient
        self.is_logged_in = False
        self.logger = logging.getLogger("finviz_client")
        self.page_row_number = page_row_number

    async def _get_session(self):
        if self.session is None:
            self.session = httpx.AsyncClient(headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0"
            })
        return self.session

    async def login(self) -> bool:
        if not self.use_elite:
            return True
        if not self.email or not self.password:
            self.logger.error("Email or password is empty")
            return False
        login_data = {"email": self.email, "password": self.password}
        try:
            session = await self._get_session()
            response = await session.post(
                "https://finviz.com/login_submit.ashx",
                data=login_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                follow_redirects=True
            )
            if response.status_code != 200:
                self.logger.error(
                    f"Login failed with status code: {response.status_code}")
                return False
            if "elite.finviz.com" not in str(response.url):
                self.logger.error(
                    f"Login failed - redirected to {response.url} instead of elite.finviz.com")
                return False
            self.is_logged_in = True
            self.logger.info("Login successful")
            return True
        except Exception as e:
            self.logger.error(f"Login exception: {str(e)}")
            return False

    async def fetch_page(self, url: str) -> Optional[str]:
        try:
            session = await self._get_session()
            response = await session.get(url)
            if response.status_code != 200:
                self.logger.error(
                    f"Failed to fetch page: {url}, status code: {response.status_code}")
                return None
            return response.text
        except Exception as e:
            self.logger.error(f"Exception fetching page {url}: {str(e)}")
            return None

    async def fetch_table(self, params: Dict[str, Any], fetch_all_pages: bool = False) -> Optional[Dict[str, Any]]:
        if self.use_elite and not self.is_logged_in:
            login_successful = await self.login()
            if not login_successful:
                self.logger.error("Not logged in and login attempt failed")
                return None
        # Convert filters dict to properly formatted filter strings
        filter_list = []
        for filter_key, filter_value in params.get("filters", {}).items():
            filter_list.append(f"{filter_key}_{filter_value}")
        
        table_params = TableParams(
            order=params.get("order", ""),
            desc=params.get("desc", False),
            signal=params.get("signal", ""),
            filters=filter_list,
            page=params.get("page", 1),
            tickers=params.get("tickers", None),
            page_row_number=self.page_row_number
        )
        uri = table_params.build_uri()
        base_url = "https://elite.finviz.com/screener.ashx" if self.use_elite else "https://finviz.com/screener.ashx"
        url = f"{base_url}?{uri}"
        html_content = await self.fetch_page(url)
        if not html_content:
            return None
        table = parse_table(html_content, self.page_row_number)
        if table is None:
            return None
        result = {
            "headers": table.headers,
            "rows": table.rows,
            "pagination": table.pagination
        }
        if fetch_all_pages and table.pagination["current_page"] < table.pagination["total_pages"]:
            all_rows = table.rows.copy()
            last_page_fetched = table.pagination["current_page"]
            for page in range(table.pagination["current_page"] + 1, table.pagination["total_pages"] + 1):
                table_params.page = page
                next_uri = table_params.build_uri()
                next_url = f"{base_url}?{next_uri}"
                next_html_content = await self.fetch_page(next_url)
                if not next_html_content:
                    break
                next_table = parse_table(
                    next_html_content, self.page_row_number)
                if next_table is None:
                    break
                all_rows.extend(next_table.rows)
                last_page_fetched = page
            result["rows"] = all_rows
            if last_page_fetched == table.pagination["total_pages"]:
                result["pagination"]["current_page"] = table.pagination["total_pages"]
            else:
                result["pagination"]["current_page"] = last_page_fetched
        return result

    async def fetch_futures(self) -> Optional[Dict[str, Any]]:
        if self.use_elite and not self.is_logged_in:
            login_successful = await self.login()
            if not login_successful:
                self.logger.error("Not logged in and login attempt failed")
                return None
        base_url = "https://elite.finviz.com" if self.use_elite else "https://finviz.com"
        url = f"{base_url}/api/futures_all.ashx?timeframe=NO"
        try:
            session = await self._get_session()
            response = await session.get(url)
            if response.status_code != 200:
                self.logger.error(
                    f"Failed to fetch futures: status code {response.status_code}")
                return None
            return response.json()
        except Exception as e:
            self.logger.error(f"Exception fetching futures: {str(e)}")
            return None

    async def fetch_news_and_blogs(self) -> Tuple[Optional[List[Dict[str, str]]], Optional[List[Dict[str, str]]]]:
        if self.use_elite and not self.is_logged_in:
            login_successful = await self.login()
            if not login_successful:
                self.logger.error("Not logged in and login attempt failed")
                return None, None
        base_url = "https://elite.finviz.com" if self.use_elite else "https://finviz.com"
        url = f"{base_url}/news.ashx"
        html_content = await self.fetch_page(url)
        if not html_content:
            return None, None
        soup = BeautifulSoup(html_content, 'html.parser')
        tables = soup.select("table.styled-table-new")
        if len(tables) < 2:
            self.logger.error("Could not find news and blogs tables")
            return None, None
        news_table = tables[0]
        blogs_table = tables[1]
        news = parse_links(news_table)
        blogs = parse_links(blogs_table)
        return news, blogs


class TableParams:
    def __init__(self, order: str = "", desc: bool = False, signal: str = "", filters: List[str] = None, page: int = 1, page_row_number: int = 100, tickers: List[str] = None):
        self.order = order
        self.desc = desc
        self.signal = signal
        self.filters = filters or []
        self.page = page
        self.page_row_number = page_row_number
        self.tickers = tickers

    def build_uri(self) -> str:
        ret = ""
        if self.order:
            if self.desc:
                ret += f"o=-{self.order}"
            else:
                ret += f"o={self.order}"
        if self.signal:
            if ret:
                ret += "&"
            ret += f"s={self.signal}"
        if self.filters:
            if ret:
                ret += "&"
            ret += "f=" + ",".join(self.filters)
        if self.page > 1:
            if ret:
                ret += "&"
            offset = (self.page - 1) * self.page_row_number + 1
            ret += f"r={offset}"
        if self.tickers:
            if ret:
                ret += "&"
            ret += f"t={','.join(self.tickers)}"
        return ret


class Table:
    def __init__(self, headers: List[str] = None, rows: List[List[str]] = None, pagination: Dict[str, int] = None):
        self.headers = headers or []
        self.rows = rows or []
        self.pagination = pagination or {
            "current_page": 1,
            "total_pages": 1,
            "total_results": 0
        }


def parse_table(html_content: str, page_row_number: int) -> Optional[Table]:
    soup = BeautifulSoup(html_content, 'html.parser')
    table_data = Table()
    pagination = {
        "current_page": 1,
        "total_pages": 1,
        "total_results": 0
    }
    total_div = soup.select_one("#screener-total")
    if total_div:
        total_text = total_div.text.strip()
        match = re.search(r'#(\d+) / (\d+) Total', total_text)
        if match:
            current_position = int(match.group(1))
            total_results = int(match.group(2))
            current_page = ((current_position - 1) // page_row_number) + 1
            total_pages = (total_results + page_row_number -
                           1) // page_row_number
            pagination["current_page"] = current_page
            pagination["total_pages"] = total_pages
            pagination["total_results"] = total_results
    table_data.pagination = pagination
    screener_table = soup.select_one("#screener-table")
    if not screener_table:
        return None
    thead = screener_table.find("thead")
    if thead:
        for th in thead.find_all("th"):
            if th.has_attr('class') and any('header' in cls for cls in th['class']):
                table_data.headers.append(th.text.strip())
    tbody = thead.find_next_sibling("tbody")
    if tbody:
        for tr in tbody.find_all("tr"):
            row = []
            for td in tr.find_all("td"):
                cell_text = td.get_text(strip=True)
                row.append(cell_text)
            if row:
                table_data.rows.append(row)
    if not table_data.headers or not table_data.rows:
        table = soup.select_one("table.screener_table") or soup.select_one(
            "table.styled-table-new")
        if table:
            thead = table.find("thead")
            if thead and not table_data.headers:
                for th in thead.find_all("th"):
                    table_data.headers.append(th.text.strip())
            tbody = table.find("tbody") or table
            if not table_data.rows:
                for tr in tbody.find_all("tr"):
                    if tr.find("th"):
                        continue
                    row = []
                    for td in tr.find_all("td"):
                        cell_text = td.get_text(strip=True)
                        row.append(cell_text)
                    if row:
                        table_data.rows.append(row)
    if not table_data.headers:
        return None
    if not table_data.rows:
        return None
    return table_data


def parse_links(table: Any) -> List[Dict[str, str]]:
    import datetime
    today = datetime.datetime.now(
        datetime.timezone(-datetime.timedelta(hours=5))).date()
    records = []
    for tr in table.select("tr.news_table-row"):
        a = tr.find("a")
        if not a or not a.has_attr("href"):
            continue
        href = a["href"]
        date_cell = tr.select_one("td.news_date-cell")
        if not date_cell:
            continue
        date_text = date_cell.text.strip()
        if date_text.endswith("AM") or date_text.endswith("PM"):
            date_str = today.strftime("%b-%d %Y")
        else:
            date_str = f"{date_text} {today.year}"
        records.append({
            "date": date_str,
            "title": a.text.strip(),
            "url": href
        })
    return records


class SandboxFinvizTool(SandboxToolsBase):
    """
    Tool for accessing Finviz screener, futures, and news/blogs data.
    Usage:
        tool = SandboxFinvizTool()
        data = await tool.run_screener({"order": "marketcap", ...})
        futures = await tool.run_futures()
        news, blogs = await tool.run_news_and_blogs()
        available_filters = await tool.get_available_filters()
    """

    def __init__(self, project_id: str, thread_manager: ThreadManager):
        super().__init__(project_id, thread_manager)
        self.logger = logging.getLogger("sandbox_finviz_tool")
        load_dotenv()
        email = config.FINVIZ_ELITE_EMAIL
        password = config.FINVIZ_ELITE_PASSWORD
        use_elite: bool = True
        page_row_number: int = 100
        if not email or not password:
            raise ValueError("Finviz email or password is not set")
        self.client = FinvizClient(
            email=email, password=password, use_elite=use_elite, page_row_number=page_row_number)

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "run_screener",
            "description": "Execute a Finviz stock screener with customizable filters and parameters. Returns filtered stock data with headers, rows, and pagination information. Supports both basic preset filters and advanced custom filter combinations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "params": {
                        "type": "object",
                        "description": "Screener configuration parameters. All fields are optional.",
                        "properties": {
                            "order": {
                                "type": "string",
                                "description": "Column to sort results by. Common values: 'ticker', 'company', 'sector', 'industry', 'country', 'marketcap', 'pe', 'price', 'change', 'volume', 'eps', 'sales', 'dividend', 'roa', 'roe', 'roi', 'perf', 'volatility', 'rsi', 'gap', 'beta', 'atr'. Use get_available_filters() to see all sortable columns.",
                                "examples": ["marketcap", "price", "volume", "change", "pe"]
                            },
                            "desc": {
                                "type": "boolean",
                                "description": "Sort in descending order (highest to lowest). Set to false for ascending order.",
                                "default": False
                            },
                            "signal": {
                                "type": "string",
                                "description": "Predefined trading signal filter. Common signals: 'ta_topgainers', 'ta_toplosers', 'ta_newhigh', 'ta_newlow', 'ta_mostactive', 'ta_mostvolatile', 'ta_overbought', 'ta_oversold', 'ta_downgrades', 'ta_upgrades', 'ta_earnings_today', 'ta_earnings_thisweek', 'ta_insiderbuy', 'ta_insidersell', 'ta_unusualvolume'",
                                "examples": ["ta_topgainers", "ta_newhigh", "ta_unusualvolume"]
                            },
                            "filters": {
                                "type": "object",
                                "description": "Dictionary of filter criteria where keys are filter names (without 'fs_' prefix) and values are filter conditions. Use get_available_filters() for complete list. Examples: {'cap': 'largeover', 'fa_pe': 'u20', 'ta_rsi': 'os30', 'sec': 'technology', 'geo': 'usa'}",
                                "additionalProperties": {
                                    "type": "string",
                                    "description": "Filter value - can be preset option or custom format for Elite users"
                                },
                                "examples": [
                                    {
                                        "cap": "largeover",
                                        "fa_pe": "u20",
                                        "sec": "technology"
                                    },
                                    {
                                        "geo": "usa",
                                        "ta_rsi": "os30",
                                        "sh_avgvol": "o1000"
                                    }
                                ]
                            },
                            "page": {
                                "type": "integer",
                                "description": "Page number to fetch (1-based). Each page contains up to 100 results by default.",
                                "minimum": 1,
                                "default": 1
                            },
                            "tickers": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Specific ticker symbols to analyze. When provided, filters are applied only to these stocks. Useful for screening a custom watchlist.",
                                "examples": [["AAPL", "MSFT", "GOOGL"], ["SPY", "QQQ"]]
                            }
                        },
                        "additionalProperties": False
                    },
                    "fetch_all_pages": {
                        "type": "boolean",
                        "description": "Fetch all available pages of results in a single call. Use with caution for large result sets as it may take longer to execute.",
                        "default": False
                    }
                },
                "required": ["params"]
            }
        }
    })
    @usage_example('''
        <function_calls>
        <invoke name="run_screener">
        <parameter name="params">{
            "order": "marketcap", 
            "desc": true, 
            "filters": {
                "cap": "largeover",
                "fa_pe": "u20",
                "sec": "technology",
                "geo": "usa"
            },
            "page": 1
        }</parameter>
        <parameter name="fetch_all_pages">false</parameter>
        </invoke>
        </function_calls>
        ''')
    async def run_screener(self, params: Dict[str, Any], fetch_all_pages: bool = False) -> ToolResult:
        """
        Execute Finviz screener with the provided parameters.

        Args:
            params: Screener parameters including filters, sorting, and pagination
            fetch_all_pages: Whether to fetch all pages of results

        Returns:
            ToolResult containing screener data with headers, rows, and pagination info
        """
        try:
            # Validate and sanitize inputs
            if not isinstance(params, dict):
                return self.fail_response("Parameters must be a dictionary.")

            # Handle fetch_all_pages parameter - it might be in params or as separate parameter
            if 'fetch_all_pages' in params:
                fetch_all_pages = params.pop('fetch_all_pages')
            
            # Log the request for debugging (remove in production)
            self.logger.info(
                f"Running screener with params: {params}, fetch_all_pages: {fetch_all_pages}")

            # Execute the screener
            result = await self.client.fetch_table(params, fetch_all_pages=fetch_all_pages)

            if not result:
                return self.fail_response("No data returned from Finviz screener. This could be due to overly restrictive filters or invalid parameters.")

            # Add metadata to the response
            enhanced_result = {
                **result,
                "request_params": params,
                "fetch_all_pages": fetch_all_pages,
                "result_count": len(result.get("rows", [])),
                "timestamp": self._get_current_timestamp()
            }

            return self.success_response(enhanced_result)

        except ValueError as e:
            return self.fail_response(f"Invalid parameter value: {str(e)}")
        except ConnectionError as e:
            return self.fail_response(f"Connection error while fetching data: {str(e)}")
        except Exception as e:
            self.logger.error(
                f"Unexpected error in run_screener: {str(e)}", exc_info=True)
            return self.fail_response(f"Error fetching screener data: {str(e)}")

    def _get_current_timestamp(self) -> str:
        """Get current timestamp in ISO format."""
        from datetime import datetime
        return datetime.now().isoformat()

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "get_available_filters",
            "description": "Get all available filter keys and their possible values for the Finviz screener.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    })
    @usage_example('''
        <function_calls>
        <invoke name="get_available_filters" />
        </function_calls>
        ''')
    async def get_available_filters(self) -> ToolResult:
        """
        Returns all available filter keys and their possible values for the Finviz screener.
        """
        filters_metadata = {
            # DESCRIPTIVE FILTERS
            "exch": {
                "name": "Exchange",
                "description": "Stock Exchange at which a stock is listed",
                "custom_available": True,
                "options": {
                    "": "Any",
                    "amex": "AMEX",
                    "cboe": "CBOE",
                    "nasd": "NASDAQ",
                    "nyse": "NYSE"
                }
            },
            "idx": {
                "name": "Index",
                "description": "A major index membership of a stock",
                "custom_available": True,
                "options": {
                    "": "Any",
                    "sp500": "S&P 500",
                    "ndx": "NASDAQ 100",
                    "dji": "DJIA",
                    "rut": "RUSSELL 2000"
                }
            },
            "sec": {
                "name": "Sector",
                "description": "The sector which a stock belongs to",
                "custom_available": True,
                "options": {
                    "": "Any",
                    "basicmaterials": "Basic Materials",
                    "communicationservices": "Communication Services",
                    "consumercyclical": "Consumer Cyclical",
                    "consumerdefensive": "Consumer Defensive",
                    "energy": "Energy",
                    "financial": "Financial",
                    "healthcare": "Healthcare",
                    "industrials": "Industrials",
                    "realestate": "Real Estate",
                    "technology": "Technology",
                    "utilities": "Utilities"
                }
            },
            "ind": {
                "name": "Industry",
                "description": "The industry which a stock belongs to",
                "custom_available": True,
                "note": "152+ industry options available - too many to list. Common ones include stocksonly, biotechnology, semiconductors, software, etc."
            },
            "geo": {
                "name": "Country",
                "description": "The country where company is based",
                "custom_available": True,
                "options": {
                    "": "Any",
                    "usa": "USA",
                    "notusa": "Foreign (ex-USA)",
                    "asia": "Asia",
                    "europe": "Europe",
                    "china": "China",
                    "canada": "Canada",
                    "japan": "Japan"
                }
            },
            "cap": {
                "name": "Market Capitalization",
                "description": "The total dollar market value of all outstanding shares",
                "custom_available": True,
                "custom_format": "Range in billions (e.g., 1to5 for $1B-$5B)",
                "options": {
                    "": "Any",
                    "mega": "Mega ($200bln+)",
                    "large": "Large ($10bln-$200bln)",
                    "mid": "Mid ($2bln-$10bln)",
                    "small": "Small ($300mln-$2bln)",
                    "micro": "Micro ($50mln-$300mln)",
                    "nano": "Nano (under $50mln)"
                }
            },
            "fa_div": {
                "name": "Dividend Yield",
                "description": "Annual dividend per share divided by stock price",
                "custom_available": True,
                "custom_format": "Percentage (e.g., 2to6 for 2%-6%)",
                "options": {
                    "": "Any",
                    "none": "None (0%)",
                    "pos": "Positive (>0%)",
                    "high": "High (>5%)",
                    "o1": "Over 1%",
                    "o2": "Over 2%",
                    "o3": "Over 3%",
                    "o4": "Over 4%",
                    "o5": "Over 5%"
                }
            },
            "sh_short": {
                "name": "Short Float",
                "description": "The amount of short-selling transactions",
                "custom_available": True,
                "custom_format": "Percentage (e.g., 5to20 for 5%-20%)",
                "options": {
                    "": "Any",
                    "low": "Low (<5%)",
                    "high": "High (>20%)",
                    "u5": "Under 5%",
                    "o5": "Over 5%",
                    "o10": "Over 10%",
                    "o20": "Over 20%"
                }
            },
            "an_recom": {
                "name": "Analyst Recommendation",
                "description": "Analyst outlook on a stock (1=Strong Buy, 5=Strong Sell)",
                "custom_available": True,
                "options": {
                    "": "Any",
                    "strongbuy": "Strong Buy (1)",
                    "buybetter": "Buy or better",
                    "buy": "Buy",
                    "hold": "Hold",
                    "sell": "Sell",
                    "strongsell": "Strong Sell (5)"
                }
            },
            "sh_opt": {
                "name": "Option/Short",
                "description": "Stocks with options and/or available to sell short",
                "custom_available": True,
                "options": {
                    "": "Any",
                    "option": "Optionable",
                    "short": "Shortable",
                    "optionshort": "Optionable and shortable"
                }
            },
            "earningsdate": {
                "name": "Earnings Date",
                "description": "Date when company reports earnings",
                "custom_available": True,
                "options": {
                    "": "Any",
                    "today": "Today",
                    "tomorrow": "Tomorrow",
                    "thisweek": "This Week",
                    "nextweek": "Next Week",
                    "thismonth": "This Month"
                }
            },
            "sh_avgvol": {
                "name": "Average Volume",
                "description": "Average number of shares traded per day",
                "custom_available": True,
                "custom_format": "Number (e.g., o1000 for over 1M)",
                "options": {
                    "": "Any",
                    "u50": "Under 50K",
                    "o100": "Over 100K",
                    "o500": "Over 500K",
                    "o1000": "Over 1M",
                    "o2000": "Over 2M"
                }
            },
            "sh_relvol": {
                "name": "Relative Volume",
                "description": "Current volume vs 3-month average",
                "custom_available": True,
                "custom_format": "Decimal (e.g., o2 for over 2x average)",
                "options": {
                    "": "Any",
                    "o2": "Over 2",
                    "o3": "Over 3",
                    "o5": "Over 5",
                    "u1": "Under 1"
                }
            },
            "sh_curvol": {
                "name": "Current Volume",
                "description": "Number of shares traded today",
                "custom_available": True,
                "custom_format": "Number (e.g., o1000 for over 1M shares)",
                "options": {
                    "": "Any",
                    "o100": "Over 100K",
                    "o500": "Over 500K",
                    "o1000": "Over 1M",
                    "o5000": "Over 5M"
                }
            },
            "sh_price": {
                "name": "Price",
                "description": "Current stock price",
                "custom_available": True,
                "custom_format": "Dollar amount (e.g., 10to50 for $10-$50)",
                "options": {
                    "": "Any",
                    "u5": "Under $5",
                    "u10": "Under $10",
                    "o5": "Over $5",
                    "o10": "Over $10",
                    "o20": "Over $20",
                    "o50": "Over $50"
                }
            },
            "targetprice": {
                "name": "Target Price",
                "description": "Analysts' mean target price relative to current price",
                "custom_available": True,
                "options": {
                    "": "Any",
                    "above": "Above Price",
                    "a20": "20% Above Price",
                    "below": "Below Price",
                    "b20": "20% Below Price"
                }
            },
            "ipodate": {
                "name": "IPO Date",
                "description": "Date when company had an IPO",
                "custom_available": True,
                "options": {
                    "": "Any",
                    "prevweek": "Last week",
                    "prevmonth": "Last month",
                    "prevyear": "Last year",
                    "more1": "More than 1 year ago"
                }
            },
            "sh_outstanding": {
                "name": "Shares Outstanding",
                "description": "Total shares issued by corporation",
                "custom_available": True,
                "custom_format": "Millions (e.g., u50 for under 50M)",
                "options": {
                    "": "Any",
                    "u50": "Under 50M",
                    "o50": "Over 50M",
                    "o100": "Over 100M",
                    "o500": "Over 500M"
                }
            },
            "sh_float": {
                "name": "Float",
                "description": "Shares available for public trading",
                "custom_available": True,
                "custom_format": "Millions or percentage (e.g., u50 for under 50M)",
                "options": {
                    "": "Any",
                    "u50": "Under 50M",
                    "o50": "Over 50M",
                    "u20p": "Under 20% of outstanding",
                    "o80p": "Over 80% of outstanding"
                }
            },

            # FUNDAMENTAL FILTERS
            "fa_pe": {
                "name": "P/E Ratio",
                "description": "Price-to-Earnings ratio (trailing twelve months)",
                "custom_available": True,
                "custom_format": "Decimal (e.g., 12to18 for P/E 12-18)",
                "options": {
                    "": "Any",
                    "low": "Low (<15)",
                    "profitable": "Profitable (>0)",
                    "u15": "Under 15",
                    "u20": "Under 20",
                    "o15": "Over 15",
                    "o25": "Over 25"
                }
            },
            "fa_fpe": {
                "name": "Forward P/E",
                "description": "P/E using forecasted earnings for next fiscal year",
                "custom_available": True,
                "custom_format": "Decimal (e.g., 10to20 for Forward P/E 10-20)",
                "options": {
                    "": "Any",
                    "low": "Low (<15)",
                    "profitable": "Profitable (>0)",
                    "u15": "Under 15",
                    "o20": "Over 20"
                }
            },
            "fa_peg": {
                "name": "PEG Ratio",
                "description": "P/E to Growth ratio",
                "custom_available": True,
                "custom_format": "Decimal (e.g., u1 for under 1.0)",
                "options": {
                    "": "Any",
                    "low": "Low (<1)",
                    "u1": "Under 1",
                    "u2": "Under 2",
                    "o1": "Over 1",
                    "o2": "Over 2"
                }
            },
            "fa_ps": {
                "name": "P/S Ratio",
                "description": "Price-to-Sales ratio",
                "custom_available": True,
                "custom_format": "Decimal (e.g., u3 for under 3.0)",
                "options": {
                    "": "Any",
                    "low": "Low (<1)",
                    "u2": "Under 2",
                    "u5": "Under 5",
                    "o5": "Over 5",
                    "o10": "Over 10"
                }
            },
            "fa_pb": {
                "name": "P/B Ratio",
                "description": "Price-to-Book ratio",
                "custom_available": True,
                "custom_format": "Decimal (e.g., u2 for under 2.0)",
                "options": {
                    "": "Any",
                    "low": "Low (<1)",
                    "u1": "Under 1",
                    "u3": "Under 3",
                    "o3": "Over 3",
                    "o5": "Over 5"
                }
            },
            "fa_pc": {
                "name": "P/C Ratio",
                "description": "Price-to-Cash ratio",
                "custom_available": True,
                "custom_format": "Decimal (e.g., u10 for under 10)",
                "options": {
                    "": "Any",
                    "low": "Low (<3)",
                    "u5": "Under 5",
                    "u10": "Under 10",
                    "o10": "Over 10",
                    "o50": "Over 50"
                }
            },
            "fa_pfcf": {
                "name": "P/FCF Ratio",
                "description": "Price to Free Cash Flow ratio",
                "custom_available": True,
                "custom_format": "Decimal (e.g., u20 for under 20)",
                "options": {
                    "": "Any",
                    "low": "Low (<15)",
                    "u15": "Under 15",
                    "u25": "Under 25",
                    "o25": "Over 25",
                    "o50": "Over 50"
                }
            },
            "fa_epsyoy": {
                "name": "EPS Growth This Year",
                "description": "EPS growth for current fiscal year",
                "custom_available": True,
                "custom_format": "Percentage (e.g., o10 for over 10%)",
                "options": {
                    "": "Any",
                    "neg": "Negative (<0%)",
                    "pos": "Positive (>0%)",
                    "o10": "Over 10%",
                    "o20": "Over 20%",
                    "u10": "Under 10%"
                }
            },
            "fa_epsyoy1": {
                "name": "EPS Growth Next Year",
                "description": "EPS growth estimate for next fiscal year",
                "custom_available": True,
                "custom_format": "Percentage (e.g., o15 for over 15%)",
                "options": {
                    "": "Any",
                    "neg": "Negative (<0%)",
                    "pos": "Positive (>0%)",
                    "o10": "Over 10%",
                    "o25": "Over 25%"
                }
            },
            "fa_epsqoq": {
                "name": "EPS Growth QoQ",
                "description": "EPS growth quarter over quarter",
                "custom_available": True,
                "custom_format": "Percentage (e.g., o20 for over 20%)",
                "options": {
                    "": "Any",
                    "neg": "Negative (<0%)",
                    "pos": "Positive (>0%)",
                    "o10": "Over 10%",
                    "o25": "Over 25%"
                }
            },
            "fa_eps5years": {
                "name": "EPS Growth Past 5 Years",
                "description": "Annual EPS growth over past 5 years",
                "custom_available": True,
                "custom_format": "Percentage (e.g., o15 for over 15%)",
                "options": {
                    "": "Any",
                    "neg": "Negative (<0%)",
                    "pos": "Positive (>0%)",
                    "o10": "Over 10%",
                    "o20": "Over 20%"
                }
            },
            "fa_salesqoq": {
                "name": "Sales Growth QoQ",
                "description": "Sales growth quarter over quarter",
                "custom_available": True,
                "custom_format": "Percentage (e.g., o10 for over 10%)",
                "options": {
                    "": "Any",
                    "neg": "Negative (<0%)",
                    "pos": "Positive (>0%)",
                    "o10": "Over 10%",
                    "o25": "Over 25%"
                }
            },
            "fa_sales5years": {
                "name": "Sales Growth Past 5 Years",
                "description": "Annual sales growth over past 5 years",
                "custom_available": True,
                "custom_format": "Percentage (e.g., o12 for over 12%)",
                "options": {
                    "": "Any",
                    "neg": "Negative (<0%)",
                    "pos": "Positive (>0%)",
                    "o10": "Over 10%",
                    "o20": "Over 20%"
                }
            },
            "fa_roa": {
                "name": "Return on Assets",
                "description": "Net income divided by total assets",
                "custom_available": True,
                "custom_format": "Percentage (e.g., o10 for over 10%)",
                "options": {
                    "": "Any",
                    "pos": "Positive (>0%)",
                    "neg": "Negative (<0%)",
                    "o10": "Over 10%",
                    "o15": "Over 15%"
                }
            },
            "fa_roe": {
                "name": "Return on Equity",
                "description": "Net income divided by shareholder equity",
                "custom_available": True,
                "custom_format": "Percentage (e.g., o15 for over 15%)",
                "options": {
                    "": "Any",
                    "pos": "Positive (>0%)",
                    "neg": "Negative (<0%)",
                    "o15": "Over 15%",
                    "o20": "Over 20%"
                }
            },
            "fa_roi": {
                "name": "Return on Investment",
                "description": "Return on invested capital",
                "custom_available": True,
                "custom_format": "Percentage (e.g., o12 for over 12%)",
                "options": {
                    "": "Any",
                    "pos": "Positive (>0%)",
                    "neg": "Negative (<0%)",
                    "o10": "Over 10%",
                    "o20": "Over 20%"
                }
            },
            "fa_curratio": {
                "name": "Current Ratio",
                "description": "Current assets divided by current liabilities",
                "custom_available": True,
                "custom_format": "Decimal (e.g., o1.5 for over 1.5)",
                "options": {
                    "": "Any",
                    "high": "High (>3)",
                    "low": "Low (<1)",
                    "o1": "Over 1",
                    "o2": "Over 2"
                }
            },
            "fa_quickratio": {
                "name": "Quick Ratio",
                "description": "Quick assets divided by current liabilities",
                "custom_available": True,
                "custom_format": "Decimal (e.g., o1 for over 1.0)",
                "options": {
                    "": "Any",
                    "high": "High (>3)",
                    "low": "Low (<0.5)",
                    "o1": "Over 1",
                    "u0.5": "Under 0.5"
                }
            },
            "fa_ltdebteq": {
                "name": "LT Debt/Equity",
                "description": "Long-term debt to equity ratio",
                "custom_available": True,
                "custom_format": "Decimal (e.g., u0.3 for under 0.3)",
                "options": {
                    "": "Any",
                    "high": "High (>0.5)",
                    "low": "Low (<0.1)",
                    "u0.3": "Under 0.3",
                    "o0.5": "Over 0.5"
                }
            },
            "fa_debteq": {
                "name": "Debt/Equity",
                "description": "Total debt to equity ratio",
                "custom_available": True,
                "custom_format": "Decimal (e.g., u0.4 for under 0.4)",
                "options": {
                    "": "Any",
                    "high": "High (>0.5)",
                    "low": "Low (<0.1)",
                    "u0.4": "Under 0.4",
                    "o0.6": "Over 0.6"
                }
            },
            "fa_grossmargin": {
                "name": "Gross Margin",
                "description": "Gross profit margin percentage",
                "custom_available": True,
                "custom_format": "Percentage (e.g., o50 for over 50%)",
                "options": {
                    "": "Any",
                    "pos": "Positive (>0%)",
                    "neg": "Negative (<0%)",
                    "high": "High (>50%)",
                    "o20": "Over 20%",
                    "o40": "Over 40%"
                }
            },
            "fa_opermargin": {
                "name": "Operating Margin",
                "description": "Operating profit margin percentage",
                "custom_available": True,
                "custom_format": "Percentage (e.g., o15 for over 15%)",
                "options": {
                    "": "Any",
                    "pos": "Positive (>0%)",
                    "neg": "Negative (<0%)",
                    "high": "High (>25%)",
                    "o10": "Over 10%",
                    "o20": "Over 20%"
                }
            },
            "fa_netmargin": {
                "name": "Net Profit Margin",
                "description": "Net profit margin percentage",
                "custom_available": True,
                "custom_format": "Percentage (e.g., o10 for over 10%)",
                "options": {
                    "": "Any",
                    "pos": "Positive (>0%)",
                    "neg": "Negative (<0%)",
                    "high": "High (>20%)",
                    "o5": "Over 5%",
                    "o15": "Over 15%"
                }
            },
            "sh_insiderown": {
                "name": "Insider Ownership",
                "description": "Percentage owned by company management",
                "custom_available": True,
                "custom_format": "Percentage (e.g., o20 for over 20%)",
                "options": {
                    "": "Any",
                    "low": "Low (<5%)",
                    "high": "High (>30%)",
                    "o10": "Over 10%",
                    "o30": "Over 30%"
                }
            },
            "sh_instown": {
                "name": "Institutional Ownership",
                "description": "Percentage owned by institutions",
                "custom_available": True,
                "custom_format": "Percentage (e.g., o50 for over 50%)",
                "options": {
                    "": "Any",
                    "low": "Low (<5%)",
                    "high": "High (>90%)",
                    "o50": "Over 50%",
                    "u90": "Under 90%"
                }
            },

            # TECHNICAL FILTERS
            "ta_perf": {
                "name": "Performance",
                "description": "Rate of return for various time periods",
                "custom_available": True,
                "custom_format": "Percentage with timeframe (e.g., -2to2-1w for -2% to +2% week)",
                "note": "Supports custom ranges like: 1w (week), 4w (month), 13w (quarter), 26w (half), ytd, 52w (year)"
            },
            "ta_volatility": {
                "name": "Volatility",
                "description": "Average daily high/low trading range",
                "custom_available": True,
                "custom_format": "Percentage with timeframe (e.g., wo5 for week over 5%)",
                "options": {
                    "": "Any",
                    "wo5": "Week - Over 5%",
                    "wo10": "Week - Over 10%",
                    "mo5": "Month - Over 5%",
                    "mo10": "Month - Over 10%"
                }
            },
            "ta_rsi": {
                "name": "RSI (14)",
                "description": "Relative Strength Index",
                "custom_available": True,
                "custom_format": "Number 0-100 (e.g., u30 for under 30, o70 for over 70)",
                "options": {
                    "": "Any",
                    "ob70": "Overbought (70)",
                    "ob80": "Overbought (80)",
                    "os30": "Oversold (30)",
                    "os20": "Oversold (20)"
                }
            },
            "ta_gap": {
                "name": "Gap",
                "description": "Difference between yesterday close and today open",
                "custom_available": True,
                "custom_format": "Percentage (e.g., u5 for gap up 5%, d3 for gap down 3%)",
                "options": {
                    "": "Any",
                    "u": "Up",
                    "d": "Down",
                    "u5": "Up 5%",
                    "d5": "Down 5%"
                }
            },
            "ta_sma20": {
                "name": "20-Day SMA",
                "description": "20-day simple moving average",
                "custom_available": True,
                "options": {
                    "": "Any",
                    "pa": "Price above SMA20",
                    "pb": "Price below SMA20",
                    "cross50a": "SMA20 crossed SMA50 above"
                }
            },
            "ta_sma50": {
                "name": "50-Day SMA",
                "description": "50-day simple moving average",
                "custom_available": True,
                "options": {
                    "": "Any",
                    "pa": "Price above SMA50",
                    "pb": "Price below SMA50",
                    "cross200a": "SMA50 crossed SMA200 above"
                }
            },
            "ta_sma200": {
                "name": "200-Day SMA",
                "description": "200-day simple moving average",
                "custom_available": True,
                "options": {
                    "": "Any",
                    "pa": "Price above SMA200",
                    "pb": "Price below SMA200",
                    "pa20": "Price 20% above SMA200"
                }
            },
            "ta_change": {
                "name": "Change",
                "description": "Change from previous close",
                "custom_available": True,
                "custom_format": "Percentage (e.g., u5 for up 5%, d3 for down 3%)",
                "options": {
                    "": "Any",
                    "u": "Up",
                    "d": "Down",
                    "u5": "Up 5%",
                    "d5": "Down 5%"
                }
            },
            "ta_changeopen": {
                "name": "Change from Open",
                "description": "Change from today's open",
                "custom_available": True,
                "custom_format": "Percentage (e.g., u3 for up 3% from open)",
                "options": {
                    "": "Any",
                    "u": "Up",
                    "d": "Down",
                    "u3": "Up 3%",
                    "d3": "Down 3%"
                }
            },
            "ta_highlow52w": {
                "name": "52-Week High/Low",
                "description": "Distance from 52-week high/low",
                "custom_available": True,
                "custom_format": "Percentage (e.g., nh for new high, b20h for 20% below high)",
                "options": {
                    "": "Any",
                    "nh": "New High",
                    "nl": "New Low",
                    "b10h": "10% below High",
                    "a50h": "50% above Low"
                }
            },
            "ta_beta": {
                "name": "Beta",
                "description": "Stock volatility relative to market",
                "custom_available": True,
                "custom_format": "Decimal (e.g., u1 for under 1.0, o1.5 for over 1.5)",
                "options": {
                    "": "Any",
                    "u1": "Under 1",
                    "o1": "Over 1",
                    "o1.5": "Over 1.5",
                    "u0.5": "Under 0.5"
                }
            },
            "ta_averagetruerange": {
                "name": "Average True Range",
                "description": "Measure of stock volatility",
                "custom_available": True,
                "custom_format": "Decimal (e.g., o1 for over 1.0)",
                "options": {
                    "": "Any",
                    "o1": "Over 1",
                    "o2": "Over 2",
                    "u1": "Under 1",
                    "u0.5": "Under 0.5"
                }
            },
            "ta_pattern": {
                "name": "Chart Pattern",
                "description": "Technical chart patterns",
                "custom_available": True,
                "note": "Elite allows multiple pattern selection",
                "options": {
                    "": "Any",
                    "horizontal": "Horizontal S/R",
                    "tlresistance": "TL Resistance",
                    "tlsupport": "TL Support",
                    "wedgeup": "Wedge Up",
                    "wedgedown": "Wedge Down",
                    "channelup": "Channel Up",
                    "channeldown": "Channel Down",
                    "doubletop": "Double Top",
                    "doublebottom": "Double Bottom",
                    "headandshoulders": "Head & Shoulders"
                }
            },
            "ta_candlestick": {
                "name": "Candlestick Pattern",
                "description": "Candlestick patterns",
                "custom_available": True,
                "note": "Elite allows multiple pattern selection",
                "options": {
                    "": "Any",
                    "h": "Hammer",
                    "ih": "Inverted Hammer",
                    "d": "Doji",
                    "stw": "Spinning Top White",
                    "stb": "Spinning Top Black",
                    "mw": "Marubozu White",
                    "mb": "Marubozu Black"
                }
            }
        }

        # Add summary information
        summary = {
            "total_filters": len(filters_metadata),
            "filters_with_custom": len([f for f in filters_metadata.values() if f.get("custom_available", False)]),
            "categories": {
                "descriptive": len([k for k in filters_metadata.keys() if not k.startswith(("fa_", "ta_"))]),
                "fundamental": len([k for k in filters_metadata.keys() if k.startswith("fa_")]),
                "technical": len([k for k in filters_metadata.keys() if k.startswith("ta_")])
            },
            "custom_format_examples": {
                "ranges": "12to18 (for values between 12 and 18)",
                "over_under": "o15 (over 15), u10 (under 10)",
                "percentages": "5to20 (for 5% to 20%)",
                "time_performance": "-2to2-1w (for -2% to +2% week performance)",
                "multiple_selection": "Use | to combine (e.g., stw|stb for multiple candlestick patterns)"
            }
        }

        result = {
            "filters": filters_metadata,
            "summary": summary
        }

        return self.success_response(result)
