from flask import Flask, render_template, jsonify, request, redirect, session, url_for
from threading import Thread
import secrets
from database import init_db, get_top_trades, get_scan_stats
from market import run_scan, get_scanner_status, scanner
from pathfinder import preload_routes_from_db
import eve_sso

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)  # For session management

# Market group presets
MARKET_GROUPS = {
    'ammunition': {'id': 11, 'name': 'Ammunition & Charges'},
    'apparel': {'id': 1396, 'name': 'Apparel'},
    'blueprints': {'id': 2, 'name': 'Blueprints & Reactions'},
    'drones': {'id': 157, 'name': 'Drones'},
    'implants': {'id': 24, 'name': 'Implants & Boosters'},
    'manufacture': {'id': 475, 'name': 'Manufacture & Research'},
    'personalization': {'id': 3628, 'name': 'Personalization'},
    'pilot_services': {'id': 1922, 'name': "Pilot's Services"},
    'planetary': {'id': 1320, 'name': 'Planetary Infrastructure'},
    'ship_equipment': {'id': 9, 'name': 'Ship Equipment'},
    'ship_skins': {'id': 1954, 'name': 'Ship SKINs'},
    'modifications': {'id': 955, 'name': 'Ship & Module Modifications'},
    'ships': {'id': 4, 'name': 'Ships'},
    'skills': {'id': 150, 'name': 'Skills'},
    'special_edition': {'id': 1659, 'name': 'Special Edition Assets'},
    'structure_equipment': {'id': 2202, 'name': 'Structure Equipment'},
    'structure_mods': {'id': 2203, 'name': 'Structure Modifications'},
    'structures': {'id': 477, 'name': 'Structures'},
    'trade_goods': {'id': 19, 'name': 'Trade Goods'},
}

# Route safety options
ROUTE_OPTIONS = {
    'secure': 'Safest (Highsec Only)',
    'shortest': 'Shortest (Any Route)',
    'insecure': 'Risky (Prefer Low/Null)'
}

# Trade mode options
TRADE_MODES = {
    'instant': 'Instant (Buy sell orders → Sell to buy orders)',
    'buy_orders': 'Buy Orders (Place buy orders → Sell to buy orders)',
    'sell_orders': 'Sell Orders (Buy sell orders → Place sell orders)',
    'patient': 'Patient (Place buy orders → Place sell orders)'
}

scan_thread = None

@app.route('/')
def index():
    # Check if user is logged in
    character = None
    if 'character_id' in session and 'access_token' in session:
        character = {
            'id': session.get('character_id'),
            'name': session.get('character_name'),
            'portrait': session.get('portrait')
        }
    
    return render_template('index.html', 
                         groups=MARKET_GROUPS,
                         route_options=ROUTE_OPTIONS,
                         trade_modes=TRADE_MODES,
                         character=character)


# ========== EVE SSO Routes ==========

@app.route('/login')
def login():
    """Initiate EVE SSO login"""
    state = secrets.token_urlsafe(16)
    code_verifier = eve_sso.generate_code_verifier()
    
    # Store in session for callback verification
    session['oauth_state'] = state
    session['code_verifier'] = code_verifier
    
    auth_url = eve_sso.get_auth_url(state, code_verifier)
    return redirect(auth_url)


@app.route('/callback')
def callback():
    """Handle SSO callback"""
    code = request.args.get('code')
    state = request.args.get('state')
    
    # Verify state
    if state != session.get('oauth_state'):
        return "Invalid state parameter", 400
    
    # Exchange code for token
    code_verifier = session.get('code_verifier')
    token_data = eve_sso.exchange_code_for_token(code, code_verifier)
    
    if not token_data:
        return "Failed to get access token", 400
    
    # Verify token and get character info
    char_info = eve_sso.verify_token(token_data['access_token'])
    if not char_info:
        return "Failed to verify token", 400
    
    # Extract character ID (handle both formats)
    char_id = char_info.get('CharacterID')
    if not char_id and 'sub' in char_info:
        char_id = int(char_info['sub'].split(':')[2])
    
    char_name = char_info.get('CharacterName') or char_info.get('name', 'Unknown')
    
    # Get character portrait
    portrait = eve_sso.get_character_portrait(char_id)
    portrait_url = portrait.get('px128x128') if portrait else None
    
    # Store in session
    session['access_token'] = token_data['access_token']
    session['refresh_token'] = token_data['refresh_token']
    session['character_id'] = char_id
    session['character_name'] = char_name
    session['portrait'] = portrait_url
    
    # Clean up OAuth state
    session.pop('oauth_state', None)
    session.pop('code_verifier', None)
    
    return redirect('/')


