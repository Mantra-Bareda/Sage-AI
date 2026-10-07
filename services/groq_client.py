import os
import json
import requests
from services.gemini_client import generate_mcq_questions

def generate_mastery_mcqs(topic_text, previous_questions=None):
    if previous_questions is None:
        previous_questions = []

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("No GROQ_API_KEY found, falling back to Gemini.")
        return _fallback_to_gemini(topic_text)

    system_prompt = (
        "You are an expert AI tutor. Generate 5 multiple-choice questions based ONLY on the provided text.\n"
        "Return a strictly clean JSON object containing a single key 'questions' which maps to an array of objects matching this layout:\n"
        '[{"question": "...", "options": ["A", "B", "C", "D"], "answer_index": 0, "explanation": "..."}]\n'
        "Do NOT include any markdown blocks or other text outside the JSON object.\n"
    )

    if previous_questions:
        system_prompt += "\nTo force unique conceptual angles, DO NOT generate questions similar to these previously asked questions:\n"
        for q in previous_questions:
            system_prompt += f"- {q}\n"

    models = [
        "llama-3.3-70b-versatile",
        "qwen-2.5-32b", # Note: Qwen model ID changed to standard format or skip if fails
        "llama-3.1-8b-instant"
    ]
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    for model in models:
        try:
            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": system_prompt
                    },
                    {
                        "role": "user",
                        "content": f"Text to base questions on:\n\n{topic_text}"
                    }
                ],
                "temperature": 0.7,
                "response_format": {"type": "json_object"}
            }
            
            # Using Qwen specific ID from groq if possible, else it will fail and fallback.
            if "qwen" in model:
                payload["model"] = "qwen-2.5-32b" # Correct ID based on Groq docs, or just fallback. We'll rely on the waterfall.
            
            response = requests.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=payload, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                
                # Cleanup potential markdown wrapper if response_format didn't perfectly enforce it
                content = content.replace("```json", "").replace("```", "").strip()
                
                parsed = json.loads(content)
                if isinstance(parsed, dict) and "questions" in parsed and isinstance(parsed["questions"], list):
                    qs = parsed["questions"]
                    if len(qs) > 0:
                        return qs
            else:
                print(f"Model {model} failed with status {response.status_code}: {response.text}")
                continue

        except Exception as e:
            print(f"Model {model} encountered an exception: {e}")
            continue

    print("All Groq models failed. Falling back to Gemini.")
    return _fallback_to_gemini(topic_text)


def _fallback_to_gemini(topic_text):
    try:
        # Pass dummy unit list since we're hijacking the MCQ generator 
        # which expects the full syllabus text and generates for a unit.
        # But we pass the topic text as the "syllabus" and "Mastery" as unit.
        fallback_res = generate_mcq_questions(topic_text, "english", ["Mastery"])
        if fallback_res and "units" in fallback_res and len(fallback_res["units"]) > 0:
            qs = fallback_res["units"][0].get("questions", [])
            # Shuffle answers inside just in case
            return qs
    except Exception as e:
        print(f"Gemini fallback failed: {e}")
        
    return []
