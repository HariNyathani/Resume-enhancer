import requests
import io
from flask import Flask, render_template, request, jsonify, send_file
import threading
import uuid
from agent import optimize_resume_logic 

app = Flask(__name__)

# In-memory dictionary to store task statuses. 
# For your major project, this will be a database or Redis.
tasks = {}

def background_task(task_id, jd, resume):
    """This runs completely independent of the web request."""
    try:
        # This can now take as many minutes as it needs without timing out the browser!
        result = optimize_resume_logic(jd, resume)
        tasks[task_id] = {"status": "completed", "result": result}
    except Exception as e:
        tasks[task_id] = {"status": "error", "message": str(e)}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/optimize', methods=['POST'])
def optimize():
    data = request.json
    job_description = data.get('jd')
    resume_latex = data.get('resume')
    
    if not job_description or not resume_latex:
         return jsonify({"status": "error", "message": "Missing JD or Resume"}), 400

    # 1. Generate a unique ID for this request
    task_id = str(uuid.uuid4())
    
    # 2. Register the task as processing
    tasks[task_id] = {"status": "processing"}
    
    # 3. Fire off the background thread
    thread = threading.Thread(target=background_task, args=(task_id, job_description, resume_latex))
    thread.start()
    
    # 4. Immediately return the ID so the browser doesn't hang
    return jsonify({"status": "accepted", "task_id": task_id})

@app.route('/status/<task_id>', methods=['GET'])
def check_status(task_id):
    """The frontend will hit this route every few seconds to check on the agent."""
    task = tasks.get(task_id)
    if not task:
        return jsonify({"status": "error", "message": "Task not found"}), 404
    
    return jsonify(task)

@app.route('/download-pdf', methods=['POST'])
def download_pdf():
    data = request.json
    latex_code = data.get('latex')
    
    if not latex_code:
        return jsonify({"status": "error", "message": "No LaTeX code provided"}), 400

    try:
        import re
        print("Sending LaTeX to latex.ytotech.com for compilation...")
        
        # FIX: Robustly strip out the problematic Jake's Resume manual margins
        # We explicitly target the exact margin variables and use \s* to ignore any AI-generated spaces.
        safe_latex_code = re.sub(r'\\usepackage\s*(?:\[.*?\])?\s*\{fullpage\}', '', latex_code)
        
        margin_vars = ['oddsidemargin', 'evensidemargin', 'textwidth', 'topmargin', 'textheight']
        for var in margin_vars:
            safe_latex_code = re.sub(rf'\\addtolength\s*\{{\\{var}\}}\s*\{{.*?\}}', '', safe_latex_code)
            safe_latex_code = re.sub(rf'\\setlength\s*\{{\\{var}\}}\s*\{{.*?\}}', '', safe_latex_code)
        
        # Force a perfect 0.5 inch margin all around on Letter paper.
        # We use re.sub with \s* to guarantee we find \begin{document} even if the AI added spaces.
        # Notice the double backslashes in the replacement string to prevent re.sub from parsing \u as a Unicode escape.
        geometry_config = r'\\usepackage[letterpaper, top=0.5in, bottom=0.5in, left=0.5in, right=0.5in]{geometry}' + '\n'
        safe_latex_code = re.sub(r'\\begin\s*\{document\}', geometry_config + r'\\begin{document}', safe_latex_code, count=1)
        
        # FAIL-SAFE: If the AI output gets truncated and misses the closing tag, 
        # pdflatex will abruptly stop and dump its buffer, ruining the final page's formatting.
        if r'\end{document}' not in safe_latex_code:
            safe_latex_code += '\n\\end{document}'
        
        # Use the LaTeX-on-HTTP API (POST JSON) which handles large documents
        payload = {
            "compiler": "pdflatex",
            "resources": [
                {
                    "main": True,
                    "content": safe_latex_code
                }
            ]
        }
        
        response = requests.post(
            'https://latex.ytotech.com/builds/sync',
            json=payload,
            timeout=60
        )

        # Check if we actually got a PDF back by looking at the file header
        # %PDF is the universal magic header for all PDF files
        if response.status_code in [200, 201] and b'%PDF' in response.content[:50]:
            print("PDF compilation successful!")
            # Send the compiled PDF back to the user's browser
            return send_file(
                io.BytesIO(response.content),
                as_attachment=True,
                download_name='Optimized_Resume.pdf',
                mimetype='application/pdf'
            )
        else:
            error_snippet = response.text[:1500] if response.text else "No error details returned."
            print(f"API Error (status {response.status_code}): {error_snippet}")
            return jsonify({"status": "error", "message": f"LaTeX compilation failed. The AI may have generated invalid LaTeX. Details: {error_snippet[:300]}"}), 500

    except Exception as e:
        print(f"Server Error: {e}")
        return jsonify({"status": "error", "message": "Failed to connect to compiler."}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)