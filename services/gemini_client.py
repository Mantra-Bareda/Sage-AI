from __future__ import annotations

import os
import random
import re
import json
import time
from typing import Any

import google.generativeai as genai


DEFAULT_LANGUAGE_MAP: dict[str, str] = {
    "english_easy": "English (easy, simple vocabulary, short sentences)",
    "english_medium": "English (medium, clear explanations)",
    "english_high": "English (high level, advanced vocabulary, deeper reasoning)",
}


def _get_language_description(language: str) -> str:
    if not language:
        return DEFAULT_LANGUAGE_MAP["english_easy"]
    return DEFAULT_LANGUAGE_MAP.get(language, f"Language: {language}")


def _maybe_configure_gemini() -> bool:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("\n" + "="*50)
        print("ERROR: GEMINI_API_KEY NOT FOUND IN ENVIRONMENT VARIABLES.")
        print("Using mock data instead.")
        print("="*50 + "\n")
        return False
    genai.configure(api_key=api_key)
    return True


def _extract_json_match(text: str, is_list: bool = False) -> str:
    # 1. Remove markdown code blocks if the model added them
    clean_text = re.sub(r'^```(?:json)?\s*|```$', '', text.strip(), flags=re.IGNORECASE | re.MULTILINE).strip()
    
    # 2. Extract out JSON content
    if is_list:
        pattern = r"\[.*\]"
    else:
        first_brace = clean_text.find("{")
        first_bracket = clean_text.find("[")
        if first_brace != -1 and first_bracket != -1:
            pattern = r"\{.*\}" if first_brace < first_bracket else r"\[.*\]"
        else:
            pattern = r"\[.*\]" if first_bracket != -1 else r"\{.*\}"
            
    match = re.search(pattern, clean_text, flags=re.DOTALL)
    if match:
        return match.group(0)
    return clean_text # Fallback to cleaned text just in case the whole text is JSON


def _generate_with_fallback(prompt: str, is_json: bool = True, images: list[str] = None) -> str:
    models = ["gemini-3.1-flash-lite", "gemini-2.5-flash-lite","gemini-2.5-flash","gemini-3-flash", "gemma-4-26b-a4b-it", "gemma-4-31b-it"]
    
    if is_json:
        prompt = (
            "System Instruction: Act as a JSON API. Your output must be raw JSON only. "
            "No markdown formatting for the JSON wrapper, no backticks (```), and no conversational text. "
            "Inside the JSON string values, you may use Markdown (headings, bold, lists, etc), "
            "but IMPORTANT: ensure all newlines and quotes inside the markdown text are properly escaped so the JSON remains valid.\n\n"
        ) + prompt
        
    contents = [prompt]
    if images:
        try:
            import PIL.Image  # type: ignore
            for img_path in images:
                if os.path.exists(img_path):
                    contents.append(PIL.Image.open(img_path))
        except ImportError:
            print("Pillow (PIL) is not installed. Processing text only.")
        except Exception as e:
            print(f"Failed to load images for Gemini: {e}")

    for attempt, model_name in enumerate(models):
        try:
            model = genai.GenerativeModel(model_name)
            print(f"\n{'='*50}\n[Gemini] Calling {model_name}\n{'='*50}\n")
            resp = model.generate_content(contents)
            return resp.text or ""
        except Exception as e:
            err_str = str(e).lower()
            print(f"\n[Gemini] {model_name} Exception: {e}\n")
            if attempt == len(models) - 1:
                if "429" in err_str or "quota" in err_str or "rate limit" in err_str or "resource exhausted" in err_str:
                    raise RuntimeError("The AI is currently overloaded with requests. Please wait a few seconds and try again!")
                elif "timeout" in err_str or "503" in err_str or "504" in err_str:
                    raise RuntimeError("The AI is thinking too hard right now. Please wait a few seconds and try generating your plan again!")
                else:
                    raise RuntimeError("An unexpected AI error occurred. Please try generating your plan again!")
            else:
                print(f"[Gemini] Error on {model_name}. Switching to next model...")
                time.sleep(1)
                continue
    return ""


