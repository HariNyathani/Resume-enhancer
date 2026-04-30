import os
import json
import google.generativeai as genai
from dotenv import load_dotenv
import prompts

# Load environment variables from .env file
load_dotenv()

# Configure the API key
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# Initialize the model (using Pro for reliable, long-form LaTeX generation)
model = genai.GenerativeModel('gemini-3.1-pro-preview', generation_config={"max_output_tokens": 8192})
# Initialize a specific model instance for strict JSON output
json_model = genai.GenerativeModel('gemini-3.1-pro-preview', generation_config={"response_mime_type": "application/json", "max_output_tokens": 8192})

def optimize_resume_logic(jd, resume_latex):
    try:
        print("Step 1: Analyzing JD...")
        jd_response = model.generate_content(f"{prompts.JD_ANALYSIS_PROMPT}\n\nJob Description:\n{jd}")
        jd_analysis = jd_response.text

        print("Step 2: Analyzing Resume...")
        res_prompt = f"{prompts.RESUME_ANALYSIS_PROMPT}\n\nJD Analysis:\n{jd_analysis}\n\nOriginal Resume:\n{resume_latex}"
        res_response = model.generate_content(res_prompt)
        res_analysis = res_response.text

        print("Step 3: Rewriting Resume in LaTeX...")
        rewrite_prompt = f"{prompts.REWRITE_PROMPT}\n\nJD Analysis:\n{jd_analysis}\n\nResume Analysis:\n{res_analysis}\n\nOriginal Resume:\n{resume_latex}"
        rewrite_response = model.generate_content(rewrite_prompt)
        updated_latex = rewrite_response.text.strip()
        
        # Clean up markdown code blocks if the LLM accidentally includes them
        updated_latex = updated_latex.replace('```latex', '').replace('```', '').strip()

        print("Step 4: Generating Scores and Suggestions (JSON parsing)...")
        score_prompt = f"{prompts.SCORING_PROMPT}\n\nJob Description:\n{jd}\n\nOriginal Resume:\n{resume_latex}\n\nUpdated Resume:\n{updated_latex}"
        score_response = json_model.generate_content(score_prompt)
        
        # Parse the JSON response
        score_data = json.loads(score_response.text)

        return {
            "status": "success",
            "initial_score": score_data.get("initial_score", "N/A"),
            "updated_score": score_data.get("updated_score", "N/A"),
            "suggestions": score_data.get("suggestions", []),
            "updated_latex": updated_latex
        }

    except Exception as e:
        print(f"Error in agent workflow: {e}")
        return {
            "status": "error",
            "message": str(e)
        }