@app.route('/logout')
def logout():
    """Log out and clear session"""
    session.clear()
    return redirect('/')


@app.route('/api/character')
def get_character():
    """Get current character info (basic)"""
    if 'character_id' not in session:
        return jsonify({'logged_in': False})
    
    char_id = session['character_id']
    access_token = session['access_token']
    
    try:
        info = eve_sso.get_full_character_info(char_id, access_token)
        info['logged_in'] = True
        return jsonify(info)
    except Exception as e:
        return try_refresh_and_retry(lambda token: eve_sso.get_full_character_info(char_id, token), e)


@app.route('/api/character/status')
def get_character_status():
    """Get full character status for status panel"""
    if 'character_id' not in session:
        return jsonify({'logged_in': False})
    
    char_id = session['character_id']
    access_token = session['access_token']
    
    try:
        status = eve_sso.get_full_character_status(char_id, access_token)
        status['logged_in'] = True
        return jsonify(status)
    except Exception as e:
        return try_refresh_and_retry(lambda token: eve_sso.get_full_character_status(char_id, token), e)


@app.route('/api/character/ship')
def get_character_ship():
    """Get current ship stats"""
    if 'character_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    char_id = session['character_id']
    access_token = session['access_token']
    
    def get_ship_data(token):
        ship = eve_sso.get_character_ship(char_id, token)
        if ship:
            ship_type_id = ship.get('ship_type_id')
            attrs = eve_sso.get_ship_attributes(ship_type_id)
            ship_type = eve_sso.get_ship_type_info(ship_type_id)
            
            return {
                'ship_type_id': ship_type_id,
                'ship_name': ship.get('ship_name', 'Unknown'),
                'ship_type_name': ship_type.get('name', 'Unknown') if ship_type else 'Unknown',
                'cargo': attrs.get('capacity', 0) if attrs else 0,
                'align_time': round(attrs.get('align_time', 10), 1) if attrs else 10,
                'warp_speed': round(attrs.get('warp_speed', 3.0), 2) if attrs else 3.0
            }
        return None
    
    try:
        result = get_ship_data(access_token)
        if result:
            return jsonify(result)
        return jsonify({'error': 'No ship data'}), 404
    except Exception as e:
        return try_refresh_and_retry(get_ship_data, e)


@app.route('/api/character/transactions')
def get_transactions():
    """Get recent wallet transactions"""
    if 'character_id' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    char_id = session['character_id']
    access_token = session['access_token']
    
    try:
        transactions = eve_sso.get_wallet_transactions(char_id, access_token)
        return jsonify(transactions[:20])  # Last 20
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def try_refresh_token():
    """Attempt to refresh the access token"""
    refresh_token = session.get('refresh_token')
    if refresh_token:
        new_tokens = eve_sso.refresh_access_token(refresh_token)
        if new_tokens:
            session['access_token'] = new_tokens['access_token']
            session['refresh_token'] = new_tokens['refresh_token']
            return new_tokens['access_token']
    return None

def try_refresh_and_retry(func, original_error):
    """Helper to refresh token and retry"""
    print(f"API call failed, attempting token refresh: {original_error}")
    new_token = try_refresh_token()
    if new_token:
        try:
            result = func(new_token)
            if isinstance(result, dict):
                result['logged_in'] = True
            return jsonify(result)
        except Exception as e:
            print(f"Retry also failed: {e}")
    
    return jsonify({'logged_in': False, 'error': 'Token expired, please re-login', 'needs_reauth': True})


@app.route('/api/set_destination', methods=['POST'])
def set_destination():
    """Set autopilot destination in-game"""
    if 'access_token' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.json
    station_id = data.get('station_id')
    route_flag = data.get('route_flag', 'secure')
    
    if not station_id:
        return jsonify({'error': 'No station ID provided'}), 400
    
    def try_set_waypoint(token):
        return eve_sso.set_waypoint(station_id, token, route_flag)
    
    success = try_set_waypoint(session['access_token'])
    
    if success:
        return jsonify({'status': 'success'})
    else:
        # Try refresh
        new_token = try_refresh_token()
        if new_token:
            success = try_set_waypoint(new_token)
            if success:
                return jsonify({'status': 'success'})
        return jsonify({'error': 'Failed to set destination', 'needs_reauth': True}), 500


@app.route('/api/open_market', methods=['POST'])
def open_market():
    """Open market window for an item"""
    if 'access_token' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    data = request.json
    type_id = data.get('type_id')
    
    if not type_id:
        return jsonify({'error': 'No type ID provided'}), 400
    
    def try_open_market(token):
        return eve_sso.open_market_window(type_id, token)
    
    success = try_open_market(session['access_token'])
    
    if success:
        return jsonify({'status': 'success'})
    else:
        # Try refresh
        new_token = try_refresh_token()
        if new_token:
            success = try_open_market(new_token)
            if success:
                return jsonify({'status': 'success'})
        return jsonify({'error': 'Failed to open market', 'needs_reauth': True}), 500


