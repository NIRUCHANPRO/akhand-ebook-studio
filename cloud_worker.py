import os
import re
import sys
import json
import time
import math
import base64
import random
import shutil
import hashlib
import threading
import subprocess
import unicodedata
import urllib.parse
from io import BytesIO
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image, ImageDraw, ImageFont

# ==========================================
# 0. 24/7 CLOUD + NETLIFY BRIDGE CONFIGURATION
# ==========================================
CLOUD_API_KEY_ENV = os.environ.get("GOOGLE_API_KEY", "").strip()
GH_TOKEN = os.environ.get("GH_TOKEN", "").strip()
GH_REPO = os.environ.get("GH_REPO", "").strip()
JOB_ID = os.environ.get("JOB_ID", "").strip()
RAW_PAYLOAD_JSON = os.environ.get("PAYLOAD_JSON", "").strip()
IS_CLOUD_RUN = bool(JOB_ID or RAW_PAYLOAD_JSON)

# Runtime overrides populated when triggered from Netlify Website
CLOUD_OVERRIDES = {
    "title": "",
    "author": "",
    "theme": "auto",
    "cover_prompt": "",
    "language": "",
    "volume": ""
}

def update_live_status(progress_pct, message, done=False, pdf_url="", error=""):
    """
    Website ke Live Progress Bar ko real-time update bhejta hai (GitHub status/<job_id>.json).
    Agar phone/local par chal raha hai toh sirf console mein print karta hai.
    """
    pct = min(100, max(1, int(progress_pct)))
    print(f"[LIVE STATUS {pct}%] {message}")
    if not IS_CLOUD_RUN or not GH_TOKEN or not GH_REPO or not JOB_ID:
        return

    status_path = f"status/{JOB_ID}.json"
    api_url = f"https://api.github.com/repos/{GH_REPO}/contents/{status_path}"
    headers = {
        "Authorization": f"Bearer {GH_TOKEN}",
        "Accept": "application/vnd.github+json"
    }

    payload_data = {
        "job_id": JOB_ID,
        "progress": pct,
        "message": message,
        "done": done,
        "pdf_url": pdf_url,
        "error": error,
        "updated_at": int(time.time())
    }
    content_b64 = base64.b64encode(json.dumps(payload_data, indent=2).encode("utf-8")).decode("ascii")

    sha = None
    try:
        r_get = requests.get(api_url, headers=headers, timeout=8)
        if r_get.status_code == 200:
            sha = r_get.json().get("sha")
    except Exception:
        pass

    body = {
        "message": f"Progress {JOB_ID}: {pct}% [skip ci]",
        "content": content_b64
    }
    if sha:
        body["sha"] = sha

    try:
        requests.put(api_url, headers=headers, json=body, timeout=10)
    except Exception as e:
        print(f"    [!] Cloud status update warning: {e}")

