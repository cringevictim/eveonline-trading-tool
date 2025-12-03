import aiohttp
import asyncio
from database import get_cached_route, cache_route

# In-memory cache for current session (faster than DB lookups)
# Key: (origin, destination, route_flag)
memory_cache = {}

# Gate camp data cache
gate_camp_cache = {}
GATE_CAMP_CACHE_DURATION = 3600  # 1 hour

# Route flags for ESI
ROUTE_FLAGS = {
    'shortest': 'shortest',
    'secure': 'secure',      # Highsec only
    'insecure': 'insecure'   # Prefer lowsec/nullsec
}

async def get_jumps_async(session, origin, destination, route_flag='secure'):
    """Get jumps between systems using ESI API (async)"""
    if origin == destination:
        return 0
    
    cache_key = (origin, destination, route_flag)
    
    # Check memory cache first
    if cache_key in memory_cache:
        return memory_cache[cache_key]
    
    # Check database cache (only for secure routes to save space)
    if route_flag == 'secure':
        cached = get_cached_route(origin, destination)
        if cached is not None:
            memory_cache[cache_key] = cached
            return cached
    
    # Fetch from ESI
    try:
        url = f"https://esi.evetech.net/latest/route/{origin}/{destination}/"
        async with session.get(url, params={"flag": route_flag}) as response:
            if response.status == 200:
                route = await response.json()
                jumps = len(route) - 1
                # Cache in memory
                memory_cache[cache_key] = jumps
                # Cache in database for secure routes
                if route_flag == 'secure':
                    cache_route(origin, destination, jumps)
                return jumps
            elif response.status == 404:
                # No route exists
                memory_cache[cache_key] = None
                if route_flag == 'secure':
                    cache_route(origin, destination, -1)
                return None
            else:
                return None
    except Exception as e:
        print(f"Route error: {e}")
        return None

async def get_route_systems_async(session, origin, destination, route_flag='secure'):
    """Get the full route (list of system IDs) between two systems"""
    if origin == destination:
        return [origin]
    
    try:
        url = f"https://esi.evetech.net/latest/route/{origin}/{destination}/"
        async with session.get(url, params={"flag": route_flag}) as response:
            if response.status == 200:
                return await response.json()
            return None
    except Exception as e:
        print(f"Route error: {e}")
        return None

async def check_gate_camps_async(session, system_ids):
    """Check for gate camps using zkillboard - returns dict of system_id: kill_count"""
    if not system_ids:
        return {}
    
    gate_camps = {}
    
    # Check zkillboard for recent kills in these systems
    for system_id in system_ids:
        # Skip if recently checked
        if system_id in gate_camp_cache:
            gate_camps[system_id] = gate_camp_cache[system_id]
            continue
        
        try:
            # Get kills in last hour for this system
            url = f"https://zkillboard.com/api/kills/solarSystemID/{system_id}/pastSeconds/3600/"
            headers = {
                'User-Agent': 'EVE Trading Tool - Contact: github.com/eve-trading-tool',
                'Accept-Encoding': 'gzip'
            }
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    kills = await response.json()
                    kill_count = len(kills) if isinstance(kills, list) else 0
                    gate_camps[system_id] = kill_count
                    gate_camp_cache[system_id] = kill_count
                else:
                    gate_camps[system_id] = 0
        except Exception as e:
            gate_camps[system_id] = 0
        
        # Rate limit for zkillboard
        await asyncio.sleep(0.1)
    
    return gate_camps

async def get_route_danger_async(session, origin, destination, route_flag='secure'):
    """Get route and check for dangerous systems along the way"""
    route = await get_route_systems_async(session, origin, destination, route_flag)
    if not route:
        return None, 0
    
    # Check for gate camps (skip first and last system)
    transit_systems = route[1:-1] if len(route) > 2 else []
    gate_camps = await check_gate_camps_async(session, transit_systems)
    
    danger_score = sum(gate_camps.values())
    return route, danger_score

async def batch_get_jumps(route_pairs, route_flag='secure'):
    """Get jumps for multiple routes in parallel"""
    connector = aiohttp.TCPConnector(limit=20)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [get_jumps_async(session, origin, dest, route_flag) for origin, dest in route_pairs]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return results

async def batch_get_routes_with_danger(route_pairs, route_flag='secure', check_camps=False):
    """Get routes with optional danger scoring"""
    connector = aiohttp.TCPConnector(limit=10)
    results = []
    
    async with aiohttp.ClientSession(connector=connector) as session:
        for origin, dest in route_pairs:
            if check_camps:
                route, danger = await get_route_danger_async(session, origin, dest, route_flag)
                jumps = len(route) - 1 if route else None
                results.append({'jumps': jumps, 'danger': danger, 'route': route})
            else:
                jumps = await get_jumps_async(session, origin, dest, route_flag)
                results.append({'jumps': jumps, 'danger': 0, 'route': None})
    
    return results

def get_jumps_sync(origin, destination, route_flag='secure'):
    """Synchronous wrapper for getting jumps"""
    if origin == destination:
        return 0
    
    cache_key = (origin, destination, route_flag)
    
    # Check memory cache
    if cache_key in memory_cache:
        result = memory_cache[cache_key]
        return None if result == -1 else result
    
    # Check database cache
    if route_flag == 'secure':
        cached = get_cached_route(origin, destination)
        if cached is not None:
            if cached == -1:
                memory_cache[cache_key] = None
                return None
            memory_cache[cache_key] = cached
            return cached
    
    # Need to fetch - use async
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(_fetch_single_route(origin, destination, route_flag))
        return result
    finally:
        loop.close()

async def _fetch_single_route(origin, destination, route_flag='secure'):
    """Fetch a single route asynchronously"""
    async with aiohttp.ClientSession() as session:
        return await get_jumps_async(session, origin, destination, route_flag)

def preload_routes_from_db():
    """Load all cached routes into memory on startup"""
    from database import get_db
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT origin_system_id, destination_system_id, jumps FROM routes")
        for row in cursor.fetchall():
            key = (row['origin_system_id'], row['destination_system_id'], 'secure')
            memory_cache[key] = row['jumps'] if row['jumps'] != -1 else None
    print(f"Loaded {len(memory_cache)} cached routes into memory")

def clear_gate_camp_cache():
    """Clear the gate camp cache"""
    global gate_camp_cache
    gate_camp_cache = {}
