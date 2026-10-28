#!/usr/bin/env python3
import json
import os
import re
import sys
import urllib.request

def normalize_english_markdown(raw_text):
    text = raw_text.strip().replace('""', '"')
    lines = text.split('\n')
    
    # Strip redundant top header like "## Changelog - Q2 v01..."
    if lines and re.match(r'^##?\s*(Changelog|V[0-9]|v[0-9]|[0-9])', lines[0].strip(), re.IGNORECASE):
        lines = lines[1:]
        
    out_blocks = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
            
        m_imp = re.match(r'^>?\s*\*?\*?Important:?\*?\*?\s*(.*)', s, re.IGNORECASE)
        if m_imp:
            out_blocks.append(f"> [!IMPORTANT]\n> {m_imp.group(1).strip()}")
            continue
            
        m_warn = re.match(r'^>?\s*\*?\*?(Notice|Warning):?\*?\*?\s*(.*)', s, re.IGNORECASE)
        if m_warn:
            out_blocks.append(f"> [!WARNING]\n> {m_warn.group(2).strip()}")
            continue
            
        out_blocks.append(s)
        
    result = "\n\n".join(out_blocks).strip()
    result = re.sub(r'(\n-\s*[^\n]+)\n\n(-\s*)', r'\1\n\2', result)
    result = re.sub(r'(#{1,6}[^\n]+)\n\n(-\s*)', r'\1\n\2', result)
    return result

def get_dynamic_free_models():
    url = "https://openrouter.ai/api/v1/models"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    blacklist = ['safety', 'guard', 'embed', 'clip', 'moderate', 'router']
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            models = [
                m['id'] for m in data.get('data', [])
                if m.get('id', '').endswith(':free') and not any(b in m['id'].lower() for b in blacklist)
            ]
            print(f"Dynamically discovered {len(models)} free text models on OpenRouter:", flush=True)
            for m in models:
                print(f"  - {m}", flush=True)
            return models
    except Exception as e:
        print(f"Warning: could not fetch dynamic models: {e}. Using fallback list.", file=sys.stderr, flush=True)
        return [
            "xingchen-agi/xing4.0-29b-a4b:free",
            "nvidia/nemotron-3.5-lightning:free",
            "google/gemma-4-26b-a4b-it:free",
            "liquid/lfm-2.5-2.6b:free",
            "recursive/flint-v1.0:free"
        ]