def upload_pdf_to_github_release(pdf_filepath):
    """
    Cloud mode mein final PDF ko GitHub Release ('ebooks-vault') par upload karke
    Netlify website ke liye Direct Download URL return karta hai.
    """
    if not IS_CLOUD_RUN or not GH_TOKEN or not GH_REPO:
        return os.path.abspath(pdf_filepath)

    tag = "ebooks-vault"
    subprocess.run(
        ["gh", "release", "create", tag, "--title", "Generated E-Books Vault", "--notes", "Automated 24/7 Akhand Granth Storage"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    subprocess.run(
        ["gh", "release", "upload", tag, pdf_filepath, "--clobber"],
        check=True
    )
    filename = os.path.basename(pdf_filepath)
    return f"https://github.com/{GH_REPO}/releases/download/{tag}/{urllib.parse.quote(filename)}"

# ==========================================
# 1. CORE FILES & KINDLE CANVAS SETUP
# ==========================================
KEY_FILE = "api_key.txt"
TOPIC_FILE = "topic.txt"
TEMP_DIR = f"temp_ebook_pages_{JOB_ID}" if JOB_ID else "temp_ebook_pages"

PAGE_WIDTH = 1240
PAGE_HEIGHT = 1754

THEMES = [
    {
        "id": "obsidian",
        "name": "Kindle Obsidian Pro",
        "paper_bg": "#FAF8F5", "cov_bg1": "#090D16", "cov_bg2": "#1E293B",
        "accent": "#06B6D4", "gold": "#F59E0B", "header_bg": "#0F172A",
        "sub_color": "#0E7490", "box_bg": "#F0F9FF", "box_border": "#7DD3FC"
    },
    {
        "id": "sapphire",
        "name": "O'Reilly Sapphire",
        "paper_bg": "#FCFBF9", "cov_bg1": "#070B19", "cov_bg2": "#1E1B4B",
        "accent": "#38BDF8", "gold": "#F59E0B", "header_bg": "#0F172A",
        "sub_color": "#0369A1", "box_bg": "#EFF6FF", "box_border": "#93C5FD"
    },
    {
        "id": "emerald",
        "name": "Emerald Press",
        "paper_bg": "#F9FBF9", "cov_bg1": "#04130E", "cov_bg2": "#064E3B",
        "accent": "#10B981", "gold": "#FBBF24", "header_bg": "#064E3B",
        "sub_color": "#047857", "box_bg": "#ECFDF5", "box_border": "#6EE7B7"
    },
    {
        "id": "crimson",
        "name": "Crimson Executive",
        "paper_bg": "#FDFBFB", "cov_bg1": "#18080A", "cov_bg2": "#4C0519",
        "accent": "#F43F5E", "gold": "#FDE047", "header_bg": "#3B0714",
        "sub_color": "#BE123C", "box_bg": "#FFF1F2", "box_border": "#FDA4AF"
    },
    {
        "id": "violet",
        "name": "Violet Quantum",
        "paper_bg": "#FBF9FD", "cov_bg1": "#0F0720", "cov_bg2": "#2E1065",
        "accent": "#A855F7", "gold": "#38BDF8", "header_bg": "#1E1035",
        "sub_color": "#6D28D9", "box_bg": "#F5F3FF", "box_border": "#C4B5FD"
    }
]

def pick_theme_for_topic(topic_text):
    choice = CLOUD_OVERRIDES.get("theme", "auto").lower()
    if choice == "random":
        return random.choice(THEMES)
    for t in THEMES:
        if t["id"] == choice or choice in t["name"].lower():
            return t
    idx = int(hashlib.md5(topic_text.encode("utf-8")).hexdigest(), 16) % len(THEMES)
    return THEMES[idx]

# ==========================================
# 2. AUTO KEY, DEEP SYLLABUS EXPANDER & HINGLISH DETECTOR
# ==========================================
def get_saved_api_keys():
    # 1. Check Cloud Environment Variable first (GitHub Secrets)
    if CLOUD_API_KEY_ENV:
        keys = [k.strip() for k in re.split(r"[\n,]+", CLOUD_API_KEY_ENV) if k.strip()]
        if keys:
            return keys

    # 2. Check local api_key.txt
    if os.path.exists(KEY_FILE):
        raw = open(KEY_FILE, "r", encoding="utf-8").read().strip()
        keys = [k.strip() for k in re.split(r"[\n,]+", raw) if k.strip()]
        if keys:
            return keys

    k = input("Apni Google API Key paste karo (1 baar, 'api_key.txt' mein save ho jayegi): ").strip()
    with open(KEY_FILE, "w", encoding="utf-8") as f:
        f.write(k)
    return [k]

def get_topic_input():
    """
    Agar Netlify/GitHub Cloud se run hua hai toh PAYLOAD_JSON se saara data uthata hai,
    warna Pydroid/Terminal par topic.txt ya console input se uthata hai!
    """
    if RAW_PAYLOAD_JSON:
        try:
            cfg = json.loads(RAW_PAYLOAD_JSON)
            raw_topic = cfg.get("topic", "").strip()
            CLOUD_OVERRIDES["title"] = cfg.get("title", "").strip()
            CLOUD_OVERRIDES["author"] = cfg.get("author", "").strip()
            CLOUD_OVERRIDES["theme"] = cfg.get("theme", "auto").strip()
            CLOUD_OVERRIDES["cover_prompt"] = cfg.get("cover_prompt", "").strip()
            CLOUD_OVERRIDES["language"] = cfg.get("language", "").strip().lower()
            CLOUD_OVERRIDES["volume"] = cfg.get("volume", "").strip().lower()

            header_injections = []
            if CLOUD_OVERRIDES["title"] and "title:" not in raw_topic.lower():
                header_injections.append(f"Title: {CLOUD_OVERRIDES['title']}")
            if CLOUD_OVERRIDES["author"] and "author:" not in raw_topic.lower():
                header_injections.append(f"Author: {CLOUD_OVERRIDES['author']}")
            if CLOUD_OVERRIDES["volume"] == "mega" and "pages" not in raw_topic.lower():
                header_injections.append("Volume Estimate: 1000-2000 pages")
            elif CLOUD_OVERRIDES["volume"] == "quick" and "page" not in raw_topic.lower():
                header_injections.append("Length: 1 page")
            if CLOUD_OVERRIDES["language"] == "hinglish" and "hinglish" not in raw_topic.lower():
                header_injections.append(
                    "Style: Write in energetic Hinglish (Bhai, Dosto, Mast, Samjho) with real-world analogy and line-by-line code breakdown."
                )

            final_cloud_topic = ("\n".join(header_injections) + "\n\n" + raw_topic).strip()
            print("[*] Cloud Payload Successfully Loaded from Netlify Website!")
            return final_cloud_topic
        except Exception as e:
            print(f"[!] Warning parsing PAYLOAD_JSON: {e}")

    if not os.path.exists(TOPIC_FILE):
        open(TOPIC_FILE, "w", encoding="utf-8").close()

    print("Topic / Full Syllabus paste karo (Ya 'topic.txt' se uthane ke liye seedha Enter dabao):")
    first_line = input().strip()

    if first_line.endswith(".txt") and os.path.exists(first_line):
        c = open(first_line, "r", encoding="utf-8").read().strip()
        if c:
            return c

    if not first_line:
        c = open(TOPIC_FILE, "r", encoding="utf-8").read().strip()
        if c:
            print(f"[*] '{TOPIC_FILE}' se full syllabus utha liya gaya hai!")
            return c
        raise ValueError(f"Bhai '{TOPIC_FILE}' khali hai aur tumne kuch type bhi nahi kiya!")

    import select
    extra_lines = [first_line]
    try:
        while select.select([sys.stdin], [], [], 0.15)[0]:
            line = sys.stdin.readline()
            if not line:
                break
            extra_lines.append(line.rstrip("\n"))
    except Exception:
        pass

    full_input = "\n".join(extra_lines).strip()
    if len(extra_lines) > 3:
        with open(TOPIC_FILE, "w", encoding="utf-8") as f:
            f.write(full_input)
    return full_input

def extract_custom_style_instructions(text):
    """Detects if user requested Hinglish, friendly 'Bhai/Dosto' tone, analogies, line-by-line code breakdown, etc."""
    low = text.lower()
    is_hinglish = (
        CLOUD_OVERRIDES.get("language") == "hinglish"
        or any(w in low for w in ["hinglish", "hindi", "bhai", "dosto", "samjhao", "kya hote hain", "karna"])
    )
    if CLOUD_OVERRIDES.get("language") == "english":
        is_hinglish = False

    custom_rules = []
    if is_hinglish:
        custom_rules.append(
            "LANGUAGE & TONE: Write in super energetic, friendly, peer-to-peer HINGLISH (Hindi + English mixed in Latin script). "
            "Use natural words like 'Bhai', 'Dosto', 'Mast', 'Chill karo', 'Ekdum', 'Samjho'. Explain like a cool senior game-dev brother teaching his younger brother!"
        )
    if "analogy" in low or "real-world" in low or is_hinglish:
        custom_rules.append(
            "REAL-WORLD ANALOGY: In every section, include vivid real-life and gaming analogies (like PUBG/GTA/Minecraft/Doraemon examples) so complex concepts feel super easy."
        )
    if "breakdown" in low or "line-by-line" in low or is_hinglish:
        custom_rules.append(
            "LINE-BY-LINE CODE BREAKDOWN: After every code snippet, explain the code line-by-line in detail so even a beginner understands every single keyword and bracket."
        )
    return "\n".join(custom_rules), is_hinglish

def parse_user_syllabus_and_constraints(text):
    low = text.lower()
    req_pages = None
    req_chapters = None

    # Supports '1000-2000 pages', '3,500+ Pages', '500 pages', '1 page'
    m_range = re.search(r"\b(\d[\d,]*)\s*[\-\–\—to]+\s*(\d[\d,]*)\+?\s*(?:page|pages|pg|panna|panne)\b", low)
    if m_range:
        req_pages = max(int(m_range.group(1).replace(",", "")), int(m_range.group(2).replace(",", "")))
    else:
        m_pg = re.search(r"\b(\d[\d,]*)\+?\s*(?:page|pages|pg|panna|panne)\b", low)
        if m_pg:
            req_pages = max(1, int(m_pg.group(1).replace(",", "")))
        elif any(x in low for x in ["one page", "ek page", "single page"]):
            req_pages = 1

    m_ch = re.search(r"\b(\d[\d,]*)\+?\s*(?:chapter|chapters|adhyay|unit|units)\b", low)
    if m_ch:
        req_chapters = max(1, int(m_ch.group(1).replace(",", "")))

    # Extract Title: supports `Title: ...` OR `titled "..."` OR `titled '...'`
    extracted_title = CLOUD_OVERRIDES.get("title") or None
    t_m1 = re.search(r"(?:^|\n)\s*(?:Book\s+)?Title\s*:\s*([^\n]+)", text, flags=re.IGNORECASE)
    t_m2 = re.search(r"titled\s+[\"\'\u201c\u201d]([^\"\'\u201c\u201d]{5,120})[\"\'\u201c\u201d]", text, flags=re.IGNORECASE)
    if not extracted_title:
        if t_m1:
            extracted_title = t_m1.group(1).strip().strip('"\'')
        elif t_m2:
            extracted_title = t_m2.group(1).strip()

    extracted_author = CLOUD_OVERRIDES.get("author") or None
    if not extracted_author:
        author_m = re.search(r"(?:^|\n)\s*Author\s*:\s*([^\n]+)", text, flags=re.IGNORECASE)
        extracted_author = author_m.group(1).strip() if author_m else None

    # Extract explicit Chapter list
    raw_chapters = []
    ch_pattern = re.compile(
        r"(?:^|\n)\s*(?:[•\-\*\u2022]|\d+\.)?\s*Chapter\s+(\d+)\s*[:\-–—]\s*([^\n]+)",
        flags=re.IGNORECASE
    )
    seen_nums = set()
    for match in ch_pattern.finditer(text):
        c_num = int(match.group(1))
        if c_num in seen_nums:
            continue
        seen_nums.add(c_num)
        raw_line = match.group(2).strip()

        # Check if subtopics are in parentheses: e.g. "Unity Engine & C# Setup (Installation, Interface walkthrough, Your first script, Hello World)"
        paren_m = re.search(r"^([^\(\n]+)\(([^\)]+)\)", raw_line)
        if paren_m:
            c_title = paren_m.group(1).strip(" :-–—")
            c_subs = paren_m.group(2).strip()
        else:
            parts = re.split(r"\s+(?:–|—|-)\s+", raw_line, maxsplit=1)
            if len(parts) == 2:
                c_title, c_subs = parts[0].strip(), parts[1].strip()
            else:
                c_title, c_subs = raw_line, raw_line

        raw_chapters.append({
            "chapter_number": c_num,
            "title": c_title,
            "summary_to_cover": c_subs
        })

    raw_chapters.sort(key=lambda x: x["chapter_number"])

    # =========================================================================
    # MEGA-BOOK DEEP EXPANSION ENGINE (14 Chapters -> 70 to 90+ Deep Units!)
    # If user wants a huge book (e.g. 500 - 2000 pages) and gave 10-25 chapters,
    # we split each chapter into multiple dedicated Deep Lectures (Sub-Units)
    # so EACH sub-unit gets its own full 8,192-token API call (~8-12 pages)!
    # =========================================================================
    if raw_chapters and req_pages and req_pages >= 250 and len(raw_chapters) <= 35:
        expanded_units = []
        unit_counter = 1
        for ch in raw_chapters:
            c_num = ch["chapter_number"]
            c_title = ch["title"]
            subs_raw = ch["summary_to_cover"]
            sub_items = [s.strip() for s in re.split(r",|\band\b|\baur\b", subs_raw) if len(s.strip()) > 2]
            if len(sub_items) < 3:
                sub_items = [
                    f"Core Foundations & Intuition of {c_title}",
                    f"Deep Architecture & Internal Mechanics of {c_title}",
                    f"Step-by-Step Practical Implementation of {c_title}",
                    f"Advanced Patterns, Optimization & Pro Workflows in {c_title}"
                ]
            else:
                sub_items.append(f"Complete Mini-Project, Code Lab & Common Mistakes in {c_title}")
                sub_items.append(f"Advanced Edge-Cases, Pro Secrets & Architecture Drills for {c_title}")

            for s_i, sub_topic in enumerate(sub_items, start=1):
                expanded_units.append({
                    "chapter_number": unit_counter,
                    "parent_chapter": c_num,
                    "title": f"Ch {c_num}.{s_i}: {c_title} -- {sub_topic}",
                    "summary_to_cover": (
                        f"Main Chapter {c_num}: {c_title} | Deep Focus Module {c_num}.{s_i}: '{sub_topic}'. "
                        f"Cover conceptual explanation, real-world game dev analogy, complete C# code with comments, "
                        f"line-by-line code breakdown, pro-tips, common mistakes, and hands-on exercises."
                    )
                })
                unit_counter += 1
        print(f"\n[🔥 MEGA-EXPANSION ACTIVE] User requested {req_pages} pages -> Expanded {len(raw_chapters)} Chapters into {len(expanded_units)} Deep Full-Length Lectures!")
        raw_chapters = expanded_units

    return req_pages, req_chapters, extracted_title, extracted_author, raw_chapters

def lock_true_book_mode(topic_text, ai_mode=""):
    low = topic_text.lower()
    story_words = [
        "kahani", "story", "katha", "novel", "fiction", "nobita", "novita",
        "doraemon", "shinchan", "horror", "ghost", "bhoot", "mystery",
        "adventure", "thriller", "fairy tale", "fable", "manga"
    ]
    code_words = [
        "python", "c++", "c#", "javascript", "unity", "godot", "coding", "programming",
        "fastapi", "selenium", "playwright", "web scraping", "game dev", "leetcode", "dsa",
        "visual studio", ".net", "oop", "class", "struct", "interface", "polymorphism",
        "linq", "async", "garbage collector", "gameobject", "prefab", "rigidbody",
        "collider", "raycast", "scriptableobject", "shader", "urp", "hdrp", "vfx",
        "navmesh", "profiler", "git", "ci/cd", "singleton", "state machine", "coroutines"
    ]
    exam_words = [
        "mathematics", "physics", "chemistry", "biology", "mext", "jee", "neet",
        "exam", "formula", "calculus", "algebra", "trigonometry", "geometry",
        "theorem", "derivation", "mock test", "coaching"
    ]

    story_score = sum(low.count(w) for w in story_words)
    code_score = sum(low.count(w) for w in code_words)
    exam_score = sum(low.count(w) for w in exam_words)

    if code_score >= 2 and code_score >= exam_score:
        return "CODING_TECH"
    if story_score >= 1 and story_score > code_score and story_score > exam_score:
        return "STORY_FICTION"
    if exam_score >= 1 and exam_score > code_score:
        return "ACADEMIC_EXAM"

    if ai_mode in ("STORY_FICTION", "CODING_TECH", "ACADEMIC_EXAM", "GENERAL_GUIDE"):
        return ai_mode
    return "GENERAL_GUIDE"

# ==========================================
# 3. 100% ASCII-SAFE MATH & CODE SANITIZER
# ==========================================
UNICODE_TO_ASCII_MATH = {
    "⁰": "^0", "¹": "^1", "²": "^2", "³": "^3", "⁴": "^4",
    "⁵": "^5", "⁶": "^6", "⁷": "^7", "⁸": "^8", "⁹": "^9",
    "⁺": "^+", "⁻": "^-", "ⁿ": "^n", "ⁱ": "^i",
    "₀": "_0", "₁": "_1", "₂": "_2", "₃": "_3", "₄": "_4",
    "₅": "_5", "₆": "_6", "₇": "_7", "₈": "_8", "₉": "_9",
    "ₐ": "_a", "ₑ": "_e", "ᵢ": "_i", "ⱼ": "_j", "ₖ": "_k",
    "ₙ": "_n", "ₘ": "_m", "ₒ": "_o", "ₚ": "_p", "ᵣ": "_r",
    "ₛ": "_s", "ₜ": "_t", "ᵤ": "_u", "ᵥ": "_v", "ₓ": "_x",
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta",
    "ε": "epsilon", "ϵ": "epsilon", "ζ": "zeta", "η": "eta",
    "θ": "theta", "ϑ": "theta", "ι": "iota", "κ": "kappa",
    "λ": "lambda", "μ": "mu", "ν": "nu", "ξ": "xi",
    "π": "pi", "ρ": "rho", "σ": "sigma", "ς": "sigma",
    "τ": "tau", "υ": "upsilon", "φ": "phi", "ϕ": "phi",
    "χ": "chi", "ψ": "psi", "ω": "omega",
    "Γ": "Gamma", "Δ": "Delta", "Θ": "Theta", "Λ": "Lambda",
    "Ξ": "Xi", "Π": "Pi", "Σ": "Sigma", "Φ": "Phi", "Ψ": "Psi", "Ω": "Omega",
    "≠": " != ", "≤": " <= ", "≥": " >= ", "±": "+/-", "∓": "-/+",
    "√": "sqrt", "∛": "cbrt", "∞": "infinity",
    "×": " * ", "÷": " / ", "·": "*", "⋅": "*", "∗": "*",
    "−": "-", "–": "-", "—": " - ", "―": "-",
    "→": " -> ", "⟶": " -> ", "⇒": " => ", "⇔": " <=> ", "↔": " <-> ",
    "≈": " ~= ", "≡": " == ", "∝": " prop_to ",
    "∈": " in ", "∉": " not_in ", "⊂": " subset ", "⊆": " subset_eq ",
    "∪": " U ", "∩": " intersect ", "∅": " empty_set ",
    "∀": "for all ", "∃": "there exists ", "∴": "therefore ", "∵": "because ",
    "∫": "integral ", "∑": "sum ", "∏": "product ", "∂": "d", "∇": "grad ",
    "°": " deg", "′": "'", "″": "''",
    "“": '"', "”": '"', "‘": "'", "’": "'", "…": "..."
}

def sanitize_to_ascii_math(text, preserve_indent=False):
    if not text:
        return ""
    s = str(text)
    s = re.sub(r"[■▪▫▬▭▮▯◆◇◈●○◎★☆►▶▸▹•‣⁃]", "", s)
    for uni_char, ascii_rep in UNICODE_TO_ASCII_MATH.items():
        if uni_char in s:
            s = s.replace(uni_char, ascii_rep)
    if not preserve_indent:
        s = re.sub(r"(?<=[0-9a-zA-Z\)])\*(?=[a-zA-Z\(])", "", s)
        s = s.replace("**", "").replace("##", "")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    if preserve_indent:
        return s.rstrip()
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()

def strip_leading_number(heading_text):
    cleaned = sanitize_to_ascii_math(heading_text)
    cleaned = re.sub(r"^[\*\#\_\-\s]+|[\*\#\_\-\s]+$", "", cleaned)
    return re.sub(r"^(?:section|topic|part|chapter|scene|act)?\s*\d+[\.\:\)\-]\s*", "", cleaned, flags=re.IGNORECASE).strip()

# ==========================================
# 4. SMART FONT & KINDLE TYPOGRAPHY ENGINE (ANDROID + LINUX CLOUD)
# ==========================================
class SmartFont:
    def __init__(self, pil_font, size, stroke_width=0):
        self.font = pil_font
        self.size = size
        self.stroke = stroke_width

def load_font(is_bold=False, size=30, is_mono=False):
    mono_paths = [
        "/system/fonts/DroidSansMono.ttf", "/system/fonts/CutiveMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        "consola.ttf"
    ]
    bold_paths = [
        "/system/fonts/Roboto-Bold.ttf", "/system/fonts/RobotoStatic-Bold.ttf",
        "/system/fonts/NotoSans-Bold.ttf", "/system/fonts/DroidSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "arialbd.ttf"
    ]
    reg_paths = [
        "/system/fonts/Roboto-Regular.ttf", "/system/fonts/NotoSans-Regular.ttf",
        "/system/fonts/DroidSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "arial.ttf"
    ]

    if is_mono:
        for p in mono_paths + reg_paths:
            if os.path.exists(p):
                try:
                    return SmartFont(ImageFont.truetype(p, size), size, stroke_width=0)
                except Exception:
                    pass

    if is_bold:
        for p in bold_paths:
            if os.path.exists(p):
                try:
                    return SmartFont(ImageFont.truetype(p, size), size, stroke_width=0)
                except Exception:
                    pass
        for p in reg_paths:
            if os.path.exists(p):
                try:
                    return SmartFont(ImageFont.truetype(p, size), size, stroke_width=1)
                except Exception:
                    pass

    for p in reg_paths:
        if os.path.exists(p):
            try:
                return SmartFont(ImageFont.truetype(p, size), size, stroke_width=0)
            except Exception:
                pass

    return SmartFont(ImageFont.load_default(size=size), size, stroke_width=0)

def text_size(draw, text, sfont: SmartFont):
    clean = re.sub(r"[\^_]([0-9a-zA-Z\+\-]+)", r"\1", sanitize_to_ascii_math(text))
    bbox = draw.textbbox((0, 0), clean, font=sfont.font, stroke_width=sfont.stroke)
    return (bbox[2] - bbox[0]), (bbox[3] - bbox[1]), bbox[1]

def draw_plain(draw, xy, text, sfont: SmartFont, fill, preserve_indent=False):
    clean = sanitize_to_ascii_math(text, preserve_indent=preserve_indent)
    draw.text(xy, clean, font=sfont.font, fill=fill, stroke_width=sfont.stroke, stroke_fill=fill)

def draw_centered(draw, center_xy, text, sfont: SmartFont, fill):
    clean = sanitize_to_ascii_math(text)
    w, h, top_offset = text_size(draw, clean, sfont)
    x = center_xy[0] - (w // 2)
    y = center_xy[1] - (h // 2) - (top_offset // 2)
    draw_plain(draw, (x, y), clean, sfont, fill)

def draw_right_aligned(draw, right_x, y, text, sfont: SmartFont, fill):
    clean = sanitize_to_ascii_math(text)
    w, _, _ = text_size(draw, clean, sfont)
    draw_plain(draw, (right_x - w, y), clean, sfont, fill)

def draw_math_line(draw, xy, text, sfont: SmartFont, fill):
    x, y = xy
    clean = sanitize_to_ascii_math(text)
    sup_size = max(14, int(sfont.size * 0.65))
    sup_sfont = load_font(is_bold=(sfont.stroke > 0), size=sup_size)

    tokens = re.split(r"(\^[0-9a-zA-Z\+\-]+|_[0-9a-zA-Z]+)", clean)
    cur_x = x
    for tok in tokens:
        if not tok:
            continue
        if tok.startswith("^") and len(tok) > 1:
            val = tok[1:]
            draw.text((cur_x + 1, y - int(sfont.size * 0.22)), val, font=sup_sfont.font, fill=fill, stroke_width=sup_sfont.stroke, stroke_fill=fill)
            bbox = draw.textbbox((0, 0), val, font=sup_sfont.font, stroke_width=sup_sfont.stroke)
            cur_x += (bbox[2] - bbox[0]) + 2
        elif tok.startswith("_") and len(tok) > 1:
            val = tok[1:]
            draw.text((cur_x + 1, y + int(sfont.size * 0.35)), val, font=sup_sfont.font, fill=fill, stroke_width=sup_sfont.stroke, stroke_fill=fill)
            bbox = draw.textbbox((0, 0), val, font=sup_sfont.font, stroke_width=sup_sfont.stroke)
            cur_x += (bbox[2] - bbox[0]) + 2
        else:
            draw.text((cur_x, y), tok, font=sfont.font, fill=fill, stroke_width=sfont.stroke, stroke_fill=fill)
            bbox = draw.textbbox((0, 0), tok, font=sfont.font, stroke_width=sfont.stroke)
            cur_x += (bbox[2] - bbox[0])

def draw_syntax_highlighted_code_line(draw, xy, code_line, sfont: SmartFont):
    x, y = xy
    clean = sanitize_to_ascii_math(code_line, preserve_indent=True)
    stripped = clean.lstrip(" ")

    if stripped.startswith(("//", "#")):
        draw.text((x, y), clean, font=sfont.font, fill="#7EE787")
        return

    keywords = {
        "public", "private", "protected", "internal", "class", "struct", "interface",
        "enum", "void", "int", "float", "double", "bool", "string", "var", "new",
        "return", "if", "else", "switch", "case", "for", "foreach", "while", "do",
        "break", "continue", "async", "await", "using", "namespace", "static",
        "readonly", "const", "virtual", "override", "abstract", "sealed", "this",
        "base", "true", "false", "null", "def", "import", "from", "try", "catch", "where"
    }
    engine_types = {
        "MonoBehaviour", "ScriptableObject", "GameObject", "Transform", "Vector2",
        "Vector3", "Quaternion", "Rigidbody", "Rigidbody2D", "Collider", "Physics",
        "Time", "Input", "Debug", "SerializeField", "Header", "List", "Dictionary",
        "Action", "Func", "Task", "IEnumerator", "Coroutine", "Mathf", "Canvas", "NavMeshAgent"
    }

    tokens = re.split(r'(\".*?\"|\b[A-Za-z_][A-Za-z0-9_]*\b|\s+|.)', clean)
    cur_x = x
    for tok in tokens:
        if not tok:
            continue
        if tok.startswith('"') and tok.endswith('"'):
            color = "#FDE047"
        elif tok in keywords:
            color = "#FF7B72"
        elif tok in engine_types:
            color = "#79C0FF"
        elif tok.isdigit():
            color = "#D2A8FF"
        else:
            color = "#E6EDF3"

        draw.text((cur_x, y), tok, font=sfont.font, fill=color)
        bbox = draw.textbbox((0, 0), tok, font=sfont.font)
        cur_x += (bbox[2] - bbox[0])

def fit_text_to_pixel_width(draw, text, sfont: SmartFont, max_px):
    text = sanitize_to_ascii_math(text)
    w, _, _ = text_size(draw, text, sfont)
    if w <= max_px:
        return text
    words = text.split()
    out = ""
    for word in words:
        test = f"{out} {word}".strip()
        tw, _, _ = text_size(draw, test + "...", sfont)
        if tw <= max_px:
            out = test
        else:
            break
    return (out + "...") if out else text[:35] + "..."

def wrap_text(draw, text, sfont: SmartFont, max_width, preserve_indent=False):
    text = sanitize_to_ascii_math(text, preserve_indent=preserve_indent)
    lines = []
    for paragraph in text.split("\n"):
        if preserve_indent:
            leading_spaces = len(paragraph) - len(paragraph.lstrip(" "))
            indent_str = " " * min(16, leading_spaces)
            words = paragraph.strip().split()
        else:
            indent_str = ""
            words = paragraph.split()

        if not words:
            lines.append("")
            continue
        curr = indent_str
        for w in words:
            test = f"{curr} {w}".strip() if curr.strip() != "" else f"{indent_str}{w}"
            tw, _, _ = text_size(draw, test, sfont)
            if tw <= max_width:
                curr = test
            else:
                if curr.strip():
                    lines.append(curr)
                curr = f"{indent_str}{w}"
        if curr.strip():
            lines.append(curr)
    return lines

# ==========================================
# 5. KINDLE PAGE & COVER DESIGN ENGINE
# ==========================================
def fetch_ai_illustration(prompt_text, width=960, height=520, seed=42):
    encoded = urllib.parse.quote(prompt_text)
    url = f"https://image.pollinations.ai/prompt/{encoded}?width={width}&height={height}&nologo=true&seed={seed}"
    try:
        r = requests.get(url, timeout=9)
        if r.status_code == 200 and len(r.content) > 4000:
            return Image.open(BytesIO(r.content)).convert("RGB")
    except Exception:
        pass
    return None

def draw_procedural_topic_art(draw, topic, theme, box, book_mode):
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    draw.rectangle(box, fill=theme["cov_bg2"])

    if book_mode in ("ACADEMIC_EXAM", "CODING_TECH"):
        for gx in range(x1, x2, 55):
            draw.line([(gx, y1), (gx, y2)], fill="#1E293B", width=1)
        for gy in range(y1, y2, 55):
            draw.line([(x1, gy), (x2, gy)], fill="#1E293B", width=1)
        pts1, pts2 = [], []
        for px in range(0, w, 8):
            py1 = int((y1 + h / 2) + math.sin(px / 50.0) * (h * 0.34))
            py2 = int((y1 + h / 2) + math.cos(px / 40.0) * (h * 0.26))
            pts1.append((x1 + px, py1))
            pts2.append((x1 + px, py2))
        if len(pts1) > 1:
            draw.line(pts1, fill=theme["accent"], width=5)
            draw.line(pts2, fill=theme["gold"], width=4)
    elif book_mode == "STORY_FICTION":
        cx, cy = x1 + int(w * 0.72), y1 + int(h * 0.36)
        draw.ellipse([cx - 90, cy - 90, cx + 90, cy + 90], fill=theme["gold"])
        draw.ellipse([cx - 70, cy - 90, cx + 100, cy + 70], fill=theme["cov_bg2"])
        for i in range(1, 22):
            sx = x1 + ((i * 173) % (w - 40)) + 20
            sy = y1 + ((i * 97) % int(h * 0.65)) + 20
            draw.ellipse([sx - 3, sy - 3, sx + 3, sy + 3], fill="#FFFFFF")
        draw.polygon([(x1, y2), (x1 + 260, y2 - 145), (x1 + 580, y2 - 60), (x2, y2 - 175), (x2, y2)], fill=theme["cov_bg1"])
    else:
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        for r in range(40, 420, 36):
            draw.ellipse([cx - r, cy - r // 2, cx + r, cy + r // 2], outline=theme["accent"], width=2)
        draw.line([(x1, y2), (x2, y1)], fill=theme["gold"], width=3)

def build_custom_cover(title, subtitle, author, category, topic, theme, total_chapters, book_mode):
    cov = Image.new("RGB", (PAGE_WIDTH, PAGE_HEIGHT), theme["cov_bg1"])
    cd = ImageDraw.Draw(cov)

    cd.polygon([(0, 0), (PAGE_WIDTH, 0), (PAGE_WIDTH, 520), (0, 320)], fill=theme["cov_bg2"])
    cd.polygon([(0, 1380), (PAGE_WIDTH, 1180), (PAGE_WIDTH, PAGE_HEIGHT), (0, PAGE_HEIGHT)], fill=theme["cov_bg2"])

    cd.rectangle([45, 45, PAGE_WIDTH - 45, PAGE_HEIGHT - 45], outline=theme["accent"], width=3)
    cd.rectangle([62, 62, PAGE_WIDTH - 62, PAGE_HEIGHT - 62], outline="#334155", width=2)

    f_badge = load_font(True, 24)
    f_title = load_font(True, 56)
    f_sub = load_font(False, 30)
    f_author = load_font(True, 28)

    cd.rounded_rectangle([110, 115, 560, 170], radius=12, fill=theme["accent"])
    draw_centered(cd, (335, 142), category[:24].upper(), f_badge, "#090D16")

    art_box = (110, 205, PAGE_WIDTH - 110, 695)
    # Uses custom cover prompt from Netlify UI if user typed one, else auto-generates from title/mode!
    user_cov_prompt = CLOUD_OVERRIDES.get("cover_prompt", "").strip()
    if user_cov_prompt:
        final_art_prompt = f"{user_cov_prompt}, high contrast digital book cover art, no text"
    else:
        style = "cinematic anime story illustration, vibrant" if book_mode == "STORY_FICTION" else "minimalist modern game engine cyber vector art, high contrast"
        final_art_prompt = f"{style} for {title[:60]}, no text"

    ai_img = fetch_ai_illustration(final_art_prompt, 1020, 490, seed=len(topic))
    if ai_img:
        ai_img = ai_img.resize((art_box[2] - art_box[0], art_box[3] - art_box[1]))
        cov.paste(ai_img, (art_box[0], art_box[1]))
    else:
        draw_procedural_topic_art(cd, topic, theme, art_box, book_mode)
    cd.rectangle(art_box, outline=theme["accent"], width=4)

    cd.rounded_rectangle([110, 740, PAGE_WIDTH - 110, 1450], radius=22, fill="#0F172A", outline=theme["gold"], width=3)
    cd.rectangle([150, 785, 380, 797], fill=theme["accent"])

    y = 825
    for line in wrap_text(cd, title.upper(), f_title, 920)[:4]:
        draw_plain(cd, (150, y), line, f_title, "#FFFFFF")
        y += 68

    y += 20
    cd.line([(150, y), (PAGE_WIDTH - 150, y)], fill="#334155", width=2)
    y += 24

    for line in wrap_text(cd, subtitle, f_sub, 920)[:3]:
        draw_plain(cd, (150, y), line, f_sub, "#CBD5E1")
        y += 44

    if author:
        cd.rounded_rectangle([150, 1340, PAGE_WIDTH - 150, 1410], radius=12, fill=theme["cov_bg2"], outline=theme["gold"], width=2)
        draw_centered(cd, (620, 1375), f"AUTHOR: {author.upper()}", f_author, theme["gold"])

    footer_label = "KINDLE ILLUSTRATED STORYBOOK" if book_mode == "STORY_FICTION" else f"{total_chapters} DEEP MODULES  |  AKHAND GRANTH EDITION"
    cd.rounded_rectangle([200, 1520, PAGE_WIDTH - 200, 1590], radius=35, fill=theme["cov_bg2"], outline=theme["accent"], width=2)
    draw_centered(cd, (620, 1555), footer_label, f_badge, "#F8FAFC")

    return cov

def build_back_cover(title, subtitle, author, category, theme, total_chapters, total_pages):
    back = Image.new("RGB", (PAGE_WIDTH, PAGE_HEIGHT), theme["cov_bg1"])
    bd = ImageDraw.Draw(back)

    bd.rectangle([55, 55, PAGE_WIDTH - 55, PAGE_HEIGHT - 55], outline=theme["accent"], width=3)
    bd.rounded_rectangle([140, 460, PAGE_WIDTH - 140, 1220], radius=24, fill="#0F172A", outline=theme["gold"], width=3)

    f_title = load_font(True, 44)
    f_sub = load_font(False, 29)
    f_badge = load_font(True, 24)

    draw_centered(bd, (620, 540), category.upper(), f_badge, theme["accent"])
    y = 615
    for line in wrap_text(bd, title.upper(), f_title, 840)[:3]:
        draw_centered(bd, (620, y), line, f_title, "#FFFFFF")
        y += 58

    y += 25
    bd.line([(260, y), (980, y)], fill="#334155", width=2)
    y += 40

    for line in wrap_text(bd, subtitle, f_sub, 820)[:3]:
        draw_centered(bd, (620, y), line, f_sub, "#94A3B8")
        y += 44

    if author:
        draw_centered(bd, (620, 995), f"WRITTEN & ARCHITECTED BY {author.upper()}", f_badge, theme["gold"])

    stats = f"TOTAL MODULES: {total_chapters}   |   TOTAL PAGES: {total_pages}"
    bd.rounded_rectangle([240, 1065, PAGE_WIDTH - 240, 1135], radius=14, fill=theme["cov_bg2"], outline=theme["accent"], width=2)
    draw_centered(bd, (620, 1100), stats, f_badge, "#F8FAFC")

    draw_centered(bd, (620, 1580), "END OF VOLUME  --  AKHAND GRANTH ENGINE V6.0", f_badge, "#64748B")
    return back

def create_blank_content_page(title, running_section, page_num, theme, progress_ratio=0.0):
    page = Image.new("RGB", (PAGE_WIDTH, PAGE_HEIGHT), theme.get("paper_bg", "#FAF8F5"))
    pd = ImageDraw.Draw(page)

    pd.rectangle([0, 0, 14, PAGE_HEIGHT], fill="#E2E8F0")
    pd.rectangle([14, 0, 22, PAGE_HEIGHT], fill="#F1F5F9")

    pd.rectangle([0, 0, PAGE_WIDTH, 98], fill=theme["header_bg"])
    pd.rectangle([0, 98, PAGE_WIDTH, 105], fill=theme["accent"])

    pd.rectangle([48, 132, PAGE_WIDTH - 48, 1612], outline="#D1D5DB", width=2)
    pd.rectangle([48, 132, 56, 1612], fill=theme["accent"])

    if progress_ratio > 0:
        bar_top = 132
        bar_bottom = 1612
        prog_y = int(bar_top + (bar_bottom - bar_top) * min(1.0, progress_ratio))
        pd.rectangle([PAGE_WIDTH - 52, bar_top, PAGE_WIDTH - 48, prog_y], fill=theme["gold"])

    f_header = load_font(True, 21)
    f_cat = load_font(True, 20)
    f_pnum = load_font(True, 23)

    safe_left_title = fit_text_to_pixel_width(pd, title.upper(), f_header, max_px=680)
    draw_plain(pd, (82, 38), safe_left_title, f_header, "#F8FAFC")

    safe_sec = fit_text_to_pixel_width(pd, sanitize_to_ascii_math(running_section).upper(), f_cat, max_px=410)
    draw_right_aligned(pd, PAGE_WIDTH - 72, 38, safe_sec, f_cat, theme["accent"])

    pd.line([(85, 1658), (505, 1658)], fill="#CBD5E1", width=2)
    pd.line([(735, 1658), (1155, 1658)], fill="#CBD5E1", width=2)
    pd.rounded_rectangle([530, 1628, 710, 1690], radius=31, fill=theme["header_bg"], outline=theme["accent"], width=3)
    draw_centered(pd, (620, 1659), f"PAGE {page_num:02d}", f_pnum, "#FFFFFF")

    return page, pd

# ==========================================
# 6. LIVE MODEL POOL & BLACKLIST
# ==========================================
_dead_models = set()
_cooldown_until = {}
_model_lock = threading.Lock()
_api_pace_lock = threading.Lock()
_last_api_call_ts = 0.0

def get_working_models(api_keys):
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_keys[0]}"
    available = []
    try:
        r = requests.get(url, timeout=8)
        if r.status_code == 200:
            for m in r.json().get("models", []):
                methods = m.get("supportedGenerationMethods", [])
                name = m.get("name", "").replace("models/", "")
                if "generateContent" in methods and "gemini" in name:
                    if any(bad in name for bad in [
                        "1.5", "2.0", "2.5", "vision", "embedding", "imagen", "image",
                        "tts", "audio", "robotics", "computer-use", "aqa", "thinking",
                        "banana", "transcribe", "customtools", "live"
                    ]):
                        continue
                    available.append(name)
    except Exception:
        pass

    def rank_model(n):
        if "flash-lite" in n:
            return 0
        if "3.8-flash" in n or "3.5-flash" in n or "3-flash" in n:
            return 1
        if "flash" in n:
            return 2
        return 3

    available.sort(key=rank_model)
    for fb in ["gemini-flash-lite-latest", "gemini-3.1-flash-lite-preview", "gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-3.8-flash"]:
        if fb not in available:
            available.insert(0, fb)

    return available

def call_ai_text(api_keys, models, prompt, max_tokens=8192, temp=0.5, seed_idx=0):
    global _last_api_call_ts
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temp,
            "maxOutputTokens": max_tokens
        }
    }

    total_combos = max(8, len(models) * len(api_keys))
    for attempt in range(total_combos):
        chosen_model = None
        now = time.time()
        with _model_lock:
            for offset in range(len(models)):
                m_cand = models[(seed_idx + attempt + offset) % len(models)]
                if m_cand in _dead_models:
                    continue
                if _cooldown_until.get(m_cand, 0) > now:
                    continue
                chosen_model = m_cand
                break

            if chosen_model is None:
                valid = [m for m in models if m not in _dead_models]
                if not valid:
                    _dead_models.clear()
                    valid = models
                chosen_model = min(valid, key=lambda m: _cooldown_until.get(m, 0))
                wait_cd = max(0.0, _cooldown_until.get(chosen_model, 0) - time.time())
                if wait_cd > 0:
                    time.sleep(min(wait_cd, 10.0))

        chosen_key = api_keys[(seed_idx + attempt) % len(api_keys)]

        with _api_pace_lock:
            now = time.time()
            gap = 1.35 - (now - _last_api_call_ts)
            if gap > 0:
                time.sleep(gap)
            _last_api_call_ts = time.time()

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{chosen_model}:generateContent?key={chosen_key}"
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=(8, 80))
            if resp.status_code == 200:
                cands = resp.json().get("candidates", [])
                if cands and "content" in cands[0]:
                    parts = cands[0]["content"].get("parts", [])
                    if parts and parts[0].get("text", "").strip():
                        return parts[0]["text"], chosen_model
            elif resp.status_code in (404, 400):
                with _model_lock:
                    _dead_models.add(chosen_model)
            elif resp.status_code == 429:
                err_txt = resp.text.lower()
                with _model_lock:
                    if "perday" in err_txt or "per_day" in err_txt or "daily" in err_txt:
                        _cooldown_until[chosen_model] = time.time() + 3600
                    else:
                        _cooldown_until[chosen_model] = time.time() + 20
            else:
                time.sleep(2.0)
        except Exception:
            time.sleep(2.0)

    return None, None

# ==========================================
# 7. CRASH-PROOF TAG PARSER & DEEP 1-ON-1 MODULE PROMPT
# ==========================================
def parse_tagged_chapter_block(raw_block, c_num, default_title, book_mode):
    heading_m = re.search(r"^\s*HEADING\s*:\s*(.+)$", raw_block, flags=re.MULTILINE | re.IGNORECASE)
    heading = heading_m.group(1).strip() if heading_m else default_title

    if book_mode == "STORY_FICTION":
        ill_m = re.search(r"^\s*ILLUSTRATION\s*:\s*(.+)$", raw_block, flags=re.MULTILINE | re.IGNORECASE)
        ill_prompt = ill_m.group(1).strip() if ill_m else f"anime scene of {heading}"
        scenes = []
        scene_splits = re.split(r"\[SCENE\s*:\s*([^\]]*)\]", raw_block, flags=re.IGNORECASE)
        if len(scene_splits) > 1:
            for i in range(1, len(scene_splits), 2):
                sc_title = scene_splits[i].strip()
                sc_body = re.split(r"\[/?(?:SCENE|END)", scene_splits[i + 1], flags=re.IGNORECASE)[0].strip()
                paras = [p.strip() for p in re.split(r"\n\s*\n", sc_body) if p.strip() and not p.strip().startswith("===")]
                if paras:
                    scenes.append({"scene_title": sc_title, "paragraphs": paras})
        if not scenes:
            clean_lines = [l.strip() for l in raw_block.split("\n") if l.strip() and not l.strip().startswith(("HEADING:", "ILLUSTRATION:", "==="))]
            scenes = [{"scene_title": "", "paragraphs": clean_lines[:12] or ["Story continues..."]}]
        return {
            "chapter_number": c_num,
            "heading": heading,
            "illustration_prompt": ill_prompt,
            "scenes": scenes
        }

    code_snippets = []
    for cm in re.finditer(r"\[CODE\s*(?::\s*([^\]]*))?\](.*?)\[/CODE\]", raw_block, flags=re.DOTALL | re.IGNORECASE):
        c_title = (cm.group(1) or "Production Script Implementation").strip()
        c_body = cm.group(2).strip()
        c_body = re.sub(r"^```[a-zA-Z0-9#\+\-]*\n?|```$", "", c_body, flags=re.MULTILINE).strip()
        if c_body:
            code_snippets.append({"title": c_title, "language": "Code", "code": c_body})

    summary_table = []
    for tm in re.finditer(r"\[TABLE\](.*?)\[/TABLE\]", raw_block, flags=re.DOTALL | re.IGNORECASE):
        for line in tm.group(1).strip().split("\n"):
            line = line.strip().lstrip("-*• ")
            if "|" in line:
                cols = [c.strip() for c in line.split("|") if c.strip()]
                if len(cols) >= 2 and not all(set(c) <= {"-", "="} for c in cols[:2]):
                    summary_table.append({"col1": cols[0], "col2": cols[1]})

    def extract_bullet_block(tag_name):
        items = []
        for bm in re.finditer(rf"\[{tag_name}\](.*?)\[/{tag_name}\]", raw_block, flags=re.DOTALL | re.IGNORECASE):
            for line in bm.group(1).strip().split("\n"):
                clean_l = re.sub(r"^[\-\*\•\d\.\)\s]+", "", line.strip()).strip()
                if len(clean_l) > 4:
                    items.append(clean_l)
        return items

    formulas = extract_bullet_block("FORMULAS")
    key_takeaways = extract_bullet_block("TAKEAWAYS")
    exam_traps = extract_bullet_block("TRAPS")
    practice_drills = extract_bullet_block("DRILLS")

    solved_examples = []
    for em in re.finditer(r"\[EXAMPLE\s*(?::\s*([^\]]*))?\](.*?)\[/EXAMPLE\]", raw_block, flags=re.DOTALL | re.IGNORECASE):
        q_head = (em.group(1) or "").strip()
        ex_lines = [l.strip() for l in em.group(2).strip().split("\n") if l.strip()]
        if not q_head and ex_lines:
            q_head = re.sub(r"^Question\s*:\s*", "", ex_lines[0], flags=re.IGNORECASE)
            ex_lines = ex_lines[1:]
        steps = [re.sub(r"^[\-\*\•]\s*", "", l) for l in ex_lines if len(l) > 2]
        if q_head and steps:
            solved_examples.append({"question": q_head, "solution_steps": steps})

    cleaned_for_sections = re.sub(
        r"\[(?:CODE|TABLE|FORMULAS|TAKEAWAYS|TRAPS|DRILLS|EXAMPLE)[^\]]*\].*?\[/(?:CODE|TABLE|FORMULAS|TAKEAWAYS|TRAPS|DRILLS|EXAMPLE)\]",
        "",
        raw_block,
        flags=re.DOTALL | re.IGNORECASE
    )

    theory_sections = []
    sec_splits = re.split(r"\[SECTION\s*:\s*([^\]]+)\]", cleaned_for_sections, flags=re.IGNORECASE)
    if len(sec_splits) > 1:
        for i in range(1, len(sec_splits), 2):
            s_title = sec_splits[i].strip()
            s_body = sec_splits[i + 1].strip()
            s_body = re.sub(r"===\s*CHAPTER.*", "", s_body, flags=re.DOTALL).strip()
            paras = [
                p.strip() for p in re.split(r"\n\s*\n", s_body)
                if len(p.strip()) > 20 and not p.strip().upper().startswith("HEADING:")
            ]
            if not paras and s_body:
                paras = [l.strip() for l in s_body.split("\n") if len(l.strip()) > 20]
            if paras:
                theory_sections.append({"sub_heading": s_title, "paragraphs": paras})

    if not theory_sections:
        raw_paras = [
            p.strip() for p in re.split(r"\n\s*\n", cleaned_for_sections)
            if len(p.strip()) > 30 and not p.strip().upper().startswith(("HEADING:", "==="))
        ]
        if raw_paras:
            theory_sections.append({"sub_heading": f"Deep Dive: {default_title}", "paragraphs": raw_paras[:10]})
        else:
            theory_sections.append({"sub_heading": default_title, "paragraphs": [f"Comprehensive architectural analysis and implementation guide for {default_title}."]})

    return {
        "chapter_number": c_num,
        "heading": heading,
        "theory_sections": theory_sections,
        "code_snippets": code_snippets,
        "summary_table": summary_table,
        "formulas": formulas,
        "key_takeaways": key_takeaways,
        "exam_traps": exam_traps,
        "solved_examples": solved_examples,
        "practice_drills": practice_drills
    }

def build_deep_single_unit_prompt(ch_info, book_title, custom_style_rules, book_mode):
    """
    DEDICATED 8,192-TOKEN SINGLE-UNIT PROMPT!
    Instead of squeezing 3 chapters into 1 call, this forces the AI to write a MASSIVE,
    exhaustive 3,500+ word deep-dive for ONE single module/chapter!
    """
    c_num = ch_info["chapter_number"]
    c_title = ch_info["title"]
    c_subs = ch_info.get("summary_to_cover", c_title)

    style_block = custom_style_rules if custom_style_rules else (
        "Write in an engaging, crystal-clear, authoritative masterclass style with deep explanations and practical examples."
    )

    if book_mode == "CODING_TECH":
        return f"""You are writing Module #{c_num}: "{c_title}" for the mega eBook "{book_title}".
Specific Focus of this Module: {c_subs}

{style_block}

CRITICAL LENGTH & DEPTH INSTRUCTION:
Write an EXHAUSTIVE, MASSIVE, DEEP-DIVE chapter (aim for 3,000+ words!). Do NOT summarize briefly!
Use the exact tags below (NO JSON, NO markdown ** stars):

===CHAPTER_START: {c_num}===
HEADING: {c_title}

[SECTION: Conceptual Explanation -- Why Do We Need This?]
<Write 4 long, detailed paragraphs explaining the concept from zero, why game devs need it, and how it works under the hood>

[SECTION: Real-World Game Dev Analogy]
<Write 3 detailed, fun paragraphs explaining this concept using real-life gaming analogies (like PUBG, GTA, Minecraft, Mario, or everyday life) so a beginner never forgets it>

[SECTION: Step-by-Step Workflow & Engine Architecture]
<Write 4 detailed paragraphs walking through the exact setup, Unity Inspector/Editor workflow, or C# memory/CPU execution step-by-step>

[CODE: Script 1 -- Basic to Intermediate Implementation]
<Write a complete, well-commented C# / Unity script (25-35 lines, keep each line under 60 chars, use Hinglish comments if Hinglish mode)>
[/CODE]

[SECTION: Line-by-Line Code Breakdown (Ek-Ek Line Samjho)]
<Write 4 long paragraphs breaking down EVERY single line, keyword, function, and bracket of Script 1 in super simple detail>

[CODE: Script 2 -- Production Gameplay System]
<Write a second, more advanced C# / Unity script showing real gameplay usage (25-35 lines, keep each line under 60 chars)>
[/CODE]

[SECTION: Advanced Usage & Deep Code Walkthrough]
<Write 3 detailed paragraphs explaining Script 2 line-by-line and how to attach and test it in a real game scene>

[TABLE]
<Keyword / Component / Method 1> | <Kya Kaam Karta Hai & Best Practice>
<Keyword / Component / Method 2> | <Kya Kaam Karta Hai & Best Practice>
<Keyword / Component / Method 3> | <Kya Kaam Karta Hai & Best Practice>
<Keyword / Component / Method 4> | <Kya Kaam Karta Hai & Best Practice>
<Keyword / Component / Method 5> | <Kya Kaam Karta Hai & Best Practice>
[/TABLE]

[TRAPS]
- <Pro-Tip / Common Mistake 1: Beginners kya galti karte hain aur usse kaise bachein>
- <Pro-Tip / Common Mistake 2: Memory/Performance trap and exact fix>
- <Pro-Tip / Common Mistake 3: Unity Editor / Compiler error and exact fix>
- <Pro-Tip / Common Mistake 4: Senior Engineer secret shortcut>
[/TRAPS]

[DRILLS]
- <Hands-on Practice Task 1> [Solution: <Detailed step-by-step solution>]
- <Hands-on Practice Task 2> [Solution: <Detailed step-by-step solution>]
- <Hands-on Practice Task 3> [Solution: <Detailed step-by-step solution>]
[/DRILLS]
===CHAPTER_END: {c_num}==="""

    elif book_mode == "ACADEMIC_EXAM":
        return f"""You are writing Module #{c_num}: "{c_title}" for the textbook "{book_title}".
Focus: {c_subs}
{style_block}
Use pure ASCII math only (alpha, beta, pi, x^2, !=, <=, >=, sqrt()). Aim for 3,000+ words!

===CHAPTER_START: {c_num}===
HEADING: {c_title}

[SECTION: Deep Theory & Fundamental Intuition]
<4 long paragraphs>

[SECTION: Mathematical Derivations & Proofs]
<4 long paragraphs>

[SECTION: Real-World & Exam Application Strategy]
<3 long paragraphs>

[TABLE]
<Property / Case 1> | <Condition & Result>
<Property / Case 2> | <Condition & Result>
<Property / Case 3> | <Condition & Result>
<Property / Case 4> | <Condition & Result>
[/TABLE]

[FORMULAS]
- <Formula / Identity 1>
- <Formula / Identity 2>
- <Formula / Identity 3>
- <Formula / Identity 4>
- <Formula / Identity 5>
[/FORMULAS]

[TRAPS]
- <Exam Trap 1 & How to avoid>
- <Exam Trap 2 & Topper shortcut>
- <Exam Trap 3 & Common sign/domain mistake>
[/TRAPS]

[EXAMPLE: <Tough Exam Problem 1>]
- Step 1: <Step 1>
- Step 2: <Step 2>
- Step 3: <Step 3>
- Final Answer: <Result>
[/EXAMPLE]

[EXAMPLE: <Tough Exam Problem 2>]
- Step 1: <Step 1>
- Step 2: <Step 2>
- Step 3: <Step 3>
- Final Answer: <Result>
[/EXAMPLE]

[EXAMPLE: <Tough Exam Problem 3>]
- Step 1: <Step 1>
- Step 2: <Step 2>
- Step 3: <Step 3>
- Final Answer: <Result>
[/EXAMPLE]

[DRILLS]
- Q1: <Practice Problem 1> [Ans: <Answer>]
- Q2: <Practice Problem 2> [Ans: <Answer>]
- Q3: <Practice Problem 3> [Ans: <Answer>]
- Q4: <Practice Problem 4> [Ans: <Answer>]
[/DRILLS]
===CHAPTER_END: {c_num}==="""

    elif book_mode == "STORY_FICTION":
        return f"""Write Part {c_num}: "{c_title}" of the story "{book_title}".
Plot: {c_subs}
{style_block}
Write a long, vivid, entertaining story chapter (no formulas or questions!):

===CHAPTER_START: {c_num}===
HEADING: {c_title}
ILLUSTRATION: <1-line visual description of the scene>

[SCENE: <Scene 1 Title>]
<4 rich story paragraphs with dialogues and emotions>

[SCENE: <Scene 2 Title>]
<4 rich story paragraphs with twist and action>

[SCENE: <Scene 3 Title>]
<4 rich story paragraphs moving the plot forward>
===CHAPTER_END: {c_num}==="""

    else:
        return f"""Write Module #{c_num}: "{c_title}" for "{book_title}".
Focus: {c_subs}
{style_block}

===CHAPTER_START: {c_num}===
HEADING: {c_title}

[SECTION: Deep Concept & Core Principles]
<4 detailed paragraphs>

[SECTION: Real-World Analogy & Case Study]
<3 detailed paragraphs>

[SECTION: Step-by-Step Execution Blueprint]
<4 detailed paragraphs>

[TABLE]
<Principle 1> | <Actionable Execution>
<Principle 2> | <Actionable Execution>
<Principle 3> | <Actionable Execution>
[/TABLE]

[TAKEAWAYS]
- <Key Takeaway 1>
- <Key Takeaway 2>
- <Key Takeaway 3>
- <Key Takeaway 4>
[/TAKEAWAYS]
===CHAPTER_END: {c_num}==="""

# ==========================================
# 8. V6.0 AKHAND GRANTH GENERATOR (WITH LIVE CLOUD PROGRESS!)
# ==========================================
def get_cache_file_path(topic):
    h = hashlib.md5(topic.strip().lower().encode("utf-8")).hexdigest()[:10]
    return f"v6_checkpoint_{h}.json"

def atomic_save_checkpoint(cache_file, outline_dict):
    tmp_path = cache_file + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(outline_dict, f, indent=2)
    os.replace(tmp_path, cache_file)

def estimate_pages_for_chapter_dict(ch_dict):
    """Estimates how many PDF pages a generated chapter/unit will occupy."""
    words = 0
    for s in ch_dict.get("theory_sections", []):
        for p in s.get("paragraphs", []):
            words += len(str(p).split())
    code_lines = sum(len(str(c.get("code", "")).split("\n")) for c in ch_dict.get("code_snippets", []))
    extras = len(ch_dict.get("summary_table", [])) + len(ch_dict.get("exam_traps", [])) + len(ch_dict.get("practice_drills", []))
    return max(2, round((words / 260.0) + (code_lines / 18.0) + (extras * 0.35)))

def generate_book_content(api_keys, models, topic):
    req_pages, req_chapters, ext_title, ext_author, explicit_chapters = parse_user_syllabus_and_constraints(topic)
    custom_style_rules, is_hinglish = extract_custom_style_instructions(topic)
    forced_mode = lock_true_book_mode(topic, "")
    cache_file = get_cache_file_path(topic)

    update_live_status(6, f"Brain Locked: {forced_mode} | Analyzing Syllabus...")

    cached_data = None
    if os.path.exists(cache_file):
        try:
            cached_data = json.load(open(cache_file, "r", encoding="utf-8"))
        except Exception:
            cached_data = None

    if cached_data and "chapters" in cached_data and len(cached_data["chapters"]) >= len(explicit_chapters):
        outline = cached_data
        already_done = len(outline.get("completed_chapters_map", {}))
        print(f"\n[*] Found V6 Checkpoint ('{cache_file}'): {already_done}/{len(outline['chapters'])} Deep Modules pehle se safe hain!")
    elif explicit_chapters:
        default_cat = "HINGLISH GAME DEV GRANTH" if is_hinglish else ("TECH ENCYCLOPEDIA" if forced_mode == "CODING_TECH" else "MASTER TEXTBOOK")
        outline = {
            "book_title": ext_title if ext_title else "Akhand Masterclass Blueprint",
            "subtitle": f"Complete {len(explicit_chapters)}-Module Deep-Dive Course (Zero to Hero Edition)",
            "author": ext_author if ext_author else "",
            "category": default_cat,
            "book_mode": forced_mode,
            "chapters": explicit_chapters,
            "completed_chapters_map": {}
        }
        atomic_save_checkpoint(cache_file, outline)
        print(f"\n[+] SYLLABUS LOCKED: {len(explicit_chapters)} Deep Modules Ready for 1-on-1 Full Generation!")
    else:
        if req_pages == 1:
            num_ch = 1
        elif req_chapters:
            num_ch = req_chapters
        elif req_pages and req_pages >= 200:
            num_ch = min(85, max(25, req_pages // 10))
        else:
            num_ch = 3 if forced_mode == "STORY_FICTION" else 12

        update_live_status(10, f"AI Planning {num_ch} Deep Modules for your topic...")
        plan_prompt = f"""Create an exhaustive book outline for: "{topic[:400]}" with {num_ch} detailed modules/chapters.
Output using this exact plain-text format:
BOOK_TITLE: <Catchy Title>
SUBTITLE: <Clear Subtitle>
CHAPTER 1: <Title> | <Subtopics to cover>
CHAPTER 2: <Title> | <Subtopics to cover>"""
        raw_plan, _ = call_ai_text(api_keys, models, plan_prompt, max_tokens=4096, temp=0.5)
        ch_list = []
        b_title = ext_title or "Akhand Masterclass Guide"
        b_sub = "From Zero to Hero Complete Edition"
        if raw_plan:
            tm = re.search(r"BOOK_TITLE\s*:\s*(.+)", raw_plan)
            sm = re.search(r"SUBTITLE\s*:\s*(.+)", raw_plan)
            if tm and not ext_title:
                b_title = tm.group(1).strip().strip('"\'')
            if sm:
                b_sub = sm.group(1).strip()
            for cm in re.finditer(r"CHAPTER\s+(\d+)\s*:\s*([^|\n]+)(?:\|\s*([^\n]+))?", raw_plan, flags=re.IGNORECASE):
                c_no = int(cm.group(1))
                c_ti = cm.group(2).strip()
                c_su = (cm.group(3) or c_ti).strip()
                ch_list.append({"chapter_number": c_no, "title": c_ti, "summary_to_cover": c_su})
        if not ch_list:
            ch_list = [{"chapter_number": 1, "title": b_title, "summary_to_cover": topic[:300]}]

        outline = {
            "book_title": b_title,
            "subtitle": b_sub,
            "author": ext_author or "",
            "category": "STORYBOOK" if forced_mode == "STORY_FICTION" else "MASTER EDITION",
            "book_mode": forced_mode,
            "chapters": ch_list,
            "completed_chapters_map": {}
        }
        atomic_save_checkpoint(cache_file, outline)

    ch_list = outline.get("chapters", [])
    book_mode = outline.get("book_mode", forced_mode)
    completed_map = {int(k): v for k, v in outline.get("completed_chapters_map", {}).items()}
    checkpoint_lock = threading.Lock()

    print(f"[+] Brain Locked : {book_mode} {'(PURE HINGLISH MODE ON!)' if is_hinglish else ''}")
    print(f"[+] Book Title   : {outline.get('book_title')}")
    if outline.get("author"):
        print(f"[+] Author       : {outline.get('author')}")
    print(f"[+] Saved Status : {len(completed_map)}/{len(ch_list)} Deep Modules Ready on Disk!")

    # STEP 2: DEDICATED 1-ON-1 DEEP MODULE GENERATOR (MAX PAGES PER MODULE!)
    def write_single_deep_module(ch_info):
        c_no = int(ch_info["chapter_number"])
        prompt = build_deep_single_unit_prompt(ch_info, outline.get("book_title", ""), custom_style_rules, book_mode)
        raw_out, used_model = call_ai_text(api_keys, models, prompt, max_tokens=8192, temp=0.55, seed_idx=c_no)
        if not raw_out:
            return 0

        c_block = re.split(r"===\s*CHAPTER_START\s*:\s*\d+\s*===", raw_out, flags=re.IGNORECASE)[-1]
        c_block = re.split(r"===\s*CHAPTER_END", c_block, flags=re.IGNORECASE)[0]
        ch_dict = parse_tagged_chapter_block(c_block, c_no, ch_info["title"], book_mode)

        with checkpoint_lock:
            completed_map[c_no] = ch_dict
            outline["completed_chapters_map"] = {str(k): v for k, v in completed_map.items()}
            atomic_save_checkpoint(cache_file, outline)
            total_done = len(completed_map)
            est_total_pages = sum(estimate_pages_for_chapter_dict(v) for v in completed_map.values())

        prog_pct = 12 + int((total_done / max(1, len(ch_list))) * 68)
        update_live_status(
            prog_pct,
            f"Module {total_done}/{len(ch_list)} Written (~{est_total_pages} Pages) -> {ch_dict['heading'][:42]}"
        )
        return 1

    wave = 1
    while len(completed_map) < len(ch_list):
        pending = [ch for ch in ch_list if int(ch["chapter_number"]) not in completed_map]
        if not pending:
            break

        print(f"\n[*] DEEP GRANTH WAVE {wave}: Writing {len(pending)} Full-Length Modules (1 Dedicated 8,192-Token Call Per Module)...")
        with ThreadPoolExecutor(max_workers=2) as ex:
            futures = [ex.submit(write_single_deep_module, ch) for ch in pending]
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as e:
                    print(f"    [!] Module warning: {e}")

        if len(completed_map) < len(ch_list):
            rem = len(ch_list) - len(completed_map)
            print(f"\n[*] {rem} modules bache hain, 6 seconds cooldown ke baad agla Wave chalu ho raha hai...")
            time.sleep(6)
            wave += 1

    outline["full_chapters"] = [completed_map[k] for k in sorted(completed_map.keys())]
    return outline, cache_file, req_pages

# ==========================================
# 9. ZERO-BLANK-SPACE KINDLE RENDERERS
# ==========================================
def draw_chapter_flowchart(draw, cy, sections, theme, f_bold, f_sub_num):
    titles = [strip_leading_number(s.get("sub_heading", "")) for s in sections if s.get("sub_heading")]
    titles = [t for t in titles if t][:3]
    if len(titles) < 2:
        return cy

    draw.rounded_rectangle([90, cy, 1150, cy + 86], radius=12, fill="#F1F5F9", outline=theme["box_border"], width=2)
    box_w = 310
    gap = 45
    start_x = 110
    by = cy + 14

    for i, t in enumerate(titles):
        bx = start_x + i * (box_w + gap)
        draw.rounded_rectangle([bx, by, bx + box_w, by + 58], radius=10, fill=theme["header_bg"], outline=theme["accent"], width=2)
        short_t = fit_text_to_pixel_width(draw, f"{i+1}. {t}", f_sub_num, box_w - 24)
        draw_centered(draw, (bx + box_w // 2, by + 29), short_t, f_sub_num, "#FFFFFF")
        if i < len(titles) - 1:
            ax1 = bx + box_w + 6
            ax2 = bx + box_w + gap - 6
            ay = by + 29
            draw.line([(ax1, ay), (ax2, ay)], fill=theme["sub_color"], width=4)
            draw.polygon([(ax2, ay), (ax2 - 10, ay - 7), (ax2 - 10, ay + 7)], fill=theme["sub_color"])

    return cy + 104

def draw_splittable_vscode_terminal(draw_state, snippet, f_bold, f_mono, max_y):
    code_title = sanitize_to_ascii_math(snippet.get("title", "Code Implementation"))
    code_raw = snippet.get("code", "")
    all_lines = []
    for raw_l in str(code_raw).split("\n"):
        wrapped = wrap_text(draw_state["draw"], raw_l, f_mono, 960, preserve_indent=True)
        all_lines.extend(wrapped if wrapped else [""])

    if not all_lines:
        return

    line_idx = 0
    part_num = 1
    while line_idx < len(all_lines):
        if draw_state["cy"] + 185 > max_y:
            if not draw_state["new_page_fn"]():
                break

        cy = draw_state["cy"]
        draw = draw_state["draw"]

        avail_px = max_y - cy - 78
        lines_fit = max(3, avail_px // 34)
        chunk = all_lines[line_idx:line_idx + lines_fit]
        box_h = 54 + (len(chunk) * 34) + 20

        draw.rounded_rectangle([90, cy, 1150, cy + box_h], radius=12, fill="#0D1117", outline="#30363D", width=3)
        draw.rounded_rectangle([90, cy, 1150, cy + 46], radius=12, fill="#161B22")
        draw.ellipse([114, cy + 15, 130, cy + 31], fill="#FF5F56")
        draw.ellipse([142, cy + 15, 158, cy + 31], fill="#FFBD2E")
        draw.ellipse([170, cy + 15, 186, cy + 31], fill="#27C93F")

        label = code_title[:52] if part_num == 1 else f"{code_title[:40]} (Continued...)"
        draw_plain(draw, (208, cy + 10), label, f_bold, "#E6EDF3")

        ly = cy + 56
        for cl in chunk:
            draw_syntax_highlighted_code_line(draw, (118, ly), cl, f_mono)
            ly += 34

        draw_state["cy"] = cy + box_h + 20
        line_idx += len(chunk)
        part_num += 1

def draw_splittable_comparison_table(draw_state, table_rows, theme, f_bold, f_body, max_y):
    if not table_rows:
        return

    def draw_table_header():
        cy = draw_state["cy"]
        d = draw_state["draw"]
        d.rounded_rectangle([90, cy, 1150, cy + 48], radius=8, fill=theme["header_bg"])
        draw_plain(d, (115, cy + 10), "CONCEPT / KEYWORD / API", f_bold, theme["accent"])
        draw_plain(d, (520, cy + 10), "EXPLANATION & BEST PRACTICE", f_bold, "#FFFFFF")
        draw_state["cy"] = cy + 52

    if draw_state["cy"] + 150 > max_y:
        if not draw_state["new_page_fn"]():
            return

    draw_state["cy"] += 6
    draw_table_header()

    for r_i, row in enumerate(table_rows):
        d = draw_state["draw"]
        c1_lines = wrap_text(d, str(row.get("col1", "")), f_bold, 380)
        c2_lines = wrap_text(d, str(row.get("col2", "")), f_body, 590)
        max_l = max(len(c1_lines), len(c2_lines), 1)
        row_h = (max_l * 38) + 18

        if draw_state["cy"] + row_h > max_y:
            if not draw_state["new_page_fn"]():
                break
            draw_table_header()
            d = draw_state["draw"]

        cy = draw_state["cy"]
        bg = "#FFFFFF" if r_i % 2 == 0 else theme["box_bg"]
        d.rectangle([90, cy, 1150, cy + row_h], fill=bg, outline="#CBD5E1", width=2)
        d.line([(495, cy), (495, cy + row_h)], fill="#CBD5E1", width=2)

        ry1 = cy + 9
        for l1 in c1_lines:
            draw_math_line(d, (110, ry1), l1, f_bold, "#0F172A")
            ry1 += 38
        ry2 = cy + 9
        for l2 in c2_lines:
            draw_math_line(d, (515, ry2), l2, f_body, "#334155")
            ry2 += 38
        draw_state["cy"] = cy + row_h

    draw_state["cy"] += 20

def fill_remaining_page_bottom_if_needed(draw, cy, ch, theme, f_bold, f_body, max_y):
    rem_h = max_y - cy
    if rem_h < 230 or rem_h > 680:
        return cy

    points = []
    for s in ch.get("theory_sections", []):
        sh = strip_leading_number(s.get("sub_heading", ""))
        if sh:
            points.append(f"Key Section: {sh}")
    for t in ch.get("summary_table", [])[:2]:
        c1 = sanitize_to_ascii_math(t.get("col1", ""))
        c2 = sanitize_to_ascii_math(t.get("col2", ""))
        if c1 and c2:
            points.append(f"{c1}: {c2}")

    if not points:
        return cy

    box_top = cy + 16
    box_bottom = max_y - 10
    draw.rounded_rectangle([90, box_top, 1150, box_bottom], radius=12, fill=theme["box_bg"], outline=theme["box_border"], width=2)
    draw.rounded_rectangle([90, box_top, 1150, box_top + 46], radius=12, fill=theme["header_bg"])
    draw_plain(draw, (115, box_top + 9), "QUICK REVISION & MODULE CHECKLIST", f_bold, theme["accent"])

    py = box_top + 62
    for pt in points:
        p_lines = wrap_text(draw, pt, f_body, 990)
        if py + (len(p_lines) * 38) > box_bottom - 14:
            break
        draw.ellipse([114, py + 11, 128, py + 25], fill=theme["sub_color"])
        for pl in p_lines:
            draw_math_line(draw, (144, py), pl, f_body, "#0F172A")
            py += 38
        py += 8

    return box_bottom

# ==========================================
# 10. DIRECT BINARY JPEG-TO-PDF STREAMER (0 MB RAM!)
# ==========================================
def compile_jpegs_to_pdf_zero_ram(jpeg_files, output_pdf_path, dpi=150.0):
    num_pages = len(jpeg_files)
    if num_pages == 0:
        raise ValueError("No pages to compile!")

    w_pt = round(PAGE_WIDTH * 72.0 / dpi, 2)
    h_pt = round(PAGE_HEIGHT * 72.0 / dpi, 2)

    total_objs = 3 + (3 * num_pages)
    offsets = [0] * total_objs

    with open(output_pdf_path, "wb") as pf:
        pf.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")

        offsets[1] = pf.tell()
        pf.write(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")

        offsets[2] = pf.tell()
        kids_str = " ".join(f"{3 + i * 3} 0 R" for i in range(num_pages))
        pf.write(f"2 0 obj\n<< /Type /Pages /Kids [{kids_str}] /Count {num_pages} >>\nendobj\n".encode("ascii"))

        for i, jpg_path in enumerate(jpeg_files):
            page_id = 3 + i * 3
            cont_id = 4 + i * 3
            img_id = 5 + i * 3

            with open(jpg_path, "rb") as jf:
                jpg_bytes = jf.read()

            offsets[page_id] = pf.tell()
            page_str = (
                f"{page_id} 0 obj\n"
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w_pt} {h_pt}] "
                f"/Resources << /XObject << /Im0 {img_id} 0 R >> >> "
                f"/Contents {cont_id} 0 R >>\nendobj\n"
            )
            pf.write(page_str.encode("ascii"))

            offsets[cont_id] = pf.tell()
            cmd_bytes = f"q {w_pt} 0 0 {h_pt} 0 0 cm /Im0 Do Q\n".encode("ascii")
            cont_head = f"{cont_id} 0 obj\n<< /Length {len(cmd_bytes)} >>\nstream\n".encode("ascii")
            pf.write(cont_head)
            pf.write(cmd_bytes)
            pf.write(b"endstream\nendobj\n")

            offsets[img_id] = pf.tell()
            img_head = (
                f"{img_id} 0 obj\n"
                f"<< /Type /XObject /Subtype /Image /Width {PAGE_WIDTH} /Height {PAGE_HEIGHT} "
                f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length {len(jpg_bytes)} >>\n"
                f"stream\n"
            ).encode("ascii")
            pf.write(img_head)
            pf.write(jpg_bytes)
            pf.write(b"\nendstream\nendobj\n")

        xref_pos = pf.tell()
        pf.write(f"xref\n0 {total_objs}\n".encode("ascii"))
        pf.write(b"0000000000 65535 f \n")
        for oid in range(1, total_objs):
            pf.write(f"{offsets[oid]:010d} 00000 n \n".encode("ascii"))

        trailer_str = (
            f"trailer\n<< /Size {total_objs} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n"
        )
        pf.write(trailer_str.encode("ascii"))

# ==========================================
# 11. MASTER KINDLE CONTINUOUS-FLOW COMPILER
# ==========================================
def build_perfect_ebook():
    api_keys = get_saved_api_keys()
    topic = get_topic_input()
    theme = pick_theme_for_topic(topic)

    update_live_status(3, f"Selected Theme: {theme['name']} | Connecting to AI Pool...")
    models = get_working_models(api_keys)
    data, cache_file, req_pages = generate_book_content(api_keys, models, topic)

    title = sanitize_to_ascii_math(data.get("book_title", "Master Book"))
    subtitle = sanitize_to_ascii_math(data.get("subtitle", "Complete Edition"))
    author = sanitize_to_ascii_math(data.get("author", ""))
    book_mode = data.get("book_mode", "GENERAL_GUIDE")
    default_cat = "STORYBOOK" if book_mode == "STORY_FICTION" else ("TECH ENCYCLOPEDIA" if book_mode == "CODING_TECH" else "MASTER TEXTBOOK")
    category = sanitize_to_ascii_math(data.get("category", default_cat))[:20]
    chapters = data.get("full_chapters", [])

    if os.path.exists(TEMP_DIR):
        shutil.rmtree(TEMP_DIR)
    os.makedirs(TEMP_DIR, exist_ok=True)

    has_toc = (len(chapters) > 1 and req_pages != 1)
    toc_pages_count = math.ceil(len(chapters) / 15) if has_toc else 0
    content_page_counter = toc_pages_count + 1

    chapter_files = []
    chapter_start_page_map = {}

    f_ch_badge = load_font(True, 22)
    f_ch_title = load_font(True, 40)
    f_sub_num = load_font(True, 21)
    f_sub_head = load_font(True, 30)
    f_body = load_font(False, 29 if book_mode == "STORY_FICTION" else 28)
    f_bold = load_font(True, 27)
    f_mono = load_font(False, 24, is_mono=True)
    f_dropcap = load_font(True, 78)

    line_h = 44 if book_mode == "STORY_FICTION" else 41
    para_gap = 26 if book_mode == "STORY_FICTION" else 20
    max_w = 1060
    max_y = 1560

    draw_state = {
        "img": None,
        "draw": None,
        "cy": 155,
        "pno": content_page_counter,
        "running_sec": category
    }

    def flush_current_page():
        if draw_state["img"] is not None:
            idx = len(chapter_files) + 1
            path = os.path.join(TEMP_DIR, f"chap_page_{idx:05d}.jpg")
            draw_state["img"].save(path, "JPEG", quality=91, optimize=True)
            draw_state["img"].close()
            draw_state["img"] = None
            chapter_files.append(path)

    def open_fresh_page(prog_ratio=0.0):
        nonlocal content_page_counter
        flush_current_page()
        pno = content_page_counter
        img, d = create_blank_content_page(title, draw_state["running_sec"], pno, theme, prog_ratio)
        content_page_counter += 1
        draw_state["img"] = img
        draw_state["draw"] = d
        draw_state["cy"] = 158
        draw_state["pno"] = pno
        return True

    update_live_status(82, f"Rendering {len(chapters)} Deep Modules into Kindle Pages (Zero Blank Space)...")

    for idx, ch in enumerate(chapters, start=1):
        prog_ratio = idx / max(1, len(chapters))
        ch_heading_clean = strip_leading_number(ch.get("heading", title))
        draw_state["running_sec"] = f"MOD {idx:02d}: {ch_heading_clean}" if len(chapters) > 1 else category

        if draw_state["img"] is None:
            open_fresh_page(prog_ratio)
        elif draw_state["cy"] > 980:
            draw_state["cy"] = fill_remaining_page_bottom_if_needed(
                draw_state["draw"], draw_state["cy"], chapters[idx - 2], theme, f_bold, f_body, max_y
            )
            open_fresh_page(prog_ratio)
        else:
            d = draw_state["draw"]
            cy = draw_state["cy"] + 18
            d.line([(90, cy), (1150, cy)], fill="#CBD5E1", width=2)
            d.line([(480, cy - 4), (760, cy - 4)], fill=theme["accent"], width=4)
            draw_state["cy"] = cy + 28

        chapter_start_page_map[idx] = draw_state["pno"]

        def ensure_space(h_needed):
            if draw_state["cy"] + h_needed > max_y:
                if req_pages == 1:
                    return False
                open_fresh_page(prog_ratio)
            return True

        draw_state["new_page_fn"] = lambda: ensure_space(max_y + 10)

        ensure_space(170)
        d = draw_state["draw"]
        cy = draw_state["cy"]
        ch_label = f"PART {idx:02d}" if book_mode == "STORY_FICTION" else (f"MODULE {idx:02d}" if len(chapters) > 1 else category.upper())
        d.rounded_rectangle([90, cy, 290, cy + 40], radius=8, fill=theme["header_bg"])
        draw_centered(d, (190, cy + 20), ch_label, f_ch_badge, theme["accent"])
        cy += 52

        for hl in wrap_text(d, ch_heading_clean, f_ch_title, max_w):
            draw_math_line(d, (90, cy), hl, f_ch_title, "#0F172A")
            cy += 50

        cy += 8
        d.line([(90, cy), (1150, cy)], fill=theme["accent"], width=4)
        draw_state["cy"] = cy + 24

        if book_mode == "STORY_FICTION":
            ill_prompt = ch.get("illustration_prompt", "")
            if ill_prompt and req_pages != 1 and idx <= 3:
                sc_img = fetch_ai_illustration(f"{ill_prompt}, anime digital art, no text", 960, 320, seed=idx * 17)
                if sc_img and ensure_space(340):
                    sc_img = sc_img.resize((1060, 310))
                    draw_state["img"].paste(sc_img, (90, draw_state["cy"]))
                    draw_state["draw"].rectangle([90, draw_state["cy"], 1150, draw_state["cy"] + 310], outline=theme["accent"], width=3)
                    draw_state["cy"] += 335

            scenes = ch.get("scenes", [])
            if not scenes and ch.get("theory_sections"):
                scenes = [{"scene_title": s.get("sub_heading", ""), "paragraphs": s.get("paragraphs", [])} for s in ch.get("theory_sections", [])]

            first_para_of_chapter = True
            for sc_i, sc in enumerate(scenes, start=1):
                sc_title = strip_leading_number(sc.get("scene_title", ""))
                if sc_title and len(scenes) > 1:
                    if not ensure_space(85):
                        break
                    draw_state["cy"] += 6
                    for sl in wrap_text(draw_state["draw"], sc_title, f_sub_head, max_w):
                        draw_plain(draw_state["draw"], (90, draw_state["cy"]), sl, f_sub_head, theme["sub_color"])
                        draw_state["cy"] += 42
                    draw_state["cy"] += 8

                for para in sc.get("paragraphs", []):
                    clean_p = sanitize_to_ascii_math(para)
                    if not clean_p:
                        continue
                    if first_para_of_chapter and len(clean_p) > 40:
                        first_para_of_chapter = False
                        first_char = clean_p[0].upper()
                        rest_text = clean_p[1:]
                        ensure_space(115)
                        d = draw_state["draw"]
                        cy = draw_state["cy"]
                        d.rounded_rectangle([90, cy + 4, 172, cy + 86], radius=10, fill=theme["header_bg"], outline=theme["accent"], width=2)
                        draw_centered(d, (131, cy + 45), first_char, f_dropcap, theme["gold"])

                        indented_lines = wrap_text(d, rest_text, f_body, max_w - 100)
                        for l_i, bl in enumerate(indented_lines):
                            if not ensure_space(line_h):
                                break
                            x_pos = 190 if l_i < 2 else 90
                            draw_plain(draw_state["draw"], (x_pos, draw_state["cy"]), bl, f_body, "#1E293B")
                            draw_state["cy"] += line_h
                        draw_state["cy"] += para_gap
                    else:
                        for bl in wrap_text(draw_state["draw"], clean_p, f_body, max_w):
                            if not ensure_space(line_h):
                                break
                            draw_plain(draw_state["draw"], (90, draw_state["cy"]), bl, f_body, "#1E293B")
                            draw_state["cy"] += line_h
                        draw_state["cy"] += para_gap

                if sc_i < len(scenes) and req_pages != 1 and ensure_space(55):
                    draw_centered(draw_state["draw"], (620, draw_state["cy"] + 10), "*   *   *", f_bold, theme["accent"])
                    draw_state["cy"] += 38

        else:
            if req_pages != 1 and ch.get("theory_sections") and ensure_space(115):
                draw_state["cy"] = draw_chapter_flowchart(
                    draw_state["draw"], draw_state["cy"], ch.get("theory_sections", []), theme, f_bold, f_sub_num
                )

            for s_idx, sec in enumerate(ch.get("theory_sections", []), start=1):
                clean_sub = strip_leading_number(sec.get("sub_heading", ""))
                if clean_sub:
                    if not ensure_space(95):
                        break
                    d = draw_state["draw"]
                    cy = draw_state["cy"]
                    badge_txt = f"{idx}.{s_idx}" if len(chapters) > 1 else f"0{s_idx}"
                    d.rounded_rectangle([90, cy + 2, 185, cy + 40], radius=8, fill=theme["sub_color"])
                    draw_centered(d, (137, cy + 21), badge_txt, f_sub_num, "#FFFFFF")

                    sub_lines = wrap_text(d, clean_sub, f_sub_head, max_w - 115)
                    for sl_i, sl in enumerate(sub_lines):
                        tx = 202 if sl_i == 0 else 90
                        draw_math_line(d, (tx, cy), sl, f_sub_head, theme["sub_color"])
                        cy += 42
                    draw_state["cy"] = cy + 8

                for para in sec.get("paragraphs", []):
                    for bl in wrap_text(draw_state["draw"], para, f_body, max_w):
                        if not ensure_space(line_h):
                            break
                        draw_math_line(draw_state["draw"], (90, draw_state["cy"]), bl, f_body, "#1E293B")
                        draw_state["cy"] += line_h
                    draw_state["cy"] += para_gap
                draw_state["cy"] += 8

            for snip in ch.get("code_snippets", []):
                if req_pages != 1:
                    draw_splittable_vscode_terminal(draw_state, snip, f_bold, f_mono, max_y)

            if ch.get("summary_table") and req_pages != 1:
                draw_splittable_comparison_table(draw_state, ch.get("summary_table", []), theme, f_bold, f_body, max_y)

            takeaways = ch.get("key_takeaways", []) if book_mode == "GENERAL_GUIDE" else ch.get("formulas", [])
            if takeaways and req_pages != 1:
                first_item_lines = wrap_text(draw_state["draw"], takeaways[0], f_bold, 995)
                ensure_space(64 + (len(first_item_lines) * 40) + 30)
                d = draw_state["draw"]
                cy = draw_state["cy"] + 6
                box_header = "KEY TAKEAWAYS & ACTIONABLE TIPS" if book_mode == "GENERAL_GUIDE" else "KEY FORMULAS & IDENTITIES CHEAT-SHEET"
                d.rounded_rectangle([90, cy, 1150, cy + 46], radius=8, fill=theme["header_bg"])
                draw_plain(d, (115, cy + 9), box_header, f_bold, theme["accent"])
                draw_state["cy"] = cy + 58

                for item in takeaways:
                    f_lines = wrap_text(draw_state["draw"], item, f_bold, 995)
                    box_h = (len(f_lines) * 40) + 22
                    ensure_space(box_h + 12)
                    d = draw_state["draw"]
                    cy = draw_state["cy"]
                    d.rounded_rectangle([90, cy, 1150, cy + box_h], radius=8, fill=theme["box_bg"], outline=theme["box_border"], width=2)
                    d.ellipse([112, cy + 20, 126, cy + 34], fill=theme["sub_color"])
                    fy = cy + 11
                    for fl in f_lines:
                        draw_math_line(d, (142, fy), fl, f_bold, "#0F172A")
                        fy += 40
                    draw_state["cy"] = cy + box_h + 12
                draw_state["cy"] += 12

            traps = ch.get("exam_traps", [])
            if traps and req_pages != 1:
                first_trap_lines = wrap_text(draw_state["draw"], traps[0], f_bold, 995)
                ensure_space(62 + (len(first_trap_lines) * 38) + 28)
                d = draw_state["draw"]
                cy = draw_state["cy"]
                trap_header = "PRO-TIPS & COMMON MISTAKES (ERRORS SE BACHNE KE LIYE)" if book_mode == "CODING_TECH" else "WARNING: COMMON EXAM TRAPS & MISTAKES TO AVOID"
                d.rounded_rectangle([90, cy, 1150, cy + 46], radius=8, fill="#991B1B")
                draw_plain(d, (115, cy + 9), trap_header, f_bold, "#FEF08A")
                draw_state["cy"] = cy + 56

                for tr in traps:
                    t_lines = wrap_text(draw_state["draw"], tr, f_bold, 995)
                    bh = (len(t_lines) * 38) + 20
                    ensure_space(bh + 10)
                    d = draw_state["draw"]
                    cy = draw_state["cy"]
                    d.rounded_rectangle([90, cy, 1150, cy + bh], radius=8, fill="#FEF2F2", outline="#FCA5A5", width=2)
                    d.rectangle([112, cy + 17, 126, cy + 31], fill="#DC2626")
                    ty = cy + 10
                    for tl in t_lines:
                        draw_math_line(d, (142, ty), tl, f_bold, "#7F1D1D")
                        ty += 38
                    draw_state["cy"] = cy + bh + 10
                draw_state["cy"] += 12

            examples = ch.get("solved_examples", [])
            if examples and req_pages != 1:
                ensure_space(135)
                d = draw_state["draw"]
                cy = draw_state["cy"]
                d.rounded_rectangle([90, cy, 1150, cy + 46], radius=8, fill="#065F46")
                draw_plain(d, (115, cy + 9), "SOLVED EXAM-LEVEL EXAMPLES (STEP-BY-STEP)", f_bold, "#FFFFFF")
                draw_state["cy"] = cy + 58

                for ex_i, ex in enumerate(examples, start=1):
                    ensure_space(100)
                    q_txt = f"Example {ex_i}: {ex.get('question', '')}"
                    for ql in wrap_text(draw_state["draw"], q_txt, f_bold, max_w):
                        ensure_space(line_h)
                        draw_math_line(draw_state["draw"], (90, draw_state["cy"]), ql, f_bold, "#064E3B")
                        draw_state["cy"] += line_h
                    draw_state["cy"] += 6

                    for st in ex.get("solution_steps", []):
                        st_lines = wrap_text(draw_state["draw"], st, f_body, 1005)
                        for st_i, stl in enumerate(st_lines):
                            ensure_space(line_h)
                            if st_i == 0:
                                draw_state["draw"].ellipse([108, draw_state["cy"] + 13, 120, draw_state["cy"] + 25], fill="#059669")
                            draw_math_line(draw_state["draw"], (135, draw_state["cy"]), stl, f_body, "#334155")
                            draw_state["cy"] += line_h
                        draw_state["cy"] += 4
                    draw_state["cy"] += 10
                    draw_state["draw"].line([(90, draw_state["cy"]), (1150, draw_state["cy"])], fill="#CBD5E1", width=2)
                    draw_state["cy"] += 18

            drills = ch.get("practice_drills", [])
            if drills and req_pages != 1:
                ensure_space(125)
                d = draw_state["draw"]
                cy = draw_state["cy"]
                drill_header = "HANDS-ON GAME DEV CHALLENGES & SOLUTIONS" if book_mode == "CODING_TECH" else "CHAPTER PRACTICE EXERCISE (WITH ANSWERS)"
                d.rounded_rectangle([90, cy, 1150, cy + 46], radius=8, fill="#7C2D12")
                draw_plain(d, (115, cy + 9), drill_header, f_bold, "#FED7AA")
                draw_state["cy"] = cy + 58

                for dr in drills:
                    dr_lines = wrap_text(draw_state["draw"], dr, f_body, 1010)
                    for dl_i, dl in enumerate(dr_lines):
                        ensure_space(line_h)
                        if dl_i == 0:
                            draw_state["draw"].rectangle([102, draw_state["cy"] + 13, 114, draw_state["cy"] + 25], fill="#EA580C")
                        draw_math_line(draw_state["draw"], (130, draw_state["cy"]), dl, f_body, "#1E293B")
                        draw_state["cy"] += line_h
                    draw_state["cy"] += 12

    flush_current_page()

    # --- PASS 2: ASSEMBLE COVER + EXACT PAGE-SYNCED TOC + CHAPTERS + BACK COVER ---
    final_page_files = []

    if req_pages != 1:
        update_live_status(90, "Generating Custom Cover Artwork & Back-Cover...")
        cov_img = build_custom_cover(title, subtitle, author, category, topic, theme, len(chapters), book_mode)
        cov_path = os.path.join(TEMP_DIR, "00_cover.jpg")
        cov_img.save(cov_path, "JPEG", quality=93, optimize=True)
        cov_img.close()
        final_page_files.append(cov_path)

    if has_toc:
        toc_pno = 1
        toc_img, toc_d = create_blank_content_page(title, "TABLE OF CONTENTS", toc_pno, theme, 0.02)
        cy = 160
        draw_plain(toc_d, (90, cy), "MASTER TABLE OF CONTENTS", f_ch_title, "#0F172A")
        cy += 54
        toc_d.line([(90, cy), (1150, cy)], fill=theme["accent"], width=4)
        cy += 28

        for idx, ch in enumerate(chapters, start=1):
            ch_name = strip_leading_number(ch.get("heading", f"Module {idx}"))
            exact_pno = chapter_start_page_map.get(idx, 1)

            toc_d.rounded_rectangle([90, cy, 1150, cy + 62], radius=10, fill=theme["box_bg"], outline=theme["box_border"], width=2)
            toc_d.rounded_rectangle([105, cy + 10, 235, cy + 52], radius=8, fill=theme["header_bg"])
            draw_centered(toc_d, (170, cy + 31), f"MOD {idx:02d}", f_sub_num, "#FFFFFF")

            trimmed_title = fit_text_to_pixel_width(toc_d, ch_name, f_bold, 680)
            draw_math_line(toc_d, (255, cy + 14), trimmed_title, f_bold, "#0F172A")

            toc_d.rounded_rectangle([985, cy + 10, 1135, cy + 52], radius=8, fill=theme["sub_color"])
            draw_centered(toc_d, (1060, cy + 31), f"PG {exact_pno:02d}", f_sub_num, "#FFFFFF")

            cy += 76
            if cy > 1520 and idx < len(chapters):
                tp_path = os.path.join(TEMP_DIR, f"01_toc_{toc_pno:02d}.jpg")
                toc_img.save(tp_path, "JPEG", quality=92, optimize=True)
                toc_img.close()
                final_page_files.append(tp_path)
                toc_pno += 1
                toc_img, toc_d = create_blank_content_page(title, "TABLE OF CONTENTS", toc_pno, theme, 0.03)
                cy = 165

        tp_path = os.path.join(TEMP_DIR, f"01_toc_{toc_pno:02d}.jpg")
        toc_img.save(tp_path, "JPEG", quality=92, optimize=True)
        toc_img.close()
        final_page_files.append(tp_path)

    final_page_files.extend(chapter_files)

    if req_pages != 1 and len(chapters) > 1:
        back_img = build_back_cover(title, subtitle, author, category, theme, len(chapters), len(final_page_files) + 1)
        back_path = os.path.join(TEMP_DIR, "99_back_cover.jpg")
        back_img.save(back_path, "JPEG", quality=93, optimize=True)
        back_img.close()
        final_page_files.append(back_path)

    safe_name = re.sub(r"[^a-zA-Z0-9]+", "_", title).strip("_")[:38] or "Akhand_Granth_Book"
    output_pdf = f"{safe_name}_{JOB_ID}.pdf" if JOB_ID else f"{safe_name}.pdf"

    update_live_status(95, f"Direct Binary Streaming {len(final_page_files)} Pages into PDF (0 MB RAM)...")
    compile_jpegs_to_pdf_zero_ram(final_page_files, output_pdf, dpi=150.0)

    shutil.rmtree(TEMP_DIR, ignore_errors=True)

    # Upload to GitHub Releases if running on 24/7 Cloud, else return local file path
    final_download_url = upload_pdf_to_github_release(output_pdf)
    update_live_status(
        100,
        f"V6.0 Akhand Granth Ready! ({len(final_page_files)} Pages, {len(chapters)} Deep Modules)",
        done=True,
        pdf_url=final_download_url
    )

    print(f"\n[SUCCESS] V6.0 Akhand Granth Ready ({len(final_page_files)} Pages, {len(chapters)} Deep Modules)!")
    print(f"Brain Mode : {book_mode}  |  Theme: {theme['name']}")
    print(f"Full Path / URL : {final_download_url}")

if __name__ == "__main__":
    try:
        build_perfect_ebook()
    except Exception as exc:
        update_live_status(100, f"Error: {exc}", done=True, error=str(exc))
        raise