# ========== Market Scan Routes ==========

@app.route('/api/scan', methods=['POST'])
def start_scan():
    global scan_thread
    
    if scanner.status == 'scanning':
        return jsonify({'error': 'Scan already in progress'}), 400
    
    data = request.json
    group_id = int(data.get('group_id', 533))
    min_profit = int(data.get('min_profit', 10_000_000))
    cargo = int(data.get('cargo_capacity', 1_030_000))
    regions = data.get('regions', ['highsec'])
    route_flag = data.get('route_flag', 'secure')
    trade_mode = data.get('trade_mode', 'instant')
    
    # Run scan in background thread
    scan_thread = Thread(target=run_scan, args=(
        group_id, min_profit, cargo, regions, route_flag, trade_mode
    ))
    scan_thread.start()
    
    return jsonify({'status': 'started'})


@app.route('/api/status')
def get_status():
    status = get_scanner_status()
    stats = get_scan_stats()
    return jsonify({**status, **stats})


@app.route('/api/trades')
def get_trades():
    limit = request.args.get('limit', 50, type=int)
    sort_by = request.args.get('sort', 'profit_per_jump')
    trades = get_top_trades(limit, sort_by)
    return jsonify(trades)


@app.route('/api/stop')
def stop_scan():
    scanner.status = 'stopped'
    return jsonify({'status': 'stopped'})


@app.route('/api/clear_db', methods=['POST'])
def clear_db():
    """Clear the database (orders and trades)."""
    from database import clear_orders, clear_trades
    clear_orders()
    clear_trades()
    # Reset scanner's last_updated timestamp
    scanner.last_updated = None
    return jsonify({'status': 'cleared'})


@app.route('/api/calculate_distances', methods=['POST'])
def calculate_distances():
    """Calculate jump distances from a source system to multiple destination systems."""
    data = request.json
    from_system = data.get('from_system')
    to_systems = data.get('to_systems', [])
    route_flag = data.get('route_flag', 'secure')
    
    if not from_system or not to_systems:
        return jsonify({})
    
    # Use the pathfinder to get routes
    from pathfinder import get_jumps_sync
    
    distances = {}
    for to_system in to_systems:
        if from_system == to_system:
            distances[to_system] = 0
        else:
            try:
                jumps = get_jumps_sync(from_system, to_system, route_flag)
                distances[to_system] = jumps if jumps is not None else 999
            except:
                distances[to_system] = 999
    
    return jsonify(distances)


@app.route('/api/check_route_security', methods=['POST'])
def check_route_security():
    """Check if a route passes through lowsec/nullsec systems."""
    data = request.json
    from_system = data.get('from_system')
    to_system = data.get('to_system')
    route_flag = data.get('route_flag', 'secure')
    
    if not from_system or not to_system:
        return jsonify({'error': 'Missing system IDs'}), 400
    
    import requests
    
    # Get the actual route from ESI
    flag_map = {'secure': 'secure', 'shortest': 'shortest', 'insecure': 'insecure'}
    esi_flag = flag_map.get(route_flag, 'secure')
    
    try:
        url = f"https://esi.evetech.net/latest/route/{from_system}/{to_system}/"
        params = {'flag': esi_flag}
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code != 200:
            return jsonify({'error': 'Failed to get route', 'safe': True})
        
        route_systems = response.json()
        
        # Check security status of each system
        dangerous_systems = []
        for system_id in route_systems:
            sys_url = f"https://esi.evetech.net/latest/universe/systems/{system_id}/"
            sys_response = requests.get(sys_url, timeout=5)
            if sys_response.status_code == 200:
                sys_data = sys_response.json()
                security = sys_data.get('security_status', 1.0)
                if security < 0.5:
                    dangerous_systems.append({
                        'id': system_id,
                        'name': sys_data.get('name', 'Unknown'),
                        'security': round(security, 1)
                    })
        
        return jsonify({
            'safe': len(dangerous_systems) == 0,
            'dangerous_systems': dangerous_systems,
            'total_jumps': len(route_systems) - 1
        })
    except Exception as e:
        print(f"Route security check error: {e}")
        return jsonify({'error': str(e), 'safe': True})


if __name__ == '__main__':
    print("Initializing database...")
    init_db()
    print("Loading cached routes...")
    preload_routes_from_db()
    print("Starting EVE Trading Tool...")
    print("Open http://localhost:5000 in your browser")
    app.run(debug=True, threaded=True)
