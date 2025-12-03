import sqlite3
from contextlib import contextmanager

DB_PATH = 'trading.db'

def init_db():
    """Initialize database with clean schema"""
    with get_db() as conn:
        cursor = conn.cursor()
        
        # Routes cache - persistent storage for jump calculations
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS routes (
                origin_system_id INTEGER,
                destination_system_id INTEGER,
                jumps INTEGER,
                PRIMARY KEY (origin_system_id, destination_system_id)
            )
        """)
        
        # Market orders - current snapshot
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id INTEGER PRIMARY KEY,
                type_id INTEGER,
                type_name TEXT,
                is_buy_order INTEGER,
                price REAL,
                volume INTEGER,
                system_id INTEGER,
                system_name TEXT,
                station_id INTEGER,
                station_name TEXT,
                security REAL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Trade opportunities - calculated profitable routes
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type_id INTEGER,
                type_name TEXT,
                buy_price REAL,
                sell_price REAL,
                amount INTEGER,
                volume_m3 REAL,
                profit REAL,
                profit_mil REAL,
                isk_per_m3 REAL,
                jumps INTEGER,
                trips INTEGER,
                total_jumps INTEGER,
                profit_per_jump REAL,
                from_system_id INTEGER,
                from_station_id INTEGER,
                from_station_name TEXT,
                from_security REAL,
                to_system_id INTEGER,
                to_station_id INTEGER,
                to_station_name TEXT,
                to_security REAL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Scan history - track what we've scanned
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scan_history (
                group_id INTEGER PRIMARY KEY,
                group_name TEXT,
                last_scan TIMESTAMP,
                item_count INTEGER
            )
        """)
        
        # Create indexes for faster queries
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_type ON orders(type_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_buy ON orders(is_buy_order)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_profit ON trades(profit_per_jump DESC)")
        
        conn.commit()

@contextmanager
def get_db():
    """Context manager for database connections"""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def clear_orders():
    """Clear all orders for fresh scan"""
    with get_db() as conn:
        conn.execute("DELETE FROM orders")
        conn.commit()

def clear_trades():
    """Clear all trades for fresh calculation"""
    with get_db() as conn:
        conn.execute("DELETE FROM trades")
        conn.commit()

def get_cached_route(origin, destination):
    """Get cached route from database"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT jumps FROM routes WHERE origin_system_id = ? AND destination_system_id = ?",
            (origin, destination)
        )
        result = cursor.fetchone()
        return result['jumps'] if result else None

def cache_route(origin, destination, jumps):
    """Cache a route in the database"""
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO routes (origin_system_id, destination_system_id, jumps) VALUES (?, ?, ?)",
            (origin, destination, jumps)
        )
        conn.commit()

def insert_order(order_data):
    """Insert a single order"""
    with get_db() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO orders 
            (order_id, type_id, type_name, is_buy_order, price, volume, 
             system_id, system_name, station_id, station_name, security)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, order_data)
        conn.commit()

def insert_orders_batch(orders):
    """Insert multiple orders efficiently"""
    with get_db() as conn:
        conn.executemany("""
            INSERT OR REPLACE INTO orders 
            (order_id, type_id, type_name, is_buy_order, price, volume, 
             system_id, system_name, station_id, station_name, security)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, orders)
        conn.commit()

def insert_trade(trade_data):
    """Insert a trade opportunity"""
    with get_db() as conn:
        conn.execute("""
            INSERT INTO trades 
            (type_id, type_name, buy_price, sell_price, amount, volume_m3,
             profit, profit_mil, isk_per_m3, jumps, trips, total_jumps, profit_per_jump,
             from_system_id, from_station_id, from_station_name, from_security,
             to_system_id, to_station_id, to_station_name, to_security)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, trade_data)
        conn.commit()

def get_top_trades(limit=50, sort_by='profit_per_jump'):
    """Get top trades sorted by specified column"""
    valid_sorts = ['profit_per_jump', 'profit', 'profit_mil', 'isk_per_m3', 'jumps']
    if sort_by not in valid_sorts:
        sort_by = 'profit_per_jump'
    
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(f"""
            SELECT * FROM trades 
            ORDER BY {sort_by} DESC 
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]

def get_scan_stats():
    """Get scanning statistics"""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM orders")
        orders = cursor.fetchone()['count']
        cursor.execute("SELECT COUNT(*) as count FROM trades")
        trades = cursor.fetchone()['count']
        cursor.execute("SELECT COUNT(*) as count FROM routes")
        routes = cursor.fetchone()['count']
        return {'orders': orders, 'trades': trades, 'cached_routes': routes}

