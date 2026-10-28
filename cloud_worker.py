import os
import re
import json
import time
import math
import base64
import shutil
import random
import hashlib
import subprocess
import unicodedata
import urllib.parse
from io import BytesIO
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image, ImageDraw, ImageFont

# ==========================================
# 1. CLOUD ENVIRONMENT & LIVE STATUS UPDATER
# ==========================================
API_KEYS = [k.strip() for k in re.split(r"[\n,]+", os.environ.get("GOOGLE_API_KEY", "")) if k.strip()]
GH_TOKEN = os.environ.get("GH_TOKEN", "")
GH_REPO = os.environ.get("GH_REPO", "")
JOB_ID = os.environ.get("JOB_ID", f"job_{int(time.time())}")
RAW_PAYLOAD = os.environ.get("PAYLOAD_JSON", "{}")

PAGE_WIDTH = 1240
PAGE_HEIGHT = 1754
TEMP_DIR = f"temp_{JOB_ID}"

def update_live_status(progress_pct, message, done=False, pdf_url="", error=""):
    """Website ke Progress Bar ko real-time update bhejta hai (with [skip ci] taaki Netlify rebuild na ho)."""
    print(f"[{progress_pct}%] {message}")
    if not GH_TOKEN or not GH_REPO:
        return

    status_path = f"status/{JOB_ID}.json"
    api_url = f"https://api.github.com/repos/{GH_REPO}/contents/{status_path}"
    headers = {
        "Authorization": f"Bearer {GH_TOKEN}",
        "Accept": "application/vnd.github+json"
    }

    payload_data = {
        "job_id": JOB_ID,
        "progress": min(100, max(1, int(progress_pct))),
        "message": message,
        "done": done,
        "pdf_url": pdf_url,
        "error": error,
        "updated_at": int(time.time())
    }
    content_b64 = base64.b64encode(json.dumps(payload_data, indent=2).encode("utf-8")).decode("ascii")

    # Get existing SHA if file already exists
    sha = None
    try:
        r_get = requests.get(api_url, headers=headers, timeout=8)
        if r_get.status_code == 200:
            sha = r_get.json().get("sha")
    except Exception:
        pass

    body = {
        "message": f"Update {JOB_ID} ({progress_pct}%) [skip ci]",
        "content": content_b64
    }
    if sha:
        body["sha"] = sha

    try:
        requests.put(api_url, headers=headers, json=body, timeout=10)
    except Exception as e:
        print(f"    [!] Status update warning: {e}")