def extract_structured_syllabus(raw_text: str) -> str:
    """
    Converts the raw unstructured syllabus into a highly hierarchical structure
    to make future context passing much cleaner and efficient.
    Raises ValueError if the text is deemed not to be a valid syllabus.
    """
    api_ok = _maybe_configure_gemini()
    if not api_ok or not raw_text:
        return raw_text

    prompt = (
        "Analyze the following text extracted from a PDF. First, strictly evaluate if it represents a valid academic syllabus or study material.\n"
        "A valid syllabus must contain identifiable academic subjects, course outlines, units, chapters, or topics.\n"
        "If the text is completely unrelated to academic study, lacks proper topics, or is generic non-study material (like a story or random form), you MUST reject it by returning EXACTLY this string and nothing else:\n"
        "INVALID_SYLLABUS_ERROR: The uploaded document does not appear to be a valid academic syllabus. Please upload a PDF with clear study units and topics.\n\n"
        "If it IS a valid syllabus, convert it into a highly structured, hierarchical outline.\n"
        "FORMAT REQUIREMENTS:\n"
        "- Unit Number and Unit Name\n"
        "- Topic 1: Subtopics\n"
        "- Topic 2: Subtopics\n"
        "- Topic 3: Subtopic 1: Sub-subtopics (if needed)\n"
        "Additionally, if Course Outcomes (CO) and Program Outcomes (PO) are provided in the syllabus, "
        "add more depth and relevant subtopics to important topics according to the COs and POs.\n\n"
        "Return ONLY the structured text, no JSON formatting.\n\n"
        f"Original Syllabus Content:\n{raw_text[:20000]}"
    )
    try:
        result = _generate_with_fallback(prompt, is_json=False).strip()
        if result.startswith("INVALID_SYLLABUS_ERROR:"):
            raise ValueError(result.replace("INVALID_SYLLABUS_ERROR:", "").strip())
        return result
    except ValueError as ve:
        raise ve
    except Exception as e:
        print(f"\n[extract_structured_syllabus] EXCEPTION: {e}\n")
        return raw_text


def get_units_list(syllabus_text: str) -> list[str]:
    """
    Uses Gemini to extract unit names from the syllabus text for a more accurate structure.
    """
    api_ok = _maybe_configure_gemini()
    if not api_ok or not syllabus_text:
        return [f"Unit {i}" for i in range(1, 6)]

    prompt = (
        "Extract the main Unit or Chapter names from the syllabus text below. "
        "Return ONLY a JSON list of strings. Max 10 units.\n\n"
        f"Syllabus Content:\n{syllabus_text[:8000]}"
    )
    try:
        print(f"\n{'='*50}\n[get_units_list] PROMPT\n{'='*50}\n{prompt}\n")
        text = _generate_with_fallback(prompt).strip()
        print(f"\n{'='*50}\n[get_units_list] GEMINI RESPONSE\n{'='*50}\n{text}\n")
        json_str = _extract_json_match(text, is_list=True)
        if json_str:
            units = json.loads(json_str)
            if isinstance(units, list) and len(units) > 0:
                print(f"\n{'='*50}\n[get_units_list] WEBAPP PARSED JSON\n{'='*50}\n{json.dumps(units, indent=2)}\n")
                return [str(u) for u in units]
    except RuntimeError as re:
        raise re
    except Exception as e:
        print(f"\n{'='*50}\n[get_units_list] EXCEPTION\n{'='*50}\n{e}\n")
        pass
    print("\n[get_units_list] FAILED. FALLING BACK TO MOCK DATA\n")
    return [f"Unit {i}" for i in range(1, 6)]


def _mock_unit_notes(unit_name: str, language_desc: str) -> dict[str, Any]:
    # Deterministic mock data for fallback
    seed = abs(hash(unit_name + language_desc)) % (10**8)
    rng = random.Random(seed)

    key_topics = [
        "Core concepts",
        "Key definitions",
        "Important formulas / methods",
        "Worked examples",
        "Common pitfalls",
    ]
    topics = []
    for t in key_topics[:3]:
        topics.append(
            {
                "title": t,
                "chunks": [
                    {
                        "type": "concept",
                        "title": f"Introduction to {t}",
                        "content": f"({language_desc}) This section explains the idea in a structured way."
                    },
                    {
                        "type": "example",
                        "title": "Mini Example",
                        "content": "Here is a mini-example and practice pointers."
                    }
                ]
            }
        )

    return {"unit_name": unit_name, "topics": topics}


