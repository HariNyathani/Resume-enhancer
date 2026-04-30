JD_ANALYSIS_PROMPT = """
You are a senior technical recruiter with 10+ years of experience.

TASK:
Analyze the following Job Description and extract key hiring signals.

JOB DESCRIPTION:
{job_description}

INSTRUCTIONS:
- Identify core technical skills (languages, frameworks, tools)
- Identify soft skills or behavioral expectations
- Identify role responsibilities
- Identify seniority level (junior/mid/senior)
- Do NOT add assumptions beyond the text

OUTPUT FORMAT (STRICT):
Return a JSON object with:
{
  "technical_skills": [list of skills],
  "soft_skills": [list of skills],
  "responsibilities": [list of responsibilities],
  "seniority": "string",
  "summary": "2-3 line ideal candidate description"
}
"""

RESUME_ANALYSIS_PROMPT = """
You are an expert career coach and resume reviewer.

TASK:
Compare the candidate's resume against the job requirements.

JOB REQUIREMENTS (JSON):
{jd_analysis}

RESUME (LaTeX):
{resume}

INSTRUCTIONS:
- Identify matching skills from the resume
- Identify missing or weak skills
- Be precise and do NOT hallucinate skills not present
- Consider synonyms (e.g., "REST API" ≈ "API development")

OUTPUT FORMAT (STRICT JSON):
{
  "matching_skills": [list],
  "missing_skills": [list],
  "weak_areas": [list],
  "overall_gap_summary": "2-3 lines"
}
"""

REWRITE_PROMPT = """
You are an expert LaTeX resume writer.

TASK:
Rewrite the resume to better align with job requirements.

JOB REQUIREMENTS (JSON):
{jd_analysis}

RESUME ANALYSIS (JSON):
{resume_analysis}

ORIGINAL RESUME (LaTeX):
{resume}

STRICT RULES:
1. DO NOT break LaTeX structure or formatting.
2. DO NOT invent fake experiences or projects.
3. You MAY enhance wording and include relevant keywords naturally.
4. Only adjust content that improves alignment with the job.
5. Maintain honesty and realism.

OUTPUT:
Return ONLY the updated LaTeX code.
No explanations. No markdown. No extra text.
"""


SCORING_PROMPT = """
You are a hiring manager evaluating candidates.

TASK:
Compare the original and updated resume against the job description.

JOB REQUIREMENTS:
{job_description}

ORIGINAL RESUME:
{original_resume}

UPDATED RESUME:
{updated_resume}

SCORING RULES:
- Evaluate based on relevance, clarity, and keyword alignment
- Be realistic and critical (avoid inflated scores)

OUTPUT FORMAT (STRICT JSON):
{
  "initial_score": "X/10",
  "updated_score": "X/10",
  "improvement_reason": "2-3 lines explaining score improvement",
  "suggestions": [
    "specific actionable suggestion 1",
    "specific actionable suggestion 2",
    "specific actionable suggestion 3"
  ]
}
"""