def upload_pdf_to_github_release(pdf_filepath):
    """PDF ko GitHub Release ('ebooks-vault') mein upload karke direct public download link nikalta hai."""
    if not GH_TOKEN or not GH_REPO:
        return os.path.abspath(pdf_filepath)

    tag = "ebooks-vault"
    subprocess.run(
        ["gh", "release", "create", tag, "--title", "Generated E-Books Vault", "--notes", "Automated 24/7 E-Book Storage"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    subprocess.run(
        ["gh", "release", "upload", tag, pdf_filepath, "--clobber"],
        check=True
    )
    filename = os.path.basename(pdf_filepath)
    return f"https://github.com/{GH_REPO}/releases/download/{tag}/{urllib.parse.quote(filename)}"

# ==========================================
# 2. THEMES & ASCII SANITIZER
# ==========================================
THEMES = {
    "obsidian": {
        "name": "Kindle Obsidian Pro", "paper_bg": "#FAF8F5", "cov_bg1": "#090D16", "cov_bg2": "#1E293B",
        "accent": "#06B6D4", "gold": "#F59E0B", "header_bg": "#0F172A", "sub_color": "#0E7490", "box_bg": "#F0F9FF", "box_border": "#7DD3FC"
    },
    "sapphire": {
        "name": "O'Reilly Sapphire", "paper_bg": "#FCFBF9", "cov_bg1": "#070B19", "cov_bg2": "#1E1B4B",
        "accent": "#38BDF8", "gold": "#F59E0B", "header_bg": "#0F172A", "sub_color": "#0369A1", "box_bg": "#EFF6FF", "box_border": "#93C5FD"
    },
    "emerald": {
        "name": "Emerald Cyber", "paper_bg": "#F9FBF9", "cov_bg1": "#04130E", "cov_bg2": "#064E3B",
        "accent": "#10B981", "gold": "#FBBF24", "header_bg": "#064E3B", "sub_color": "#047857", "box_bg": "#ECFDF5", "box_border": "#6EE7B7"
    },
    "crimson": {
        "name": "Crimson Executive", "paper_bg": "#FDFBFB", "cov_bg1": "#18080A", "cov_bg2": "#4C0519",
        "accent": "#F43F5E", "gold": "#FDE047", "header_bg": "#3B0714", "sub_color": "#BE123C", "box_bg": "#FFF1F2", "box_border": "#FDA4AF"
    },
    "violet": {
        "name": "Violet Quantum", "paper_bg": "#FBF9FD", "cov_bg1": "#0F0720", "cov_bg2": "#2E1065",
        "accent": "#A855F7", "gold": "#38BDF8", "header_bg": "#1E1035", "sub_color": "#6D28D9", "box_bg": "#F5F3FF", "box_border": "#C4B5FD"
    }
}

UNICODE_TO_ASCII_MATH = {
    "⁰": "^0", "¹": "^1", "²": "^2", "³": "^3", "⁴": "^4", "⁵": "^5", "⁶": "^6", "⁷": "^7", "⁸": "^8", "⁹": "^9",
    "⁺": "^+", "⁻": "^-", "ⁿ": "^n", "₀": "_0", "₁": "_1", "₂": "_2", "₃": "_3", "₄": "_4", "₅": "_5",
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "θ": "theta", "λ": "lambda", "π": "pi", "σ": "sigma", "Δ": "Delta",
    "≠": " != ", "≤": " <= ", "≥": " >= ", "±": "+/-", "√": "sqrt", "∞": "infinity", "→": " -> ", "⇒": " => ",
    "“": '"', "”": '"', "‘": "'", "’": "'", "…": "...", "–": "-", "—": " - "
}

def sanitize_to_ascii_math(text, preserve_indent=False):
    if not text:
        return ""
    s = str(text)
    s = re.sub(r"[■▪▫▬▭▮▯◆◇◈●○◎★☆►▶▸▹•‣⁃]", "", s)
    for k, v in UNICODE_TO_ASCII_MATH.items():
        if k in s:
            s = s.replace(k, v)
    if not preserve_indent:
        s = s.replace("**", "").replace("##", "")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return s.rstrip() if preserve_indent else re.sub(r"[ \t]+", " ", s).strip()

def strip_leading_number(t):
    c = sanitize_to_ascii_math(t)
    c = re.sub(r"^[\*\#\_\-\s]+|[\*\#\_\-\s]+$", "", c)
    return re.sub(r"^(?:section|topic|part|chapter|scene|module)?\s*\d+[\.\:\)\-]\s*", "", c, flags=re.IGNORECASE).strip()

# ==========================================
# 3. FONT & DRAWING HELPERS
# ==========================================
class SmartFont:
    def __init__(self, pil_font, size, stroke_width=0):
        self.font = pil_font
        self.size = size
        self.stroke = stroke_width

def load_font(is_bold=False, size=30, is_mono=False):
    mono_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/system/fonts/DroidSansMono.ttf", "consola.ttf"
    ]
    bold_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/system/fonts/Roboto-Bold.ttf", "arialbd.ttf"
    ]
    reg_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/system/fonts/Roboto-Regular.ttf", "arial.ttf"
    ]
    pool = mono_candidates if is_mono else (bold_candidates if is_bold else reg_candidates)
    for p in pool + reg_candidates:
        if os.path.exists(p):
            try:
                return SmartFont(ImageFont.truetype(p, size), size, 0)
            except Exception:
                pass
    return SmartFont(ImageFont.load_default(size=size), size, 0)

def text_size(draw, text, sfont):
    clean = re.sub(r"[\^_]([0-9a-zA-Z\+\-]+)", r"\1", sanitize_to_ascii_math(text))
    bbox = draw.textbbox((0, 0), clean, font=sfont.font, stroke_width=sfont.stroke)
    return (bbox[2] - bbox[0]), (bbox[3] - bbox[1]), bbox[1]

def draw_plain(draw, xy, text, sfont, fill, preserve_indent=False):
    clean = sanitize_to_ascii_math(text, preserve_indent=preserve_indent)
    draw.text(xy, clean, font=sfont.font, fill=fill, stroke_width=sfont.stroke, stroke_fill=fill)

