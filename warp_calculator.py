"""
EVE Online Warp Time Calculator

Warp mechanics:
1. Align phase - ship accelerates to 75% max velocity (align_time seconds)
2. Warp phase - ship travels at warp speed (AU/s)
3. Deceleration phase - ship slows down (approximately align_time seconds)

Gate-to-gate jumps add:
- Gate activation time (~10 seconds including loading)
- Warp within system (variable based on distance)
"""

import aiohttp
import asyncio
import math

# EVE coordinate system: 1 meter = 1 unit, 1 AU = 149,597,870,700 meters
METERS_PER_AU = 149_597_870_700

# Cache for system/gate data
system_cache = {}
gate_cache = {}

# Default ship stats (can be customized)
DEFAULT_SHIP_STATS = {
    'align_time_empty': 22.0,      # seconds
    'align_time_full': 44.0,       # seconds (with cargo expanders)
    'warp_speed_empty': 1.5,       # AU/s
    'warp_speed_full': 1.5,        # AU/s (with cargo expanders)
    'gate_activation': 10.0,       # seconds (jump + session change + loading)
}

async def get_system_info(session, system_id):
    """Get system information including stargates"""
    if system_id in system_cache:
        return system_cache[system_id]
    
    try:
        url = f"https://esi.evetech.net/latest/universe/systems/{system_id}/"
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.json()
                system_cache[system_id] = data
                return data
    except Exception as e:
        print(f"Error fetching system {system_id}: {e}")
    return None

async def get_stargate_info(session, stargate_id):
    """Get stargate information including position"""
    if stargate_id in gate_cache:
        return gate_cache[stargate_id]
    
    try:
        url = f"https://esi.evetech.net/latest/universe/stargates/{stargate_id}/"
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.json()
                gate_cache[stargate_id] = data
                return data
    except Exception as e:
        print(f"Error fetching stargate {stargate_id}: {e}")
    return None

async def get_station_info(session, station_id):
    """Get station position"""
    try:
        url = f"https://esi.evetech.net/latest/universe/stations/{station_id}/"
        async with session.get(url) as response:
            if response.status == 200:
                return await response.json()
    except:
        pass
    return None

def calculate_distance_au(pos1, pos2):
    """Calculate distance between two positions in AU"""
    dx = pos1['x'] - pos2['x']
    dy = pos1['y'] - pos2['y']
    dz = pos1['z'] - pos2['z']
    distance_meters = math.sqrt(dx*dx + dy*dy + dz*dz)
    return distance_meters / METERS_PER_AU

def calculate_warp_time(distance_au, warp_speed, align_time):
    """
    Calculate warp time for a given distance
    
    Simplified model:
    - Warp has 3 phases: acceleration, cruise, deceleration
    - For short warps (<1 AU), ship may not reach max speed
    - For longer warps, use: align + cruise + decel
    
    More accurate EVE warp formula:
    - Acceleration takes ~align_time to reach warp
    - Max warp speed is reached after ~3 AU
    - Deceleration is similar to acceleration
    """
    if distance_au <= 0:
        return 0
    
    # Minimum warp time (very short warps)
    min_warp = align_time * 2  # align + decel
    
    # For distances under ~1 AU, use minimum
    if distance_au < 0.5:
        return min_warp + 2  # Add small travel time
    
    # Cruise time at max warp speed
    # Account for acceleration/deceleration phases eating into distance
    effective_distance = max(0, distance_au - 1.0)  # First ~1 AU is accel/decel
    cruise_time = effective_distance / warp_speed
    
    # Total: align + accel + cruise + decel
    # Simplified: align_time covers accel, another align_time for decel
    total_time = align_time + cruise_time + align_time + 3  # +3 for short accel phase
    
    return max(min_warp, total_time)

def calculate_gate_jump_time(align_time, gate_activation=10.0):
    """Time to jump through a stargate (align + activate + session change)"""
    return align_time + gate_activation