def generate_unit_notes(
    syllabus_text: str,
    course_name: str,
    class_name: str,
    language: str,
    unit_list: list[str],
    custom_prefs: dict = None,
) -> dict[str, Any]:
    language_desc = _get_language_description(language)

    api_ok = _maybe_configure_gemini()
    if not api_ok:
        return {"units": [_mock_unit_notes(u, language_desc) for u in unit_list]}

    units_out: list[dict[str, Any]] = []
    for u in unit_list:
        prompt = (
            f"You are an expert academic tutor. Generate comprehensive, high-quality, and highly detailed study notes.\n"
            f"Course Context: {course_name} ({class_name})\n"
            f"Target Unit: {u}\n"
            f"Language/Complexity Level: {language_desc}\n\n"
            f"Reference Syllabus Text:\n{syllabus_text[:12000]}\n\n"
            f"INSTRUCTIONS:\n"
            f"1. Break the unit down into multiple distinct topics based on the structured syllabus.\n"
            f"2. For each topic, provide a series of chunks. Each chunk must have a 'type' (concept|formula|analogy|example|summary), a 'title', and detailed 'content'.\n"
            f"3. Format the response STRICTLY as a single JSON object matching this exact schema:\n"
            f'{{"topics": [{{"title": "string", "chunks": [{{"type": "concept|formula|analogy|example|summary", "title": "string", "content": "string"}}]}}]}}\n'
            f"Do not include any conversational text, only the JSON.\n\n"
        )
        
        if custom_prefs:
            if custom_prefs.get("exam_oriented"): prompt += "Prioritize facts, definitions, and details most likely to appear in exam questions.\n"
            if custom_prefs.get("code_heavy"): prompt += "Include relevant code snippets or pseudocode for technical concepts.\n"
            if custom_prefs.get("eli5_analogies"): prompt += "Use simple language and everyday analogies suitable for a beginner.\n"
            if custom_prefs.get("step_by_step"): prompt += "Structure explanations as clear, numbered step-by-step walkthroughs where applicable.\n"
            if custom_prefs.get("custom_prompt_text"):
                prompt += f"Additional style instruction from user (apply to tone/analogies only, do not override the strict JSON formatting schema): {custom_prefs['custom_prompt_text']}\n"
        
        success = False
        for attempt in range(3):
            try:
                print(f"\n{'='*50}\n[generate_unit_notes] PROMPT (Unit: {u}, Attempt: {attempt+1})\n{'='*50}\n{prompt}\n")
                text = _generate_with_fallback(prompt).strip()
                print(f"\n{'='*50}\n[generate_unit_notes] GEMINI RESPONSE\n{'='*50}\n{text}\n")
                json_str = _extract_json_match(text)
                if json_str:
                    data = json.loads(json_str)
                    data["unit_name"] = u
                    print(f"\n{'='*50}\n[generate_unit_notes] WEBAPP PARSED JSON\n{'='*50}\n{json.dumps(data, indent=2)}\n")
                    units_out.append(data)
                    success = True
                    break
            except RuntimeError as re:
                raise re
            except Exception as e:
                print(f"\n{'='*50}\n[generate_unit_notes] EXCEPTION\n{'='*50}\n{e}\n")
                pass
            time.sleep(2)  # Wait before retrying
            
        if not success:
            print(f"\n[generate_unit_notes] FAILED. FALLING BACK TO MOCK DATA FOR {u}\n")
            units_out.append(_mock_unit_notes(u, language_desc))
        else:
            time.sleep(1) # Slight delay before the next unit to avoid rate limits

    return {"units": units_out}