def draw_centered(draw, center_xy, text, sfont, fill):
    clean = sanitize_to_ascii_math(text)
    w, h, top_off = text_size(draw, clean, sfont)
    draw_plain(draw, (center_xy[0] - w // 2, center_xy[1] - h // 2 - top_off // 2), clean, sfont, fill)

def draw_right_aligned(draw, right_x, y, text, sfont, fill):
    clean = sanitize_to_ascii_math(text)
    w, _, _ = text_size(draw, clean, sfont)
    draw_plain(draw, (right_x - w, y), clean, sfont, fill)

def fit_text_to_pixel_width(draw, text, sfont, max_px):
    text = sanitize_to_ascii_math(text)
    w, _, _ = text_size(draw, text, sfont)
    if w <= max_px:
        return text
    out = ""
    for word in text.split():
        test = f"{out} {word}".strip()
        if text_size(draw, test + "...", sfont)[0] <= max_px:
            out = test
        else:
            break
    return (out + "...") if out else text[:32] + "..."

def wrap_text(draw, text, sfont, max_width, preserve_indent=False):
    text = sanitize_to_ascii_math(text, preserve_indent=preserve_indent)
    lines = []
    for paragraph in text.split("\n"):
        indent_str = " " * min(16, len(paragraph) - len(paragraph.lstrip(" "))) if preserve_indent else ""
        words = paragraph.strip().split() if preserve_indent else paragraph.split()
        if not words:
            lines.append("")
            continue
        curr = indent_str
        for w in words:
            test = f"{curr} {w}".strip() if curr.strip() != "" else f"{indent_str}{w}"
            if text_size(draw, test, sfont)[0] <= max_width:
                curr = test
            else:
                if curr.strip():
                    lines.append(curr)
                curr = f"{indent_str}{w}"
        if curr.strip():
            lines.append(curr)
    return lines

def draw_syntax_highlighted_code_line(draw, xy, code_line, sfont):
    x, y = xy
    clean = sanitize_to_ascii_math(code_line, preserve_indent=True)
    if clean.lstrip(" ").startswith(("//", "#")):
        draw.text((x, y), clean, font=sfont.font, fill="#7EE787")
        return
    keywords = {"public", "private", "protected", "class", "struct", "interface", "void", "int", "float", "bool", "string", "var", "new", "return", "if", "else", "for", "foreach", "while", "async", "await", "using", "static", "override", "def", "import", "from"}
    types = {"MonoBehaviour", "ScriptableObject", "GameObject", "Transform", "Vector2", "Vector3", "Quaternion", "Rigidbody", "Collider", "Physics", "Time", "Input", "Debug", "List", "Dictionary", "Coroutine", "Mathf"}
    tokens = re.split(r'(\".*?\"|\b[A-Za-z_][A-Za-z0-9_]*\b|\s+|.)', clean)
    cur_x = x
    for tok in tokens:
        if not tok:
            continue
        color = "#FDE047" if (tok.startswith('"') and tok.endswith('"')) else ("#FF7B72" if tok in keywords else ("#79C0FF" if tok in types else ("#D2A8FF" if tok.isdigit() else "#E6EDF3")))
        draw.text((cur_x, y), tok, font=sfont.font, fill=color)
        bbox = draw.textbbox((0, 0), tok, font=sfont.font)
        cur_x += (bbox[2] - bbox[0])

# ==========================================
# 4. CUSTOM / RANDOM COVER & PAGE DESIGNER
# ==========================================
def fetch_ai_illustration(prompt_text, width=1020, height=490, seed=42):
    encoded = urllib.parse.quote(prompt_text)
    url = f"https://image.pollinations.ai/prompt/{encoded}?width={width}&height={height}&nologo=true&seed={seed}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200 and len(r.content) > 4000:
            return Image.open(BytesIO(r.content)).convert("RGB")
    except Exception:
        pass
    return None

def build_custom_cover(title, subtitle, author, category, cover_prompt, theme, total_chapters):
    cov = Image.new("RGB", (PAGE_WIDTH, PAGE_HEIGHT), theme["cov_bg1"])
    cd = ImageDraw.Draw(cov)
    cd.polygon([(0, 0), (PAGE_WIDTH, 0), (PAGE_WIDTH, 520), (0, 320)], fill=theme["cov_bg2"])
    cd.polygon([(0, 1380), (PAGE_WIDTH, 1180), (PAGE_WIDTH, PAGE_HEIGHT), (0, PAGE_HEIGHT)], fill=theme["cov_bg2"])
    cd.rectangle([45, 45, PAGE_WIDTH - 45, PAGE_HEIGHT - 45], outline=theme["accent"], width=3)

    f_badge = load_font(True, 24)
    f_title = load_font(True, 54)
    f_sub = load_font(False, 30)
    f_author = load_font(True, 28)

    cd.rounded_rectangle([110, 115, 560, 170], radius=12, fill=theme["accent"])
    draw_centered(cd, (335, 142), category[:24].upper(), f_badge, "#090D16")

    art_box = (110, 205, PAGE_WIDTH - 110, 695)
    art_query = cover_prompt if cover_prompt else f"modern high contrast digital book cover illustration for {title}, cinematic lighting, no text"
    ai_img = fetch_ai_illustration(art_query, 1020, 490, seed=len(title))
    if ai_img:
        cov.paste(ai_img.resize((1020, 490)), (110, 205))
    else:
        cd.rectangle(art_box, fill=theme["cov_bg2"])
        for gx in range(110, 1130, 55):
            cd.line([(gx, 205), (gx, 695)], fill="#1E293B", width=1)
        pts = [(110 + px, int(450 + math.sin(px / 50.0) * 160)) for px in range(0, 1020, 8)]
        cd.line(pts, fill=theme["accent"], width=5)
    cd.rectangle(art_box, outline=theme["accent"], width=4)

    cd.rounded_rectangle([110, 740, PAGE_WIDTH - 110, 1450], radius=22, fill="#0F172A", outline=theme["gold"], width=3)
    cd.rectangle([150, 785, 380, 797], fill=theme["accent"])

    y = 825
    for line in wrap_text(cd, title.upper(), f_title, 920)[:4]:
        draw_plain(cd, (150, y), line, f_title, "#FFFFFF")
        y += 66
    y += 18
    cd.line([(150, y), (PAGE_WIDTH - 150, y)], fill="#334155", width=2)
    y += 24
    for line in wrap_text(cd, subtitle, f_sub, 920)[:3]:
        draw_plain(cd, (150, y), line, f_sub, "#CBD5E1")
        y += 44

    if author:
        cd.rounded_rectangle([150, 1340, PAGE_WIDTH - 150, 1410], radius=12, fill=theme["cov_bg2"], outline=theme["gold"], width=2)
        draw_centered(cd, (620, 1375), f"AUTHOR: {author.upper()}", f_author, theme["gold"])

    cd.rounded_rectangle([200, 1520, PAGE_WIDTH - 200, 1590], radius=35, fill=theme["cov_bg2"], outline=theme["accent"], width=2)
    draw_centered(cd, (620, 1555), f"{total_chapters} MODULES  |  KINDLE CLOUD EDITION", f_badge, "#F8FAFC")
    return cov

def create_blank_content_page(title, running_section, page_num, theme, progress_ratio=0.0):
    page = Image.new("RGB", (PAGE_WIDTH, PAGE_HEIGHT), theme.get("paper_bg", "#FAF8F5"))
    pd = ImageDraw.Draw(page)
    pd.rectangle([0, 0, PAGE_WIDTH, 98], fill=theme["header_bg"])
    pd.rectangle([0, 98, PAGE_WIDTH, 105], fill=theme["accent"])
    pd.rectangle([48, 132, PAGE_WIDTH - 48, 1612], outline="#D1D5DB", width=2)
    pd.rectangle([48, 132, 56, 1612], fill=theme["accent"])

    if progress_ratio > 0:
        prog_y = int(132 + (1612 - 132) * min(1.0, progress_ratio))
        pd.rectangle([PAGE_WIDTH - 52, 132, PAGE_WIDTH - 48, prog_y], fill=theme["gold"])

    f_header = load_font(True, 21)
    f_cat = load_font(True, 20)
    f_pnum = load_font(True, 23)

    draw_plain(pd, (82, 38), fit_text_to_pixel_width(pd, title.upper(), f_header, 680), f_header, "#F8FAFC")
    draw_right_aligned(pd, PAGE_WIDTH - 72, 38, fit_text_to_pixel_width(pd, running_section.upper(), f_cat, 410), f_cat, theme["accent"])

    pd.rounded_rectangle([530, 1628, 710, 1690], radius=31, fill=theme["header_bg"], outline=theme["accent"], width=3)
    draw_centered(pd, (620, 1659), f"PAGE {page_num:02d}", f_pnum, "#FFFFFF")
    return page, pd

# ==========================================
# 5. AI CALLER & BINARY PDF COMPILER
# ==========================================
def get_working_models(api_key):
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    models = []
    try:
        r = requests.get(url, timeout=8)
        if r.status_code == 200:
            for m in r.json().get("models", []):
                name = m.get("name", "").replace("models/", "")
                if "generateContent" in m.get("supportedGenerationMethods", []) and "gemini" in name:
                    if not any(b in name for b in ["1.5", "2.0", "2.5", "vision", "embedding", "imagen", "image", "tts", "audio", "thinking", "live"]):
                        models.append(name)
    except Exception:
        pass
    for fb in ["gemini-flash-lite-latest", "gemini-3.1-flash-lite-preview", "gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-3.8-flash"]:
        if fb not in models:
            models.insert(0, fb)
    return models

def call_ai(models, prompt, seed=0):
    headers = {"Content-Type": "application/json"}
    payload = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.5, "maxOutputTokens": 8192}}
    for attempt in range(8):
        model = models[(seed + attempt) % len(models)]
        key = API_KEYS[(seed + attempt) % len(API_KEYS)]
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=75)
            if resp.status_code == 200:
                cands = resp.json().get("candidates", [])
                if cands:
                    return cands[0]["content"]["parts"][0]["text"]
            elif resp.status_code == 429:
                time.sleep(4)
        except Exception:
            time.sleep(2)
    return ""

