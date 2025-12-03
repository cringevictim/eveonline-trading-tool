from flask import Flask, render_template, jsonify, request
from threading import Thread
from database import init_db, get_top_trades, get_scan_stats
from market import run_scan, get_scanner_status, scanner
from pathfinder import preload_routes_from_db, clear_gate_camp_cache

app = Flask(__name__)

# Market group presets
MARKET_GROUPS = {
    'materials': {'id': 533, 'name': 'Materials'},
    'ships': {'id': 4, 'name': 'Ships'},
    'ship_equipment': {'id': 9, 'name': 'Ship Equipment'},
    'drones': {'id': 157, 'name': 'Drones'},
    'implants': {'id': 24, 'name': 'Implants & Boosters'},
    'manufacture': {'id': 475, 'name': 'Manufacture & Research'},
    'modifications': {'id': 955, 'name': 'Ship Modifications'},
    'pilot_services': {'id': 1922, 'name': "Pilot's Services"}
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
    return render_template('index.html', 
                         groups=MARKET_GROUPS,
                         route_options=ROUTE_OPTIONS,
                         trade_modes=TRADE_MODES)

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
    check_camps = data.get('check_camps', False)
    
    # Clear gate camp cache if checking camps
    if check_camps:
        clear_gate_camp_cache()
    
    # Run scan in background thread
    scan_thread = Thread(target=run_scan, args=(
        group_id, min_profit, cargo, regions, route_flag, trade_mode, check_camps
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

if __name__ == '__main__':
    print("Initializing database...")
    init_db()
    print("Loading cached routes...")
    preload_routes_from_db()
    print("Starting EVE Trading Tool...")
    print("Open http://localhost:5000 in your browser")
    app.run(debug=True, threaded=True)