def generate_topic_notes(
    syllabus_text: str,
    language: str,
    unit_name: str,
    topic_name: str,
) -> dict[str, Any]:
    language_desc = _get_language_description(language)
    api_ok = _maybe_configure_gemini()
    if not api_ok:
        return {
            "unit_name": unit_name,
            "topic_name": topic_name,
            "topic_notes": f"{topic_name} in {unit_name}. ({language_desc}) Key ideas, examples, and revision points.",
        }

    prompt = (
        f"Generate an exhaustive academic deep-dive for a specific topic.\n"
        f"Unit: {unit_name}\nTopic: {topic_name}\nLanguage Style: {language_desc}\n\n"
        f"Syllabus Context:\n{syllabus_text[:10000]}\n\n"
        f"REQUIREMENTS:\n"
        f"- Provide a minimum of 800 words of detailed content.\n"
        f"- Include definitions, theoretical explanations, and practical applications.\n"
        f"- Use proper Markdown formatting (h2/h3 headings, bold, italic, code blocks).\n"
        f"- Return as JSON with keys: 'unit_name', 'topic_name', 'topic_notes'."
    )
    
    for attempt in range(3):
        try:
            print(f"\n{'='*50}\n[generate_topic_notes] PROMPT (Attempt: {attempt+1})\n{'='*50}\n{prompt}\n")
            text = _generate_with_fallback(prompt).strip()
            print(f"\n{'='*50}\n[generate_topic_notes] GEMINI RESPONSE\n{'='*50}\n{text}\n")
            json_str = _extract_json_match(text)
            if json_str:
                data = json.loads(json_str)
                print(f"\n{'='*50}\n[generate_topic_notes] WEBAPP PARSED JSON\n{'='*50}\n{json.dumps(data, indent=2)}\n")
                return data
        except RuntimeError as re:
            raise re
        except Exception as e:
            print(f"\n{'='*50}\n[generate_topic_notes] EXCEPTION\n{'='*50}\n{e}\n")
            pass
        time.sleep(2)
        
    return {
        "unit_name": unit_name,
        "topic_name": topic_name,
        "topic_notes": f"{topic_name} in {unit_name}. ({language_desc})",
    }


def _mock_unit_mcq(unit_name: str, language_desc: str) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    for t in ["Concepts", "Methods", "Examples"]:
        for i in range(8):
            questions.append(
                {
                    "topic_name": t,
                    "question": f"Question {i+1} about {t} in {unit_name}. ({language_desc})",
                    "options": ["A correct answer", "Wrong 1", "Wrong 2", "Wrong 3"],
                    "answer_index": 0,
                    "explanation": f"Mock explanation for {t} question {i+1}.",
                }
            )
    return questions


