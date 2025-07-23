import os
import logging
from typing import Dict, Any, Optional, List, Tuple
import httpx
from urllib.parse import urlencode
from bs4 import BeautifulSoup
import re
from dotenv import load_dotenv
from agentpress.tool import Tool, ToolResult, openapi_schema, xml_schema
from utils.config import config
from sandbox.tool_base import SandboxToolsBase
from agentpress.thread_manager import ThreadManager


class FinvizClient:
    def __init__(self, email: str = "", password: str = "", use_elite: bool = False, page_row_number: int = 20):
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
        table_params = TableParams(
            order=params.get("order", ""),
            desc=params.get("desc", False),
            signal=params.get("signal", ""),
            filters=list(params.get("filters", {}).values()),
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
    def __init__(self, order: str = "", desc: bool = False, signal: str = "", filters: List[str] = None, page: int = 1, page_row_number: int = 20, tickers: List[str] = None):
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
        load_dotenv()
        email = config.FINVIZ_ELITE_EMAIL
        password = config.FINVIZ_ELITE_PASSWORD
        use_elite: bool = True
        page_row_number: int = 20
        if not email or not password:
            raise ValueError("Finviz email or password is not set")
        self.client = FinvizClient(
            email=email, password=password, use_elite=use_elite, page_row_number=page_row_number)

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "run_screener",
            "description": "Fetch screener table data from Finviz. Returns headers, rows, and pagination info. Params can include order, desc, signal, filters, page, tickers. All fields are optional and can be left empty.",
            "parameters": {
                "type": "object",
                "properties": {
                    "params": {
                        "type": "object",
                        "description": "Screener parameters for Finviz. All fields are optional and can be left empty.",
                        "properties": {
                            "order": {
                                "type": "string",
                                "description": "Column to order by (e.g., 'marketcap', 'price', etc.). Optional."
                            },
                            "desc": {
                                "type": "boolean",
                                "description": "Whether to sort in descending order. Optional."
                            },
                            "signal": {
                                "type": "string",
                                "description": "Signal filter (e.g., 'ta_topgainers'). Optional."
                            },
                            "filters": {
                                "type": "object",
                                "description": "Dictionary of filter keys and values (e.g., {'fs_sh_curvol': 'sh_curvol_o1000'}). Optional. For a full list of supported filter keys and their possible values, call the 'get_available_filters' function of this tool.",
                            },
                            "page": {
                                "type": "integer",
                                "description": "Page number to fetch (1-based). Optional."
                            },
                            "tickers": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of ticker symbols to filter. Optional. example: ['AAPL']"
                            }
                        },
                        "required": []
                    },
                    "fetch_all_pages": {
                        "type": "boolean",
                        "description": "Whether to fetch all pages of results. Optional.",
                        "default": False
                    }
                },
                "required": ["params"]
            }
        }
    })
    @xml_schema(
        tag_name="run-screener",
        mappings=[
            {"param_name": "params", "node_type": "content", "path": "."},
            {"param_name": "fetch_all_pages", "node_type": "attribute", "path": "."}
        ],
        example='''
        <function_calls>
        <invoke name="run_screener">
        <parameter name="params">{"order": "marketcap", "desc": true, "filters": {"fs_sh_curvol": "sh_curvol_o1000", "fs_ta_change": "ta_change_u15", "fs_ind": "ind_stocksonly"}}</parameter>
        <parameter name="fetch_all_pages">true</parameter>
        </invoke>
        </function_calls>
        '''
    )
    async def run_screener(self, params: Dict[str, Any], fetch_all_pages: bool = False) -> ToolResult:
        try:
            result = await self.client.fetch_table(
                params, fetch_all_pages=fetch_all_pages)
            print('result result result', result)
            if not result:
                return self.fail_response("No data returned from Finviz screener.")
            return self.success_response(result)
        except Exception as e:
            return self.fail_response(f"Error fetching screener data: {str(e)}")

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
    @xml_schema(
        tag_name="get-available-filters",
        mappings=[],
        example='''
        <function_calls>
        <invoke name="get_available_filters" />
        </function_calls>
        '''
    )
    async def get_available_filters(self) -> ToolResult:
        """
        Returns all available filter keys and their possible values for the Finviz screener.
        """
        filters_metadata = {
            # Basic Information
            "exch": {
                "name": "Exchange",
                "description": "Stock Exchange at which a stock is listed",
                "options": {
                    "": "Any",
                    "amex": "AMEX",
                    "cboe": "CBOE",
                    "nasd": "NASDAQ",
                    "nyse": "NYSE",
                    "modal": "Custom"
                }
            },
            "idx": {
                "name": "Index",
                "description": "A major index membership of a stock",
                "options": {
                    "": "Any",
                    "sp500": "S&P 500",
                    "ndx": "NASDAQ 100",
                    "dji": "DJIA",
                    "rut": "RUSSELL 2000",
                    "modal": "Custom"
                }
            },
            "sec": {
                "name": "Sector",
                "description": "The sector which a stock belongs to",
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
                    "u40": "Under $40",
                    "u50": "Under $50",
                    "o1": "Over $1",
                    "o2": "Over $2",
                    "o3": "Over $3",
                    "o4": "Over $4",
                    "o5": "Over $5",
                    "o7": "Over $7",
                    "o10": "Over $10",
                    "o15": "Over $15",
                    "o20": "Over $20",
                    "o30": "Over $30",
                    "o40": "Over $40",
                    "o50": "Over $50",
                    "o60": "Over $60",
                    "o70": "Over $70",
                    "o80": "Over $80",
                    "o90": "Over $90",
                    "o100": "Over $100",
                    "1to5": "$1 to $5",
                    "1to10": "$1 to $10",
                    "1to20": "$1 to $20",
                    "5to10": "$5 to $10",
                    "5to20": "$5 to $20",
                    "5to50": "$5 to $50",
                    "10to20": "$10 to $20",
                    "10to50": "$10 to $50",
                    "20to50": "$20 to $50",
                    "50to100": "$50 to $100",
                    "frange": "Custom"
                }
            },
            "targetprice": {
                "name": "Target Price",
                "description": "Analysts' mean target price",
                "options": {
                    "": "Any",
                    "a50": "50% Above Price",
                    "a40": "40% Above Price",
                    "a30": "30% Above Price",
                    "a20": "20% Above Price",
                    "a10": "10% Above Price",
                    "a5": "5% Above Price",
                    "above": "Above Price",
                    "below": "Below Price",
                    "b5": "5% Below Price",
                    "b10": "10% Below Price",
                    "b20": "20% Below Price",
                    "b30": "30% Below Price",
                    "b40": "40% Below Price",
                    "b50": "50% Below Price",
                    "modal": "Custom"
                }
            },

            # Company Information
            "ipodate": {
                "name": "IPO Date",
                "description": "Date when company had an IPO",
                "options": {
                    "": "Any",
                    "today": "Today",
                    "yesterday": "Yesterday",
                    "prevweek": "In the last week",
                    "prevmonth": "In the last month",
                    "prevquarter": "In the last quarter",
                    "prevyear": "In the last year",
                    "prev2yrs": "In the last 2 years",
                    "prev3yrs": "In the last 3 years",
                    "prev5yrs": "In the last 5 years",
                    "more1": "More than a year ago",
                    "more5": "More than 5 years ago",
                    "more10": "More than 10 years ago",
                    "more15": "More than 15 years ago",
                    "more20": "More than 20 years ago",
                    "more25": "More than 25 years ago",
                    "modal": "Custom"
                }
            },
            "sh_outstanding": {
                "name": "Shares Outstanding",
                "description": "Shares outstanding represent the total number of shares issued by a corporation and held by its shareholders",
                "options": {
                    "": "Any",
                    "u1": "Under 1M",
                    "u5": "Under 5M",
                    "u10": "Under 10M",
                    "u20": "Under 20M",
                    "u50": "Under 50M",
                    "u100": "Under 100M",
                    "o1": "Over 1M",
                    "o2": "Over 2M",
                    "o5": "Over 5M",
                    "o10": "Over 10M",
                    "o20": "Over 20M",
                    "o50": "Over 50M",
                    "o100": "Over 100M",
                    "o200": "Over 200M",
                    "o500": "Over 500M",
                    "o1000": "Over 1000M",
                    "frange": "Custom"
                }
            },
            "sh_float": {
                "name": "Float",
                "description": "Float is the number of stock shares that are available for trading to the public. This doesn't include shares held by insiders",
                "options": {
                    "": "Any",
                    "u1": "Under 1M shares",
                    "u5": "Under 5M shares",
                    "u10": "Under 10M shares",
                    "u20": "Under 20M shares",
                    "u50": "Under 50M shares",
                    "u100": "Under 100M shares",
                    "o1": "Over 1M shares",
                    "o2": "Over 2M shares",
                    "o5": "Over 5M shares",
                    "o10": "Over 10M shares",
                    "o20": "Over 20M shares",
                    "o50": "Over 50M shares",
                    "o100": "Over 100M shares",
                    "o200": "Over 200M shares",
                    "o500": "Over 500M shares",
                    "o1000": "Over 1000M shares",
                    "u10p": "Under 10% of outstanding",
                    "u20p": "Under 20% of outstanding",
                    "u30p": "Under 30% of outstanding",
                    "u40p": "Under 40% of outstanding",
                    "u50p": "Under 50% of outstanding",
                    "u60p": "Under 60% of outstanding",
                    "u70p": "Under 70% of outstanding",
                    "u80p": "Under 80% of outstanding",
                    "u90p": "Under 90% of outstanding",
                    "o10p": "Over 10% of outstanding",
                    "o20p": "Over 20% of outstanding",
                    "o30p": "Over 30% of outstanding",
                    "o40p": "Over 40% of outstanding",
                    "o50p": "Over 50% of outstanding",
                    "o60p": "Over 60% of outstanding",
                    "o70p": "Over 70% of outstanding",
                    "o80p": "Over 80% of outstanding",
                    "o90p": "Over 90% of outstanding",
                    "modal": "Custom"
                }
            },

            # After-Hours Trading
            "ah_close": {
                "name": "After-Hours Close",
                "description": "The current stock price in after-hours trading",
                "options": {
                    "": "Any",
                    "u1": "Under $1",
                    "u2": "Under $2",
                    "u3": "Under $3",
                    "u4": "Under $4",
                    "u5": "Under $5",
                    "u7": "Under $7",
                    "u10": "Under $10",
                    "u15": "Under $15",
                    "u20": "Under $20",
                    "u30": "Under $30",
                    "u40": "Under $40",
                    "u50": "Under $50",
                    "o1": "Over $1",
                    "o2": "Over $2",
                    "o3": "Over $3",
                    "o4": "Over $4",
                    "o5": "Over $5",
                    "o7": "Over $7",
                    "o10": "Over $10",
                    "o15": "Over $15",
                    "o20": "Over $20",
                    "o30": "Over $30",
                    "o40": "Over $40",
                    "o50": "Over $50",
                    "o60": "Over $60",
                    "o70": "Over $70",
                    "o80": "Over $80",
                    "o90": "Over $90",
                    "o100": "Over $100",
                    "1to5": "$1 to $5",
                    "1to10": "$1 to $10",
                    "1to20": "$1 to $20",
                    "5to10": "$5 to $10",
                    "5to20": "$5 to $20",
                    "5to50": "$5 to $50",
                    "10to20": "$10 to $20",
                    "10to50": "$10 to $50",
                    "20to50": "$20 to $50",
                    "50to100": "$50 to $100",
                    "frange": "Custom"
                }
            },
            "ah_change": {
                "name": "After-Hours Change",
                "description": "The difference between regular trading close price and after-hours price",
                "options": {
                    "": "Any",
                    "u": "Up",
                    "u1": "Up 1%",
                    "u2": "Up 2%",
                    "u3": "Up 3%",
                    "u4": "Up 4%",
                    "u5": "Up 5%",
                    "u6": "Up 6%",
                    "u7": "Up 7%",
                    "u8": "Up 8%",
                    "u9": "Up 9%",
                    "u10": "Up 10%",
                    "u15": "Up 15%",
                    "u20": "Up 20%",
                    "d": "Down",
                    "d1": "Down 1%",
                    "d2": "Down 2%",
                    "d3": "Down 3%",
                    "d4": "Down 4%",
                    "d5": "Down 5%",
                    "d6": "Down 6%",
                    "d7": "Down 7%",
                    "d8": "Down 8%",
                    "d9": "Down 9%",
                    "d10": "Down 10%",
                    "d15": "Down 15%",
                    "d20": "Down 20%",
                    "frange": "Custom"
                }
            },

            # News Filters
            "news_date": {
                "name": "Latest News",
                "description": "Date of the latest reported news",
                "options": {
                    "": "Any",
                    "today": "Today",
                    "todayafter": "Aftermarket Today",
                    "sinceyesterday": "Since Yesterday",
                    "sinceyesterdayafter": "Since the Aftermarket Yesterday",
                    "yesterday": "Yesterday",
                    "yesterdayafter": "In the Aftermarket Yesterday",
                    "prevminutes5": "In the last 5 minutes",
                    "prevminutes30": "In the last 30 minutes",
                    "prevhours1": "In the last hour",
                    "prevhours24": "In the last 24 hours",
                    "prevdays7": "In the last 7 days",
                    "prevmonth": "In the last month",
                    "modal": "Custom"
                }
            },
            "news_keywords": {
                "name": "News Keywords",
                "description": "Article titles from the last 24 hours that contain ANY of the specified keywords",
                "type": "text_input",
                "options": None  # This is a text input field, not a dropdown
            },
            "ind": {
                "name": "Industry",
                "description": "The industry which a stock belongs to",
                "options": {
                    "": "Any",
                    "stocksonly": "Stocks only (ex-Funds)",
                    "exchangetradedfund": "Exchange Traded Fund",
                    "advertisingagencies": "Advertising Agencies",
                    "aerospacedefense": "Aerospace & Defense",
                    "agriculturalinputs": "Agricultural Inputs",
                    "airlines": "Airlines",
                    "airportsairservices": "Airports & Air Services",
                    "aluminum": "Aluminum",
                    "apparelmanufacturing": "Apparel Manufacturing",
                    "apparelretail": "Apparel Retail",
                    "assetmanagement": "Asset Management",
                    "automanufacturers": "Auto Manufacturers",
                    "autoparts": "Auto Parts",
                    "autotruckdealerships": "Auto & Truck Dealerships",
                    "banksdiversified": "Banks - Diversified",
                    "banksregional": "Banks - Regional",
                    "beveragesbrewers": "Beverages - Brewers",
                    "beveragesnonalcoholic": "Beverages - Non-Alcoholic",
                    "beverageswineriesdistilleries": "Beverages - Wineries & Distilleries",
                    "biotechnology": "Biotechnology",
                    "broadcasting": "Broadcasting",
                    "buildingmaterials": "Building Materials",
                    "buildingproductsequipment": "Building Products & Equipment",
                    "businessequipmentsupplies": "Business Equipment & Supplies",
                    "capitalmarkets": "Capital Markets",
                    "chemicals": "Chemicals",
                    "closedendfunddebt": "Closed-End Fund - Debt",
                    "closedendfundequity": "Closed-End Fund - Equity",
                    "closedendfundforeign": "Closed-End Fund - Foreign",
                    "cokingcoal": "Coking Coal",
                    "communicationequipment": "Communication Equipment",
                    "computerhardware": "Computer Hardware",
                    "confectioners": "Confectioners",
                    "conglomerates": "Conglomerates",
                    "consultingservices": "Consulting Services",
                    "consumerelectronics": "Consumer Electronics",
                    "copper": "Copper",
                    "creditservices": "Credit Services",
                    "departmentstores": "Department Stores",
                    "diagnosticsresearch": "Diagnostics & Research",
                    "discountstores": "Discount Stores",
                    "drugmanufacturersgeneral": "Drug Manufacturers - General",
                    "drugmanufacturersspecialtygeneric": "Drug Manufacturers - Specialty & Generic",
                    "educationtrainingservices": "Education & Training Services",
                    "electricalequipmentparts": "Electrical Equipment & Parts",
                    "electroniccomponents": "Electronic Components",
                    "electronicgamingmultimedia": "Electronic Gaming & Multimedia",
                    "electronicscomputerdistribution": "Electronics & Computer Distribution",
                    "engineeringconstruction": "Engineering & Construction",
                    "entertainment": "Entertainment",
                    "farmheavyconstructionmachinery": "Farm & Heavy Construction Machinery",
                    "farmproducts": "Farm Products",
                    "financialconglomerates": "Financial Conglomerates",
                    "financialdatastockexchanges": "Financial Data & Stock Exchanges",
                    "fooddistribution": "Food Distribution",
                    "footwearaccessories": "Footwear & Accessories",
                    "furnishingsfixturesappliances": "Furnishings, Fixtures & Appliances",
                    "gambling": "Gambling",
                    "gold": "Gold",
                    "grocerystores": "Grocery Stores",
                    "healthcareplans": "Healthcare Plans",
                    "healthinformationservices": "Health Information Services",
                    "homeimprovementretail": "Home Improvement Retail",
                    "householdpersonalproducts": "Household & Personal Products",
                    "industrialdistribution": "Industrial Distribution",
                    "informationtechnologyservices": "Information Technology Services",
                    "infrastructureoperations": "Infrastructure Operations",
                    "insurancebrokers": "Insurance Brokers",
                    "insurancediversified": "Insurance - Diversified",
                    "insurancelife": "Insurance - Life",
                    "insurancepropertycasualty": "Insurance - Property & Casualty",
                    "insurancereinsurance": "Insurance - Reinsurance",
                    "insurancespecialty": "Insurance - Specialty",
                    "integratedfreightlogistics": "Integrated Freight & Logistics",
                    "internetcontentinformation": "Internet Content & Information",
                    "internetretail": "Internet Retail",
                    "leisure": "Leisure",
                    "lodging": "Lodging",
                    "lumberwoodproduction": "Lumber & Wood Production",
                    "luxurygoods": "Luxury Goods",
                    "marineshipping": "Marine Shipping",
                    "medicalcarefacilities": "Medical Care Facilities",
                    "medicaldevices": "Medical Devices",
                    "medicaldistribution": "Medical Distribution",
                    "medicalinstrumentssupplies": "Medical Instruments & Supplies",
                    "metalfabrication": "Metal Fabrication",
                    "mortgagefinance": "Mortgage Finance",
                    "oilgasdrilling": "Oil & Gas Drilling",
                    "oilgasep": "Oil & Gas E&P",
                    "oilgasequipmentservices": "Oil & Gas Equipment & Services",
                    "oilgasintegrated": "Oil & Gas Integrated",
                    "oilgasmidstream": "Oil & Gas Midstream",
                    "oilgasrefiningmarketing": "Oil & Gas Refining & Marketing",
                    "otherindustrialmetalsmining": "Other Industrial Metals & Mining",
                    "otherpreciousmetalsmining": "Other Precious Metals & Mining",
                    "packagedfoods": "Packaged Foods",
                    "packagingcontainers": "Packaging & Containers",
                    "paperpaperproducts": "Paper & Paper Products",
                    "personalservices": "Personal Services",
                    "pharmaceuticalretailers": "Pharmaceutical Retailers",
                    "pollutiontreatmentcontrols": "Pollution & Treatment Controls",
                    "publishing": "Publishing",
                    "railroads": "Railroads",
                    "realestatedevelopment": "Real Estate - Development",
                    "realestatediversified": "Real Estate - Diversified",
                    "realestateservices": "Real Estate Services",
                    "recreationalvehicles": "Recreational Vehicles",
                    "reitdiversified": "REIT - Diversified",
                    "reithealthcarefacilities": "REIT - Healthcare Facilities",
                    "reithotelmotel": "REIT - Hotel & Motel",
                    "reitindustrial": "REIT - Industrial",
                    "reitmortgage": "REIT - Mortgage",
                    "reitoffice": "REIT - Office",
                    "reitresidential": "REIT - Residential",
                    "reitretail": "REIT - Retail",
                    "reitspecialty": "REIT - Specialty",
                    "rentalleasingservices": "Rental & Leasing Services",
                    "residentialconstruction": "Residential Construction",
                    "resortscasinos": "Resorts & Casinos",
                    "restaurants": "Restaurants",
                    "scientifictechnicalinstruments": "Scientific & Technical Instruments",
                    "securityprotectionservices": "Security & Protection Services",
                    "semiconductorequipmentmaterials": "Semiconductor Equipment & Materials",
                    "semiconductors": "Semiconductors",
                    "shellcompanies": "Shell Companies",
                    "silver": "Silver",
                    "softwareapplication": "Software - Application",
                    "softwareinfrastructure": "Software - Infrastructure",
                    "solar": "Solar",
                    "specialtybusinessservices": "Specialty Business Services",
                    "specialtychemicals": "Specialty Chemicals",
                    "specialtyindustrialmachinery": "Specialty Industrial Machinery",
                    "specialtyretail": "Specialty Retail",
                    "staffingemploymentservices": "Staffing & Employment Services",
                    "steel": "Steel",
                    "telecomservices": "Telecom Services",
                    "textilemanufacturing": "Textile Manufacturing",
                    "thermalcoal": "Thermal Coal",
                    "tobacco": "Tobacco",
                    "toolsaccessories": "Tools & Accessories",
                    "travelservices": "Travel Services",
                    "trucking": "Trucking",
                    "uranium": "Uranium",
                    "utilitiesdiversified": "Utilities - Diversified",
                    "utilitiesindependentpowerproducers": "Utilities - Independent Power Producers",
                    "utilitiesregulatedelectric": "Utilities - Regulated Electric",
                    "utilitiesregulatedgas": "Utilities - Regulated Gas",
                    "utilitiesregulatedwater": "Utilities - Regulated Water",
                    "utilitiesrenewable": "Utilities - Renewable",
                    "wastemanagement": "Waste Management",
                    "modal": "Custom"
                }
            },
            "geo": {
                "name": "Country",
                "description": "The country where company of selected stock is based",
                "options": {
                    "": "Any",
                    "usa": "USA",
                    "notusa": "Foreign (ex-USA)",
                    "asia": "Asia",
                    "europe": "Europe",
                    "latinamerica": "Latin America",
                    "bric": "BRIC",
                    "argentina": "Argentina",
                    "australia": "Australia",
                    "bahamas": "Bahamas",
                    "belgium": "Belgium",
                    "benelux": "BeNeLux",
                    "bermuda": "Bermuda",
                    "brazil": "Brazil",
                    "canada": "Canada",
                    "caymanislands": "Cayman Islands",
                    "chile": "Chile",
                    "china": "China",
                    "chinahongkong": "China & Hong Kong",
                    "colombia": "Colombia",
                    "cyprus": "Cyprus",
                    "denmark": "Denmark",
                    "finland": "Finland",
                    "france": "France",
                    "germany": "Germany",
                    "greece": "Greece",
                    "hongkong": "Hong Kong",
                    "hungary": "Hungary",
                    "iceland": "Iceland",
                    "india": "India",
                    "indonesia": "Indonesia",
                    "ireland": "Ireland",
                    "israel": "Israel",
                    "italy": "Italy",
                    "japan": "Japan",
                    "jordan": "Jordan",
                    "kazakhstan": "Kazakhstan",
                    "luxembourg": "Luxembourg",
                    "malaysia": "Malaysia",
                    "malta": "Malta",
                    "mexico": "Mexico",
                    "monaco": "Monaco",
                    "netherlands": "Netherlands",
                    "newzealand": "New Zealand",
                    "norway": "Norway",
                    "panama": "Panama",
                    "peru": "Peru",
                    "philippines": "Philippines",
                    "portugal": "Portugal",
                    "russia": "Russia",
                    "singapore": "Singapore",
                    "southafrica": "South Africa",
                    "southkorea": "South Korea",
                    "spain": "Spain",
                    "sweden": "Sweden",
                    "switzerland": "Switzerland",
                    "taiwan": "Taiwan",
                    "thailand": "Thailand",
                    "turkey": "Turkey",
                    "unitedarabemirates": "United Arab Emirates",
                    "unitedkingdom": "United Kingdom",
                    "uruguay": "Uruguay",
                    "vietnam": "Vietnam",
                    "modal": "Custom"
                }
            },

            # Market Capitalization & Valuation
            "cap": {
                "name": "Market Capitalization",
                "description": "The total dollar market value of all of a company's outstanding shares",
                "options": {
                    "": "Any",
                    "mega": "Mega ($200bln and more)",
                    "large": "Large ($10bln to $200bln)",
                    "mid": "Mid ($2bln to $10bln)",
                    "small": "Small ($300mln to $2bln)",
                    "micro": "Micro ($50mln to $300mln)",
                    "nano": "Nano (under $50mln)",
                    "largeover": "+Large (over $10bln)",
                    "midover": "+Mid (over $2bln)",
                    "smallover": "+Small (over $300mln)",
                    "microover": "+Micro (over $50mln)",
                    "largeunder": "-Large (under $200bln)",
                    "midunder": "-Mid (under $10bln)",
                    "smallunder": "-Small (under $2bln)",
                    "microunder": "-Micro (under $300mln)",
                    "frange": "Custom"
                }
            },
            "fa_pe": {
                "name": "Price-to-Earnings Ratio",
                "description": "A valuation ratio of a company's current share price compared to its per-share earnings (ttm)",
                "options": {
                    "": "Any",
                    "low": "Low (<15)",
                    "profitable": "Profitable (>0)",
                    "high": "High (>50)",
                    "u5": "Under 5",
                    "u10": "Under 10",
                    "u15": "Under 15",
                    "u20": "Under 20",
                    "u25": "Under 25",
                    "u30": "Under 30",
                    "u35": "Under 35",
                    "u40": "Under 40",
                    "u45": "Under 45",
                    "u50": "Under 50",
                    "o5": "Over 5",
                    "o10": "Over 10",
                    "o15": "Over 15",
                    "o20": "Over 20",
                    "o25": "Over 25",
                    "o30": "Over 30",
                    "o35": "Over 35",
                    "o40": "Over 40",
                    "o45": "Over 45",
                    "o50": "Over 50",
                    "frange": "Custom"
                }
            },
            "fa_fpe": {
                "name": "Forward Price-to-Earnings Ratio",
                "description": "A measure of the price-to-earnings ratio using forecasted earnings for the P/E calculation. Value for next fiscal year",
                "options": {
                    "": "Any",
                    "low": "Low (<15)",
                    "profitable": "Profitable (>0)",
                    "high": "High (>50)",
                    "u5": "Under 5",
                    "u10": "Under 10",
                    "u15": "Under 15",
                    "u20": "Under 20",
                    "u25": "Under 25",
                    "u30": "Under 30",
                    "u35": "Under 35",
                    "u40": "Under 40",
                    "u45": "Under 45",
                    "u50": "Under 50",
                    "o5": "Over 5",
                    "o10": "Over 10",
                    "o15": "Over 15",
                    "o20": "Over 20",
                    "o25": "Over 25",
                    "o30": "Over 30",
                    "o35": "Over 35",
                    "o40": "Over 40",
                    "o45": "Over 45",
                    "o50": "Over 50",
                    "frange": "Custom"
                }
            },
            "fa_peg": {
                "name": "Price-to-Earnings-to-Growth",
                "description": "A ratio used to determine a stock's value while taking into account earnings growth",
                "options": {
                    "": "Any",
                    "low": "Low (<1)",
                    "high": "High (>2)",
                    "u1": "Under 1",
                    "u2": "Under 2",
                    "u3": "Under 3",
                    "o1": "Over 1",
                    "o2": "Over 2",
                    "o3": "Over 3",
                    "frange": "Custom"
                }
            },
            "fa_ps": {
                "name": "Price-to-Sales Ratio",
                "description": "P/S number reflects the value placed on sales by the market. It is calculated by dividing the current closing price of the stock by the dollar-sales value per share",
                "options": {
                    "": "Any",
                    "low": "Low (<1)",
                    "high": "High (>10)",
                    "u1": "Under 1",
                    "u2": "Under 2",
                    "u3": "Under 3",
                    "u4": "Under 4",
                    "u5": "Under 5",
                    "u6": "Under 6",
                    "u7": "Under 7",
                    "u8": "Under 8",
                    "u9": "Under 9",
                    "u10": "Under 10",
                    "o1": "Over 1",
                    "o2": "Over 2",
                    "o3": "Over 3",
                    "o4": "Over 4",
                    "o5": "Over 5",
                    "o6": "Over 6",
                    "o7": "Over 7",
                    "o8": "Over 8",
                    "o9": "Over 9",
                    "o10": "Over 10",
                    "frange": "Custom"
                }
            },
            "fa_pb": {
                "name": "Price-to-Book Ratio",
                "description": "A ratio used to compare a stock's market value to its book value. It is calculated by dividing the current closing price of the stock by the latest quarter's book value per share",
                "options": {
                    "": "Any",
                    "low": "Low (<1)",
                    "high": "High (>5)",
                    "u1": "Under 1",
                    "u2": "Under 2",
                    "u3": "Under 3",
                    "u4": "Under 4",
                    "u5": "Under 5",
                    "u6": "Under 6",
                    "u7": "Under 7",
                    "u8": "Under 8",
                    "u9": "Under 9",
                    "u10": "Under 10",
                    "o1": "Over 1",
                    "o2": "Over 2",
                    "o3": "Over 3",
                    "o4": "Over 4",
                    "o5": "Over 5",
                    "o6": "Over 6",
                    "o7": "Over 7",
                    "o8": "Over 8",
                    "o9": "Over 9",
                    "o10": "Over 10",
                    "frange": "Custom"
                }
            },
            "fa_pc": {
                "name": "Price-to-Cash Ratio",
                "description": "A ratio used to compare a stock's market value to its cash assets. It is calculated by dividing the current closing price of the stock by the latest quarter's cash per share",
                "options": {
                    "": "Any",
                    "low": "Low (<3)",
                    "high": "High (>50)",
                    "u1": "Under 1",
                    "u2": "Under 2",
                    "u3": "Under 3",
                    "u4": "Under 4",
                    "u5": "Under 5",
                    "u6": "Under 6",
                    "u7": "Under 7",
                    "u8": "Under 8",
                    "u9": "Under 9",
                    "u10": "Under 10",
                    "o1": "Over 1",
                    "o2": "Over 2",
                    "o3": "Over 3",
                    "o4": "Over 4",
                    "o5": "Over 5",
                    "o6": "Over 6",
                    "o7": "Over 7",
                    "o8": "Over 8",
                    "o9": "Over 9",
                    "o10": "Over 10",
                    "o20": "Over 20",
                    "o30": "Over 30",
                    "o40": "Over 40",
                    "o50": "Over 50",
                    "frange": "Custom"
                }
            },
            "fa_pfcf": {
                "name": "Price to Free Cash Flow (ttm)",
                "description": "A valuation metric that compares a company's market price to its level of annual free cash flow",
                "options": {
                    "": "Any",
                    "low": "Low (<15)",
                    "high": "High (>50)",
                    "u5": "Under 5",
                    "u10": "Under 10",
                    "u15": "Under 15",
                    "u20": "Under 20",
                    "u25": "Under 25",
                    "u30": "Under 30",
                    "u35": "Under 35",
                    "u40": "Under 40",
                    "u45": "Under 45",
                    "u50": "Under 50",
                    "u60": "Under 60",
                    "u70": "Under 70",
                    "u80": "Under 80",
                    "u90": "Under 90",
                    "u100": "Under 100",
                    "o5": "Over 5",
                    "o10": "Over 10",
                    "o15": "Over 15",
                    "o20": "Over 20",
                    "o25": "Over 25",
                    "o30": "Over 30",
                    "o35": "Over 35",
                    "o40": "Over 40",
                    "o45": "Over 45",
                    "o50": "Over 50",
                    "o60": "Over 60",
                    "o70": "Over 70",
                    "o80": "Over 80",
                    "o90": "Over 90",
                    "o100": "Over 100",
                    "frange": "Custom"
                }
            },
            "fa_evebitda": {
                "name": "Enterprise Value to EBITDA",
                "description": "Total value of a company, including debt and excluding cash divided by Earnings Before Interest, Taxes, Depreciation, and Amortization (ttm)",
                "options": {
                    "": "Any",
                    "negative": "Negative (<0)",
                    "low": "Low (<15)",
                    "profitable": "Profitable (>0)",
                    "high": "High (>50)",
                    "u5": "Under 5",
                    "u10": "Under 10",
                    "u15": "Under 15",
                    "u20": "Under 20",
                    "u25": "Under 25",
                    "u30": "Under 30",
                    "u35": "Under 35",
                    "u40": "Under 40",
                    "u45": "Under 45",
                    "u50": "Under 50",
                    "o5": "Over 5",
                    "o10": "Over 10",
                    "o15": "Over 15",
                    "o20": "Over 20",
                    "o25": "Over 25",
                    "o30": "Over 30",
                    "o35": "Over 35",
                    "o40": "Over 40",
                    "o45": "Over 45",
                    "o50": "Over 50",
                    "frange": "Custom"
                }
            },
            "fa_evsales": {
                "name": "Enterprise Value to Sales",
                "description": "Total value of a company, including debt and excluding cash divided by Revenue (ttm)",
                "options": {
                    "": "Any",
                    "negative": "Negative (<0)",
                    "low": "Low (<1)",
                    "positive": "Positive (>0)",
                    "high": "High (>10)",
                    "u1": "Under 1",
                    "u2": "Under 2",
                    "u3": "Under 3",
                    "u4": "Under 4",
                    "u5": "Under 5",
                    "u6": "Under 6",
                    "u7": "Under 7",
                    "u8": "Under 8",
                    "u9": "Under 9",
                    "u10": "Under 10",
                    "o1": "Over 1",
                    "o2": "Over 2",
                    "o3": "Over 3",
                    "o4": "Over 4",
                    "o5": "Over 5",
                    "o6": "Over 6",
                    "o7": "Over 7",
                    "o8": "Over 8",
                    "o9": "Over 9",
                    "o10": "Over 10",
                    "frange": "Custom"
                }
            },


            # Dividend Metrics
            "fa_divgrowth": {
                "name": "Dividend Growth",
                "description": "Annualized dividend growth over 1, 3, or 5 years or consecutive years of growth",
                "options": {
                    "": "Any",
                    "1ypos": "1 Year Positive",
                    "1yo5": "1 Year Over 5%",
                    "1yo10": "1 Year Over 10%",
                    "1yo15": "1 Year Over 15%",
                    "1yo20": "1 Year Over 20%",
                    "1yo25": "1 Year Over 25%",
                    "1yo30": "1 Year Over 30%",
                    "3ypos": "3 Years Positive",
                    "3yo5": "3 Years Over 5%",
                    "3yo10": "3 Years Over 10%",
                    "3yo15": "3 Years Over 15%",
                    "3yo20": "3 Years Over 20%",
                    "3yo25": "3 Years Over 25%",
                    "3yo30": "3 Years Over 30%",
                    "5ypos": "5 Years Positive",
                    "5yo5": "5 Years Over 5%",
                    "5yo10": "5 Years Over 10%",
                    "5yo15": "5 Years Over 15%",
                    "5yo20": "5 Years Over 20%",
                    "5yo25": "5 Years Over 25%",
                    "5yo30": "5 Years Over 30%",
                    "cy1": "Growing 1+ Year",
                    "cy2": "Growing 2+ Years",
                    "cy3": "Growing 3+ Years",
                    "cy4": "Growing 4+ Years",
                    "cy5": "Growing 5+ Years",
                    "cy6": "Growing 6+ Years",
                    "cy7": "Growing 7+ Years",
                    "cy8": "Growing 8+ Years",
                    "cy9": "Growing 9+ Years",
                    "modal": "Custom"
                }
            },
            "fa_div": {
                "name": "Dividend Yield",
                "description": "The dividend yield equals the annual dividend per share divided by the stock's price. This measurement tells what percentage return a company pays out to shareholders in the form of dividends. If there is no forward dividend estimate available, trailing twelve month (TTM) value is used",
                "options": {
                    "": "Any",
                    "none": "None (0%)",
                    "pos": "Positive (>0%)",
                    "high": "High (>5%)",
                    "veryhigh": "Very High (>10%)",
                    "o1": "Over 1%",
                    "o2": "Over 2%",
                    "o3": "Over 3%",
                    "o4": "Over 4%",
                    "o5": "Over 5%",
                    "o6": "Over 6%",
                    "o7": "Over 7%",
                    "o8": "Over 8%",
                    "o9": "Over 9%",
                    "o10": "Over 10%",
                    "frange": "Custom"
                }
            },
            "fa_payoutratio": {
                "name": "Payout Ratio",
                "description": "The percentage of earnings paid to shareholders in dividends",
                "options": {
                    "": "Any",
                    "none": "None (0%)",
                    "pos": "Positive (>0%)",
                    "low": "Low (<20%)",
                    "high": "High (>50%)",
                    "o0": "Over 0%",
                    "o10": "Over 10%",
                    "o20": "Over 20%",
                    "o30": "Over 30%",
                    "o40": "Over 40%",
                    "o50": "Over 50%",
                    "o60": "Over 60%",
                    "o70": "Over 70%",
                    "o80": "Over 80%",
                    "o90": "Over 90%",
                    "o100": "Over 100%",
                    "u10": "Under 10%",
                    "u20": "Under 20%",
                    "u30": "Under 30%",
                    "u40": "Under 40%",
                    "u50": "Under 50%",
                    "u60": "Under 60%",
                    "u70": "Under 70%",
                    "u80": "Under 80%",
                    "u90": "Under 90%",
                    "u100": "Under 100%",
                    "frange": "Custom"
                }
            },

            # Growth Metrics
            "fa_epsyoy": {
                "name": "EPS Growth This Year",
                "description": "EPS is the portion of a company's profit allocated to each outstanding share of common stock. EPS serves as an indicator of a company's profitability. Value for current fiscal year",
                "options": {
                    "": "Any",
                    "neg": "Negative (<0%)",
                    "pos": "Positive (>0%)",
                    "poslow": "Positive Low (0-10%)",
                    "high": "High (>25%)",
                    "u5": "Under 5%",
                    "u10": "Under 10%",
                    "u15": "Under 15%",
                    "u20": "Under 20%",
                    "u25": "Under 25%",
                    "u30": "Under 30%",
                    "o5": "Over 5%",
                    "o10": "Over 10%",
                    "o15": "Over 15%",
                    "o20": "Over 20%",
                    "o25": "Over 25%",
                    "o30": "Over 30%",
                    "frange": "Custom"
                }
            },
            "fa_epsyoy1": {
                "name": "EPS Growth Next Year",
                "description": "EPS is the portion of a company's profit allocated to each outstanding share of common stock. EPS serves as an indicator of a company's profitability. Estimate for next fiscal year",
                "options": {
                    "": "Any",
                    "neg": "Negative (<0%)",
                    "pos": "Positive (>0%)",
                    "poslow": "Positive Low (0-10%)",
                    "high": "High (>25%)",
                    "u5": "Under 5%",
                    "u10": "Under 10%",
                    "u15": "Under 15%",
                    "u20": "Under 20%",
                    "u25": "Under 25%",
                    "u30": "Under 30%",
                    "o5": "Over 5%",
                    "o10": "Over 10%",
                    "o15": "Over 15%",
                    "o20": "Over 20%",
                    "o25": "Over 25%",
                    "o30": "Over 30%",
                    "frange": "Custom"
                }
            },
            "fa_epsqoq": {
                "name": "EPS Growth Qtr Over Qtr",
                "description": "EPS is the portion of a company's profit allocated to each outstanding share of common stock. EPS serves as an indicator of a company's profitability. Quarter over quarter growth",
                "options": {
                    "": "Any",
                    "neg": "Negative (<0%)",
                    "pos": "Positive (>0%)",
                    "poslow": "Positive Low (0-10%)",
                    "high": "High (>25%)",
                    "u5": "Under 5%",
                    "u10": "Under 10%",
                    "u15": "Under 15%",
                    "u20": "Under 20%",
                    "u25": "Under 25%",
                    "u30": "Under 30%",
                    "o5": "Over 5%",
                    "o10": "Over 10%",
                    "o15": "Over 15%",
                    "o20": "Over 20%",
                    "o25": "Over 25%",
                    "o30": "Over 30%",
                    "frange": "Custom"
                }
            },
            "fa_epsyoyttm": {
                "name": "EPS Growth TTM",
                "description": "Trailing twelve months growth compared on the previous audited financial year",
                "options": {
                    "": "Any",
                    "neg": "Negative (<0%)",
                    "pos": "Positive (>0%)",
                    "poslow": "Positive Low (0-10%)",
                    "high": "High (>25%)",
                    "u5": "Under 5%",
                    "u10": "Under 10%",
                    "u15": "Under 15%",
                    "u20": "Under 20%",
                    "u25": "Under 25%",
                    "u30": "Under 30%",
                    "o5": "Over 5%",
                    "o10": "Over 10%",
                    "o15": "Over 15%",
                    "o20": "Over 20%",
                    "o25": "Over 25%",
                    "o30": "Over 30%",
                    "frange": "Custom"
                }
            },
            "fa_eps3years": {
                "name": "EPS Growth Past 3 Years",
                "description": "EPS is the portion of a company's profit allocated to each outstanding share of common stock. EPS serves as an indicator of a company's profitability. Annual growth over past 3 years",
                "options": {
                    "": "Any",
                    "neg": "Negative (<0%)",
                    "pos": "Positive (>0%)",
                    "poslow": "Positive Low (0-10%)",
                    "high": "High (>25%)",
                    "u5": "Under 5%",
                    "u10": "Under 10%",
                    "u15": "Under 15%",
                    "u20": "Under 20%",
                    "u25": "Under 25%",
                    "u30": "Under 30%",
                    "o5": "Over 5%",
                    "o10": "Over 10%",
                    "o15": "Over 15%",
                    "o20": "Over 20%",
                    "o25": "Over 25%",
                    "o30": "Over 30%",
                    "frange": "Custom"
                }
            },
            "fa_eps5years": {
                "name": "EPS Growth Past 5 Years",
                "description": "EPS is the portion of a company's profit allocated to each outstanding share of common stock. EPS serves as an indicator of a company's profitability. Annual growth over past 5 years",
                "options": {
                    "": "Any",
                    "neg": "Negative (<0%)",
                    "pos": "Positive (>0%)",
                    "poslow": "Positive Low (0-10%)",
                    "high": "High (>25%)",
                    "u5": "Under 5%",
                    "u10": "Under 10%",
                    "u15": "Under 15%",
                    "u20": "Under 20%",
                    "u25": "Under 25%",
                    "u30": "Under 30%",
                    "o5": "Over 5%",
                    "o10": "Over 10%",
                    "o15": "Over 15%",
                    "o20": "Over 20%",
                    "o25": "Over 25%",
                    "o30": "Over 30%",
                    "frange": "Custom"
                }
            },
            "fa_estltgrowth": {
                "name": "EPS Growth Next 5 Years",
                "description": "EPS is the portion of a company's profit allocated to each outstanding share of common stock. EPS serves as an indicator of a company's profitability. Long term annual growth estimate",
                "options": {
                    "": "Any",
                    "neg": "Negative (<0%)",
                    "pos": "Positive (>0%)",
                    "poslow": "Positive Low (<10%)",
                    "high": "High (>25%)",
                    "u5": "Under 5%",
                    "u10": "Under 10%",
                    "u15": "Under 15%",
                    "u20": "Under 20%",
                    "u25": "Under 25%",
                    "u30": "Under 30%",
                    "o5": "Over 5%",
                    "o10": "Over 10%",
                    "o15": "Over 15%",
                    "o20": "Over 20%",
                    "o25": "Over 25%",
                    "o30": "Over 30%",
                    "frange": "Custom"
                }
            },
            "fa_salesqoq": {
                "name": "Sales Growth Qtr Over Qtr",
                "description": "Quarter over quarter growth compared on a year over year basis",
                "options": {
                    "": "Any",
                    "neg": "Negative (<0%)",
                    "pos": "Positive (>0%)",
                    "poslow": "Positive Low (0-10%)",
                    "high": "High (>25%)",
                    "u5": "Under 5%",
                    "u10": "Under 10%",
                    "u15": "Under 15%",
                    "u20": "Under 20%",
                    "u25": "Under 25%",
                    "u30": "Under 30%",
                    "o5": "Over 5%",
                    "o10": "Over 10%",
                    "o15": "Over 15%",
                    "o20": "Over 20%",
                    "o25": "Over 25%",
                    "o30": "Over 30%",
                    "frange": "Custom"
                }
            },
            "fa_salesyoyttm": {
                "name": "Sales Growth TTM",
                "description": "Trailing twelve months growth compared on the previous audited financial year",
                "options": {
                    "": "Any",
                    "neg": "Negative (<0%)",
                    "pos": "Positive (>0%)",
                    "poslow": "Positive Low (0-10%)",
                    "high": "High (>25%)",
                    "u5": "Under 5%",
                    "u10": "Under 10%",
                    "u15": "Under 15%",
                    "u20": "Under 20%",
                    "u25": "Under 25%",
                    "u30": "Under 30%",
                    "o5": "Over 5%",
                    "o10": "Over 10%",
                    "o15": "Over 15%",
                    "o20": "Over 20%",
                    "o25": "Over 25%",
                    "o30": "Over 30%",
                    "frange": "Custom"
                }
            },
            "fa_sales3years": {
                "name": "Sales Growth Past 3 Years",
                "description": "Annual growth over past 3 years",
                "options": {
                    "": "Any",
                    "neg": "Negative (<0%)",
                    "pos": "Positive (>0%)",
                    "poslow": "Positive Low (0-10%)",
                    "high": "High (>25%)",
                    "u5": "Under 5%",
                    "u10": "Under 10%",
                    "u15": "Under 15%",
                    "u20": "Under 20%",
                    "u25": "Under 25%",
                    "u30": "Under 30%",
                    "o5": "Over 5%",
                    "o10": "Over 10%",
                    "o15": "Over 15%",
                    "o20": "Over 20%",
                    "o25": "Over 25%",
                    "o30": "Over 30%",
                    "frange": "Custom"
                }
            },
            "fa_sales5years": {
                "name": "Sales Growth Past 5 Years",
                "description": "Annual growth over past 5 years",
                "options": {
                    "": "Any",
                    "neg": "Negative (<0%)",
                    "pos": "Positive (>0%)",
                    "poslow": "Positive Low (0-10%)",
                    "high": "High (>25%)",
                    "u5": "Under 5%",
                    "u10": "Under 10%",
                    "u15": "Under 15%",
                    "u20": "Under 20%",
                    "u25": "Under 25%",
                    "u30": "Under 30%",
                    "o5": "Over 5%",
                    "o10": "Over 10%",
                    "o15": "Over 15%",
                    "o20": "Over 20%",
                    "o25": "Over 25%",
                    "o30": "Over 30%",
                    "frange": "Custom"
                }
            },

            # Profitability Metrics
            "fa_roa": {
                "name": "Return on Assets (ttm)",
                "description": "An indicator of how profitable a company is relative to its total assets. ROA gives an idea as to how efficient management is at using its assets to generate earnings. Calculated by dividing a company's annual earnings by its total assets, ROA is displayed as a percentage",
                "options": {
                    "": "Any",
                    "pos": "Positive (>0%)",
                    "neg": "Negative (<0%)",
                    "verypos": "Very Positive (>15%)",
                    "veryneg": "Very Negative (<-15%)",
                    "u-50": "Under -50%",
                    "u-45": "Under -45%",
                    "u-40": "Under -40%",
                    "u-35": "Under -35%",
                    "u-30": "Under -30%",
                    "u-25": "Under -25%",
                    "u-20": "Under -20%",
                    "u-15": "Under -15%",
                    "u-10": "Under -10%",
                    "u-5": "Under -5%",
                    "o5": "Over +5%",
                    "o10": "Over +10%",
                    "o15": "Over +15%",
                    "o20": "Over +20%",
                    "o25": "Over +25%",
                    "o30": "Over +30%",
                    "o35": "Over +35%",
                    "o40": "Over +40%",
                    "o45": "Over +45%",
                    "o50": "Over +50%",
                    "frange": "Custom"
                }
            },
            "fa_roe": {
                "name": "Return on Equity (ttm)",
                "description": "A measure of a corporation's profitability that reveals how much profit a company generates with the money shareholders have invested. Calculated as Net Income / Shareholder's Equity",
                "options": {
                    "": "Any",
                    "pos": "Positive (>0%)",
                    "neg": "Negative (<0%)",
                    "verypos": "Very Positive (>30%)",
                    "veryneg": "Very Negative (<-15%)",
                    "u-50": "Under -50%",
                    "u-45": "Under -45%",
                    "u-40": "Under -40%",
                    "u-35": "Under -35%",
                    "u-30": "Under -30%",
                    "u-25": "Under -25%",
                    "u-20": "Under -20%",
                    "u-15": "Under -15%",
                    "u-10": "Under -10%",
                    "u-5": "Under -5%",
                    "o5": "Over +5%",
                    "o10": "Over +10%",
                    "o15": "Over +15%",
                    "o20": "Over +20%",
                    "o25": "Over +25%",
                    "o30": "Over +30%",
                    "o35": "Over +35%",
                    "o40": "Over +40%",
                    "o45": "Over +45%",
                    "o50": "Over +50%",
                    "frange": "Custom"
                }
            },
            "fa_roi": {
                "name": "Return on Invested Capital (ttm)",
                "description": "A financial metric used to measure a company's efficiency in allocating its capital to generate returns. It assesses how well a company is using its resources to create value for its investors. ROIC is particularly useful for evaluating a company's profitability and its ability to sustain competitive advantages over time. ROIC is calculated as Net Income / Invested Capital",
                "options": {
                    "": "Any",
                    "pos": "Positive (>0%)",
                    "neg": "Negative (<0%)",
                    "verypos": "Very Positive (>25%)",
                    "veryneg": "Very Negative (<-10%)",
                    "u-50": "Under -50%",
                    "u-45": "Under -45%",
                    "u-40": "Under -40%",
                    "u-35": "Under -35%",
                    "u-30": "Under -30%",
                    "u-25": "Under -25%",
                    "u-20": "Under -20%",
                    "u-15": "Under -15%",
                    "u-10": "Under -10%",
                    "u-5": "Under -5%",
                    "o5": "Over +5%",
                    "o10": "Over +10%",
                    "o15": "Over +15%",
                    "o20": "Over +20%",
                    "o25": "Over +25%",
                    "o30": "Over +30%",
                    "o35": "Over +35%",
                    "o40": "Over +40%",
                    "o45": "Over +45%",
                    "o50": "Over +50%",
                    "frange": "Custom"
                }
            },
            "fa_grossmargin": {
                "name": "Gross Margin (ttm)",
                "description": "A company's total sales revenue minus its cost of goods sold, divided by the total sales revenue, expressed as a percentage. The gross margin represents the percent of total sales revenue that the company retains after incurring the direct costs associated with producing the goods and services sold by a company. The higher the percentage, the more the company retains on each dollar of sales to service its other costs and obligations",
                "options": {
                    "": "Any",
                    "pos": "Positive (>0%)",
                    "neg": "Negative (<0%)",
                    "high": "High (>50%)",
                    "u90": "Under 90%",
                    "u80": "Under 80%",
                    "u70": "Under 70%",
                    "u60": "Under 60%",
                    "u50": "Under 50%",
                    "u45": "Under 45%",
                    "u40": "Under 40%",
                    "u35": "Under 35%",
                    "u30": "Under 30%",
                    "u25": "Under 25%",
                    "u20": "Under 20%",
                    "u15": "Under 15%",
                    "u10": "Under 10%",
                    "u5": "Under 5%",
                    "u0": "Under 0%",
                    "u-10": "Under -10%",
                    "u-20": "Under -20%",
                    "u-30": "Under -30%",
                    "u-50": "Under -50%",
                    "u-70": "Under -70%",
                    "u-100": "Under -100%",
                    "o0": "Over 0%",
                    "o5": "Over 5%",
                    "o10": "Over 10%",
                    "o15": "Over 15%",
                    "o20": "Over 20%",
                    "o25": "Over 25%",
                    "o30": "Over 30%",
                    "o35": "Over 35%",
                    "o40": "Over 40%",
                    "o45": "Over 45%",
                    "o50": "Over 50%",
                    "o60": "Over 60%",
                    "o70": "Over 70%",
                    "o80": "Over 80%",
                    "o90": "Over 90%",
                    "frange": "Custom"
                }
            },
            "fa_opermargin": {
                "name": "Operating Margin (ttm)",
                "description": "Operating margin is a measurement of what proportion of a company's revenue is left over after paying for variable costs of production such as wages, raw materials, etc. A healthy operating margin is required for a company to be able to pay for its fixed costs, such as interest on debt. Calculated as Operating Income / Net Sales",
                "options": {
                    "": "Any",
                    "pos": "Positive (>0%)",
                    "neg": "Negative (<0%)",
                    "veryneg": "Very Negative (<-20%)",
                    "high": "High (>25%)",
                    "u90": "Under 90%",
                    "u80": "Under 80%",
                    "u70": "Under 70%",
                    "u60": "Under 60%",
                    "u50": "Under 50%",
                    "u45": "Under 45%",
                    "u40": "Under 40%",
                    "u35": "Under 35%",
                    "u30": "Under 30%",
                    "u25": "Under 25%",
                    "u20": "Under 20%",
                    "u15": "Under 15%",
                    "u10": "Under 10%",
                    "u5": "Under 5%",
                    "u0": "Under 0%",
                    "u-10": "Under -10%",
                    "u-20": "Under -20%",
                    "u-30": "Under -30%",
                    "u-50": "Under -50%",
                    "u-70": "Under -70%",
                    "u-100": "Under -100%",
                    "o0": "Over 0%",
                    "o5": "Over 5%",
                    "o10": "Over 10%",
                    "o15": "Over 15%",
                    "o20": "Over 20%",
                    "o25": "Over 25%",
                    "o30": "Over 30%",
                    "o35": "Over 35%",
                    "o40": "Over 40%",
                    "o45": "Over 45%",
                    "o50": "Over 50%",
                    "o60": "Over 60%",
                    "o70": "Over 70%",
                    "o80": "Over 80%",
                    "o90": "Over 90%",
                    "frange": "Custom"
                }
            },
            "fa_netmargin": {
                "name": "Net Profit Margin (ttm)",
                "description": "A ratio of profitability calculated as net income divided by revenues, or net profits divided by sales. It measures how much out of every dollar of sales a company actually keeps in earnings",
                "options": {
                    "": "Any",
                    "pos": "Positive (>0%)",
                    "neg": "Negative (<0%)",
                    "veryneg": "Very Negative (<-20%)",
                    "high": "High (>20%)",
                    "u90": "Under 90%",
                    "u80": "Under 80%",
                    "u70": "Under 70%",
                    "u60": "Under 60%",
                    "u50": "Under 50%",
                    "u45": "Under 45%",
                    "u40": "Under 40%",
                    "u35": "Under 35%",
                    "u30": "Under 30%",
                    "u25": "Under 25%",
                    "u20": "Under 20%",
                    "u15": "Under 15%",
                    "u10": "Under 10%",
                    "u5": "Under 5%",
                    "u0": "Under 0%",
                    "u-10": "Under -10%",
                    "u-20": "Under -20%",
                    "u-30": "Under -30%",
                    "u-50": "Under -50%",
                    "u-70": "Under -70%",
                    "u-100": "Under -100%",
                    "o0": "Over 0%",
                    "o5": "Over 5%",
                    "o10": "Over 10%",
                    "o15": "Over 15%",
                    "o20": "Over 20%",
                    "o25": "Over 25%",
                    "o30": "Over 30%",
                    "o35": "Over 35%",
                    "o40": "Over 40%",
                    "o45": "Over 45%",
                    "o50": "Over 50%",
                    "o60": "Over 60%",
                    "o70": "Over 70%",
                    "o80": "Over 80%",
                    "o90": "Over 90%",
                    "frange": "Custom"
                }
            },

            # Financial Strength Metrics
            "fa_curratio": {
                "name": "Current Ratio (mrq)",
                "description": "A liquidity ratio that measures a company's ability to pay short-term obligations. Calculated as Current Assets / Current Liabilities",
                "options": {
                    "": "Any",
                    "high": "High (>3)",
                    "low": "Low (<1)",
                    "u1": "Under 1",
                    "u0.5": "Under 0.5",
                    "o0.5": "Over 0.5",
                    "o1": "Over 1",
                    "o1.5": "Over 1.5",
                    "o2": "Over 2",
                    "o3": "Over 3",
                    "o4": "Over 4",
                    "o5": "Over 5",
                    "o10": "Over 10",
                    "frange": "Custom"
                }
            },
            "fa_quickratio": {
                "name": "Quick Ratio (mrq)",
                "description": "An indicator of a company's short-term liquidity. The quick ratio measures a company's ability to meet its short-term obligations with its most liquid assets. The higher the quick ratio, the better the position of the company. Calculated as (Current Assets - Inventories) / Current Liabilities",
                "options": {
                    "": "Any",
                    "high": "High (>3)",
                    "low": "Low (<0.5)",
                    "u1": "Under 1",
                    "u0.5": "Under 0.5",
                    "o0.5": "Over 0.5",
                    "o1": "Over 1",
                    "o1.5": "Over 1.5",
                    "o2": "Over 2",
                    "o3": "Over 3",
                    "o4": "Over 4",
                    "o5": "Over 5",
                    "o10": "Over 10",
                    "frange": "Custom"
                }
            },
            "fa_ltdebteq": {
                "name": "Long Term Debt to Equity (mrq)",
                "description": "A measure of a company's financial leverage calculated by dividing its long term debt by stockholders' equity. It indicates what proportion of equity and debt the company is using to finance its assets",
                "options": {
                    "": "Any",
                    "high": "High (>0.5)",
                    "low": "Low (<0.1)",
                    "u1": "Under 1",
                    "u0.9": "Under 0.9",
                    "u0.8": "Under 0.8",
                    "u0.7": "Under 0.7",
                    "u0.6": "Under 0.6",
                    "u0.5": "Under 0.5",
                    "u0.4": "Under 0.4",
                    "u0.3": "Under 0.3",
                    "u0.2": "Under 0.2",
                    "u0.1": "Under 0.1",
                    "o0.1": "Over 0.1",
                    "o0.2": "Over 0.2",
                    "o0.3": "Over 0.3",
                    "o0.4": "Over 0.4",
                    "o0.5": "Over 0.5",
                    "o0.6": "Over 0.6",
                    "o0.7": "Over 0.7",
                    "o0.8": "Over 0.8",
                    "o0.9": "Over 0.9",
                    "o1": "Over 1",
                    "frange": "Custom"
                }
            },
            "fa_debteq": {
                "name": "Total Debt to Equity (mrq)",
                "description": "A measure of a company's financial leverage calculated by dividing its liabilities by stockholders' equity. It indicates what proportion of equity and debt the company is using to finance its assets",
                "options": {
                    "": "Any",
                    "high": "High (>0.5)",
                    "low": "Low (<0.1)",
                    "u1": "Under 1",
                    "u0.9": "Under 0.9",
                    "u0.8": "Under 0.8",
                    "u0.7": "Under 0.7",
                    "u0.6": "Under 0.6",
                    "u0.5": "Under 0.5",
                    "u0.4": "Under 0.4",
                    "u0.3": "Under 0.3",
                    "u0.2": "Under 0.2",
                    "u0.1": "Under 0.1",
                    "o0.1": "Over 0.1",
                    "o0.2": "Over 0.2",
                    "o0.3": "Over 0.3",
                    "o0.4": "Over 0.4",
                    "o0.5": "Over 0.5",
                    "o0.6": "Over 0.6",
                    "o0.7": "Over 0.7",
                    "o0.8": "Over 0.8",
                    "o0.9": "Over 0.9",
                    "o1": "Over 1",
                    "frange": "Custom"
                }
            },

            # Ownership Metrics
            "sh_insiderown": {
                "name": "Insider Ownership",
                "description": "Level to which a company is owned by its own management",
                "options": {
                    "": "Any",
                    "low": "Low (<5%)",
                    "high": "High (>30%)",
                    "veryhigh": "Very High (>50%)",
                    "o10": "Over 10%",
                    "o20": "Over 20%",
                    "o30": "Over 30%",
                    "o40": "Over 40%",
                    "o50": "Over 50%",
                    "o60": "Over 60%",
                    "o70": "Over 70%",
                    "o80": "Over 80%",
                    "o90": "Over 90%",
                    "frange": "Custom"
                }
            },
            "sh_insidertrans": {
                "name": "Insider Transactions",
                "description": "A company's shares being purchased or sold by its own management. Value represents 6-month percentual change in total insider ownership",
                "options": {
                    "": "Any",
                    "veryneg": "Very Negative (<-20%)",
                    "neg": "Negative (<0%)",
                    "pos": "Positive (>0%)",
                    "verypos": "Very Positive (>20%)",
                    "u-90": "Under -90%",
                    "u-80": "Under -80%",
                    "u-70": "Under -70%",
                    "u-60": "Under -60%",
                    "u-50": "Under -50%",
                    "u-45": "Under -45%",
                    "u-40": "Under -40%",
                    "u-35": "Under -35%",
                    "u-30": "Under -30%",
                    "u-25": "Under -25%",
                    "u-20": "Under -20%",
                    "u-15": "Under -15%",
                    "u-10": "Under -10%",
                    "u-5": "Under -5%",
                    "o5": "Over +5%",
                    "o10": "Over +10%",
                    "o15": "Over +15%",
                    "o20": "Over +20%",
                    "o25": "Over +25%",
                    "o30": "Over +30%",
                    "o35": "Over +35%",
                    "o40": "Over +40%",
                    "o45": "Over +45%",
                    "o50": "Over +50%",
                    "o60": "Over +60%",
                    "o70": "Over +70%",
                    "o80": "Over +80%",
                    "o90": "Over +90%",
                    "frange": "Custom"
                }
            },
            "sh_instown": {
                "name": "Institutional Ownership",
                "description": "Level to which a company is owned by financial institutions",
                "options": {
                    "": "Any",
                    "low": "Low (<5%)",
                    "high": "High (>90%)",
                    "u90": "Under 90%",
                    "u80": "Under 80%",
                    "u70": "Under 70%",
                    "u60": "Under 60%",
                    "u50": "Under 50%",
                    "u40": "Under 40%",
                    "u30": "Under 30%",
                    "u20": "Under 20%",
                    "u10": "Under 10%",
                    "o10": "Over 10%",
                    "o20": "Over 20%",
                    "o30": "Over 30%",
                    "o40": "Over 40%",
                    "o50": "Over 50%",
                    "o60": "Over 60%",
                    "o70": "Over 70%",
                    "o80": "Over 80%",
                    "o90": "Over 90%",
                    "frange": "Custom"
                }
            },
            "sh_insttrans": {
                "name": "Institutional Transactions",
                "description": "A company's shares being purchased or sold by financial institutions. Value represents 3-month change in institutional ownership",
                "options": {
                    "": "Any",
                    "veryneg": "Very Negative (<-20%)",
                    "neg": "Negative (<0%)",
                    "pos": "Positive (>0%)",
                    "verypos": "Very Positive (>20%)",
                    "u-50": "Under -50%",
                    "u-45": "Under -45%",
                    "u-40": "Under -40%",
                    "u-35": "Under -35%",
                    "u-30": "Under -30%",
                    "u-25": "Under -25%",
                    "u-20": "Under -20%",
                    "u-15": "Under -15%",
                    "u-10": "Under -10%",
                    "u-5": "Under -5%",
                    "o5": "Over +5%",
                    "o10": "Over +10%",
                    "o15": "Over +15%",
                    "o20": "Over +20%",
                    "o25": "Over +25%",
                    "o30": "Over +30%",
                    "o35": "Over +35%",
                    "o40": "Over +40%",
                    "o45": "Over +45%",
                    "o50": "Over +50%",
                    "frange": "Custom"
                }
            },
            "sh_short": {
                "name": "Short Float",
                "description": "The amount of short-selling transactions of given stock",
                "options": {
                    "": "Any",
                    "low": "Low (<5%)",
                    "high": "High (>20%)",
                    "u5": "Under 5%",
                    "u10": "Under 10%",
                    "u15": "Under 15%",
                    "u20": "Under 20%",
                    "u25": "Under 25%",
                    "u30": "Under 30%",
                    "o5": "Over 5%",
                    "o10": "Over 10%",
                    "o15": "Over 15%",
                    "o20": "Over 20%",
                    "o25": "Over 25%",
                    "o30": "Over 30%",
                    "frange": "Custom"
                }
            },

            # Analyst Metrics
            "an_recom": {
                "name": "Analyst Recommendation",
                "description": "An outlook of a stock-market analyst on a stock",
                "options": {
                    "": "Any",
                    "strongbuy": "Strong Buy (1)",
                    "buybetter": "Buy or better",
                    "buy": "Buy",
                    "holdbetter": "Hold or better",
                    "hold": "Hold",
                    "holdworse": "Hold or worse",
                    "sell": "Sell",
                    "sellworse": "Sell or worse",
                    "strongsell": "Strong Sell (5)",
                    "modal": "Custom"
                }
            },
            "fa_epsrev": {
                "name": "Earnings & Revenue Surprise",
                "description": "Company's reported earnings/revenue are above or below analysts' expectations",
                "options": {
                    "": "Any",
                    "bp": "Both positive (>0%)",
                    "bm": "Both met (0%)",
                    "bn": "Both negative (<0%)",
                    "ep": "EPS Positive (>0%)",
                    "em": "EPS Met (0%)",
                    "en": "EPS Negative (<0%)",
                    "eu100": "EPS Under -100%",
                    "eu50": "EPS Under -50%",
                    "eu40": "EPS Under -40%",
                    "eu30": "EPS Under -30%",
                    "eu20": "EPS Under -20%",
                    "eu10": "EPS Under -10%",
                    "eu5": "EPS Under -5%",
                    "eo5": "EPS Over 5%",
                    "eo10": "EPS Over 10%",
                    "eo20": "EPS Over 20%",
                    "eo30": "EPS Over 30%",
                    "eo40": "EPS Over 40%",
                    "eo50": "EPS Over 50%",
                    "eo60": "EPS Over 60%",
                    "eo70": "EPS Over 70%",
                    "eo80": "EPS Over 80%",
                    "eo90": "EPS Over 90%",
                    "eo100": "EPS Over 100%",
                    "eo200": "EPS Over 200%",
                    "rp": "Revenue Positive (>0%)",
                    "rm": "Revenue Met (0%)",
                    "rn": "Revenue Negative (<0%)",
                    "ru100": "Revenue Under -100%",
                    "ru50": "Revenue Under -50%",
                    "ru40": "Revenue Under -40%",
                    "ru30": "Revenue Under -30%",
                    "ru20": "Revenue Under -20%",
                    "ru10": "Revenue Under -10%",
                    "ru5": "Revenue Under -5%",
                    "ro5": "Revenue Over 5%",
                    "ro10": "Revenue Over 10%",
                    "ro20": "Revenue Over 20%",
                    "ro30": "Revenue Over 30%",
                    "ro40": "Revenue Over 40%",
                    "ro50": "Revenue Over 50%",
                    "ro60": "Revenue Over 60%",
                    "ro70": "Revenue Over 70%",
                    "ro80": "Revenue Over 80%",
                    "ro90": "Revenue Over 90%",
                    "ro100": "Revenue Over 100%",
                    "ro200": "Revenue Over 200%",
                    "modal": "Custom"
                }
            },

            # Trading & Earnings Metrics
            "sh_opt": {
                "name": "Option/Short",
                "description": "Stocks with options and/or available to sell short",
                "options": {
                    "": "Any",
                    "option": "Optionable",
                    "short": "Shortable",
                    "notoption": "Not optionable",
                    "notshort": "Not shortable",
                    "optionshort": "Optionable and shortable",
                    "optionnotshort": "Optionable and not shortable",
                    "notoptionshort": "Not optionable and shortable",
                    "notoptionnotshort": "Not optionable and not shortable",
                    "shortsalerestricted": "Short sale restricted",
                    "notshortsalerestricted": "Not short sale restricted",
                    "halted": "Halted",
                    "nothalted": "Not halted",
                    "so10k": "Over 10K shares available to short",
                    "so100k": "Over 100K shares available to short",
                    "so1m": "Over 1M shares available to short",
                    "so10m": "Over 10M shares available to short",
                    "uo1m": "Over $1M available to short",
                    "uo10m": "Over $10M available to short",
                    "uo100m": "Over $100M available to short",
                    "uo1b": "Over $1B available to short",
                    "modal": "Custom"
                }
            },
            "earningsdate": {
                "name": "Earnings Date",
                "description": "Date when company reports earnings",
                "options": {
                    "": "Any",
                    "today": "Today",
                    "todaybefore": "Today Before Market Open",
                    "todayafter": "Today After Market Close",
                    "tomorrow": "Tomorrow",
                    "tomorrowbefore": "Tomorrow Before Market Open",
                    "tomorrowafter": "Tomorrow After Market Close",
                    "yesterday": "Yesterday",
                    "yesterdaybefore": "Yesterday Before Market Open",
                    "yesterdayafter": "Yesterday After Market Close",
                    "nextdays5": "Next 5 Days",
                    "prevdays5": "Previous 5 Days",
                    "thisweek": "This Week",
                    "nextweek": "Next Week",
                    "prevweek": "Previous Week",
                    "thismonth": "This Month",
                    "modal": "Custom"
                }
            },

            # Technical Analysis - Performance
            "ta_perf": {
                "name": "Performance",
                "description": "Rate of return for a given stock",
                "options": {
                    "": "Any",
                    "modal_intraday": "Intraday",
                    "dup": "Today Up",
                    "ddown": "Today Down",
                    "d15u": "Today -15%",
                    "d10u": "Today -10%",
                    "d5u": "Today -5%",
                    "d5o": "Today +5%",
                    "d10o": "Today +10%",
                    "d15o": "Today +15%",
                    "1w30u": "Week -30%",
                    "1w20u": "Week -20%",
                    "1w10u": "Week -10%",
                    "1wdown": "Week Down",
                    "1wup": "Week Up",
                    "1w10o": "Week +10%",
                    "1w20o": "Week +20%",
                    "1w30o": "Week +30%",
                    "4w50u": "Month -50%",
                    "4w30u": "Month -30%",
                    "4w20u": "Month -20%",
                    "4w10u": "Month -10%",
                    "4wdown": "Month Down",
                    "4wup": "Month Up",
                    "4w10o": "Month +10%",
                    "4w20o": "Month +20%",
                    "4w30o": "Month +30%",
                    "4w50o": "Month +50%",
                    "13w50u": "Quarter -50%",
                    "13w30u": "Quarter -30%",
                    "13w20u": "Quarter -20%",
                    "13w10u": "Quarter -10%",
                    "13wdown": "Quarter Down",
                    "13wup": "Quarter Up",
                    "13w10o": "Quarter +10%",
                    "13w20o": "Quarter +20%",
                    "13w30o": "Quarter +30%",
                    "13w50o": "Quarter +50%",
                    "26w75u": "Half -75%",
                    "26w50u": "Half -50%",
                    "26w30u": "Half -30%",
                    "26w20u": "Half -20%",
                    "26w10u": "Half -10%",
                    "26wdown": "Half Down",
                    "26wup": "Half Up",
                    "26w10o": "Half +10%",
                    "26w20o": "Half +20%",
                    "26w30o": "Half +30%",
                    "26w50o": "Half +50%",
                    "26w100o": "Half +100%",
                    "ytd75u": "YTD -75%",
                    "ytd50u": "YTD -50%",
                    "ytd30u": "YTD -30%",
                    "ytd20u": "YTD -20%",
                    "ytd10u": "YTD -10%",
                    "ytd5u": "YTD -5%",
                    "ytddown": "YTD Down",
                    "ytdup": "YTD Up",
                    "ytd5o": "YTD +5%",
                    "ytd10o": "YTD +10%",
                    "ytd20o": "YTD +20%",
                    "ytd30o": "YTD +30%",
                    "ytd50o": "YTD +50%",
                    "ytd100o": "YTD +100%",
                    "52w75u": "Year -75%",
                    "52w50u": "Year -50%",
                    "52w30u": "Year -30%",
                    "52w20u": "Year -20%",
                    "52w10u": "Year -10%",
                    "52wdown": "Year Down",
                    "52wup": "Year Up",
                    "52w10o": "Year +10%",
                    "52w20o": "Year +20%",
                    "52w30o": "Year +30%",
                    "52w50o": "Year +50%",
                    "52w100o": "Year +100%",
                    "52w200o": "Year +200%",
                    "52w300o": "Year +300%",
                    "52w500o": "Year +500%",
                    "3y90u": "3 Years -90%",
                    "3y75u": "3 Years -75%",
                    "3y50u": "3 Years -50%",
                    "3y30u": "3 Years -30%",
                    "3y20u": "3 Years -20%",
                    "3y10u": "3 Years -10%",
                    "3ydown": "3 Years Down",
                    "3yup": "3 Years Up",
                    "3y10o": "3 Years +10%",
                    "3y20o": "3 Years +20%",
                    "3y30o": "3 Years +30%",
                    "3y50o": "3 Years +50%",
                    "3y100o": "3 Years +100%",
                    "3y200o": "3 Years +200%",
                    "3y300o": "3 Years +300%",
                    "3y500o": "3 Years +500%",
                    "3y1000o": "3 Years +1000%",
                    "5y90u": "5 Years -90%",
                    "5y75u": "5 Years -75%",
                    "5y50u": "5 Years -50%",
                    "5y30u": "5 Years -30%",
                    "5y20u": "5 Years -20%",
                    "5y10u": "5 Years -10%",
                    "5ydown": "5 Years Down",
                    "5yup": "5 Years Up",
                    "5y10o": "5 Years +10%",
                    "5y20o": "5 Years +20%",
                    "5y30o": "5 Years +30%",
                    "5y50o": "5 Years +50%",
                    "5y100o": "5 Years +100%",
                    "5y200o": "5 Years +200%",
                    "5y300o": "5 Years +300%",
                    "5y500o": "5 Years +500%",
                    "5y1000o": "5 Years +1000%",
                    "10y90u": "10 Years -90%",
                    "10y75u": "10 Years -75%",
                    "10y50u": "10 Years -50%",
                    "10y30u": "10 Years -30%",
                    "10y20u": "10 Years -20%",
                    "10y10u": "10 Years -10%",
                    "10ydown": "10 Years Down",
                    "10yup": "10 Years Up",
                    "10y10o": "10 Years +10%",
                    "10y20o": "10 Years +20%",
                    "10y30o": "10 Years +30%",
                    "10y50o": "10 Years +50%",
                    "10y100o": "10 Years +100%",
                    "10y200o": "10 Years +200%",
                    "10y300o": "10 Years +300%",
                    "10y500o": "10 Years +500%",
                    "10y1000o": "10 Years +1000%",
                    "modal": "Custom"
                }
            },
            "ta_perf2": {
                "name": "Performance 2",
                "description": "Rate of return for a given stock",
                "options": {
                    "": "Any",
                    "modal_intraday": "Intraday",
                    "dup": "Today Up",
                    "ddown": "Today Down",
                    "d15u": "Today -15%",
                    "d10u": "Today -10%",
                    "d5u": "Today -5%",
                    "d5o": "Today +5%",
                    "d10o": "Today +10%",
                    "d15o": "Today +15%",
                    "1w30u": "Week -30%",
                    "1w20u": "Week -20%",
                    "1w10u": "Week -10%",
                    "1wdown": "Week Down",
                    "1wup": "Week Up",
                    "1w10o": "Week +10%",
                    "1w20o": "Week +20%",
                    "1w30o": "Week +30%",
                    "4w50u": "Month -50%",
                    "4w30u": "Month -30%",
                    "4w20u": "Month -20%",
                    "4w10u": "Month -10%",
                    "4wdown": "Month Down",
                    "4wup": "Month Up",
                    "4w10o": "Month +10%",
                    "4w20o": "Month +20%",
                    "4w30o": "Month +30%",
                    "4w50o": "Month +50%",
                    "13w50u": "Quarter -50%",
                    "13w30u": "Quarter -30%",
                    "13w20u": "Quarter -20%",
                    "13w10u": "Quarter -10%",
                    "13wdown": "Quarter Down",
                    "13wup": "Quarter Up",
                    "13w10o": "Quarter +10%",
                    "13w20o": "Quarter +20%",
                    "13w30o": "Quarter +30%",
                    "13w50o": "Quarter +50%",
                    "26w75u": "Half -75%",
                    "26w50u": "Half -50%",
                    "26w30u": "Half -30%",
                    "26w20u": "Half -20%",
                    "26w10u": "Half -10%",
                    "26wdown": "Half Down",
                    "26wup": "Half Up",
                    "26w10o": "Half +10%",
                    "26w20o": "Half +20%",
                    "26w30o": "Half +30%",
                    "26w50o": "Half +50%",
                    "26w100o": "Half +100%",
                    "ytd75u": "YTD -75%",
                    "ytd50u": "YTD -50%",
                    "ytd30u": "YTD -30%",
                    "ytd20u": "YTD -20%",
                    "ytd10u": "YTD -10%",
                    "ytd5u": "YTD -5%",
                    "ytddown": "YTD Down",
                    "ytdup": "YTD Up",
                    "ytd5o": "YTD +5%",
                    "ytd10o": "YTD +10%",
                    "ytd20o": "YTD +20%",
                    "ytd30o": "YTD +30%",
                    "ytd50o": "YTD +50%",
                    "ytd100o": "YTD +100%",
                    "52w75u": "Year -75%",
                    "52w50u": "Year -50%",
                    "52w30u": "Year -30%",
                    "52w20u": "Year -20%",
                    "52w10u": "Year -10%",
                    "52wdown": "Year Down",
                    "52wup": "Year Up",
                    "52w10o": "Year +10%",
                    "52w20o": "Year +20%",
                    "52w30o": "Year +30%",
                    "52w50o": "Year +50%",
                    "52w100o": "Year +100%",
                    "52w200o": "Year +200%",
                    "52w300o": "Year +300%",
                    "52w500o": "Year +500%",
                    "3y90u": "3 Years -90%",
                    "3y75u": "3 Years -75%",
                    "3y50u": "3 Years -50%",
                    "3y30u": "3 Years -30%",
                    "3y20u": "3 Years -20%",
                    "3y10u": "3 Years -10%",
                    "3ydown": "3 Years Down",
                    "3yup": "3 Years Up",
                    "3y10o": "3 Years +10%",
                    "3y20o": "3 Years +20%",
                    "3y30o": "3 Years +30%",
                    "3y50o": "3 Years +50%",
                    "3y100o": "3 Years +100%",
                    "3y200o": "3 Years +200%",
                    "3y300o": "3 Years +300%",
                    "3y500o": "3 Years +500%",
                    "3y1000o": "3 Years +1000%",
                    "5y90u": "5 Years -90%",
                    "5y75u": "5 Years -75%",
                    "5y50u": "5 Years -50%",
                    "5y30u": "5 Years -30%",
                    "5y20u": "5 Years -20%",
                    "5y10u": "5 Years -10%",
                    "5ydown": "5 Years Down",
                    "5yup": "5 Years Up",
                    "5y10o": "5 Years +10%",
                    "5y20o": "5 Years +20%",
                    "5y30o": "5 Years +30%",
                    "5y50o": "5 Years +50%",
                    "5y100o": "5 Years +100%",
                    "5y200o": "5 Years +200%",
                    "5y300o": "5 Years +300%",
                    "5y500o": "5 Years +500%",
                    "5y1000o": "5 Years +1000%",
                    "10y90u": "10 Years -90%",
                    "10y75u": "10 Years -75%",
                    "10y50u": "10 Years -50%",
                    "10y30u": "10 Years -30%",
                    "10y20u": "10 Years -20%",
                    "10y10u": "10 Years -10%",
                    "10ydown": "10 Years Down",
                    "10yup": "10 Years Up",
                    "10y10o": "10 Years +10%",
                    "10y20o": "10 Years +20%",
                    "10y30o": "10 Years +30%",
                    "10y50o": "10 Years +50%",
                    "10y100o": "10 Years +100%",
                    "10y200o": "10 Years +200%",
                    "10y300o": "10 Years +300%",
                    "10y500o": "10 Years +500%",
                    "10y1000o": "10 Years +1000%",
                    "modal": "Custom"
                }
            },

            # Technical Analysis - Volatility & Indicators
            "ta_volatility": {
                "name": "Volatility",
                "description": "A statistical measure of the dispersion of returns for a given stock. Represents average daily high/low trading range",
                "options": {
                    "": "Any",
                    "wo2": "Week - Over 2%",
                    "wo3": "Week - Over 3%",
                    "wo4": "Week - Over 4%",
                    "wo5": "Week - Over 5%",
                    "wo6": "Week - Over 6%",
                    "wo7": "Week - Over 7%",
                    "wo8": "Week - Over 8%",
                    "wo9": "Week - Over 9%",
                    "wo10": "Week - Over 10%",
                    "wo12": "Week - Over 12%",
                    "wo15": "Week - Over 15%",
                    "mo2": "Month - Over 2%",
                    "mo3": "Month - Over 3%",
                    "mo4": "Month - Over 4%",
                    "mo5": "Month - Over 5%",
                    "mo6": "Month - Over 6%",
                    "mo7": "Month - Over 7%",
                    "mo8": "Month - Over 8%",
                    "mo9": "Month - Over 9%",
                    "mo10": "Month - Over 10%",
                    "mo12": "Month - Over 12%",
                    "mo15": "Month - Over 15%",
                    "modal": "Custom"
                }
            },
            "ta_rsi": {
                "name": "RSI (14)",
                "description": "The Relative Strength Index (RSI) is a technical analysis oscillator showing price strength by comparing upward and downward close-to-close movements",
                "options": {
                    "": "Any",
                    "ob90": "Overbought (90)",
                    "ob80": "Overbought (80)",
                    "ob70": "Overbought (70)",
                    "ob60": "Overbought (60)",
                    "os40": "Oversold (40)",
                    "os30": "Oversold (30)",
                    "os20": "Oversold (20)",
                    "os10": "Oversold (10)",
                    "nob60": "Not Overbought (<60)",
                    "nob50": "Not Overbought (<50)",
                    "nos50": "Not Oversold (>50)",
                    "nos40": "Not Oversold (>40)",
                    "frange": "Custom"
                }
            },
            "ta_gap": {
                "name": "Gap",
                "description": "The difference between yesterday's closing price and today's opening price",
                "options": {
                    "": "Any",
                    "u": "Up",
                    "u0": "Up 0%",
                    "u1": "Up 1%",
                    "u2": "Up 2%",
                    "u3": "Up 3%",
                    "u4": "Up 4%",
                    "u5": "Up 5%",
                    "u6": "Up 6%",
                    "u7": "Up 7%",
                    "u8": "Up 8%",
                    "u9": "Up 9%",
                    "u10": "Up 10%",
                    "u15": "Up 15%",
                    "u20": "Up 20%",
                    "d": "Down",
                    "d0": "Down 0%",
                    "d1": "Down 1%",
                    "d2": "Down 2%",
                    "d3": "Down 3%",
                    "d4": "Down 4%",
                    "d5": "Down 5%",
                    "d6": "Down 6%",
                    "d7": "Down 7%",
                    "d8": "Down 8%",
                    "d9": "Down 9%",
                    "d10": "Down 10%",
                    "d15": "Down 15%",
                    "d20": "Down 20%",
                    "frange": "Custom"
                }
            },

            # Moving Averages
            "ta_sma20": {
                "name": "20-Day Simple Moving Average",
                "description": "20-Day simple moving average of closing price is the mean of the previous 20 days' closing prices",
                "options": {
                    "": "Any",
                    "pb": "Price below SMA20",
                    "pb10": "Price 10% below SMA20",
                    "pb20": "Price 20% below SMA20",
                    "pb30": "Price 30% below SMA20",
                    "pb40": "Price 40% below SMA20",
                    "pb50": "Price 50% below SMA20",
                    "pa": "Price above SMA20",
                    "pa10": "Price 10% above SMA20",
                    "pa20": "Price 20% above SMA20",
                    "pa30": "Price 30% above SMA20",
                    "pa40": "Price 40% above SMA20",
                    "pa50": "Price 50% above SMA20",
                    "pc": "Price crossed SMA20",
                    "pca": "Price crossed SMA20 above",
                    "pcb": "Price crossed SMA20 below",
                    "cross50": "SMA20 crossed SMA50",
                    "cross50a": "SMA20 crossed SMA50 above",
                    "cross50b": "SMA20 crossed SMA50 below",
                    "cross200": "SMA20 crossed SMA200",
                    "cross200a": "SMA20 crossed SMA200 above",
                    "cross200b": "SMA20 crossed SMA200 below",
                    "sa50": "SMA20 above SMA50",
                    "sb50": "SMA20 below SMA50",
                    "sa200": "SMA20 above SMA200",
                    "sb200": "SMA20 below SMA200",
                    "modal": "Custom"
                }
            },
            "ta_sma50": {
                "name": "50-Day Simple Moving Average",
                "description": "50-Day simple moving average of closing price is the mean of the previous 50 days' closing prices",
                "options": {
                    "": "Any",
                    "pb": "Price below SMA50",
                    "pb10": "Price 10% below SMA50",
                    "pb20": "Price 20% below SMA50",
                    "pb30": "Price 30% below SMA50",
                    "pb40": "Price 40% below SMA50",
                    "pb50": "Price 50% below SMA50",
                    "pa": "Price above SMA50",
                    "pa10": "Price 10% above SMA50",
                    "pa20": "Price 20% above SMA50",
                    "pa30": "Price 30% above SMA50",
                    "pa40": "Price 40% above SMA50",
                    "pa50": "Price 50% above SMA50",
                    "pc": "Price crossed SMA50",
                    "pca": "Price crossed SMA50 above",
                    "pcb": "Price crossed SMA50 below",
                    "cross20": "SMA50 crossed SMA20",
                    "cross20a": "SMA50 crossed SMA20 above",
                    "cross20b": "SMA50 crossed SMA20 below",
                    "cross200": "SMA50 crossed SMA200",
                    "cross200a": "SMA50 crossed SMA200 above",
                    "cross200b": "SMA50 crossed SMA200 below",
                    "sa20": "SMA50 above SMA20",
                    "sb20": "SMA50 below SMA20",
                    "sa200": "SMA50 above SMA200",
                    "sb200": "SMA50 below SMA200",
                    "modal": "Custom"
                }
            },
            "ta_sma200": {
                "name": "200-Day Simple Moving Average",
                "description": "200-Day simple moving average of closing price is the mean of the previous 200 days' closing prices",
                "options": {
                    "": "Any",
                    "pb": "Price below SMA200",
                    "pb10": "Price 10% below SMA200",
                    "pb20": "Price 20% below SMA200",
                    "pb30": "Price 30% below SMA200",
                    "pb40": "Price 40% below SMA200",
                    "pb50": "Price 50% below SMA200",
                    "pb60": "Price 60% below SMA200",
                    "pb70": "Price 70% below SMA200",
                    "pb80": "Price 80% below SMA200",
                    "pb90": "Price 90% below SMA200",
                    "pa": "Price above SMA200",
                    "pa10": "Price 10% above SMA200",
                    "pa20": "Price 20% above SMA200",
                    "pa30": "Price 30% above SMA200",
                    "pa40": "Price 40% above SMA200",
                    "pa50": "Price 50% above SMA200",
                    "pa60": "Price 60% above SMA200",
                    "pa70": "Price 70% above SMA200",
                    "pa80": "Price 80% above SMA200",
                    "pa90": "Price 90% above SMA200",
                    "pa100": "Price 100% above SMA200",
                    "pc": "Price crossed SMA200",
                    "pca": "Price crossed SMA200 above",
                    "pcb": "Price crossed SMA200 below",
                    "cross20": "SMA200 crossed SMA20",
                    "cross20a": "SMA200 crossed SMA20 above",
                    "cross20b": "SMA200 crossed SMA20 below",
                    "cross50": "SMA200 crossed SMA50",
                    "cross50a": "SMA200 crossed SMA50 above",
                    "cross50b": "SMA200 crossed SMA50 below",
                    "sa20": "SMA200 above SMA20",
                    "sb20": "SMA200 below SMA20",
                    "sa50": "SMA200 above SMA50",
                    "sb50": "SMA200 below SMA50",
                    "modal": "Custom"
                }
            },

            # Price Changes
            "ta_change": {
                "name": "Change from previous Close",
                "description": "The difference between previous's close price and today's last price",
                "options": {
                    "": "Any",
                    "u": "Up",
                    "u1": "Up 1%",
                    "u2": "Up 2%",
                    "u3": "Up 3%",
                    "u4": "Up 4%",
                    "u5": "Up 5%",
                    "u6": "Up 6%",
                    "u7": "Up 7%",
                    "u8": "Up 8%",
                    "u9": "Up 9%",
                    "u10": "Up 10%",
                    "u15": "Up 15%",
                    "u20": "Up 20%",
                    "d": "Down",
                    "d1": "Down 1%",
                    "d2": "Down 2%",
                    "d3": "Down 3%",
                    "d4": "Down 4%",
                    "d5": "Down 5%",
                    "d6": "Down 6%",
                    "d7": "Down 7%",
                    "d8": "Down 8%",
                    "d9": "Down 9%",
                    "d10": "Down 10%",
                    "d15": "Down 15%",
                    "d20": "Down 20%",
                    "frange": "Custom"
                }
            },
            "ta_changeopen": {
                "name": "Change from Open",
                "description": "The difference between today's open price and today's last price",
                "options": {
                    "": "Any",
                    "u": "Up",
                    "u1": "Up 1%",
                    "u2": "Up 2%",
                    "u3": "Up 3%",
                    "u4": "Up 4%",
                    "u5": "Up 5%",
                    "u6": "Up 6%",
                    "u7": "Up 7%",
                    "u8": "Up 8%",
                    "u9": "Up 9%",
                    "u10": "Up 10%",
                    "u15": "Up 15%",
                    "u20": "Up 20%",
                    "d": "Down",
                    "d1": "Down 1%",
                    "d2": "Down 2%",
                    "d3": "Down 3%",
                    "d4": "Down 4%",
                    "d5": "Down 5%",
                    "d6": "Down 6%",
                    "d7": "Down 7%",
                    "d8": "Down 8%",
                    "d9": "Down 9%",
                    "d10": "Down 10%",
                    "d15": "Down 15%",
                    "d20": "Down 20%",
                    "frange": "Custom"
                }
            },

            # High/Low Analysis
            "ta_highlow20d": {
                "name": "20-Day High/Low",
                "description": "Maximum/minimum of previous 20 days' highs/lows",
                "options": {
                    "": "Any",
                    "nh": "New High",
                    "nl": "New Low",
                    "b5h": "5% or more below High",
                    "b10h": "10% or more below High",
                    "b15h": "15% or more below High",
                    "b20h": "20% or more below High",
                    "b30h": "30% or more below High",
                    "b40h": "40% or more below High",
                    "b50h": "50% or more below High",
                    "b0to3h": "0-3% below High",
                    "b0to5h": "0-5% below High",
                    "b0to10h": "0-10% below High",
                    "a5h": "5% or more above Low",
                    "a10h": "10% or more above Low",
                    "a15h": "15% or more above Low",
                    "a20h": "20% or more above Low",
                    "a30h": "30% or more above Low",
                    "a40h": "40% or more above Low",
                    "a50h": "50% or more above Low",
                    "a0to3h": "0-3% above Low",
                    "a0to5h": "0-5% above Low",
                    "a0to10h": "0-10% above Low",
                    "modal": "Custom"
                }
            },
            "ta_highlow50d": {
                "name": "50-Day High/Low",
                "description": "Maximum/minimum of previous 50 days' highs/lows",
                "options": {
                    "": "Any",
                    "nh": "New High",
                    "nl": "New Low",
                    "b5h": "5% or more below High",
                    "b10h": "10% or more below High",
                    "b15h": "15% or more below High",
                    "b20h": "20% or more below High",
                    "b30h": "30% or more below High",
                    "b40h": "40% or more below High",
                    "b50h": "50% or more below High",
                    "b0to3h": "0-3% below High",
                    "b0to5h": "0-5% below High",
                    "b0to10h": "0-10% below High",
                    "a5h": "5% or more above Low",
                    "a10h": "10% or more above Low",
                    "a15h": "15% or more above Low",
                    "a20h": "20% or more above Low",
                    "a30h": "30% or more above Low",
                    "a40h": "40% or more above Low",
                    "a50h": "50% or more above Low",
                    "a0to3h": "0-3% above Low",
                    "a0to5h": "0-5% above Low",
                    "a0to10h": "0-10% above Low",
                    "modal": "Custom"
                }
            },
            "ta_highlow52w": {
                "name": "52-Week High/Low",
                "description": "Maximum/minimum of previous 52 weeks' highs/lows",
                "options": {
                    "": "Any",
                    "nh": "New High",
                    "nl": "New Low",
                    "b5h": "5% or more below High",
                    "b10h": "10% or more below High",
                    "b15h": "15% or more below High",
                    "b20h": "20% or more below High",
                    "b30h": "30% or more below High",
                    "b40h": "40% or more below High",
                    "b50h": "50% or more below High",
                    "b60h": "60% or more below High",
                    "b70h": "70% or more below High",
                    "b80h": "80% or more below High",
                    "b90h": "90% or more below High",
                    "b0to3h": "0-3% below High",
                    "b0to5h": "0-5% below High",
                    "b0to10h": "0-10% below High",
                    "a5h": "5% or more above Low",
                    "a10h": "10% or more above Low",
                    "a15h": "15% or more above Low",
                    "a20h": "20% or more above Low",
                    "a30h": "30% or more above Low",
                    "a40h": "40% or more above Low",
                    "a50h": "50% or more above Low",
                    "a60h": "60% or more above Low",
                    "a70h": "70% or more above Low",
                    "a80h": "80% or more above Low",
                    "a90h": "90% or more above Low",
                    "a100h": "100% or more above Low",
                    "a120h": "120% or more above Low",
                    "a150h": "150% or more above Low",
                    "a200h": "200% or more above Low",
                    "a300h": "300% or more above Low",
                    "a500h": "500% or more above Low",
                    "a0to3h": "0-3% above Low",
                    "a0to5h": "0-5% above Low",
                    "a0to10h": "0-10% above Low",
                    "modal": "Custom"
                }
            },
            "ta_alltime": {
                "name": "All-Time High/Low",
                "description": "Maximum/minimum of all-time highs/lows",
                "options": {
                    "": "Any",
                    "nh": "New High",
                    "nl": "New Low",
                    "b5h": "5% or more below High",
                    "b10h": "10% or more below High",
                    "b15h": "15% or more below High",
                    "b20h": "20% or more below High",
                    "b30h": "30% or more below High",
                    "b40h": "40% or more below High",
                    "b50h": "50% or more below High",
                    "b60h": "60% or more below High",
                    "b70h": "70% or more below High",
                    "b80h": "80% or more below High",
                    "b90h": "90% or more below High",
                    "b0to3h": "0-3% below High",
                    "b0to5h": "0-5% below High",
                    "b0to10h": "0-10% below High",
                    "a5h": "5% or more above Low",
                    "a10h": "10% or more above Low",
                    "a15h": "15% or more above Low",
                    "a20h": "20% or more above Low",
                    "a30h": "30% or more above Low",
                    "a40h": "40% or more above Low",
                    "a50h": "50% or more above Low",
                    "a60h": "60% or more above Low",
                    "a70h": "70% or more above Low",
                    "a80h": "80% or more above Low",
                    "a90h": "90% or more above Low",
                    "a100h": "100% or more above Low",
                    "a120h": "120% or more above Low",
                    "a150h": "150% or more above Low",
                    "a200h": "200% or more above Low",
                    "a300h": "300% or more above Low",
                    "a500h": "500% or more above Low",
                    "a0to3h": "0-3% above Low",
                    "a0to5h": "0-5% above Low",
                    "a0to10h": "0-10% above Low",
                    "modal": "Custom"
                }
            },

            # Chart Patterns & Candlesticks
            "ta_pattern": {
                "name": "Pattern",
                "description": "A chart pattern is a distinct formation on a stock chart that creates a trading signal, or a sign of future price movements. Chartists use these patterns to identify current trends and trend reversals and to trigger buy and sell signals",
                "options": {
                    "": "Any",
                    "horizontal": "Horizontal S/R",
                    "horizontal2": "Horizontal S/R (Strong)",
                    "tlresistance": "TL Resistance",
                    "tlresistance2": "TL Resistance (Strong)",
                    "tlsupport": "TL Support",
                    "tlsupport2": "TL Support (Strong)",
                    "wedgeup": "Wedge Up",
                    "wedgeup2": "Wedge Up (Strong)",
                    "wedgedown": "Wedge Down",
                    "wedgedown2": "Wedge Down (Strong)",
                    "wedgeresistance": "Triangle Ascending",
                    "wedgeresistance2": "Triangle Ascending (Strong)",
                    "wedgesupport": "Triangle Descending",
                    "wedgesupport2": "Triangle Descending (Strong)",
                    "wedge": "Wedge",
                    "wedge2": "Wedge (Strong)",
                    "channelup": "Channel Up",
                    "channelup2": "Channel Up (Strong)",
                    "channeldown": "Channel Down",
                    "channeldown2": "Channel Down (Strong)",
                    "channel": "Channel",
                    "channel2": "Channel (Strong)",
                    "doubletop": "Double Top",
                    "doublebottom": "Double Bottom",
                    "multipletop": "Multiple Top",
                    "multiplebottom": "Multiple Bottom",
                    "headandshoulders": "Head & Shoulders",
                    "headandshouldersinv": "Head & Shoulders Inverse",
                    "modal": "Custom"
                }
            },
            "ta_candlestick": {
                "name": "Candlestick",
                "description": "Candlesticks are usually composed of the body (black or white), an upper and a lower shadow (wick). The wick illustrates the highest and lowest traded prices of a stock during the time interval represented. The body illustrates the opening and closing trades",
                "options": {
                    "": "Any",
                    "lls": "Long Lower Shadow",
                    "lus": "Long Upper Shadow",
                    "h": "Hammer",
                    "ih": "Inverted Hammer",
                    "stw": "Spinning Top White",
                    "stb": "Spinning Top Black",
                    "d": "Doji",
                    "dd": "Dragonfly Doji",
                    "gd": "Gravestone Doji",
                    "mw": "Marubozu White",
                    "mb": "Marubozu Black",
                    "modal": "Custom"
                }
            },

            # Beta & ATR
            "ta_beta": {
                "name": "Beta",
                "description": "A measure of a stock's price volatility relative to the market. An asset with a beta of 0 means that its price is not at all correlated with the market. A positive beta means that the asset generally follows the market. A negative beta shows that the asset inversely follows the market, decreases in value if the market goes up",
                "options": {
                    "": "Any",
                    "u0": "Under 0",
                    "u0.5": "Under 0.5",
                    "u1": "Under 1",
                    "u1.5": "Under 1.5",
                    "u2": "Under 2",
                    "o0": "Over 0",
                    "o0.5": "Over 0.5",
                    "o1": "Over 1",
                    "o1.5": "Over 1.5",
                    "o2": "Over 2",
                    "o2.5": "Over 2.5",
                    "o3": "Over 3",
                    "o4": "Over 4",
                    "0to0.5": "0 to 0.5",
                    "0to1": "0 to 1",
                    "0.5to1": "0.5 to 1",
                    "0.5to1.5": "0.5 to 1.5",
                    "1to1.5": "1 to 1.5",
                    "1to2": "1 to 2",
                    "frange": "Custom"
                }
            },
            "ta_averagetruerange": {
                "name": "Average True Range",
                "description": "A measure of stock volatility. The Average True Range is an exponential moving average (14-days) of the True Ranges. The range of a day's trading is high−low, True Range extends it to yesterday's closing price if it was outside of today's range",
                "options": {
                    "": "Any",
                    "o0.25": "Over 0.25",
                    "o0.5": "Over 0.5",
                    "o0.75": "Over 0.75",
                    "o1": "Over 1",
                    "o1.5": "Over 1.5",
                    "o2": "Over 2",
                    "o2.5": "Over 2.5",
                    "o3": "Over 3",
                    "o3.5": "Over 3.5",
                    "o4": "Over 4",
                    "o4.5": "Over 4.5",
                    "o5": "Over 5",
                    "u0.25": "Under 0.25",
                    "u0.5": "Under 0.5",
                    "u0.75": "Under 0.75",
                    "u1": "Under 1",
                    "u1.5": "Under 1.5",
                    "u2": "Under 2",
                    "u2.5": "Under 2.5",
                    "u3": "Under 3",
                    "u3.5": "Under 3.5",
                    "u4": "Under 4",
                    "u4.5": "Under 4.5",
                    "u5": "Under 5",
                    "frange": "Custom"
                }
            },

            # Volume Metrics
            "sh_avgvol": {
                "name": "Average Volume",
                "description": "The average number of shares traded in a security per day",
                "options": {
                    "": "Any",
                    "u50": "Under 50K",
                    "u100": "Under 100K",
                    "u500": "Under 500K",
                    "u750": "Under 750K",
                    "u1000": "Under 1M",
                    "o50": "Over 50K",
                    "o100": "Over 100K",
                    "o200": "Over 200K",
                    "o300": "Over 300K",
                    "o400": "Over 400K",
                    "o500": "Over 500K",
                    "o750": "Over 750K",
                    "o1000": "Over 1M",
                    "o2000": "Over 2M",
                    "100to500": "100K to 500K",
                    "100to1000": "100K to 1M",
                    "500to1000": "500K to 1M",
                    "500to10000": "500K to 10M",
                    "frange": "Custom"
                }
            },
            "sh_relvol": {
                "name": "Relative Volume",
                "description": "Ratio between current volume and 3-month average, intraday adjusted",
                "options": {
                    "": "Any",
                    "o10": "Over 10",
                    "o5": "Over 5",
                    "o3": "Over 3",
                    "o2": "Over 2",
                    "o1.5": "Over 1.5",
                    "o1": "Over 1",
                    "o0.75": "Over 0.75",
                    "o0.5": "Over 0.5",
                    "o0.25": "Over 0.25",
                    "u2": "Under 2",
                    "u1.5": "Under 1.5",
                    "u1": "Under 1",
                    "u0.75": "Under 0.75",
                    "u0.5": "Under 0.5",
                    "u0.25": "Under 0.25",
                    "u0.1": "Under 0.1",
                    "frange": "Custom"
                }
            },
            "sh_curvol": {
                "name": "Current Volume",
                "description": "Number of shares traded today",
                "options": {
                    "": "Any",
                    "u50": "Under 50K shares",
                    "u100": "Under 100K shares",
                    "u500": "Under 500K shares",
                    "u750": "Under 750K shares",
                    "u1000": "Under 1M shares",
                    "o0": "Over 0 shares",
                    "o50": "Over 50K shares",
                    "o100": "Over 100K shares",
                    "o200": "Over 200K shares",
                    "o300": "Over 300K shares",
                    "o400": "Over 400K shares",
                    "o500": "Over 500K shares",
                    "o750": "Over 750K shares",
                    "o1000": "Over 1M shares",
                    "o2000": "Over 2M shares",
                    "o5000": "Over 5M shares",
                    "o10000": "Over 10M shares",
                    "o20000": "Over 20M shares",
                    "o50sf": "Over 50% shares float",
                    "o100sf": "Over 100% shares float",
                    "uusd1000": "Under $1M USD",
                    "uusd10000": "Under $10M USD",
                    "uusd100000": "Under $100M USD",
                    "uusd1000000": "Under $1B USD",
                    "ousd1000": "Over $1M USD",
                    "ousd10000": "Over $10M USD",
                    "ousd100000": "Over $100M USD",
                    "ousd1000000": "Over $1B USD",
                    "modal": "Custom"
                }
            },
            "sh_trades": {
                "name": "Trades",
                "description": "Number of trades today",
                "options": {
                    "": "Any",
                    "u100": "Under 100",
                    "u500": "Under 500",
                    "u1000": "Under 1K",
                    "u5000": "Under 5K",
                    "u10000": "Under 10K",
                    "o0": "Over 0",
                    "o100": "Over 100",
                    "o500": "Over 500",
                    "o1000": "Over 1K",
                    "o5000": "Over 5K",
                    "o10000": "Over 10K",
                    "o15000": "Over 15K",
                    "o20000": "Over 20K",
                    "o50000": "Over 50K",
                    "o100000": "Over 100K",
                    "frange": "Custom"
                }
            },

            # Price Metrics
            "sh_price": {
                "name": "Price $",
                "description": "The current stock price",
                "options": {
                    "": "Any",
                    "u1": "Under $1",
                    "u2": "Under $2",
                    "u3": "Under $3",
                    "u4": "Under $4",
                    "u5": "Under $5",
                    "u7": "Under $7",
                    "u10": "Under $10",
                    "u15": "Under $15",
                    "u20": "Under $20",
                    "u30": "Under $30",
                    "u40": "Under $40"
                }
            }

        }
        return self.success_response(filters_metadata)
