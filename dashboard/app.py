"""
SWATH Dashboard — Production-Grade Real-Time Web Interface

Features:
- Server-Sent Events (SSE) for live scan streaming
- REST API for scan state, events, and report data
- Real-time file watching via polling endpoints
- Markdown report rendering with syntax highlighting
"""

from flask import Flask, render_template, request, jsonify, abort, Response
import os
import json
import sqlite3
import time
import subprocess
import sys
from datetime import datetime

app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB_PATH = os.path.expanduser("~/.swath/history.db")


# ─────────────────────────────────────────────────────────────────
# Utility Functions
# ─────────────────────────────────────────────────────────────────

def resolve_output_dir(path):
    """Resolve output directory path, handling Docker container paths."""
    if not path:
        return None
    # Direct match — absolute path that exists on this OS
    if os.path.isabs(path) and os.path.exists(path):
        return path

    # Docker container path mapping: /swath/output/domain → output/domain
    # The container runs from /swath/, so strip that prefix
    docker_prefixes = ['/swath/', '/swath']
    stripped = path
    for prefix in docker_prefixes:
        if path.startswith(prefix):
            stripped = path[len(prefix):]
            break

    # Try relative to project root (handles both Docker paths and relative paths)
    for candidate in [stripped, path]:
        abs_path = os.path.abspath(os.path.join(PROJECT_ROOT, candidate))
        if os.path.exists(abs_path):
            return abs_path

    # Try as-is (might be relative to dashboard CWD)
    if os.path.exists(path):
        return os.path.abspath(path)
    print(f"[RESOLVE FAILED] path={path} stripped={stripped} PROJECT_ROOT={PROJECT_ROOT}", flush=True)
    return None


def get_db_connection():
    if not os.path.exists(DB_PATH):
        return None
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        # Auto-migrate: add columns if they don't exist
        try:
            existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(scans)").fetchall()}
            migrations = {
                'current_phase': "ALTER TABLE scans ADD COLUMN current_phase TEXT",
                'current_tool': "ALTER TABLE scans ADD COLUMN current_tool TEXT",
                'tools_completed': "ALTER TABLE scans ADD COLUMN tools_completed INTEGER DEFAULT 0",
                'tools_total': "ALTER TABLE scans ADD COLUMN tools_total INTEGER DEFAULT 0",
            }
            for col, sql in migrations.items():
                if col not in existing_cols:
                    conn.execute(sql)
            conn.commit()
        except sqlite3.Error:
            pass
        return conn
    except sqlite3.Error:
        return None


def safe_read_json(path, default=None):
    """Read a JSON file safely, returning default on any error."""
    if default is None:
        default = {}
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError):
        pass
    return default


def get_scan_record(scan_id):
    """Fetch a single scan record from DB."""
    conn = get_db_connection()
    if not conn:
        return None
    try:
        scan = conn.execute('SELECT * FROM scans WHERE id = ?', (scan_id,)).fetchone()
        return scan
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def render_markdown_to_html(md_text):
    """Convert markdown to HTML with syntax highlighting."""
    try:
        import markdown
        from markdown.extensions.codehilite import CodeHiliteExtension
        from markdown.extensions.tables import TableExtension
        from markdown.extensions.fenced_code import FencedCodeExtension
        from markdown.extensions.toc import TocExtension

        html = markdown.markdown(
            md_text,
            extensions=[
                TableExtension(),
                FencedCodeExtension(),
                CodeHiliteExtension(css_class='highlight', guess_lang=True),
                TocExtension(permalink=False),
                'markdown.extensions.nl2br',
            ]
        )
        return html
    except ImportError:
        # Fallback: return raw markdown wrapped in <pre>
        return f'<pre style="white-space: pre-wrap;">{md_text}</pre>'


# ─────────────────────────────────────────────────────────────────
# Page Routes
# ─────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    conn = get_db_connection()
    scans = []
    stats = {
        'total_targets': 0,
        'active_scans': 0,
        'total_tags': 0,
        'status_counts': {'COMPLETED': 0, 'FAILED': 0, 'RUNNING': 0, 'INTERRUPTED': 0}
    }

    if conn:
        try:
            scans = conn.execute('SELECT * FROM scans ORDER BY id DESC LIMIT 50').fetchall()

            unique_domains = set()
            for row in scans:
                unique_domains.add(row['domain'])
                if row['status'] == 'RUNNING':
                    stats['active_scans'] += 1
                stats['total_tags'] += (row['tag_count'] or 0)

                status_upper = row['status'].upper() if row['status'] else 'UNKNOWN'
                if status_upper in stats['status_counts']:
                    stats['status_counts'][status_upper] += 1
                else:
                    stats['status_counts'][status_upper] = 1

            stats['total_targets'] = len(unique_domains)

        except sqlite3.Error:
            scans = []
        finally:
            conn.close()

    return render_template('index.html', scans=scans, stats=stats)


