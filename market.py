import aiohttp
import asyncio
import math
from database import insert_orders_batch, insert_trade, clear_orders, clear_trades, get_db
from pathfinder import batch_get_jumps

# Security level thresholds
SECURITY_LEVELS = {
    'highsec': (0.5, 1.0),
    'lowsec': (0.1, 0.45),
    'nullsec': (-1.0, 0.0)
}

# Trade modes
TRADE_MODES = {
    'instant': 'Buy from sell orders, sell to buy orders (instant)',
    'buy_orders': 'Place buy orders, sell to buy orders',
    'sell_orders': 'Buy from sell orders, place sell orders',
    'patient': 'Place buy orders, place sell orders (most patient)'
}

class MarketScanner:
    def __init__(self):
        self.status = "idle"
        self.progress = 0
        self.current_item = ""
        self.total_items = 0
        self.scanned_items = 0
        self.settings = {
            'min_profit': 10_000_000,
            'cargo_capacity': 830000,
            'group_id': 533,
            'regions': ['highsec'],  # Which regions to include
            'route_flag': 'secure',   # shortest, secure, insecure
            'trade_mode': 'instant'   # instant, buy_orders, sell_orders, patient
        }
    
    def get_min_security(self):
        """Get minimum security based on selected regions"""
        regions = self.settings.get('regions', ['highsec'])
        if 'nullsec' in regions:
            return -1.0
        elif 'lowsec' in regions:
            return 0.1
        else:
            return 0.5
    
    def is_security_allowed(self, security):
        """Check if a security level is allowed based on settings"""
        regions = self.settings.get('regions', ['highsec'])
        
        if security >= 0.5 and 'highsec' in regions:
            return True
        if 0.1 <= security < 0.5 and 'lowsec' in regions:
            return True
        if security < 0.1 and 'nullsec' in regions:
            return True
        return False
    
    async def fetch_json(self, session, url):
        """Fetch JSON from URL with error handling"""
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    return await response.json()
                return None
        except Exception as e:
            print(f"Fetch error: {e}")
            return None
    
    async def get_market_groups(self, session):
        """Get all market groups"""
        url = "https://evetycoon.com/api/v1/market/groups"
        return await self.fetch_json(session, url)
    
    async def get_group_types(self, session, group_id):
        """Get all types in a market group"""
        url = f"https://evetycoon.com/api/v1/market/groups/{group_id}/types"
        return await self.fetch_json(session, url)
    
    async def get_orders(self, session, type_id):
        """Get orders for a specific type"""
        url = f"https://evetycoon.com/api/v1/market/orders/{type_id}"
        return await self.fetch_json(session, url)
    
    def expand_groups(self, groups, parent_id):
        """Recursively expand market groups to find all leaf groups with types"""
        result = []
        for group in groups:
            if len(group) == 8 and int(group.get("parentGroupID", 0)) == parent_id:
                if group.get("hasTypes", False):
                    result.append(group["marketGroupID"])
                else:
                    result.extend(self.expand_groups(groups, group["marketGroupID"]))
        return result
    
    async def scan_group(self, group_id, min_profit):
        """Scan a market group for trading opportunities"""
        self.status = "scanning"
        self.progress = 0
        self.settings['group_id'] = group_id
        self.settings['min_profit'] = min_profit
        
        clear_orders()
        clear_trades()
        
        connector = aiohttp.TCPConnector(limit=10)
        async with aiohttp.ClientSession(connector=connector) as session:
            # Get market groups
            self.current_item = "Loading market groups..."
            groups = await self.get_market_groups(session)
            if not groups:
                self.status = "error"
                return
            
            # Find all leaf groups
            leaf_groups = self.expand_groups(groups, group_id)
            
            # Get all type IDs
            all_types = []
            for gid in leaf_groups:
                types = await self.get_group_types(session, gid)
                if types:
                    for t in types:
                        all_types.append({
                            'type_id': t['typeID'],
                            'type_name': t['typeName']
                        })
            
            self.total_items = len(all_types)
            self.scanned_items = 0
            
            # Process items in batches
            batch_size = 5
            for i in range(0, len(all_types), batch_size):
                batch = all_types[i:i+batch_size]
                tasks = [self.process_item(session, item) for item in batch]
                await asyncio.gather(*tasks)
                self.scanned_items += len(batch)
                self.progress = int((self.scanned_items / self.total_items) * 100)
        
        self.status = "complete"
        self.progress = 100
    
    async def process_item(self, session, item):
        """Process a single item type"""
        type_id = item['type_id']
        type_name = item['type_name']
        self.current_item = type_name
        
        data = await self.get_orders(session, type_id)
        if not data or 'orders' not in data:
            return
        
        orders = data['orders']
        systems = data.get('systems', {})
        item_volume = data.get('itemType', {}).get('volume', 1)
        
        # Separate buy and sell orders, filter by security
        buy_orders = []
        sell_orders = []
        orders_to_insert = []
        
        for order in orders:
            system_id = order['systemId']
            security = systems.get(str(system_id), {}).get('security', 0)
            
            # Check if this security level is allowed
            if not self.is_security_allowed(security):
                continue
            
            station_name = self.get_station_name(order['locationId'], data)
            system_name = systems.get(str(system_id), {}).get('name', 'Unknown')
            
            order_tuple = (
                order['orderId'],
                type_id,
                type_name,
                1 if order['isBuyOrder'] else 0,
                order['price'],
                order['volumeRemain'],
                system_id,
                system_name,
                order['locationId'],
                station_name,
                security
            )
            orders_to_insert.append(order_tuple)
            
            order_data = {
                'price': order['price'],
                'volume': order['volumeRemain'],
                'system_id': system_id,
                'station_id': order['locationId'],
                'station_name': station_name,
                'security': security
            }
            
            if order['isBuyOrder']:
                buy_orders.append(order_data)
            else:
                sell_orders.append(order_data)
        
        # Insert orders
        if orders_to_insert:
            insert_orders_batch(orders_to_insert)
        
        # Find profitable trades based on trade mode
        await self.find_trades(type_id, type_name, item_volume, sell_orders, buy_orders)
    
    def get_station_name(self, location_id, data):
        """Get station name from API data"""
        if location_id > 70000000:
            return data.get('structureNames', {}).get(str(location_id), 'Unknown Structure')
        else:
            return data.get('stationNames', {}).get(str(location_id), 'Unknown Station')
    
    async def find_trades(self, type_id, type_name, item_volume, sell_orders, buy_orders):
        """Find profitable trading opportunities based on trade mode"""
        min_profit = self.settings['min_profit']
        cargo = self.settings['cargo_capacity']
        trade_mode = self.settings.get('trade_mode', 'instant')
        route_flag = self.settings.get('route_flag', 'secure')
        
        potential_trades = []
        
        if trade_mode == 'instant':
            # Buy from sell orders, sell to buy orders (traditional hauling)
            potential_trades = self._find_instant_trades(sell_orders, buy_orders, min_profit, item_volume)
        
        elif trade_mode == 'buy_orders':
            # Place buy orders (cheaper), sell to existing buy orders
            # Compare lowest sell order price to buy order prices
            potential_trades = self._find_buy_order_trades(sell_orders, buy_orders, min_profit, item_volume)
        
        elif trade_mode == 'sell_orders':
            # Buy from sell orders, place sell orders (higher price)
            potential_trades = self._find_sell_order_trades(sell_orders, buy_orders, min_profit, item_volume)
        
        elif trade_mode == 'patient':
            # Place buy orders AND place sell orders (maximum profit, longest wait)
            potential_trades = self._find_patient_trades(sell_orders, buy_orders, min_profit, item_volume)
        
        if not potential_trades:
            return
        
        # Collect unique routes
        route_pairs = list(set((t['origin'], t['dest']) for t in potential_trades))
        
        # Get jumps for all routes
        jumps_results = await batch_get_jumps(route_pairs, route_flag)
        route_data = {route_pairs[i]: jumps_results[i] for i in range(len(route_pairs))}
        
        # Process trades with route data
        for trade in potential_trades:
            jumps = route_data.get((trade['origin'], trade['dest']))
            
            if jumps is None or isinstance(jumps, Exception):
                continue
            
            volume_m3 = trade['volume'] * item_volume
            trips = math.ceil(volume_m3 / cargo)
            total_jumps = jumps * 2 * trips
            profit_per_jump = trade['profit'] / total_jumps if total_jumps > 0 else trade['profit']
            
            trade_data = (
                type_id,
                type_name,
                trade['buy_price'],
                trade['sell_price'],
                trade['volume'],
                volume_m3,
                trade['profit'],
                trade['profit'] / 1_000_000,
                trade['profit'] / volume_m3 if volume_m3 > 0 else 0,
                jumps,
                trips,
                total_jumps,
                profit_per_jump,
                trade['origin'],
                trade['origin_station'],
                trade['origin_name'],
                trade.get('origin_security', 0),
                trade['dest'],
                trade['dest_station'],
                trade['dest_name'],
                trade.get('dest_security', 0)
            )
            insert_trade(trade_data)
    
    def _find_instant_trades(self, sell_orders, buy_orders, min_profit, item_volume):
        """Find instant trades: buy from sell orders, sell to buy orders"""
        trades = []
        for sell in sell_orders:
            for buy in buy_orders:
                if sell['price'] >= buy['price']:
                    continue
                
                volume = min(sell['volume'], buy['volume'])
                profit = int((buy['price'] - sell['price']) * volume)
                
                if profit < min_profit:
                    continue
                
                trades.append({
                    'buy_price': sell['price'],
                    'sell_price': buy['price'],
                    'volume': volume,
                    'profit': profit,
                    'origin': sell['system_id'],
                    'origin_station': sell['station_id'],
                    'origin_name': sell['station_name'],
                    'origin_security': sell.get('security', 0),
                    'dest': buy['system_id'],
                    'dest_station': buy['station_id'],
                    'dest_name': buy['station_name'],
                    'dest_security': buy.get('security', 0)
                })
        return trades
    
    def _find_buy_order_trades(self, sell_orders, buy_orders, min_profit, item_volume):
        """Find trades using buy orders: place buy order cheaper than sell orders, sell to buy orders"""
        trades = []
        if not sell_orders or not buy_orders:
            return trades
        
        # Get lowest sell price per station to undercut
        station_min_sell = {}
        for sell in sell_orders:
            key = sell['station_id']
            if key not in station_min_sell or sell['price'] < station_min_sell[key]['price']:
                station_min_sell[key] = sell
        
        # For each location with sell orders, calculate potential profit
        # by placing a buy order slightly below the sell price
        for station_id, sell in station_min_sell.items():
            # Place buy order at 95% of sell price
            buy_order_price = sell['price'] * 0.95
            
            for buy in buy_orders:
                if buy_order_price >= buy['price']:
                    continue
                
                volume = min(sell['volume'], buy['volume'])
                profit = int((buy['price'] - buy_order_price) * volume)
                
                if profit < min_profit:
                    continue
                
                trades.append({
                    'buy_price': buy_order_price,
                    'sell_price': buy['price'],
                    'volume': volume,
                    'profit': profit,
                    'origin': sell['system_id'],
                    'origin_station': sell['station_id'],
                    'origin_name': sell['station_name'] + ' (Buy Order)',
                    'origin_security': sell.get('security', 0),
                    'dest': buy['system_id'],
                    'dest_station': buy['station_id'],
                    'dest_name': buy['station_name'],
                    'dest_security': buy.get('security', 0)
                })
        return trades
    
    def _find_sell_order_trades(self, sell_orders, buy_orders, min_profit, item_volume):
        """Find trades using sell orders: buy from sell orders, place sell order higher than buy orders"""
        trades = []
        if not sell_orders or not buy_orders:
            return trades
        
        # Get highest buy price per station
        station_max_buy = {}
        for buy in buy_orders:
            key = buy['station_id']
            if key not in station_max_buy or buy['price'] > station_max_buy[key]['price']:
                station_max_buy[key] = buy
        
        for sell in sell_orders:
            for station_id, buy in station_max_buy.items():
                # Place sell order at 105% of buy price
                sell_order_price = buy['price'] * 1.05
                
                if sell['price'] >= sell_order_price:
                    continue
                
                volume = min(sell['volume'], buy['volume'])
                profit = int((sell_order_price - sell['price']) * volume)
                
                if profit < min_profit:
                    continue
                
                trades.append({
                    'buy_price': sell['price'],
                    'sell_price': sell_order_price,
                    'volume': volume,
                    'profit': profit,
                    'origin': sell['system_id'],
                    'origin_station': sell['station_id'],
                    'origin_name': sell['station_name'],
                    'origin_security': sell.get('security', 0),
                    'dest': buy['system_id'],
                    'dest_station': buy['station_id'],
                    'dest_name': buy['station_name'] + ' (Sell Order)',
                    'dest_security': buy.get('security', 0)
                })
        return trades
    
    def _find_patient_trades(self, sell_orders, buy_orders, min_profit, item_volume):
        """Find patient trades: place buy orders AND place sell orders"""
        trades = []
        if not sell_orders or not buy_orders:
            return trades
        
        # Get reference prices
        station_min_sell = {}
        for sell in sell_orders:
            key = sell['station_id']
            if key not in station_min_sell or sell['price'] < station_min_sell[key]['price']:
                station_min_sell[key] = sell
        
        station_max_buy = {}
        for buy in buy_orders:
            key = buy['station_id']
            if key not in station_max_buy or buy['price'] > station_max_buy[key]['price']:
                station_max_buy[key] = buy
        
        for sell_station, sell in station_min_sell.items():
            for buy_station, buy in station_max_buy.items():
                # Buy at 95% of sell price, sell at 105% of buy price
                buy_order_price = sell['price'] * 0.95
                sell_order_price = buy['price'] * 1.05
                
                if buy_order_price >= sell_order_price:
                    continue
                
                volume = min(sell['volume'], buy['volume'])
                profit = int((sell_order_price - buy_order_price) * volume)
                
                if profit < min_profit:
                    continue
                
                trades.append({
                    'buy_price': buy_order_price,
                    'sell_price': sell_order_price,
                    'volume': volume,
                    'profit': profit,
                    'origin': sell['system_id'],
                    'origin_station': sell['station_id'],
                    'origin_name': sell['station_name'] + ' (Buy Order)',
                    'origin_security': sell.get('security', 0),
                    'dest': buy['system_id'],
                    'dest_station': buy['station_id'],
                    'dest_name': buy['station_name'] + ' (Sell Order)',
                    'dest_security': buy.get('security', 0)
                })
        return trades

# Global scanner instance
scanner = MarketScanner()

def run_scan(group_id, min_profit, cargo_capacity, regions, route_flag, trade_mode):
    """Run a market scan (called from Flask)"""
    scanner.settings['cargo_capacity'] = cargo_capacity
    scanner.settings['regions'] = regions
    scanner.settings['route_flag'] = route_flag
    scanner.settings['trade_mode'] = trade_mode
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(scanner.scan_group(group_id, min_profit))
    finally:
        loop.close()

def get_scanner_status():
    """Get current scanner status"""
    return {
        'status': scanner.status,
        'progress': scanner.progress,
        'current_item': scanner.current_item,
        'scanned': scanner.scanned_items,
        'total': scanner.total_items
    }