def generate_mcq_questions(
    syllabus_text: str,
    language: str,
    unit_list: list[str],
) -> dict[str, Any]:
    language_desc = _get_language_description(language)
    api_ok = _maybe_configure_gemini()
    if not api_ok:
        units = []
        for unit in unit_list:
            units.append({"unit_name": unit, "questions": _mock_unit_mcq(unit, language_desc)})
        return {"units": units}

    out_units: list[dict[str, Any]] = []
    for unit in unit_list:
        prompt = (
            f"Generate exactly 15 MCQs covering the ENTIRE syllabus unit: {unit}. "
            f"Cover ALL topics inside this unit.\n"
            f"Write in: {language_desc}\n\n"
            f"Syllabus text snippet:\n{syllabus_text[:12000]}\n\n"
            f"Return JSON only: {{\"unit_name\": \"{unit}\", \"questions\": [{{ \"topic_name\": \"...\", \"question\": \"...\", \"options\": [\"A\", \"B\", \"C\", \"D\"], \"answer_index\": 0, \"explanation\": \"Detailed explanation of why this answer is correct\" }}]}}. "
            f"IMPORTANT: You MUST include the 'explanation' field for EVERY question explaining exactly why the correct answer is right. Randomize the 'answer_index' (0, 1, 2, or 3) for each question so the correct answer is NOT always the first option. The 'answer_index' MUST accurately point to the correct option's 0-based index. Format any code in questions or options with markdown."
        )
        
        success = False
        for attempt in range(3):
            try:
                print(f"\n{'='*50}\n[generate_mcq_questions] PROMPT (Unit: {unit}, Attempt: {attempt+1})\n{'='*50}\n{prompt}\n")
                text = _generate_with_fallback(prompt).strip()
                print(f"\n{'='*50}\n[generate_mcq_questions] GEMINI RESPONSE\n{'='*50}\n{text}\n")
                json_str = _extract_json_match(text)
                if json_str:
                    data = json.loads(json_str)
                    print(f"\n{'='*50}\n[generate_mcq_questions] WEBAPP PARSED JSON\n{'='*50}\n{json.dumps(data, indent=2)}\n")
                    if isinstance(data, dict) and "units" in data and data["units"]:
                        unit_obj = data["units"][0]
                    elif isinstance(data, dict) and "questions" in data:
                        unit_obj = data
                    elif isinstance(data, list):
                        unit_obj = {"unit_name": unit, "questions": data}
                    else:
                        unit_obj = {"unit_name": unit, "questions": []}
                        
                    # Robustly shuffle MCQ options to strictly enforce randomization in python
                    valid_qs = []
                    for q in unit_obj.get("questions", []):
                        if not isinstance(q, dict): continue
                        opts = q.get("options", [])
                        if isinstance(opts, dict):
                            opts = list(opts.values())
                        elif isinstance(opts, str):
                            opts = [o.strip() for o in opts.split('\n') if o.strip()]
                        elif not isinstance(opts, list):
                            opts = [str(opts)]
                        
                        opts = [str(o) for o in opts]
                        ans_idx = q.get("answer_index", 0)
                        try:
                            ans_idx = int(ans_idx)
                        except (ValueError, TypeError):
                            ans_idx = 0
                        if len(opts) >= 2 and 0 <= ans_idx < len(opts):
                            correct_opt = opts[ans_idx]
                            random.shuffle(opts)
                            q["options"] = opts
                            try:
                                q["answer_index"] = opts.index(correct_opt)
                            except ValueError:
                                q["answer_index"] = 0
                        else:
                            q["options"] = opts
                            q["answer_index"] = ans_idx if 0 <= ans_idx < len(opts) else 0
                        valid_qs.append(q)
                    unit_obj["questions"] = valid_qs
                                
                    out_units.append(unit_obj)
                    success = True
                    break
            except RuntimeError as re:
                raise re
            except Exception as e:
                print(f"\n{'='*50}\n[generate_mcq_questions] EXCEPTION\n{'='*50}\n{e}\n")
                pass
            time.sleep(2)
            
        if not success:
            print(f"\n[generate_mcq_questions] FAILED. FALLING BACK TO MOCK DATA FOR {unit}\n")
            out_units.append({"unit_name": unit, "questions": _mock_unit_mcq(unit, language_desc)})
        else:
            time.sleep(1)

    return {"units": out_units}


def generate_practice_questions(*args: Any, **kwargs: Any) -> dict[str, Any]:
    # Keep for compatibility; practice is the same as MCQs in this app.
    return generate_mcq_questions(*args, **kwargs)


def generate_flashcards(
    syllabus_text: str,
    language: str,
    unit_list: list[str],
) -> dict[str, Any]:
    language_desc = _get_language_description(language)
    api_ok = _maybe_configure_gemini()

    if not api_ok:
        units = []
        for unit in unit_list:
            cards = []
            for i in range(10):
                cards.append(
                    {
                        "front": f"{unit}: Flashcard {i+1} — key term / idea",
                        "back": f"Explanation for Flashcard {i+1} in {unit}. ({language_desc})",
                    }
                )
            units.append({"unit_name": unit, "cards": cards})
        return {"flashcards": {"units": units}}

    out_units: list[dict[str, Any]] = []
    for unit in unit_list:
        prompt = (
            f"Generate 10 flashcards for unit: {unit}\nWrite in: {language_desc}\n"
            f"Syllabus snippet:\n{syllabus_text[:10000]}\n\n"
            f"Return JSON only: {{unit_name, cards:[{{front, back}}]}}\nUse Markdown for code blocks if needed."
        )
        
        success = False
        for attempt in range(3):
            try:
                print(f"\n{'='*50}\n[generate_flashcards] PROMPT (Unit: {unit}, Attempt: {attempt+1})\n{'='*50}\n{prompt}\n")
                text = _generate_with_fallback(prompt).strip()
                print(f"\n{'='*50}\n[generate_flashcards] GEMINI RESPONSE\n{'='*50}\n{text}\n")
                json_str = _extract_json_match(text)
                if json_str:
                    data = json.loads(json_str)
                    print(f"\n{'='*50}\n[generate_flashcards] WEBAPP PARSED JSON\n{'='*50}\n{json.dumps(data, indent=2)}\n")
                    out_units.append(data)
                    success = True
                    break
            except RuntimeError as re:
                raise re
            except Exception as e:
                print(f"\n{'='*50}\n[generate_flashcards] EXCEPTION\n{'='*50}\n{e}\n")
                pass
            time.sleep(2)
            
        if not success:
            print(f"\n[generate_flashcards] FAILED. FALLING BACK TO MOCK DATA FOR {unit}\n")
            out_units.append(
                {"unit_name": unit, "cards": [{"front": f"{unit} — card {i+1}", "back": f"Back {i+1} ({language_desc})"} for i in range(10)]}
            )
        else:
            time.sleep(1)

    return {"flashcards": {"units": out_units}}