def compile_jpegs_to_pdf_zero_ram(jpeg_files, output_pdf_path, dpi=150.0):
    num_pages = len(jpeg_files)
    w_pt, h_pt = round(PAGE_WIDTH * 72.0 / dpi, 2), round(PAGE_HEIGHT * 72.0 / dpi, 2)
    total_objs = 3 + (3 * num_pages)
    offsets = [0] * total_objs
    with open(output_pdf_path, "wb") as pf:
        pf.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets[1] = pf.tell()
        pf.write(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
        offsets[2] = pf.tell()
        kids = " ".join(f"{3 + i * 3} 0 R" for i in range(num_pages))
        pf.write(f"2 0 obj\n<< /Type /Pages /Kids [{kids}] /Count {num_pages} >>\nendobj\n".encode("ascii"))
        for i, jpg_path in enumerate(jpeg_files):
            page_id, cont_id, img_id = 3 + i * 3, 4 + i * 3, 5 + i * 3
            jpg_bytes = open(jpg_path, "rb").read()
            offsets[page_id] = pf.tell()
            pf.write(f"{page_id} 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w_pt} {h_pt}] /Resources << /XObject << /Im0 {img_id} 0 R >> >> /Contents {cont_id} 0 R >>\nendobj\n".encode("ascii"))
            offsets[cont_id] = pf.tell()
            cmd = f"q {w_pt} 0 0 {h_pt} 0 0 cm /Im0 Do Q\n".encode("ascii")
            pf.write(f"{cont_id} 0 obj\n<< /Length {len(cmd)} >>\nstream\n".encode("ascii") + cmd + b"endstream\nendobj\n")
            offsets[img_id] = pf.tell()
            pf.write(f"{img_id} 0 obj\n<< /Type /XObject /Subtype /Image /Width {PAGE_WIDTH} /Height {PAGE_HEIGHT} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length {len(jpg_bytes)} >>\nstream\n".encode("ascii") + jpg_bytes + b"\nendstream\nendobj\n")
        xref_pos = pf.tell()
        pf.write(f"xref\n0 {total_objs}\n0000000000 65535 f \n".encode("ascii"))
        for oid in range(1, total_objs):
            pf.write(f"{offsets[oid]:010d} 00000 n \n".encode("ascii"))
        pf.write(f"trailer\n<< /Size {total_objs} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii"))

