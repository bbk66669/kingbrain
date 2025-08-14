import os
import json
from flask import Flask, request, jsonify, Response
from .api import orchestrator

app = Flask(__name__)

@app.route('/kb-api/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({
        "status": "ok",
        "mode": orchestrator.mode
    })

@app.route('/kb-api/config', methods=['GET'])
def config():
    """Get current configuration"""
    config_data = orchestrator.get_config()
    response = jsonify(config_data)
    response.headers['x-kb-mode'] = orchestrator.mode
    return response

@app.route('/kb-api/events/<event_id>', methods=['GET'])
def get_event(event_id):
    """Get a specific event by ID"""
    event = orchestrator.get_event(event_id)
    if event:
        return jsonify(event)
    else:
        return jsonify({"error": "Event not found"}), 404

@app.route('/kb-api/plan', methods=['POST'])
def plan():
    """Handle plan workflow"""
    data = request.json or {}
    task = data.get('task', '')
    notes = data.get('notes', '')
    paths = data.get('paths_to_write', [])
    
    # Plan phase is always PLAN
    result = orchestrator.process_workflow(task, notes, "PLAN", paths)
    
    if "error" in result:
        return jsonify(result)
    
    return jsonify(result)

@app.route('/kb-api/ack', methods=['POST'])
def ack():
    """Handle ack workflow"""
    data = request.json or {}
    task = data.get('task', '')
    notes = data.get('notes', '')
    phase = data.get('phase', 'ACK')
    paths = data.get('paths_to_write', [])
    
    result = orchestrator.process_workflow(task, notes, phase, paths)
    
    if "error" in result:
        return jsonify(result)
    
    return jsonify(result)

@app.route('/kb-api/borrow', methods=['POST'])
def borrow():
    """Handle borrow workflow"""
    data = request.json or {}
    task = data.get('task', '')
    notes = data.get('notes', '')
    phase = data.get('phase', 'BORROW')
    paths = data.get('paths_to_write', [])
    
    result = orchestrator.process_workflow(task, notes, phase, paths)
    
    if "error" in result:
        return jsonify(result)
    
    return jsonify(result)

@app.route('/kb-api/diff', methods=['POST'])
def diff():
    """Handle diff workflow"""
    data = request.json or {}
    task = data.get('task', '')
    notes = data.get('notes', '')
    phase = data.get('phase', 'DIFF')
    paths = data.get('paths_to_write', [])
    
    result = orchestrator.process_workflow(task, notes, phase, paths)
    
    if "error" in result:
        return jsonify(result)
    
    return jsonify(result)

@app.route('/kb-api/cr', methods=['POST'])
def cr():
    """Handle cr workflow"""
    data = request.json or {}
    task = data.get('task', '')
    notes = data.get('notes', '')
    phase = data.get('phase', 'CR')
    paths = data.get('paths_to_write', [])
    
    result = orchestrator.process_workflow(task, notes, phase, paths)
    
    if "error" in result:
        return jsonify(result)
    
    return jsonify(result)

def run_server():
    """Run the Flask server"""
    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', 8000))
    app.run(host=host, port=port)

if __name__ == '__main__':
    run_server()
