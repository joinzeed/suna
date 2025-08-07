import requests
from urllib.parse import parse_qs, unquote, urlencode
import json
from typing import Dict, List, Optional, Any
from datetime import datetime
from agentpress.tool import Tool, ToolResult, openapi_schema, usage_example
from agentpress.thread_manager import ThreadManager
from sandbox.tool_base import SandboxToolsBase

class MarketChameleonScreener:
    """
    A comprehensive Market Chameleon options screener that follows the WebSearch tool pattern.
    Provides methods for screening options, detecting unusual activity, and analyzing options data.
    """
    
    def __init__(self):
        self.base_url = "https://marketchameleon.com"
        self.session = requests.Session()
        
        # Headers matching the working browser request
        self.headers = {
            'accept': 'application/json, text/javascript, */*; q=0.01',
            'accept-language': 'en,zh-CN;q=0.9,zh;q=0.8',
            'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'origin': 'https://marketchameleon.com',
            'priority': 'u=1, i',
            'referer': 'https://marketchameleon.com/Screeners/Stocks',
            'sec-ch-ua': '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"macOS"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
            'x-mobdisp': 'true',
            'x-requested-with': 'XMLHttpRequest'
        }
        
        # Only endpoint we need
        self.endpoint = '/EquityScreener/EquityScreenerData'
        
        # Valid filter values for each column
        self.valid_market_cap_values = [
            '-Any-', 'Over 100000000000', 'Over 50000000000', 'Over 20000000000',
            'Over 10000000000', 'Over 5000000000', 'Over 1000000000',
            '20000000000 To 100000000000', '1000000000 To 10000000000',
            '500000000 To 1000000000', 'Under 10000000000', 'Under 5000000000',
            'Under 1000000000', 'Under 500000000', 'Under 100000000'
        ]
        
        self.valid_iv30_values = [
            '-Any-', 'Above 5.0', 'Above 10.0', 'Above 20.0', 'Above 30.0',
            'Above 50.0', 'Above 70.0', 'Above 90.0', 'Above 120.0',
            '5.0 To 20.0', '20.0 To 50.0', '50.0 To 100.0',
            'Below 5.0', 'Below 10.0', 'Below 20.0', 'Below 50.0',
            'Below 70.0', 'Below 90.0', 'Below 120.0'
        ]
        
        # Industries list
        self.valid_industries = [
            '-Any-', 'Advertising Agencies', 'Aerospace & Defense', 'Agricultural Inputs',
            'Airlines', 'Airports & Air Services', 'Aluminum', 'Apparel Manufacturing',
            'Apparel Retail', 'Asset Management', 'Auto & Truck Dealerships',
            'Auto Manufacturers', 'Auto Parts', 'Banks-Regional', 'Banks - Diversified',
            'Banks - Regional', 'Banks - Regional - US', 'Beverages - Brewers',
            'Beverages - Non-Alcoholic', 'Beverages - Wineries & Distilleries',
            'Biotechnology', 'Broadcasting', 'Building Materials',
            'Building Products & Equipment', 'Business Equipment & Supplies',
            'Capital Markets', 'Chemicals', 'Coking Coal', 'Communication Equipment',
            'Computer Hardware', 'Confectioners', 'Conglomerates', 'Consulting Services',
            'Consumer Electronics', 'Copper', 'Credit Services', 'Department Stores',
            'Diagnostics & Research', 'Discount Stores', 'Drug Manufacturers - General',
            'Drug Manufacturers - Specialty & Generic', 'Education & Training Services',
            'Electrical Equipment & Parts', 'Electronic Components',
            'Electronic Gaming & Multimedia', 'Electronics & Computer Distribution',
            'Engineering & Construction', 'Entertainment',
            'Farm & Heavy Construction Machinery', 'Farm Products',
            'Financial Conglomerates', 'Financial Data & Stock Exchanges',
            'Food Distribution', 'Footwear & Accessories',
            'Furnishings Fixtures & Appliances', 'Gambling', 'Gold', 'Grocery Stores',
            'Health Information Services', 'Healthcare Plans', 'Home Improvement Retail',
            'Household & Personal Products', 'Independent Oil & Gas',
            'Industrial Distribution', 'Information Technology Services',
            'Infrastructure Operations', 'Insurance - Diversified', 'Insurance - Life',
            'Insurance - Property & Casualty', 'Insurance - Reinsurance',
            'Insurance - Specialty', 'Insurance Brokers', 'Integrated Freight & Logistics',
            'Internet Content & Information', 'Internet Retail', 'Leisure', 'Lodging',
            'Lumber & Wood Production', 'Luxury Goods', 'Marine Shipping',
            'Medical Care Facilities', 'Medical Devices', 'Medical Distribution',
            'Medical Instruments & Supplies', 'Metal Fabrication', 'Mortgage Finance',
            'N/A', 'Oil & Gas Drilling', 'Oil & Gas E&P', 'Oil & Gas Equipment & Services',
            'Oil & Gas Integrated', 'Oil & Gas Midstream', 'Oil & Gas Refining & Marketing',
            'Other Industrial Metals & Mining', 'Other Precious Metals & Mining',
            'Packaged Foods', 'Packaging & Containers', 'Paper & Paper Products',
            'Personal Services', 'Pharmaceutical Retailers',
            'Pollution & Treatment Controls', 'Publishing', 'Railroads',
            'Real Estate - Development', 'Real Estate - Diversified',
            'Real Estate Services', 'Recreational Vehicles', 'REIT-Mortgage',
            'REIT - Diversified', 'REIT - Healthcare Facilities', 'REIT - Hotel & Motel',
            'REIT - Industrial', 'REIT - Mortgage', 'REIT - Office', 'REIT - Residential',
            'REIT - Retail', 'REIT - Specialty', 'Rental & Leasing Services',
            'Residential Construction', 'Resorts & Casinos', 'Restaurants',
            'Scientific & Technical Instruments', 'Security & Protection Services',
            'Semiconductor Equipment & Materials', 'Semiconductors', 'Shell Companies',
            'Silver', 'Software - Application', 'Software - Infrastructure', 'Solar',
            'Specialty Business Services', 'Specialty Chemicals',
            'Specialty Industrial Machinery', 'Specialty Retail',
            'Staffing & Employment Services', 'Steel', 'Telecom Services',
            'Textile Manufacturing', 'Thermal Coal', 'Tobacco', 'Tools & Accessories',
            'Travel Services', 'Trucking', 'Uranium', 'Utilities - Diversified',
            'Utilities - Independent Power Producers', 'Utilities - Regulated Electric',
            'Utilities - Regulated Gas', 'Utilities - Regulated Water',
            'Utilities - Renewable', 'Waste Management'
        ]
    
    def _build_body(self, market_cap: Optional[str] = None, industry: Optional[str] = None, 
                    iv30: Optional[str] = None, sort_column: int = 7, sort_order: str = "desc", 
                    limit: int = 100, start: int = 0) -> str:
        """
        Build a complete DataTables-style request body with only Market Cap, Industry, and IV30 filters.
        
        Args:
            market_cap: Market cap filter value (e.g., 'Over 1000000000')
            industry: Industry filter value (e.g., 'Biotechnology')
            iv30: IV30 filter value (e.g., 'Above 50.0')
            sort_column: Column index to sort by (default 7 = MarketCap)
            sort_order: Sort order ('asc' or 'desc')
            limit: Number of results to return
            start: Starting index for pagination (default 0)
            
        Returns:
            Dictionary of form data in DataTables format
        """
        # Base DataTables structure
        body = {
            'draw': '1',
            'start': str(start),  # Use the start parameter for pagination
            'length': str(limit),
            'search[regex]': 'false',
            'order[0][column]': str(sort_column),
            'order[0][dir]': sort_order
        }
        
        # Full column definitions - MUST match exactly what Market Chameleon expects
        # Note: The column indices don't match the order - they use specific name mappings
        columns = [
            # 0-9
            ('Symbol', 'c0'),
            ('BD.Name', 'c1'),
            ('BD.Px', 'c2'),
            ('BD.PxChgPct', 'c3'),
            ('BD.Volume', 'c6'),  # Note: index 4 uses name c6
            ('BD.AvgVolume', 'c51'),  # Note: index 5 uses name c51
            ('BD.RelVolume', 'c7'),
            ('BD.MarketCap', 'c8'),
            ('BD.DivYield', 'c9'),
            ('Fm.pe_ratio', 'c10'),
            # 10-19
            ('BD.Chg2D', 'c53'),
            ('BD.Chg3D', 'c54'),
            ('BD.Chg4D', 'c55'),
            ('BD.Chg5D', 'c56'),
            ('BD.Chg6D', 'c57'),
            ('BD.Chg7D', 'c58'),
            ('BD.Chg2Wk', 'c11'),
            ('BD.Chg3M', 'c12'),
            ('BD.Chg6M', 'c13'),
            ('BD.Chg1Y', 'c14'),
            # 20-29
            ('BD.ChgYTD', 'c15'),
            ('BD.Chg3Y', 'c200'),
            ('BD.Chg5Y', 'c201'),
            ('BD.ChgCloseToOpen', 'c4'),
            ('BD.ChgOpenPx', 'c5'),
            ('BD.Chg52WLo', 'c16'),
            ('BD.Chg52WHi', 'c17'),
            ('BD.ChgMA20', 'c18'),
            ('BD.ChgMA50', 'c19'),
            ('BD.ChgMA250', 'c20'),
            # 30-39
            ('BD.IV30', 'c21'),
            ('BD.IV30Chg', 'c22'),
            ('BD.OptVolume', 'c23'),
            ('BD.RelOptVolume', 'c24'),
            ('BD.IVRank', 'c25'),
            ('BD.Sector', 'c26'),
            ('BD.Industry', 'c27'),
            ('BD.EquityType', 'c28'),
            ('BD.DivGrowth1Yr', 'c32'),
            ('BD.DivGrowth3Yr', 'c33'),
            # 40-49
            ('BD.PayoutRatio', 'c34'),
            ('BD.RSI14', 'c45'),
            ('BD.DivIncreases3Yr', 'c46'),
            ('BD.DivDecreases3Yr', 'c47'),
            ('BD.Vol1Day', 'c48'),
            ('BD.Vol20Day', 'c49'),
            ('BD.Vol1Year', 'c50'),
            ('BD.Skew25DSort', 'c52'),
            ('BD.Country', 'c80'),
            ('BD.ShrOut', 'c90'),
            # 50-59
            ('BD.ShrFloat', 'c91'),
            ('BD.MA_20D', 'MA_20D'),
            ('BD.MA_50D', 'MA_50D'),
            ('BD.MA_250D', 'MA_250D'),
            ('BD.MA_20v50', '_20MA_vs_50MA'),
            ('BD.MA_20v250', '_20MA_vs_250MA'),
            ('BD.MA_50v250', '_50MA_vs_250MA'),
            ('BD.MA_Name', 'c59'),
            ('BD.StockSD_Out', 'c60'),
            ('BD.DayVWMinSD', 'c81'),
            # 60-69
            ('BD.DayVWAP', 'c82'),
            ('BD.DayVWPlusSD', 'c83'),
            ('BD.PvVWStd', 'c84'),
            ('BD.PvVWPct', 'c85'),
            ('BD.VwSD', 'c86'),
            ('Fm.pe_ratio', 'c101'),
            ('Fm.pe_normalized_eps', 'c102'),
            ('Fm.px_to_sales', 'c103'),
            ('Fm.px_to_bookval', 'c104'),
            ('Fm.px_to_tangiblebookval', 'c105'),
            # 70-79
            ('Fm.peg_ratio', 'c106'),
            ('Fm.px_to_cash', 'c107'),
            ('Fm.px_to_freecashflow', 'c108'),
            ('Fm.px_to_ebitda', 'c109'),
            ('Fm.forward_pe_ratio', 'c110'),
            ('Fm.peg_pay_back', 'c111'),
            ('Fm.price_to_cfo', 'c112'),
            ('Fm.ev_to_ebitda', 'c113'),
            ('Fm.gross_margin', 'c121'),
            ('Fm.ebitda_margin', 'c122'),
            # 80-89
            ('Fm.ebit_margin', 'c123'),
            ('Fm.net_profit_marg', 'c124'),
            ('Fm.norm_net_profit_marg', 'c125'),
            ('Fm.tax_rate', 'c126'),
            ('Fm.return_on_equity', 'c127'),
            ('Fm.sales_per_employee', 'c128'),
            ('Fm.pretax_marg', 'c129'),
            ('Fm.roic', 'c130'),
            ('Fm.ops_marg', 'c131'),
            ('Fm.roa', 'c132'),
            # 90-99
            ('Fm.cash_return', 'c133'),
            ('Fm.debt_to_equity', 'c141'),
            ('Fm.debt_to_assets', 'c142'),
            ('Fm.debt_to_ebitda', 'c143'),
            ('Fm.debt_to_cash', 'c144'),
            ('Fm.interest_coverage', 'c145'),
            ('Fm.quick_ratio', 'c146'),
            ('Fm.current_ratio', 'c147'),
            ('Fm.debt_to_capital', 'c148'),
            ('Fm.financial_leverage', 'c149'),
            # 100-109
            ('Fm.diluted_eps_growth', 'c161'),
            ('Fm.sust_growth_rate', 'c162'),
            ('Fm.revenue_growth', 'c163'),
            ('Fm.ops_income_growth', 'c164'),
            ('Fm.gross_profit_ann_5y_growth', 'c165'),
            ('Fm.cap_expend_ann_5y_growth', 'c166'),
            ('Fm.net_income_growth', 'c167'),
            ('DailyPerf15.PctPos', 'c170'),
            ('DailyPerf15.AvgRet', 'c171'),
            ('DailyPerf15.StdDev', 'c172'),
            # 110-119
            ('DailyPerf15.Sharpe', 'c173'),
            ('DailyPerf30.PctPos', 'c174'),
            ('DailyPerf30.AvgRet', 'c175'),
            ('DailyPerf30.StdDev', 'c176'),
            ('DailyPerf30.Sharpe', 'c177'),
            ('DailyPerf90.PctPos', 'c178'),
            ('DailyPerf90.AvgRet', 'c179'),
            ('DailyPerf90.StdDev', 'c180'),
            ('DailyPerf90.Sharpe', 'c181'),
            ('InWatchlist', 'InWatchlist'),
            # 120-123
            ('BD.EtfHoldingsList', 'c29'),
            ('BD.EarningsType', 'c30'),
            ('BD.HasOptions', 'c31'),
            ('BD.StockIdeas', 'StockIdeas')
        ]
        
        # Add all 124 column definitions
        for i in range(124):
            if i < len(columns):
                data, name = columns[i]
            else:
                # Fill any missing columns with defaults
                data = f'Column{i}'
                name = f'c{i}'
            
            body[f'columns[{i}][data]'] = data
            body[f'columns[{i}][name]'] = name
            body[f'columns[{i}][searchable]'] = 'true'
            body[f'columns[{i}][orderable]'] = 'true'
            body[f'columns[{i}][search][regex]'] = 'false'
        
        # Apply the 3 filters
        # Market Cap is column 7 (not 8)
        if market_cap and market_cap != '-Any-':
            body['columns[7][search][value]'] = market_cap
            
        # Industry is column 36 (not 27)
        if industry and industry != '-Any-':
            body['columns[36][search][value]'] = industry
            
        # IV30 is column 30 (not 21)
        if iv30 and iv30 != '-Any-':
            body['columns[30][search][value]'] = iv30
        
        return urlencode(body)
    
    def screen(
        self,
        market_cap: Optional[str] = None,
        industry: Optional[str] = None,
        iv30: Optional[str] = None,
        sort_by: str = "market_cap",
        sort_order: str = "desc",
        limit: int = 50,
        start: int = 0
    ) -> Dict[str, Any]:
        """
        Simple screening with only 3 filters: Market Cap, Industry, and IV30.
        
        Args:
            market_cap: Market cap filter (e.g., 'Over 1000000000', 'Under 10000000000')
            industry: Industry name (e.g., 'Biotechnology', 'Software - Application')
            iv30: IV30 filter (e.g., 'Above 50.0', 'Below 20.0')
            sort_by: Field to sort by ('market_cap', 'iv30', 'volume')
            sort_order: Sort order (asc/desc)
            limit: Maximum number of results
            start: Starting index for pagination (default 0)
            
        Returns:
            Dictionary containing success status and results/error
        """
        # Validate inputs
        if market_cap and market_cap not in self.valid_market_cap_values:
            return {
                'success': False,
                'error': f'Invalid market_cap value. Must be one of: {self.valid_market_cap_values}'
            }
            
        if industry and industry not in self.valid_industries:
            return {
                'success': False,
                'error': f'Invalid industry. Must be one of: {self.valid_industries}'
            }
            
        if iv30 and iv30 not in self.valid_iv30_values:
            return {
                'success': False,
                'error': f'Invalid iv30 value. Must be one of: {self.valid_iv30_values}'
            }
        
        # Map sort_by to column index for sorting
        # These are the column indices in the DataTable results
        sort_column_map = {
            'market_cap': 7,   # BD.MarketCap data is at column 7
            'iv30': 30,        # BD.IV30 data is at column 30
            'volume': 4,      # BD.Volume data is at column 4
            'opt_volume': 32   # BD.OptVolume data is at column 32
        }
        sort_column = sort_column_map.get(sort_by.lower(), 7)  # Default to market cap
        
        # Build DataTables format body
        body = self._build_body(market_cap, industry, iv30, sort_column, sort_order, limit, start)
        
        # Count active filters
        active_filters = sum(1 for f in [market_cap, industry, iv30] if f and f != '-Any-')
        print(f"Screening with {active_filters} filters, sorting by column {sort_column}")
        
        # Make request
        try:
            response = self.session.post(
                self.base_url + self.endpoint,
                data=body,
                headers=self.headers,
                timeout=30
            )
            
            if response.status_code == 200:
                result = {
                    'success': True,
                    'data': response.json(),
                    'status_code': 200
                }
            else:
                result = {
                    'success': False,
                    'error': f'HTTP {response.status_code}',
                    'status_code': response.status_code
                }
        except Exception as e:
            result = {
                'success': False,
                'error': str(e)
            }
        
        return result
    