# ==========================================
# 6. MAIN CLOUD WORKFLOW EXECUTION
# ==========================================
def run_cloud_job():
    try:
        cfg = json.loads(RAW_PAYLOAD)
    except Exception:
        cfg = {}

    topic = cfg.get("topic", "Complete Unity & C# Masterclass")
    custom_title = cfg.get("title", "").strip()
    author = cfg.get("author", "").strip()
    theme_key = cfg.get("theme", "random")
    cover_prompt = cfg.get("cover_prompt", "").strip()
    lang_style = cfg.get("language", "hinglish")
    volume_mode = cfg.get("volume", "standard")  # 'quick', 'standard', 'mega'

    theme = random.choice(list(THEMES.values())) if theme_key == "random" or theme_key not in THEMES else THEMES[theme_key]
    update_live_status(5, f"Initializing Engine (Theme: {theme['name']})...")

    models = get_working_models(API_KEYS[0])
    os.makedirs(TEMP_DIR, exist_ok=True)

    # Extract explicit chapters if user pasted a syllabus
    ch_pattern = re.compile(r"(?:^|\n)\s*(?:[•\-\*\u2022]|\d+\.)?\s*Chapter\s+(\d+)\s*[:\-–—]\s*([^\n]+)", flags=re.IGNORECASE)
    chapters_plan = []
    for m in ch_pattern.finditer(topic):
        chapters_plan.append({"num": int(m.group(1)), "title": m.group(2).strip()})

    if not chapters_plan:
        target_ch = 1 if volume_mode == "quick" else (18 if volume_mode == "mega" else 7)
        update_live_status(10, f"AI Planning {target_ch} Chapters for your topic...")
        plan_txt = call_ai(models, f"Create a {target_ch}-chapter book outline for: {topic}\nFormat:\nBOOK_TITLE: <title>\nCHAPTER 1: <title>\nCHAPTER 2: <title>")
        if not custom_title:
            tm = re.search(r"BOOK_TITLE\s*:\s*(.+)", plan_txt)
            if tm:
                custom_title = tm.group(1).strip()
        for cm in re.finditer(r"CHAPTER\s+(\d+)\s*:\s*(.+)", plan_txt, flags=re.IGNORECASE):
            chapters_plan.append({"num": int(cm.group(1)), "title": cm.group(2).strip()})
        if not chapters_plan:
            chapters_plan = [{"num": 1, "title": custom_title or "Complete Guide"}]

    book_title = custom_title or "Akhand Masterclass E-Book"
    lang_rule = (
        "Write in energetic, friendly HINGLISH (Hindi + English in Latin script, using words like 'Bhai', 'Dosto', 'Mast', 'Samjho'). Include Real-World Analogies and Line-by-Line breakdowns!"
        if lang_style == "hinglish" else
        "Write in clear, authoritative, professional English with deep explanations and examples."
    )

    total_ch = len(chapters_plan)
    completed_chapters = {}

    for idx, ch in enumerate(chapters_plan, start=1):
        pct = 12 + int((idx / total_ch) * 68)
        update_live_status(pct, f"Writing Chapter {idx}/{total_ch}: {ch['title'][:45]}...")
        prompt = f"""Write Chapter {idx}: "{ch['title']}" for the book "{book_title}" on "{topic[:300]}".
{lang_rule}
Use exact tags below (no JSON):
[SECTION: Core Concept & Intuition]
<3 detailed paragraphs>
[SECTION: Real-World Analogy & Deep Dive]
<3 detailed paragraphs>
[CODE: Complete Implementation]
<15-25 lines of clean code or structured example>
[/CODE]
[SECTION: Step-by-Step Breakdown & Pro Tips]
<3 detailed paragraphs>"""
        raw_ch = call_ai(models, prompt, seed=idx)
        completed_chapters[idx] = {"title": ch["title"], "raw": raw_ch}

    update_live_status(85, "Rendering High-Res Kindle Pages & Cover Artwork...")
    page_files = []

    if volume_mode != "quick":
        cov = build_custom_cover(book_title, f"Complete {total_ch}-Module Edition", author, "KINDLE CLOUD EDITION", cover_prompt, theme, total_ch)
        cov_path = os.path.join(TEMP_DIR, "page_0000.jpg")
        cov.save(cov_path, "JPEG", quality=92)
        cov.close()
        page_files.append(cov_path)

    f_ch_title = load_font(True, 40)
    f_sub = load_font(True, 30)
    f_body = load_font(False, 28)
    f_mono = load_font(False, 24, is_mono=True)

    pno = 1
    img, draw = create_blank_content_page(book_title, "CHAPTER 01", pno, theme, 0.05)
    cy = 158
    max_y = 1560

    def save_and_new_page(sec_label, prog):
        nonlocal pno, img, draw, cy
        p_path = os.path.join(TEMP_DIR, f"page_{pno:04d}.jpg")
        img.save(p_path, "JPEG", quality=91)
        img.close()
        page_files.append(p_path)
        pno += 1
        img, draw = create_blank_content_page(book_title, sec_label, pno, theme, prog)
        cy = 158

    for idx in sorted(completed_chapters.keys()):
        ch_obj = completed_chapters[idx]
        prog = idx / max(1, total_ch)
        sec_label = f"CH {idx:02d}: {ch_obj['title'][:28]}"
        if cy > 950:
            save_and_new_page(sec_label, prog)
        elif idx > 1:
            draw.line([(90, cy + 15), (1150, cy + 15)], fill=theme["accent"], width=3)
            cy += 35

        for hl in wrap_text(draw, f"Chapter {idx}: {ch_obj['title']}", f_ch_title, 1060):
            if cy + 52 > max_y:
                save_and_new_page(sec_label, prog)
            draw_plain(draw, (90, cy), hl, f_ch_title, "#0F172A")
            cy += 52
        cy += 18

        raw_text = ch_obj["raw"]
        # Split code blocks vs normal text
        tokens = re.split(r"(\[CODE[^\]]*\].*?\[/CODE\])", raw_text, flags=re.DOTALL | re.IGNORECASE)
        for tok in tokens:
            if tok.upper().startswith("[CODE"):
                code_body = re.sub(r"^\[CODE[^\]]*\]|\[/CODE\]$", "", tok, flags=re.IGNORECASE).strip()
                c_lines = []
                for rl in code_body.split("\n"):
                    c_lines.extend(wrap_text(draw, rl, f_mono, 960, preserve_indent=True) or [""])
                i_line = 0
                while i_line < len(c_lines):
                    if cy + 160 > max_y:
                        save_and_new_page(sec_label, prog)
                    fit_n = max(3, (max_y - cy - 70) // 34)
                    chunk = c_lines[i_line:i_line + fit_n]
                    bh = 50 + len(chunk) * 34 + 16
                    draw.rounded_rectangle([90, cy, 1150, cy + bh], radius=10, fill="#0D1117", outline="#30363D", width=2)
                    ly = cy + 44
                    for cl in chunk:
                        draw_syntax_highlighted_code_line(draw, (115, ly), cl, f_mono)
                        ly += 34
                    cy += bh + 18
                    i_line += len(chunk)
            else:
                for line in tok.split("\n"):
                    line_s = line.strip()
                    if not line_s:
                        continue
                    if line_s.upper().startswith("[SECTION:"):
                        sh = line_s.split(":", 1)[-1].rstrip("]").strip()
                        if cy + 90 > max_y:
                            save_and_new_page(sec_label, prog)
                        cy += 10
                        for sl in wrap_text(draw, sh, f_sub, 1060):
                            draw_plain(draw, (90, cy), sl, f_sub, theme["sub_color"])
                            cy += 42
                        cy += 8
                    else:
                        for bl in wrap_text(draw, line_s, f_body, 1060):
                            if cy + 41 > max_y:
                                save_and_new_page(sec_label, prog)
                            draw_plain(draw, (90, cy), bl, f_body, "#1E293B")
                            cy += 41
                        cy += 18

    p_path = os.path.join(TEMP_DIR, f"page_{pno:04d}.jpg")
    img.save(p_path, "JPEG", quality=91)
    img.close()
    page_files.append(p_path)

    safe_title = re.sub(r"[^a-zA-Z0-9]+", "_", book_title).strip("_")[:35] or "Cloud_EBook"
    pdf_filename = f"{safe_title}_{JOB_ID}.pdf"

    update_live_status(94, f"Compiling {len(page_files)} Pages into PDF & Uploading...")
    compile_jpegs_to_pdf_zero_ram(page_files, pdf_filename, dpi=150.0)
    shutil.rmtree(TEMP_DIR, ignore_errors=True)

    pdf_url = upload_pdf_to_github_release(pdf_filename)
    update_live_status(100, f"E-Book Ready ({len(page_files)} Pages)!", done=True, pdf_url=pdf_url)

if __name__ == "__main__":
    try:
        run_cloud_job()
    except Exception as exc:
        update_live_status(100, f"Error: {exc}", done=True, error=str(exc))
        raise
