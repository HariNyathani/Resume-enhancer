import io
import os
import json
import sqlite3
import tempfile
import subprocess
import contextlib
import time
import datetime
import concurrent.futures
from collections import OrderedDict
from flask import Flask, render_template, request, jsonify, send_file
import threading
import uuid
from agent import optimize_resume_logic 

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', os.urandom(24))

# Thread pool for background tasks
executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

# Initialize SQLite database for thread-safe cross-worker task storage
def get_db():
    conn = sqlite3.connect('tasks.db', timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL;')
    return conn

db_initialized = False
db_init_lock = threading.Lock()
rate_limit_cache = OrderedDict()
rate_limit_lock = threading.Lock()
queue_semaphore = threading.Semaphore(50)

@app.before_request
def initialize_database():
    global db_initialized
    if not db_initialized:
        with db_init_lock:
            if not db_initialized:
                with contextlib.closing(get_db()) as conn:
                    conn.execute('''CREATE TABLE IF NOT EXISTS tasks
                                 (id TEXT PRIMARY KEY, status TEXT, result TEXT, message TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
                    conn.execute('CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at)')
                    conn.commit()
                db_initialized = True

@app.before_request
def rate_limit():
    if request.endpoint in ['optimize', 'download_pdf']:
        ip = request.remote_addr
        now = time.time()
        
        with rate_limit_lock:
            current_times = rate_limit_cache.get(ip, [])
            current_times = [t for t in current_times if now - t < 60]
            if len(current_times) >= 5:
                return jsonify({"status": "error", "message": "Rate limit exceeded"}), 429
            current_times.append(now)
            rate_limit_cache[ip] = current_times
            rate_limit_cache.move_to_end(ip)
            
            # Cap max dictionary size to prevent OOM
            if len(rate_limit_cache) > 10000:
                rate_limit_cache.popitem(last=False)

def background_task(task_id, jd, resume):
    """This runs completely independent of the web request."""
    try:
        # This can now take as many minutes as it needs without timing out the browser!
        result = optimize_resume_logic(jd, resume)
        with contextlib.closing(get_db()) as conn:
            conn.execute('UPDATE tasks SET status = ?, result = ? WHERE id = ?', ('completed', json.dumps(result), task_id))
            conn.commit()
    except Exception as e:
        print(f"Background task {task_id} failed: {e}")
        with contextlib.closing(get_db()) as conn:
            conn.execute('UPDATE tasks SET status = ?, message = ? WHERE id = ?', ('error', 'An internal error occurred during processing.', task_id))
            conn.commit()

def background_task_wrapper(task_id, jd, resume):
    try:
        background_task(task_id, jd, resume)
    finally:
        queue_semaphore.release()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/optimize', methods=['POST'])
def optimize():
    data = request.get_json(silent=True) or {}
    job_description = data.get('jd')
    resume_latex = data.get('resume')
    
    if not job_description or not resume_latex:
         return jsonify({"status": "error", "message": "Missing JD or Resume"}), 400
         
    if not isinstance(job_description, str) or not isinstance(resume_latex, str):
         return jsonify({"status": "error", "message": "Invalid input format"}), 400
         
    # Input size validation
    if len(job_description) > 20000 or len(resume_latex) > 50000:
         return jsonify({"status": "error", "message": "Input too large"}), 413

    # 1. Generate a unique ID for this request
    task_id = str(uuid.uuid4())
    
    # 2. Register the task as processing
    with contextlib.closing(get_db()) as conn:
        conn.execute('INSERT INTO tasks (id, status) VALUES (?, ?)', (task_id, 'processing'))
        # Clean up old tasks to prevent unbounded growth
        conn.execute('DELETE FROM tasks WHERE created_at < datetime("now", "-1 day")')
        conn.commit()
    
    # 3. Prevent unbound memory growth by checking the queue size
    if not queue_semaphore.acquire(blocking=False):
         return jsonify({"status": "error", "message": "Server is currently overloaded. Please try again later."}), 503

    # 4. Fire off the background thread using the executor
    executor.submit(background_task_wrapper, task_id, job_description, resume_latex)
    
    # 4. Immediately return the ID so the browser doesn't hang
    return jsonify({"status": "accepted", "task_id": task_id})

@app.route('/status/<task_id>', methods=['GET'])
def check_status(task_id):
    """The frontend will hit this route every few seconds to check on the agent."""
    with contextlib.closing(get_db()) as conn:
        task = conn.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()
        if not task:
            return jsonify({"status": "error", "message": "Task not found"}), 404
        
        task_dict = dict(task)
        
        # Check for zombie task using an atomic update
        if task_dict['status'] == 'processing':
            cursor = conn.execute('''
                UPDATE tasks SET status = 'error', message = 'Task timed out or worker crashed.' 
                WHERE id = ? AND status = 'processing' AND created_at < datetime("now", "-5 minutes")
            ''', (task_id,))
            conn.commit()
            if cursor.rowcount > 0:
                task_dict['status'] = 'error'
                task_dict['message'] = 'Task timed out or worker crashed.'
                
        if task_dict.get('result'):
            task_dict['result'] = json.loads(task_dict['result'])
        return jsonify(task_dict)

@app.route('/download-pdf', methods=['POST'])
def download_pdf():
    data = request.get_json(silent=True) or {}
    latex_code = data.get('latex')
    
    if not latex_code:
        return jsonify({"status": "error", "message": "No LaTeX code provided"}), 400
        
    if len(latex_code) > 100000:
        return jsonify({"status": "error", "message": "Input too large"}), 413

    try:
        import re
        print("Compiling LaTeX locally...")
        
        # FIX: Robustly strip out the problematic Jake's Resume manual margins
        # We explicitly target the exact margin variables and use \s* to ignore any AI-generated spaces.
        safe_latex_code = re.sub(r'\\usepackage\s*(?:\[.*?\])?\s*\{fullpage\}', '', latex_code)
        
        margin_vars = ['oddsidemargin', 'evensidemargin', 'textwidth', 'topmargin', 'textheight']
        for var in margin_vars:
            safe_latex_code = re.sub(rf'\\addtolength\s*\{{\\{var}\}}\s*\{{.*?\}}', '', safe_latex_code)
            safe_latex_code = re.sub(rf'\\setlength\s*\{{\\{var}\}}\s*\{{.*?\}}', '', safe_latex_code)
        
        # LaTeX security sandbox: Strip dangerous file access commands
        dangerous_commands = [r'\\input', r'\\include', r'\\CatchFileEdef', r'\\read', r'\\openin', r'\\file', r'\\write', r'\\usepackage\s*\{catchfile\}']
        for cmd in dangerous_commands:
            safe_latex_code = re.sub(cmd, '', safe_latex_code, flags=re.IGNORECASE)
            
        # FIX geometry package conflict by stripping out any existing geometry package first
        safe_latex_code = re.sub(r'\\usepackage\s*(?:\[.*?\])?\s*\{geometry\}', '', safe_latex_code)
        
        # Force a perfect 0.5 inch margin all around on Letter paper.
        # We use re.sub with \s* to guarantee we find \begin{document} even if the AI added spaces.
        # Notice the double backslashes in the replacement string to prevent re.sub from parsing \u as a Unicode escape.
        geometry_config = r'\\usepackage[letterpaper, top=0.5in, bottom=0.5in, left=0.5in, right=0.5in]{geometry}' + '\n'
        safe_latex_code = re.sub(r'\\begin\s*\{document\}', geometry_config + r'\\begin{document}', safe_latex_code, count=1)
        
        # FAIL-SAFE: If the AI output gets truncated and misses the closing tag, 
        # pdflatex will abruptly stop and dump its buffer, ruining the final page's formatting.
        if r'\end{document}' not in safe_latex_code:
            safe_latex_code += '\n\\end{document}'
        
        # Local compilation using subprocess
        with tempfile.TemporaryDirectory() as temp_dir:
            tex_file = os.path.join(temp_dir, 'resume.tex')
            with open(tex_file, 'w', encoding='utf-8') as f:
                f.write(safe_latex_code)
            
            # Run pdflatex twice for cross-references
            for _ in range(2):
                try:
                    # Paranoid mode prevents arbitrary file read even if regex is bypassed
                    custom_env = os.environ.copy()
                    custom_env['openin_any'] = 'p'
                    custom_env['openout_any'] = 'p'
                    
                    process = subprocess.run(
                        ['pdflatex', '-interaction=nonstopmode', '-no-shell-escape', '-halt-on-error', 'resume.tex'],
                        cwd=temp_dir,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=15,
                        env=custom_env
                    )
                    if process.returncode != 0:
                        error_output = process.stdout.decode('utf-8', errors='ignore')
                        # Log error safely and do not return stdout to client
                        print(f"LaTeX Compilation Error:\n{error_output}")
                        return jsonify({"status": "error", "message": "LaTeX compilation failed. Please check your input."}), 500
                except subprocess.TimeoutExpired:
                    return jsonify({"status": "error", "message": "LaTeX compilation timed out. Possible infinite loop."}), 400
            
            pdf_file = os.path.join(temp_dir, 'resume.pdf')
            if os.path.exists(pdf_file):
                with open(pdf_file, 'rb') as f:
                    pdf_bytes = f.read()
                return send_file(
                    io.BytesIO(pdf_bytes),
                    as_attachment=True,
                    download_name='Optimized_Resume.pdf',
                    mimetype='application/pdf'
                )
            else:
                return jsonify({"status": "error", "message": "Local LaTeX compilation failed. PDF not found."}), 500

    except Exception as e:
        print(f"Server Error: {e}")
        return jsonify({"status": "error", "message": "Failed to compile locally."}), 500

if __name__ == '__main__':
    is_debug = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(debug=is_debug, port=5000)