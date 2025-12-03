"""
EVE Online SSO Authentication Module
Uses OAuth 2.0 for character authentication
"""

import os
import base64
import hashlib
import secrets
import requests
from urllib.parse import urlencode

# EVE SSO Configuration
CLIENT_ID = "7372242eb6a74669bbb128b6aae345b6"
CALLBACK_URL = "http://localhost:5000/callback"

# EVE SSO Endpoints
SSO_AUTH_URL = "https://login.eveonline.com/v2/oauth/authorize"
SSO_TOKEN_URL = "https://login.eveonline.com/v2/oauth/token"
SSO_VERIFY_URL = "https://esi.evetech.net/verify/"
ESI_BASE_URL = "https://esi.evetech.net/latest"

# Request timeout (seconds)
REQUEST_TIMEOUT = 10


def esi_get(url, headers=None, params=None):
    """Make a GET request with timeout and error handling"""
    try:
        response = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        return response
    except requests.exceptions.Timeout:
        print(f"ESI request timed out: {url}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"ESI request failed: {url} - {e}")
        return None


def esi_post(url, data=None, headers=None, json_data=None):
    """Make a POST request with timeout and error handling"""
    try:
        if json_data:
            response = requests.post(url, json=json_data, headers=headers, timeout=REQUEST_TIMEOUT)
        else:
            response = requests.post(url, data=data, headers=headers, timeout=REQUEST_TIMEOUT)
        return response
    except requests.exceptions.Timeout:
        print(f"ESI request timed out: {url}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"ESI request failed: {url} - {e}")
        return None

# Scopes we need
SCOPES = [
    "esi-location.read_location.v1",
    "esi-location.read_ship_type.v1", 
    "esi-ui.write_waypoint.v1",
    "esi-ui.open_window.v1",
    "esi-wallet.read_character_wallet.v1",
    "esi-skills.read_skills.v1"
]


def generate_code_verifier():
    """Generate PKCE code verifier"""
    return secrets.token_urlsafe(32)


def generate_code_challenge(verifier):
    """Generate PKCE code challenge from verifier"""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode()


def get_auth_url(state, code_verifier):
    """Generate the SSO authorization URL"""
    code_challenge = generate_code_challenge(code_verifier)
    
    params = {
        "response_type": "code",
        "redirect_uri": CALLBACK_URL,
        "client_id": CLIENT_ID,
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256"
    }
    
    return f"{SSO_AUTH_URL}?{urlencode(params)}"


def exchange_code_for_token(code, code_verifier):
    """Exchange authorization code for access token"""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": CLIENT_ID,
        "code_verifier": code_verifier
    }
    
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Host": "login.eveonline.com"
    }
    
    response = requests.post(SSO_TOKEN_URL, data=data, headers=headers)
    
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Token exchange failed: {response.status_code} - {response.text}")
        return None


def refresh_access_token(refresh_token):
    """Refresh an expired access token"""
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": CLIENT_ID
    }
    
    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }
    
    response = requests.post(SSO_TOKEN_URL, data=data, headers=headers)
    
    if response.status_code == 200:
        return response.json()
    return None


def decode_jwt_payload(token):
    """Decode JWT payload without verification (we trust EVE's token)"""
    import json
    # JWT format: header.payload.signature
    parts = token.split('.')
    if len(parts) != 3:
        return None
    
    # Decode payload (add padding if needed)
    payload = parts[1]
    padding = 4 - len(payload) % 4
    if padding != 4:
        payload += '=' * padding
    
    try:
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception as e:
        print(f"JWT decode error: {e}")
        return None