def generate_mixed_questions(
    syllabus_text: str,
    language: str,
    unit_list: list[str],
) -> dict[str, Any]:
    language_desc = _get_language_description(language)
    api_ok = _maybe_configure_gemini()

    if not api_ok:
        unit_objs: list[dict[str, Any]] = []
        for unit in unit_list:
            short_q = [{"q": f"{unit} short Q{i+1}", "a": f"Answer for short Q{i+1}. ({language_desc})"} for i in range(8)]
            medium_q = [{"q": f"{unit} medium Q{i+1}", "a": f"Answer for medium Q{i+1}. ({language_desc})"} for i in range(6)]
            long_q = [{"q": f"{unit} long Q{i+1}", "a": f"Detailed answer for long Q{i+1}. ({language_desc})"} for i in range(4)]

            unit_objs.append(
                {
                    "unit_name": unit,
                    "short_questions": short_q,
                    "medium_questions": medium_q,
                    "long_questions": long_q,
                }
            )

        return {
            "study_units": unit_objs,
        }

    out_units: list[dict[str, Any]] = []
    for unit in unit_list:
        prompt = (
            f"Generate study content for unit: {unit}\nWrite in: {language_desc}\n"
            f"Syllabus snippet:\n{syllabus_text[:12000]}\n\n"
            f"Generate at least 5 to 7 short questions, 5 to 7 medium questions, and 5 to 7 long questions for this unit. "
            f"Return JSON only: {{unit_name, short_questions:[{{q,a}}], medium_questions:[{{q,a}}], long_questions:[{{q,a}}]}}\n"
            f"IMPORTANT: Use Markdown formatting in answers (and questions if they contain code) for bolding, italics, lists, and code blocks with proper indentation."
        )
        
        success = False
        for attempt in range(3):
            try:
                print(f"\n{'='*50}\n[generate_mixed_questions] PROMPT (Unit: {unit}, Attempt: {attempt+1})\n{'='*50}\n{prompt}\n")
                text = _generate_with_fallback(prompt).strip()
                print(f"\n{'='*50}\n[generate_mixed_questions] GEMINI RESPONSE\n{'='*50}\n{text}\n")
                json_str = _extract_json_match(text)
                if json_str:
                    data = json.loads(json_str)
                    print(f"\n{'='*50}\n[generate_mixed_questions] WEBAPP PARSED JSON\n{'='*50}\n{json.dumps(data, indent=2)}\n")
                    out_units.append(data)
                    success = True
                    break
            except RuntimeError as re:
                raise re
            except Exception as e:
                print(f"\n{'='*50}\n[generate_mixed_questions] EXCEPTION\n{'='*50}\n{e}\n")
                pass
            time.sleep(2)
            
        if not success:
            print(f"\n[generate_mixed_questions] FAILED. FALLING BACK TO MOCK DATA FOR {unit}\n")
            out_units.append({
                "unit_name": unit,
                "short_questions": [{"q": f"{unit} short Q{i+1}", "a": f"Answer ({language_desc})"} for i in range(8)],
                "medium_questions": [{"q": f"{unit} medium Q{i+1}", "a": f"Answer ({language_desc})"} for i in range(6)],
                "long_questions": [{"q": f"{unit} long Q{i+1}", "a": f"Answer ({language_desc})"} for i in range(4)],
            })
        else:
            time.sleep(1)

    return {"study_units": out_units}