async def get_route_gate_distances(session, route_systems):
    """
    Get the gate-to-gate distances within each system along a route
    
    Returns list of distances in AU for each system transition
    """
    if len(route_systems) < 2:
        return []
    
    distances = []
    
    # Get all system info
    system_infos = {}
    for system_id in route_systems:
        info = await get_system_info(session, system_id)
        if info:
            system_infos[system_id] = info
    
    # For each pair of consecutive systems, find the connecting gates
    for i in range(len(route_systems) - 1):
        current_system = route_systems[i]
        next_system = route_systems[i + 1]
        
        current_info = system_infos.get(current_system)
        if not current_info or 'stargates' not in current_info:
            distances.append(10.0)  # Default 10 AU if unknown
            continue
        
        # Find the gate that leads to next system
        exit_gate = None
        entry_gate_id = None
        
        for gate_id in current_info.get('stargates', []):
            gate_info = await get_stargate_info(session, gate_id)
            if gate_info and gate_info.get('destination', {}).get('system_id') == next_system:
                exit_gate = gate_info
                entry_gate_id = gate_info.get('destination', {}).get('stargate_id')
                break
        
        if not exit_gate:
            distances.append(10.0)  # Default
            continue
        
        # Get entry gate in next system
        if entry_gate_id:
            entry_gate = await get_stargate_info(session, entry_gate_id)
        else:
            entry_gate = None
        
        # For within-system distance, we need the next gate in the chain
        # This is the distance from entry gate to the exit gate to the next system
        if i + 2 < len(route_systems):
            # Find the exit gate from next_system to the system after
            next_next_system = route_systems[i + 2]
            next_system_info = system_infos.get(next_system)
            
            if next_system_info and 'stargates' in next_system_info:
                for gate_id in next_system_info.get('stargates', []):
                    gate_info = await get_stargate_info(session, gate_id)
                    if gate_info and gate_info.get('destination', {}).get('system_id') == next_next_system:
                        # Calculate distance from entry gate to this exit gate
                        if entry_gate and gate_info:
                            entry_pos = entry_gate.get('position', {})
                            exit_pos = gate_info.get('position', {})
                            if entry_pos and exit_pos:
                                dist = calculate_distance_au(entry_pos, exit_pos)
                                distances.append(dist)
                            else:
                                distances.append(10.0)
                        else:
                            distances.append(10.0)
                        break
                else:
                    distances.append(10.0)
            else:
                distances.append(10.0)
        else:
            # Last system - just use a default for station warp
            distances.append(5.0)
    
    return distances

def calculate_trip_time(jumps, in_system_distances, ship_stats, is_loaded=False):
    """
    Calculate total time for a one-way trip
    
    Parameters:
    - jumps: number of gate jumps
    - in_system_distances: list of AU distances for in-system warps
    - ship_stats: dict with align times and warp speeds
    - is_loaded: whether ship is carrying cargo
    """
    if is_loaded:
        align_time = ship_stats.get('align_time_full', 14.0)
        warp_speed = ship_stats.get('warp_speed_full', 2.5)
    else:
        align_time = ship_stats.get('align_time_empty', 10.0)
        warp_speed = ship_stats.get('warp_speed_empty', 3.0)
    
    gate_activation = ship_stats.get('gate_activation', 10.0)
    
    total_time = 0
    
    # Time for each gate jump
    for i in range(jumps):
        # Gate activation time
        total_time += gate_activation
        
        # In-system warp to next gate (if we have distance data)
        if i < len(in_system_distances):
            warp_dist = in_system_distances[i]
        else:
            warp_dist = 10.0  # Default 10 AU
        
        total_time += calculate_warp_time(warp_dist, warp_speed, align_time)
    
    # Add time for station undock/dock (approximately)
    total_time += 20  # Undock + dock time
    
    return total_time

def calculate_round_trip_time(jumps, in_system_distances, ship_stats, num_trips=1):
    """
    Calculate total time for round trip(s)
    
    - Outbound: empty cargo (faster)
    - Return: full cargo (slower)
    """
    # Outbound trip (empty)
    outbound_time = calculate_trip_time(jumps, in_system_distances, ship_stats, is_loaded=False)
    
    # Return trip (loaded)
    return_time = calculate_trip_time(jumps, in_system_distances, ship_stats, is_loaded=True)
    
    # Total for all trips
    total_time = (outbound_time + return_time) * num_trips
    
    return {
        'outbound_time': outbound_time,
        'return_time': return_time,
        'single_round_trip': outbound_time + return_time,
        'total_time': total_time,
        'num_trips': num_trips
    }

async def estimate_route_time(origin_system, destination_system, route_systems, ship_stats, num_trips=1):
    """
    Estimate total travel time for a trading route
    """
    async with aiohttp.ClientSession() as session:
        # Get in-system distances along the route
        distances = await get_route_gate_distances(session, route_systems)
        
        jumps = len(route_systems) - 1 if route_systems else 0
        
        return calculate_round_trip_time(jumps, distances, ship_stats, num_trips)

# For simple estimation without detailed gate data
def estimate_simple_route_time(jumps, ship_stats, num_trips=1, avg_system_size_au=10.0):
    """
    Simple time estimate using average system size
    
    Good for quick calculations when gate positions aren't available
    """
    # Create fake distance list
    distances = [avg_system_size_au] * jumps
    
    return calculate_round_trip_time(jumps, distances, ship_stats, num_trips)

def format_time(seconds):
    """Format seconds into human readable time"""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        minutes = int(seconds / 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs}s"
    else:
        hours = int(seconds / 3600)
        minutes = int((seconds % 3600) / 60)
        return f"{hours}h {minutes}m"

def calculate_profit_per_hour(profit, time_seconds):
    """Calculate ISK per hour"""
    if time_seconds <= 0:
        return 0
    hours = time_seconds / 3600
    return profit / hours