def verify_token(access_token):
    """Verify token and get character info"""
    # First try to decode JWT directly
    jwt_data = decode_jwt_payload(access_token)
    if jwt_data:
        # JWT contains 'sub' in format "CHARACTER:EVE:123456789"
        sub = jwt_data.get('sub', '')
        if sub and ':' in sub:
            parts = sub.split(':')
            if len(parts) >= 3:
                return {
                    'CharacterID': int(parts[2]),
                    'CharacterName': jwt_data.get('name', 'Unknown'),
                    'sub': sub,
                    'name': jwt_data.get('name', 'Unknown')
                }
    
    # Fallback to verify endpoint
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(SSO_VERIFY_URL, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        # Normalize response format
        return {
            'CharacterID': data.get('CharacterID'),
            'CharacterName': data.get('CharacterName'),
            'sub': f"CHARACTER:EVE:{data.get('CharacterID')}",
            'name': data.get('CharacterName')
        }
    return None


def get_character_public_info(character_id):
    """Get public character information"""
    url = f"{ESI_BASE_URL}/characters/{character_id}/"
    response = esi_get(url)
    
    if response and response.status_code == 200:
        return response.json()
    return None


def get_character_portrait(character_id):
    """Get character portrait URLs"""
    url = f"{ESI_BASE_URL}/characters/{character_id}/portrait/"
    response = esi_get(url)
    
    if response and response.status_code == 200:
        return response.json()
    return None


def get_character_location(character_id, access_token):
    """Get character's current location"""
    url = f"{ESI_BASE_URL}/characters/{character_id}/location/"
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        return response.json()
    return None


def get_character_ship(character_id, access_token):
    """Get character's current ship"""
    url = f"{ESI_BASE_URL}/characters/{character_id}/ship/"
    headers = {"Authorization": f"Bearer {access_token}"}
    response = esi_get(url, headers=headers)
    
    if response and response.status_code == 200:
        return response.json()
    return None


def get_character_wallet(character_id, access_token):
    """Get character's wallet balance"""
    url = f"{ESI_BASE_URL}/characters/{character_id}/wallet/"
    headers = {"Authorization": f"Bearer {access_token}"}
    response = esi_get(url, headers=headers)
    
    if response and response.status_code == 200:
        return response.json()  # Returns ISK balance as float
    return None


def get_ship_type_info(type_id):
    """Get ship type information"""
    url = f"{ESI_BASE_URL}/universe/types/{type_id}/"
    response = esi_get(url)
    
    if response and response.status_code == 200:
        return response.json()
    return None


def get_system_name(system_id):
    """Get solar system name"""
    url = f"{ESI_BASE_URL}/universe/systems/{system_id}/"
    response = esi_get(url)
    
    if response and response.status_code == 200:
        data = response.json()
        return data.get('name', 'Unknown')
    return 'Unknown'


def get_system_info(system_id):
    """Get solar system info including security status"""
    url = f"{ESI_BASE_URL}/universe/systems/{system_id}/"
    response = esi_get(url)
    
    if response and response.status_code == 200:
        data = response.json()
        return {
            'name': data.get('name', 'Unknown'),
            'security_status': data.get('security_status', 0)
        }
    return {'name': 'Unknown', 'security_status': 0}


def get_station_name(station_id):
    """Get station name"""
    url = f"{ESI_BASE_URL}/universe/stations/{station_id}/"
    response = requests.get(url)
    
    if response.status_code == 200:
        data = response.json()
        return data.get('name', 'Unknown')
    return None


def get_structure_name(structure_id, access_token):
    """Get structure name (requires auth for player structures)"""
    url = f"{ESI_BASE_URL}/universe/structures/{structure_id}/"
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        return data.get('name', 'Unknown Structure')
    return 'Unknown Structure'


def set_waypoint(destination_id, access_token, route_flag='secure', clear_waypoints=True, beginning=False):
    """Set autopilot waypoint in-game"""
    url = f"{ESI_BASE_URL}/ui/autopilot/waypoint/"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {
        "add_to_beginning": str(beginning).lower(),
        "clear_other_waypoints": str(clear_waypoints).lower(),
        "destination_id": destination_id
    }
    
    try:
        response = requests.post(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        print(f"Set waypoint response: {response.status_code}")
        return response.status_code == 204
    except Exception as e:
        print(f"Set waypoint error: {e}")
        return False


def open_market_window(type_id, access_token):
    """Open market details window for an item"""
    url = f"{ESI_BASE_URL}/ui/openwindow/marketdetails/"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"type_id": type_id}
    
    try:
        response = requests.post(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
        print(f"Open market response: {response.status_code}")
        return response.status_code == 204
    except Exception as e:
        print(f"Open market error: {e}")
        return False


def get_full_character_info(character_id, access_token):
    """Get comprehensive character info for display"""
    info = {
        'character_id': character_id,
        'name': 'Unknown',
        'portrait': None,
        'location': None,
        'location_name': 'Unknown',
        'ship_type_id': None,
        'ship_name': 'Unknown',
        'ship_type_name': 'Unknown',
        'wallet': 0
    }
    
    # Get public info
    public_info = get_character_public_info(character_id)
    if public_info:
        info['name'] = public_info.get('name', 'Unknown')
    
    # Get portrait
    portrait = get_character_portrait(character_id)
    if portrait:
        info['portrait'] = portrait.get('px128x128')
    
    # Get location
    location = get_character_location(character_id, access_token)
    if location:
        system_id = location.get('solar_system_id')
        info['location'] = system_id
        info['location_name'] = get_system_name(system_id)
        
        # Check if docked
        station_id = location.get('station_id')
        structure_id = location.get('structure_id')
        if station_id:
            station_name = get_station_name(station_id)
            if station_name:
                info['location_name'] = station_name
        elif structure_id:
            structure_name = get_structure_name(structure_id, access_token)
            info['location_name'] = structure_name
    
    # Get ship
    ship = get_character_ship(character_id, access_token)
    if ship:
        info['ship_type_id'] = ship.get('ship_type_id')
        info['ship_name'] = ship.get('ship_name', 'Unknown')
        
        ship_type = get_ship_type_info(ship.get('ship_type_id'))
        if ship_type:
            info['ship_type_name'] = ship_type.get('name', 'Unknown')
    
    # Get wallet
    wallet = get_character_wallet(character_id, access_token)
    if wallet is not None:
        info['wallet'] = wallet
    
    return info


# ========== Skills & Trading ==========

# Relevant skill IDs for trading
SKILL_IDS = {
    'broker_relations': 3446,      # Reduces broker fee by 0.3% per level
    'accounting': 16622,           # Reduces sales tax by 11% per level
    'trade': 3443,                 # +4 order slots per level
    'retail': 3444,                # +8 order slots per level
    'wholesale': 16596,            # +16 order slots per level
    'tycoon': 18580,               # +32 order slots per level
    'margin_trading': 16597,       # Reduced escrow requirements
    'daytrading': 16595,           # Remote order modification
    'visibility': 3447,            # Range of remote orders
    'procurement': 16594,          # Remote buy orders
    'marketing': 16598,            # Remote sell orders
}


def get_character_skills(character_id, access_token):
    """Get character's skills"""
    url = f"{ESI_BASE_URL}/characters/{character_id}/skills/"
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        return response.json()
    return None


def get_trading_skills(character_id, access_token):
    """Get character's trading-relevant skills"""
    skills_data = get_character_skills(character_id, access_token)
    if not skills_data:
        return {}
    
    skills = skills_data.get('skills', [])
    trading_skills = {}
    
    skill_id_to_name = {v: k for k, v in SKILL_IDS.items()}
    
    for skill in skills:
        skill_id = skill.get('skill_id')
        if skill_id in skill_id_to_name:
            trading_skills[skill_id_to_name[skill_id]] = {
                'level': skill.get('active_skill_level', 0),
                'trained_level': skill.get('trained_skill_level', 0)
            }
    
    return trading_skills


def calculate_broker_fee(skills, base_fee=3.0):
    """Calculate broker fee based on skills (default 3% base)"""
    broker_level = skills.get('broker_relations', {}).get('level', 0)
    # Each level reduces fee by 0.3%
    fee = base_fee - (broker_level * 0.3)
    return max(fee, 1.0)  # Minimum 1%


def calculate_sales_tax(skills, base_tax=8.0):
    """Calculate sales tax based on skills (default 8% base)"""
    accounting_level = skills.get('accounting', {}).get('level', 0)
    # Each level reduces tax by 11% (multiplicative)
    tax = base_tax * (1 - 0.11) ** accounting_level
    return tax


def get_character_orders(character_id, access_token):
    """Get character's active market orders"""
    url = f"{ESI_BASE_URL}/characters/{character_id}/orders/"
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        return response.json()
    return []


def get_wallet_transactions(character_id, access_token):
    """Get recent wallet transactions"""
    url = f"{ESI_BASE_URL}/characters/{character_id}/wallet/transactions/"
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(url, headers=headers)
    
    if response.status_code == 200:
        return response.json()
    return []


def get_route(origin_id, destination_id, flag='secure'):
    """Get route between two systems"""
    url = f"{ESI_BASE_URL}/route/{origin_id}/{destination_id}/"
    params = {'flag': flag}
    response = requests.get(url, params=params)
    
    if response.status_code == 200:
        return response.json()  # List of system IDs
    return []


def get_waypoints(character_id, access_token):
    """Note: ESI doesn't have a direct waypoint read endpoint.
    We track position instead."""
    return None


def get_ship_attributes(type_id):
    """Get ship attributes including cargo, align time, warp speed"""
    url = f"{ESI_BASE_URL}/universe/types/{type_id}/"
    response = requests.get(url)
    
    if response.status_code != 200:
        return None
    
    data = response.json()
    
    # Extract relevant dogma attributes
    attributes = {}
    dogma_attrs = data.get('dogma_attributes', [])
    
    # Attribute IDs we care about
    ATTR_IDS = {
        38: 'capacity',           # Cargo capacity
        48: 'agility',            # Agility (for align time)
        552: 'warp_speed_mult',   # Warp speed multiplier
        4: 'mass',                # Ship mass
        161: 'volume',            # Ship volume
    }
    
    for attr in dogma_attrs:
        attr_id = attr.get('attribute_id')
        if attr_id in ATTR_IDS:
            attributes[ATTR_IDS[attr_id]] = attr.get('value')
    
    # Calculate base align time: align_time = -ln(0.25) * mass * agility / 500000
    # Simplified formula
    if 'mass' in attributes and 'agility' in attributes:
        import math
        attributes['align_time'] = math.log(2) * attributes['mass'] * attributes['agility'] / 500000
    
    # Get base warp speed (default 3 AU/s for most ships, multiplied by warp_speed_mult)
    base_warp = 3.0
    if 'warp_speed_mult' in attributes:
        attributes['warp_speed'] = base_warp * attributes['warp_speed_mult']
    else:
        attributes['warp_speed'] = base_warp
    
    attributes['name'] = data.get('name', 'Unknown')
    attributes['type_id'] = type_id
    
    return attributes


def get_full_character_status(character_id, access_token):
    """Get comprehensive character status for the status panel"""
    status = {
        'character_id': character_id,
        'name': 'Unknown',
        'portrait': None,
        'location_system_id': None,
        'location_system_name': 'Unknown',
        'location_station_id': None,
        'location_station_name': None,
        'is_docked': False,
        'ship_type_id': None,
        'ship_type_name': 'Unknown',
        'ship_name': 'Unknown',
        'ship_stats': None,
        'wallet': 0,
        'skills': {},
        'broker_fee': 3.0,
        'sales_tax': 8.0,
        'active_orders': 0
    }
    
    # Get public info
    public_info = get_character_public_info(character_id)
    if public_info:
        status['name'] = public_info.get('name', 'Unknown')
    
    # Get portrait
    portrait = get_character_portrait(character_id)
    if portrait:
        status['portrait'] = portrait.get('px128x128')
    
    # Get location
    location = get_character_location(character_id, access_token)
    if location:
        system_id = location.get('solar_system_id')
        status['location_system_id'] = system_id
        
        # Get system info including security status
        system_info = get_system_info(system_id)
        status['location_system_name'] = system_info.get('name', 'Unknown')
        status['location_security'] = system_info.get('security_status', 0)
        
        station_id = location.get('station_id')
        structure_id = location.get('structure_id')
        
        if station_id:
            status['location_station_id'] = station_id
            status['location_station_name'] = get_station_name(station_id)
            status['is_docked'] = True
        elif structure_id:
            status['location_station_id'] = structure_id
            status['location_station_name'] = get_structure_name(structure_id, access_token)
            status['is_docked'] = True
    
    # Get ship
    ship = get_character_ship(character_id, access_token)
    if ship:
        status['ship_type_id'] = ship.get('ship_type_id')
        status['ship_name'] = ship.get('ship_name', 'Unknown')
        
        ship_type = get_ship_type_info(ship.get('ship_type_id'))
        if ship_type:
            status['ship_type_name'] = ship_type.get('name', 'Unknown')
        
        # Get ship attributes
        ship_attrs = get_ship_attributes(ship.get('ship_type_id'))
        if ship_attrs:
            status['ship_stats'] = {
                'cargo': ship_attrs.get('capacity', 0),
                'align_time': round(ship_attrs.get('align_time', 10), 1),
                'warp_speed': round(ship_attrs.get('warp_speed', 3.0), 2)
            }
    
    # Get wallet
    wallet = get_character_wallet(character_id, access_token)
    if wallet is not None:
        status['wallet'] = wallet
    
    # Get trading skills
    skills = get_trading_skills(character_id, access_token)
    status['skills'] = skills
    status['broker_fee'] = calculate_broker_fee(skills)
    status['sales_tax'] = calculate_sales_tax(skills)
    
    # Get active orders count
    orders = get_character_orders(character_id, access_token)
    if orders:
        status['active_orders'] = len(orders)
    
    return status


def open_info_window(type_id, access_token):
    """Open info window for an item/station"""
    url = f"{ESI_BASE_URL}/ui/openwindow/information/"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"target_id": type_id}
    
    response = requests.post(url, headers=headers, params=params)
    return response.status_code == 204