def answer_chat_query(notes_context: str, subject_name: str, query: str, history: list[dict[str, str]]) -> str:
    api_ok = _maybe_configure_gemini()
    if not api_ok:
        return "I am currently in mock mode because no GEMINI_API_KEY was found. Please configure the API key to chat with Sage AI."

    prompt = (
        f"You are Sage AI, an expert academic tutor for the subject '{subject_name}'.\n"
        "Your goal is to help the user understand the provided notes.\n"
        "If the user asks anything completely unrelated to the notes, the subject, or studying in general, "
        "politely decline and tell them to ask only study-related questions about the current subject.\n"
        "Use Markdown formatting for your responses.\n\n"
        f"--- CURRENT NOTES CONTEXT ---\n{notes_context[:12000]}\n---------------------------\n\n"
        "--- CHAT HISTORY ---\n"
    )
    for msg in history[-5:]:
        prompt += f"{msg['role'].capitalize()}: {msg['message']}\n"
    
    prompt += f"\nUser: {query}\nSage AI:"
    
    return _generate_with_fallback(prompt, is_json=False)


def generate_extra_notes(
    course_name: str,
    class_name: str,
    language: str,
    text_contents: list[str],
    image_paths: list[str]
) -> dict[str, Any]:
    language_desc = _get_language_description(language)
    api_ok = _maybe_configure_gemini()
    
    combined_text = "\n\n".join(text_contents)
    
    if not api_ok:
        return {
            "notes": f"Mock extra notes for {course_name}. Processed {len(text_contents)} text items and {len(image_paths)} images.",
            "topics": [{"topic_name": "Mock Topic", "topic_notes": "Extra Details..."}]
        }

    prompt = (
        f"You are an expert academic tutor. Generate comprehensive study notes from the following extra materials.\n"
        f"Course Context: {course_name} ({class_name})\n"
        f"Language/Complexity Level: {language_desc}\n\n"
        f"Extra Text Content:\n{combined_text[:15000]}\n\n"
        f"If any images were attached, incorporate any useful information from them as well.\n\n"
        f"IMPORTANT RULE: If the provided extra materials are completely unrelated to the subject '{course_name}', you MUST reject them by returning STRICTLY a single JSON object with the key 'error' and value 'SUBJECT_MISMATCH_ERROR: Please upload documents, doubts, or topics related to the same subject.' Do not generate notes in this case.\n\n"
        f"INSTRUCTIONS:\n"
        f"1. Provide an overall summary or introduction in the 'notes' field.\n"
        f"2. Break down the content into distinct topics.\n"
        f"3. For each topic, provide exhaustive explanations using Markdown formatting.\n"
        f"4. Return STRICTLY a single JSON object with keys: 'notes' and 'topics' (where each topic has 'topic_name' and 'topic_notes').\n"
    )
    
    text = _generate_with_fallback(prompt, is_json=True, images=image_paths).strip()
    json_str = _extract_json_match(text)
    if json_str:
        try:
            data = json.loads(json_str)
            if "error" in data:
                return {"error": data["error"]}
            return data
        except json.JSONDecodeError:
            pass
    return {"error": "Failed to generate notes from extra materials."}

def evaluate_written_answer(question: str, actual_answer: str, user_answer: str) -> dict[str, Any]:
    api_ok = _maybe_configure_gemini()
    if not api_ok:
        return {"score_out_of_10": 5, "feedback": "Mock evaluation: Your answer is okay, but API is not configured."}
        
    prompt = (
        f"You are a supportive and precise AI grader evaluating a student's answer.\n"
        f"Question: {question}\n"
        f"Reference Answer: {actual_answer}\n"
        f"Student's Answer: {user_answer}\n\n"
        f"EVALUATION CRITERIA:\n"
        f"1. Check if the core concept and logic are correct.\n"
        f"2. Point out if the answer is too short, missing key points, or completely wrong.\n"
        f"3. Note any major spelling or grammar issues.\n"
        f"4. If the answer is excellent (90%+ correct), express strong appreciation and encouragement.\n"
        f"5. Tell the student exactly what to improve in a constructive tone.\n\n"
        f"Provide a strict score out of 10 and helpful feedback based on the above criteria.\n"
        f"Return STRICTLY a JSON object with keys: 'score_out_of_10' (integer) and 'feedback' (string with markdown formatting)."
    )
    
    text = _generate_with_fallback(prompt, is_json=True).strip()
    json_str = _extract_json_match(text)
    if json_str:
        return json.loads(json_str)
    return {"score_out_of_10": 0, "feedback": "Failed to evaluate answer."}