class SandboxOptionsScreenerTool(SandboxToolsBase):
    """AI-friendly tool for screening stocks with options using Market Chameleon."""
    
    def __init__(self, project_id: str, thread_manager: ThreadManager):
        super().__init__(project_id, thread_manager)
        self.screener = MarketChameleonScreener()
    
    @openapi_schema({
        "type": "function",
        "function": {
            "name": "screen_stocks_with_options",
            "description": "Screen stocks with options based on Market Cap, Industry, and Implied Volatility (IV30). This tool helps find stocks matching specific criteria for options trading opportunities.",
            "parameters": {
                "type": "object",
                "properties": {
                    "market_cap": {
                        "type": "string",
                        "description": "Market capitalization filter",
                        "enum": [
                            "-Any-", "Over 100000000000", "Over 50000000000", "Over 20000000000",
                            "Over 10000000000", "Over 5000000000", "Over 1000000000",
                            "20000000000 To 100000000000", "1000000000 To 10000000000",
                            "500000000 To 1000000000", "Under 10000000000", "Under 5000000000",
                            "Under 1000000000", "Under 500000000", "Under 100000000"
                        ],
                        "default": "-Any-"
                    },
                    "industry": {
                        "type": "string", 
                        "description": "Industry sector filter (e.g., 'Biotechnology', 'Software - Application', 'Semiconductors')",
                        "default": "-Any-"
                    },
                    "iv30": {
                        "type": "string",
                        "description": "30-day Implied Volatility filter", 
                        "enum": [
                            "-Any-", "Above 5.0", "Above 10.0", "Above 20.0", "Above 30.0",
                            "Above 50.0", "Above 70.0", "Above 90.0", "Above 120.0",
                            "5.0 To 20.0", "20.0 To 50.0", "50.0 To 100.0",
                            "Below 5.0", "Below 10.0", "Below 20.0", "Below 50.0",
                            "Below 70.0", "Below 90.0", "Below 120.0"
                        ],
                        "default": "-Any-"
                    },
                    "sort_by": {
                        "type": "string",
                        "description": "Field to sort results by",
                        "enum": ["market_cap", "iv30", "volume", "opt_volume"],
                        "default": "market_cap"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results to return",
                        "default": 100,
                        "minimum": 1,
                        "maximum": 200
                    },
                    "start": {
                        "type": "integer",
                        "description": "Starting index for pagination (0-based). Use this to fetch next batch of results.",
                        "default": 0,
                        "minimum": 0
                    }
                },
                "required": []  # No required parameters, all have defaults
            }
        }
    })
    @usage_example('''
        <function_calls>
        <invoke name="screen_stocks_with_options">
        <parameter name="market_cap">Over 10000000000</parameter>
        <parameter name="iv30">Above 50.0</parameter>
        <parameter name="industry">Biotechnology</parameter>
        <parameter name="sort_by">iv30</parameter>
        <parameter name="limit">20</parameter>
        </invoke>
        </function_calls>
        
        <!-- Find high volatility tech stocks -->
        <function_calls>
        <invoke name="screen_stocks_with_options">
        <parameter name="industry">Software - Application</parameter>
        <parameter name="iv30">Above 70.0</parameter>
        <parameter name="sort_by">iv30</parameter>
        </invoke>
        </function_calls>
        
        <!-- Get next page of results -->
        <function_calls>
        <invoke name="screen_stocks_with_options">
        <parameter name="market_cap">Over 1000000000</parameter>
        <parameter name="limit">100</parameter>
        <parameter name="start">100</parameter>
        </invoke>
        </function_calls>
        ''')
    async def screen_stocks_with_options(
        self,
        market_cap: str = "-Any-",
        industry: str = "-Any-", 
        iv30: str = "-Any-",
        sort_by: str = "market_cap",
        limit: int = 100,
        start: int = 0
    ) -> ToolResult:
        """Screen stocks with options based on market cap, industry, and implied volatility."""
        try:
            # Ensure sandbox is initialized
            await self._ensure_sandbox()
            
            # Call the screener
            result = self.screener.screen(
                market_cap=market_cap,
                industry=industry,
                iv30=iv30,
                sort_by=sort_by,
                sort_order="desc",
                limit=limit,
                start=start
            )
            
            if result['success']:
                data = result['data']
                total = data.get('recordsTotal', 0)
                filtered = data.get('recordsFiltered', 0)
                results = data.get('data', [])
                
                # Prepare summary (no file saving needed)
                message = f"Found {filtered} stocks matching criteria (from {total} total).\n\n"
                
                if results:
                    message += f"Top {min(5, len(results))} results:\n"
                    for i, row in enumerate(results[:5]):
                        symbol = row.get('Symbol', 'N/A')
                        bd = row.get('BD', {})
                        name = bd.get('Name', 'N/A')
                        mkt_cap = bd.get('MarketCapStr', 'N/A')
                        iv = bd.get('IV30', 'N/A')
                        
                        message += f"{i+1}. {symbol} - {name}\n"
                        message += f"   Market Cap: {mkt_cap}, IV30: {iv}%\n"
                
                return ToolResult(
                    success=True,
                    output=message
                )
            else:
                return ToolResult(
                    success=False,
                    output=f"Screening failed: {result.get('error', 'Unknown error')}"
                )
                
        except Exception as e:
            return ToolResult(
                success=False,
                output=f"Error screening stocks: {str(e)[:200]}"
            )