@app.route('/scan/<int:scan_id>')
def view_scan(scan_id):
    scan = get_scan_record(scan_id)
    if not scan:
        return render_template('scan_detail.html', scan=None, tags={}, budget={}, output_files={}), 404

    output_dir = resolve_output_dir(scan['output_dir']) or scan['output_dir']

    # Load tags
    tags = safe_read_json(os.path.join(output_dir, 'active_tags.json'))

    # Load budget
    budget_raw = safe_read_json(os.path.join(output_dir, 'processed', 'budget_status.json'))
    budget = {
        'requests_used': budget_raw.get('requests_used', budget_raw.get('requests_made', 0)),
        'max_requests': budget_raw.get('max_requests', 'unlimited'),
        'elapsed_minutes': budget_raw.get('elapsed_minutes', round(budget_raw.get('elapsed_seconds', 0) / 60, 2)),
        'percent_used': budget_raw.get('percent_used', 0)
    } if budget_raw else {}

    # Load live state
    live_state = safe_read_json(os.path.join(output_dir, 'live_state.json'))

    # Load checkpoint for completed tools
    checkpoint = safe_read_json(os.path.join(output_dir, 'checkpoint.json'))
    completed_tools = checkpoint.get('completed_tools', [])

    # Reconstruct live_state from checkpoint + events if live_state.json doesn't exist
    if not live_state and completed_tools:
        # Determine current phase from last completed tool
        last_phase = completed_tools[-1].get('phase', '') if completed_tools else ''
        last_tool = completed_tools[-1].get('tool', '') if completed_tools else ''
        completed_count = len([t for t in completed_tools if t.get('status') == 'completed'])
        failed_count = len([t for t in completed_tools if t.get('status') == 'failed'])
        live_state = {
            'phase': last_phase,
            'tool': last_tool,
            'status': scan['status'].lower() if scan['status'] else 'unknown',
            'tools_completed': completed_count,
            'tools_failed': failed_count,
            'tools_total': max(completed_count + failed_count, 1),
            'tags_count': len(tags),
        }

    # Load scan events for the log terminal
    events = []
    events_path = os.path.join(output_dir, 'logs', 'scan_events.jsonl')
    if os.path.exists(events_path):
        try:
            with open(events_path, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except OSError:
            pass

    # Output files
    output_files = {'raw': [], 'processed': [], 'logs': []}
    if os.path.exists(output_dir):
        for sub in ['raw', 'processed', 'logs']:
            target_dir = os.path.join(output_dir, sub)
            if os.path.exists(target_dir):
                for f in os.listdir(target_dir):
                    if os.path.isfile(os.path.join(target_dir, f)):
                        output_files[sub].append(f"{sub}/{f}")

    # Check if AI report exists
    report_path = os.path.join(output_dir, 'logs', 'ai_report.md')
    has_report = os.path.exists(report_path)

    return render_template('scan_detail.html',
                         scan=scan, tags=tags, budget=budget,
                         output_files=output_files, live_state=live_state,
                         completed_tools=completed_tools, has_report=has_report,
                         events=events)


@app.route('/scan/<int:scan_id>/report')
def view_report(scan_id):
    scan = get_scan_record(scan_id)
    if not scan:
        abort(404)

    output_dir = resolve_output_dir(scan['output_dir']) or scan['output_dir']
    report_path = os.path.join(output_dir, 'logs', 'ai_report.md')

    if not os.path.exists(report_path):
        return render_template('report.html', scan=scan, report_html=None,
                             report_md=None, report_meta={})

    with open(report_path, 'r', encoding='utf-8', errors='replace') as f:
        report_md = f.read()

    report_html = render_markdown_to_html(report_md)
    report_meta = safe_read_json(os.path.join(output_dir, 'logs', 'ai_report_meta.json'))

    return render_template('report.html', scan=scan, report_html=report_html,
                         report_md=report_md, report_meta=report_meta)


@app.route('/scan/<int:scan_id>/output/<path:filename>')
def view_scan_output(scan_id, filename):
    scan = get_scan_record(scan_id)
    if not scan:
        abort(404)

    base_dir = resolve_output_dir(scan['output_dir']) or scan['output_dir']
    filepath = os.path.normpath(os.path.join(base_dir, filename))
    if not filepath.startswith(os.path.normpath(base_dir)):
        abort(403)

    if not os.path.isfile(filepath):
        abort(404)

    content = ''
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()
    except OSError:
        abort(500)

    return render_template('scan_output.html', scan=scan, filename=filename, content=content)


# ─────────────────────────────────────────────────────────────────
# REST API Endpoints — Real-Time Data
# ─────────────────────────────────────────────────────────────────

@app.route('/api/scans')
def api_scans():
    """JSON list of all scans for AJAX polling."""
    conn = get_db_connection()
    if not conn:
        return jsonify([])
    try:
        rows = conn.execute('SELECT * FROM scans ORDER BY id DESC LIMIT 50').fetchall()
        return jsonify([dict(row) for row in rows])
    except sqlite3.Error:
        return jsonify([])
    finally:
        conn.close()


@app.route('/api/scan/<int:scan_id>/status')
def api_scan_status(scan_id):
    """Live scan state — polled every 2 seconds by the dashboard."""
    scan = get_scan_record(scan_id)
    if not scan:
        return jsonify({'error': 'Scan not found'}), 404

    output_dir = resolve_output_dir(scan['output_dir']) or scan['output_dir']

    # Try live_state.json first (most up-to-date during scans)
    live_state = safe_read_json(os.path.join(output_dir, 'live_state.json'))

    # Load checkpoint for tool details
    checkpoint = safe_read_json(os.path.join(output_dir, 'checkpoint.json'))

    # Load budget
    budget = safe_read_json(os.path.join(output_dir, 'processed', 'budget_status.json'))

    # Load tags
    tags = safe_read_json(os.path.join(output_dir, 'active_tags.json'))

    # Scan events from JSONL log
    events = []
    events_path = os.path.join(output_dir, 'logs', 'scan_events.jsonl')
    if os.path.exists(events_path):
        try:
            with open(events_path, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except OSError:
            pass

    # Output files list
    output_files = {'raw': [], 'processed': [], 'logs': []}
    if os.path.exists(output_dir):
        for sub in ['raw', 'processed', 'logs']:
            target_dir = os.path.join(output_dir, sub)
            if os.path.exists(target_dir):
                for f_name in os.listdir(target_dir):
                    fpath = os.path.join(target_dir, f_name)
                    if os.path.isfile(fpath):
                        output_files[sub].append({
                            'name': f_name,
                            'path': f"{sub}/{f_name}",
                            'size': os.path.getsize(fpath)
                        })

    # Check for report
    has_report = os.path.exists(os.path.join(output_dir, 'logs', 'ai_report.md'))

    return jsonify({
        'scan': dict(scan),
        'live_state': live_state,
        'checkpoint': {
            'completed_tools': checkpoint.get('completed_tools', []),
            'tags': checkpoint.get('tags', {})
        },
        'budget': budget,
        'tags': tags,
        'events': events[-100:],  # Last 100 events
        'output_files': output_files,
        'has_report': has_report,
        'timestamp': datetime.utcnow().isoformat() + 'Z'
    })


@app.route('/api/scan/<int:scan_id>/events')
def api_scan_events(scan_id):
    """Return scan events from JSONL log."""
    scan = get_scan_record(scan_id)
    if not scan:
        return jsonify({'error': 'Scan not found'}), 404

    output_dir = resolve_output_dir(scan['output_dir']) or scan['output_dir']
    events_path = os.path.join(output_dir, 'logs', 'scan_events.jsonl')

    events = []
    if os.path.exists(events_path):
        try:
            with open(events_path, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        except OSError:
            pass

    return jsonify({'events': events})


@app.route('/api/scan/<int:scan_id>/stream')
def api_scan_stream(scan_id):
    """Server-Sent Events stream for real-time dashboard updates."""
    def generate():
        last_event_count = 0
        last_state_mtime = 0

        while True:
            scan = get_scan_record(scan_id)
            if not scan:
                yield f"data: {json.dumps({'error': 'Scan not found'})}\n\n"
                break

            output_dir = resolve_output_dir(scan['output_dir']) or scan['output_dir']
            if not output_dir:
                time.sleep(2)
                continue

            # Check live state file modification time
            state_path = os.path.join(output_dir, 'live_state.json')
            events_path = os.path.join(output_dir, 'logs', 'scan_events.jsonl')

            state_data = {}
            if os.path.exists(state_path):
                try:
                    mtime = os.path.getmtime(state_path)
                    if mtime != last_state_mtime:
                        last_state_mtime = mtime
                        state_data = safe_read_json(state_path)
                except OSError:
                    pass

            # Check for new events
            new_events = []
            if os.path.exists(events_path):
                try:
                    with open(events_path, 'r', encoding='utf-8', errors='replace') as f:
                        all_lines = f.readlines()
                    if len(all_lines) > last_event_count:
                        for line in all_lines[last_event_count:]:
                            line = line.strip()
                            if line:
                                try:
                                    new_events.append(json.loads(line))
                                except json.JSONDecodeError:
                                    pass
                        last_event_count = len(all_lines)
                except OSError:
                    pass

            # Build SSE payload
            payload = {
                'scan_status': scan['status'],
                'live_state': state_data,
                'new_events': new_events,
                'has_report': os.path.exists(os.path.join(output_dir, 'logs', 'ai_report.md')),
                'timestamp': datetime.utcnow().isoformat() + 'Z'
            }

            yield f"data: {json.dumps(payload)}\n\n"

            # Stop streaming if scan is complete
            if scan['status'] in ('COMPLETED', 'FAILED', 'INTERRUPTED'):
                # Send one final update then close
                yield f"data: {json.dumps({'final': True, 'scan_status': scan['status']})}\n\n"
                break

            time.sleep(2)

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
    )


@app.route('/api/scan/<int:scan_id>/urls', methods=['GET'])
def get_scan_urls(scan_id):
    scan = get_scan_record(scan_id)
    if not scan:
        return jsonify({'error': 'Scan not found'}), 404

    output_dir = resolve_output_dir(scan['output_dir']) or scan['output_dir']
    urls = set()

    all_urls_path = os.path.join(output_dir, 'processed', 'all_urls.txt')
    if os.path.exists(all_urls_path):
        try:
            with open(all_urls_path, 'r') as f:
                urls.update(line.strip() for line in f if line.strip())
        except Exception:
            pass

    params_path = os.path.join(output_dir, 'processed', 'parameters.json')
    if os.path.exists(params_path):
        try:
            with open(params_path, 'r') as f:
                data = json.load(f)
                if isinstance(data, list):
                    urls.update(str(item) for item in data)
        except Exception:
            pass

    return jsonify({'urls': sorted(urls)})


@app.route('/api/scan/<int:scan_id>/generate-report', methods=['POST'])
def api_generate_report(scan_id):
    """Trigger AI report generation for a completed scan."""
    scan = get_scan_record(scan_id)
    if not scan:
        return jsonify({'error': 'Scan not found'}), 404

    # Launch report generation in background
    subprocess.Popen(
        [sys.executable, 'swath.py', 'report', scan['domain']],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    return jsonify({'status': 'ok', 'message': 'Report generation started'})


@app.route('/api/scan/<int:scan_id>/launch-precision', methods=['POST'])
def launch_precision_strike(scan_id):
    data = request.json
    if not data or 'urls' not in data:
        return jsonify({'error': 'Invalid request payload'}), 400

    urls = data['urls']
    if not urls:
        return jsonify({'error': 'No URLs provided'}), 400

    scan = get_scan_record(scan_id)
    if not scan:
        return jsonify({'error': 'Scan not found'}), 404

    output_dir = resolve_output_dir(scan['output_dir']) or scan['output_dir']
    os.makedirs(output_dir, exist_ok=True)

    precision_path = os.path.join(output_dir, 'precision_targets.txt')
    with open(precision_path, 'w') as f:
        f.write('\n'.join(urls))

    subprocess.Popen(
        [sys.executable, 'swath.py', 'precision', scan['domain'], '--file', precision_path],
        cwd=PROJECT_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    return jsonify({'status': 'ok', 'message': 'Precision Strike Launched!'})


if __name__ == '__main__':
    port = int(os.environ.get('FLASK_RUN_PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