def translate_with_model(model, raw_text, api_key, system_instruction):
    req_body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": f"Changelog to translate:\n\n{raw_text}"}
        ],
        "temperature": 0.1,
        "stream": False
    }

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(req_body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://github.com/kokoc26/q2-test",
            "X-Title": "QIDI Firmware Translation"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            return content
    except Exception as e:
        return f"ERROR: {e}"

def clean_translated_markdown(text):
    # Ensure empty line between consecutive GitHub alerts
    text = re.sub(r'(> [^\n]+)\n(> \[!(?:IMPORTANT|WARNING|NOTE|TIP|CAUTION)\])', r'\1\n\n\2', text)
    # Ensure empty line after alert before header
    text = re.sub(r'(> [^\n]+)\n(#{1,6}\s)', r'\1\n\n\2', text)
    # Ensure no extra blank lines between bullet points
    text = re.sub(r'(\n-\s*[^\n]+)\n\n(-\s*)', r'\1\n\2', text)
    # Wrap error codes like QDC_100_005_000_001 in backticks if not already
    text = re.sub(r'(?<!`)\b(QDC_[0-9A-Z_]+)\b(?!`)', r'`\1`', text)
    return text.strip()

def main():
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        print("::error::OPENROUTER_API_KEY secret is missing or empty!", file=sys.stderr)
        sys.exit(1)

    input_file = sys.argv[1] if len(sys.argv) > 1 else "raw_changelog.txt"
    with open(input_file, "r", encoding="utf-8") as f:
        raw_text = f.read().strip()

    if not raw_text:
        print("Input text is empty. Creating fallback descriptions.")
        desc_en = "No changelog provided."
        desc_ru = "Информация об изменениях отсутствует."
        with open("firmware_description_en.txt", "w", encoding="utf-8") as f:
            f.write(desc_en + "\n")
        with open("firmware_description_ru.txt", "w", encoding="utf-8") as f:
            f.write(desc_ru + "\n")
        return

    # 1. Normalize English changelog
    desc_en = normalize_english_markdown(raw_text)
    with open("firmware_description_en.txt", "w", encoding="utf-8") as f:
        f.write(desc_en + "\n")
    print("Normalized English changelog saved to firmware_description_en.txt")

    # 2. Translate to Russian with dynamically discovered free models
    system_instruction = (
        "You are a professional technical translator specializing in 3D printing, Klipper, Moonraker, and Fluidd firmware.\n"
        "Translate the provided changelog from English into natural, grammatically correct Russian.\n\n"
        "CRITICAL RULES:\n"
        "1. FAITHFUL TRANSLATION:\n"
        "   - Translate ONLY what is present in the source text. NEVER invent, add, or assume new sections, bullet points, or optimizations.\n"
        "   - If a section (like 'Optimized' or 'New') is NOT in the source text, DO NOT include it.\n\n"
        "2. 3D PRINTING TERMINOLOGY:\n"
        "   - 'filament' -> 'филамент' (NEVER 'нить накала' or 'нить накаливания').\n"
        "   - 'homing' -> 'парковка (homing)' or 'исходное положение'.\n"
        "   - 'unload filament' -> 'выгрузите филамент' / 'извлеките филамент'.\n"
        "   - 'load' / 'Load' -> 'загрузка' / `Load`.\n"
        "   - 'nozzle' -> 'сопло'.\n"
        "   - 'boxes' (in QIDI context) -> 'модулей BOX' / 'устройств BOX'.\n"
        "   - 'paging with multiple boxes' -> 'переключение страниц при нескольких подключенных модулях BOX'.\n"
        "   - DO NOT translate proper names, software, or hardware: Fluidd, Klipper, QIDI Studio, QIDI Maker, BOX, error codes (e.g. QDC_100_005_000_001), UI.\n\n"
        "3. GITHUB ALERT FORMATTING:\n"
        "   - If the source contains 'Important:' or '> Important:' or '> [!IMPORTANT]', convert it into GitHub Alert syntax:\n"
        "     > [!IMPORTANT]\n"
        "     > <Russian translation>\n\n"
        "   - If the source contains 'Notice:' or 'Warning:' or '> [!WARNING]', convert it into GitHub Alert syntax:\n"
        "     > [!WARNING]\n"
        "     > <Russian translation>\n\n"
        "   - Ensure each line inside an alert block starts with '> ' and add a blank line between separate alert blocks.\n\n"
        "4. HEADERS:\n"
        "   - '### Fixed' -> '### Исправлено'\n"
        "   - '### New' -> '### Новое'\n"
        "   - '### Optimized' -> '### Оптимизировано'\n"
        "   (Only translate the headers that actually exist in the source text!)\n\n"
        "5. OUTPUT FORMAT:\n"
        "   - Output ONLY the final translated Markdown. No preamble, no postscript, no code fences wrapping the entire output."
    )

    models = get_dynamic_free_models()
    translated_text = None
    chosen_model = None

    for model in models:
        print(f"\nTrying model: {model}...", flush=True)
        out = translate_with_model(model, desc_en, api_key, system_instruction)
        if out.startswith("ERROR:"):
            print(f"  -> {out}", flush=True)
            continue
            
        if len(out) > 30 and "User Safety" not in out:
            translated_text = clean_translated_markdown(out)
            chosen_model = model
            print(f"  -> SUCCESS with model: {model}!", flush=True)
            break
        else:
            print(f"  -> Model returned unexpected content: {out[:60]}", flush=True)

    if not translated_text:
        print("::error::All free models on OpenRouter failed or were rate-limited!", file=sys.stderr)
        sys.exit(1)

    with open("firmware_description_ru.txt", "w", encoding="utf-8") as f:
        f.write(translated_text + "\n")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output and os.path.exists(github_output):
        with open(github_output, "a", encoding="utf-8") as f:
            f.write(f"model_used={chosen_model}\n")
            f.write("description_ru<<EOF_RU\n" + translated_text + "\nEOF_RU\n")
            f.write("description_en<<EOF_EN\n" + desc_en + "\nEOF_EN\n")

    print(f"\nAll done! Successfully translated using dynamically selected model: {chosen_model}")

if __name__ == "__main__":
    main()
