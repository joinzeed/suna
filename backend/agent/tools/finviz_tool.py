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
            }
        }
        return self.success_response(filters_metadata)