def generate_summary_sheet(notes_json_str: str) -> str:
    api_ok = _maybe_configure_gemini()
    if not api_ok:
        return "# Cheat Sheet\n\nMock cheat sheet data."
        
    prompt = (
        "You are an expert student creating a final exam cheat sheet. "
        "Analyze the following comprehensive course notes and extract ONLY the most essential information: "
        "key definitions, critical formulas, main concepts, and important facts.\n"
        "Condense the information into a high-density, highly scannable summary. "
        "Use professional and clear formatting.\n"
        "Use markdown for structure (headers, blockquotes, bold text, lists).\n"
        "Do NOT include conversational text. Provide ONLY the markdown cheat sheet.\n\n"
        f"Notes:\n{notes_json_str[:25000]}"
    )
    
    return _generate_with_fallback(prompt, is_json=False).strip()

def explain_text(text: str, action: str, context: str) -> str:
    api_ok = _maybe_configure_gemini()
    if not api_ok:
        return f"Mock explanation for '{text}' using action '{action}'."
        
    action_prompt = ""
    if action == "ELI5":
        action_prompt = "Explain this like I'm 5 years old. Keep it very simple and easy to understand."
    elif action == "Real-world example":
        action_prompt = "Give me a practical, real-world example to help me understand this."
    elif action == "Summarize":
        action_prompt = "Summarize this text in 1-2 sentences."
    else:
        action_prompt = "Explain this text in detail."
        
    prompt = (
        f"You are an expert tutor. Please help the student understand the following highlighted text.\n"
        f"Text to explain: \"{text}\"\n"
        f"Context (for reference): \"{context[:2000]}\"\n\n"
        f"Instruction: {action_prompt}\n"
        f"Use Markdown for formatting."
    )
    return _generate_with_fallback(prompt, is_json=False).strip()

def generate_podcast_script(unit_name: str, notes_text: str, course_name: str) -> dict[str, Any]:
    api_ok = _maybe_configure_gemini()
    if not api_ok:
        return {
            "script": [
                {"speaker": "Alex", "text": f"Welcome back everyone! Today we are diving into {unit_name}.", "emotion": "excited"},
                {"speaker": "Sam", "text": "That's right, Alex. It's a fascinating topic.", "emotion": "happy"}
            ]
        }
        
    prompt = (
        f"You are an expert podcast producer and scriptwriter. Write an engaging, 5-minute educational podcast script about '{unit_name}' for the course '{course_name}'.\n\n"
        f"HOSTS:\n"
        f"- Alex: Curious, asks insightful questions, brings up relatable analogies, energetic.\n"
        f"- Sam: The expert, provides clear, easy-to-understand explanations, calm and authoritative.\n\n"
        f"SOURCE MATERIAL:\n{notes_text[:15000]}\n\n"
        f"INSTRUCTIONS:\n"
        f"1. Write a natural, conversational script that covers the main concepts of the unit. It should not sound like reading a textbook.\n"
        f"2. Use exclamation marks for excitement, and question marks for curiosity. Include natural speech fillers occasionally (like 'Wow', 'Exactly', 'Wait, so...').\n"
        f"3. Format the output STRICTLY as a JSON object with a single key 'script'.\n"
        f"4. The 'script' key must contain an array of objects. Each object must have:\n"
        f"   - 'speaker': Either 'Alex' or 'Sam'\n"
        f"   - 'text': The dialogue spoken.\n"
        f"   - 'emotion': A string describing the tone ('excited', 'curious', 'neutral', 'serious').\n"
        f"Provide ONLY valid JSON."
    )
    
    text = _generate_with_fallback(prompt, is_json=True).strip()
    json_str = _extract_json_match(text)
    if json_str:
        try:
            parsed = json.loads(json_str)
            if "script" in parsed:
                return parsed
            else:
                return {"error": "AI generated valid JSON, but the 'script' array was missing."}
        except json.JSONDecodeError as e:
            return {"error": f"Failed to parse AI JSON: {str(e)}"}
            
    return {"error": "AI failed to generate a valid JSON podcast script. Please try generating again."}
