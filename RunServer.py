#    MMS Player - procedural animation of Sign Language avatars
#    Copyright (C) 2024 German Research Center for Artificial Intelligence (DFKI)
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.

import csv
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock, Thread

from flask import Flask, Response, jsonify, request, send_from_directory

BLENDER_EXE = os.getenv('BLENDER_EXE')
if BLENDER_EXE is None:
    raise Exception("Environment variable BLENDER_EXE is not set.")

CORPUS_DIR = os.getenv("AVASAG_CORPUS_DIR")
if CORPUS_DIR is None:
    raise Exception("Environment variable AVASAG_CORPUS_DIR is not set.")

PROJECT_ROOT = Path(__file__).resolve().parent
MAIN_SCRIPT = PROJECT_ROOT / "main.py"
if not MAIN_SCRIPT.exists():
    raise Exception(f"Main script '{MAIN_SCRIPT}' not found.")

JSON_EXPORTER_SCRIPT = PROJECT_ROOT / "exporter" / "json_exporter.py"
if not JSON_EXPORTER_SCRIPT.exists():
    JSON_EXPORTER_SCRIPT = PROJECT_ROOT / "Docs" / "exporter" / "json_exporter.py"
if not JSON_EXPORTER_SCRIPT.exists():
    raise Exception(f"JSON exporter script '{JSON_EXPORTER_SCRIPT}' not found.")


GENERATED_CORPUS_PATH = Path(CORPUS_DIR) / "generated"

if not GENERATED_CORPUS_PATH.exists():
    raise Exception(f"Generated dir in Corpus path '{GENERATED_CORPUS_PATH}' doesn't exist.")


RENDER_OUTPUT_DIR = PROJECT_ROOT / "renders"
RENDER_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

LIVE_RENDER_DIR = RENDER_OUTPUT_DIR / "live_cache"
LIVE_RENDER_DIR.mkdir(parents=True, exist_ok=True)

PRESET_RENDER_DIR = RENDER_OUTPUT_DIR / "preset_library"
PRESET_RENDER_DIR.mkdir(parents=True, exist_ok=True)

RENDER_LOCK = Lock()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()


try:
        from basic_english_sentences import PHRASES as HARD_PHRASES
except Exception:
        HARD_PHRASES = []


WORD_TO_GLOSS = {
        "hello": ["HALLO"],
        "hi": ["HALLO"],
        "i": ["ICH"],
        "me": ["ICH"],
    "my": ["ICH"],
        "deaf": ["GEBAERDEN"],
        "sign": ["GEBAERDEN"],
        "signing": ["GEBAERDEN"],
    "gesture": ["GEBAERDEN"],
        "slow": ["LANGSAM"],
        "slowly": ["LANGSAM"],
    "repeat": ["LANGSAM"],
        "please": ["BITTE"],
        "thanks": ["DANKE"],
        "thank": ["DANKE"],
        "doctor": ["ARZT"],
    "hospital": ["ARZT"],
    "clinic": ["ARZT"],
        "sick": ["KRANK"],
        "appointment": ["TREFFEN(-hingehen)"],
    "book": ["TREFFEN(-hingehen)"],
    "booking": ["TREFFEN(-hingehen)"],
    "checkin": ["TREFFEN(-hingehen)"],
        "meet": ["TREFFEN(-hingehen)"],
        "today": ["HEUTE"],
        "later": ["SPAETER"],
    "wait": ["SPAETER"],
    "leave": ["SPAETER"],
        "time": ["ZEIT", "UHR"],
        "reply": ["BESCHEID(-auf-mich)"],
    "message": ["BESCHEID(-auf-mich)"],
    "contact": ["BESCHEID(-auf-mich)"],
        "confirm": ["OK"],
        "confirmed": ["OK"],
        "ok": ["OK"],
        "understand": ["VERSTEHEN"],
        "can": ["KANN"],
        "cannot": ["KANN-NICHT"],
        "cant": ["KANN-NICHT"],
        "urgent": ["ZEITNAH"],
}


GLOSS_TO_ENGLISH = {
    "HALLO": "hello",
    "ICH": "i",
    "GEBAERDEN": "deaf",
    "LANGSAM": "slow",
    "BITTE": "please",
    "DANKE": "thank you",
    "ARZT": "doctor",
    "KRANK": "sick",
    "TREFFEN(-hingehen)": "appointment",
    "HEUTE": "today",
    "SPAETER": "later",
    "ZEIT": "time",
    "UHR": "o'clock",
    "BESCHEID(-auf-mich)": "reply",
    "OK": "ok",
    "VERSTEHEN": "understand",
    "KANN": "can",
    "KANN-NICHT": "cannot",
    "ZEITNAH": "urgent",
}


def normalize_text(text: str) -> str:
        """Lowercase and simplify punctuation for robust phrase matching."""
        normalized = text.lower().strip()
        normalized = re.sub(r"[^a-z0-9 ]+", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized)
        return normalized


def gloss_token_to_english(gloss_token: str) -> str:
    """Map corpus gloss tokens to readable English labels for UI display."""
    if ":" in gloss_token:
        category, value = gloss_token.split(":", 1)
        if category == "num":
            return value
        if category == "uhr":
            return f"minute {value}"
        if category == "signs":
            gloss_token = value
        else:
            return f"{category} {value}".replace("_", " ").lower()

    mapped = GLOSS_TO_ENGLISH.get(gloss_token)
    if mapped:
        return mapped

    cleaned = re.sub(r"[^a-zA-Z0-9]+", " ", gloss_token).strip().lower()
    return cleaned if cleaned else gloss_token


def glosses_to_english(glosses):
    return [gloss_token_to_english(gloss) for gloss in glosses]


def gloss_token_to_path(gloss_token: str) -> Path:
        """Map a gloss token such as 'signs:HALLO' or 'num:10' to its blend path."""
        if ":" in gloss_token:
                category, name = gloss_token.split(":", 1)
        else:
                category, name = "signs", gloss_token

        return GENERATED_CORPUS_PATH / category / "trimmed" / f"{name}.blend"


def gloss_exists(gloss_token: str) -> bool:
        return gloss_token_to_path(gloss_token).exists()


def filter_available_glosses(glosses):
        """Keep only known corpus glosses and collapse immediate duplicates."""
        output = []
        for gloss in glosses:
                if not gloss_exists(gloss):
                        continue
                if len(output) > 0 and output[-1] == gloss:
                        continue
                output.append(gloss)
        return output


def build_phrase_to_gloss_index():
        """Load hardcoded phrase mappings from basic_english_sentences.py."""
        mapping = {}
        for item in HARD_PHRASES:
                english = str(item.get("english", ""))
                glosses = item.get("glosses", [])
                if not english or not isinstance(glosses, list) or len(glosses) == 0:
                        continue
                key = normalize_text(english)
                key = key.rstrip(".")
                mapping[key] = filter_available_glosses(glosses)
        return mapping


PHRASE_TO_GLOSSES = build_phrase_to_gloss_index()


def text_to_glosses(text: str):
        """Convert plain text to a corpus-backed gloss sequence."""
        normalized = normalize_text(text)

        if normalized in PHRASE_TO_GLOSSES and len(PHRASE_TO_GLOSSES[normalized]) > 0:
                return PHRASE_TO_GLOSSES[normalized]

        raw_glosses = []
        for token in normalized.split(" "):
                if token.isdigit():
                        number_gloss = f"num:{token}"
                        if gloss_exists(number_gloss):
                                raw_glosses.append(number_gloss)
                        continue

                for gloss in WORD_TO_GLOSS.get(token, []):
                        raw_glosses.append(gloss)

        return filter_available_glosses(raw_glosses)[:28]


def write_mms_csv(mms_path: Path, glosses, duration=0.8, transition=0.15):
        """Write a small MMS CSV from a gloss list using relative timing fields."""
        with open(mms_path, "w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow(["maingloss", "framestart", "frameend", "duration", "transition"])
                for i, gloss in enumerate(glosses):
                        writer.writerow([gloss, 0, 0, duration, 0 if i == 0 else transition])


def normalize_phrase_id(raw_text: str) -> str:
    normalized = re.sub(r"[^a-z0-9_]+", "_", raw_text.lower()).strip("_")
    return normalized if normalized else "phrase"


def build_preset_phrase_library():
    library = []
    used_ids = set()

    for index, item in enumerate(HARD_PHRASES):
        english = str(item.get("english", "")).strip()
        glosses = item.get("glosses", [])
        if not english or not isinstance(glosses, list) or len(glosses) == 0:
            continue

        available_glosses = filter_available_glosses(glosses)
        if len(available_glosses) == 0:
            continue

        raw_id = str(item.get("id", f"phrase_{index + 1}"))
        phrase_id = normalize_phrase_id(raw_id)

        if phrase_id in used_ids:
            suffix = 2
            while f"{phrase_id}_{suffix}" in used_ids:
                suffix += 1
            phrase_id = f"{phrase_id}_{suffix}"

        used_ids.add(phrase_id)
        category = str(item.get("category", "general")).strip() or "general"
        rel_path = f"preset_library/{phrase_id}.mp4"

        library.append({
            "id": phrase_id,
            "category": category,
            "english": english,
            "glosses": available_glosses,
            "englishGlosses": glosses_to_english(available_glosses),
            "videoRelPath": rel_path,
            "videoUrl": f"/renders/{rel_path}",
        })

    return library


def preset_output_path(phrase):
    return RENDER_OUTPUT_DIR / phrase["videoRelPath"]


def preset_payload(phrase):
    output_path = preset_output_path(phrase)
    return {
        "id": phrase["id"],
        "category": phrase["category"],
        "english": phrase["english"],
        "glosses": phrase["glosses"],
        "englishGlosses": phrase.get("englishGlosses", glosses_to_english(phrase["glosses"])),
        "ready": output_path.exists(),
        "videoUrl": phrase["videoUrl"],
    }


def live_cache_output_path(glosses):
    cache_key = "|".join(glosses)
    cache_id = hashlib.sha1(cache_key.encode("utf-8")).hexdigest()[:12]
    output_name = f"live_{cache_id}.mp4"
    output_path = LIVE_RENDER_DIR / output_name
    video_url = f"/renders/live_cache/{output_name}"
    return output_path, cache_id, video_url


def render_glosses_to_mp4(glosses, output_path: Path):
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with TemporaryDirectory(prefix="MMStext") as tmp_dir:
        mms_path = Path(tmp_dir) / "input.mms.csv"
        write_mms_csv(mms_path, glosses)
        generate_mp4_animation_data(mms_path, True, output_path)


def ensure_preset_video(phrase):
    output_path = preset_output_path(phrase)
    if output_path.exists():
        return output_path

    with RENDER_LOCK:
        if output_path.exists():
            return output_path

        print(f"[Preset] Rendering '{phrase['id']}'")
        render_glosses_to_mp4(phrase["glosses"], output_path)

    return output_path


def build_missing_preset_videos():
    total = len(PRESET_PHRASE_LIBRARY)
    built = 0
    existing = 0
    failures = []

    for index, phrase in enumerate(PRESET_PHRASE_LIBRARY, start=1):
        output_path = preset_output_path(phrase)
        if output_path.exists():
            existing += 1
            continue

        print(f"[Preset] {index}/{total} building '{phrase['id']}'")
        try:
            ensure_preset_video(phrase)
            built += 1
        except Exception as error:
            failures.append({"id": phrase["id"], "error": str(error)})
            print(f"[Preset] Failed '{phrase['id']}': {error}")

    ready = sum(1 for phrase in PRESET_PHRASE_LIBRARY if preset_output_path(phrase).exists())
    return {
        "total": total,
        "ready": ready,
        "built": built,
        "existing": existing,
        "failed": len(failures),
        "errors": failures[:10],
    }


def start_background_preset_build():
    if len(PRESET_PHRASE_LIBRARY) == 0:
        return

    def _build_worker():
        print(f"Prebuilding preset library ({len(PRESET_PHRASE_LIBRARY)} phrases) in background...")
        summary = build_missing_preset_videos()
        print(
            "Preset library ready: "
            f"{summary['ready']}/{summary['total']} "
            f"(built={summary['built']}, existing={summary['existing']}, failed={summary['failed']})"
        )

    Thread(target=_build_worker, daemon=True).start()


PRESET_PHRASE_LIBRARY = build_preset_phrase_library()
PRESET_PHRASES_BY_ID = {item["id"]: item for item in PRESET_PHRASE_LIBRARY}
PRESET_PHRASE_BY_TEXT = {
    normalize_text(item["english"]).rstrip("."): item
    for item in PRESET_PHRASE_LIBRARY
}

if len(PRESET_PHRASE_LIBRARY) < 25:
    print(f"Warning: only {len(PRESET_PHRASE_LIBRARY)} preset phrases are available.")


DEMO_HTML_TEMPLATE = """<!doctype html>
<html lang=\"en\">
<head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>MMS Instant Phrase Library</title>
    <link rel=\"preconnect\" href=\"https://fonts.googleapis.com\" />
    <link rel=\"preconnect\" href=\"https://fonts.gstatic.com\" crossorigin />
    <link href=\"https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap\" rel=\"stylesheet\" />
    <style>
        :root {
            --bg-a: #081822;
            --bg-b: #132532;
            --panel: rgba(12, 27, 36, 0.82);
            --border: rgba(165, 224, 236, 0.25);
            --text: #ecf8fc;
            --muted: #b4cfd8;
            --accent: #ffc95f;
            --accent-2: #57ddd2;
            --ok: #8ae863;
            --warn: #ff8f7e;
        }

        * { box-sizing: border-box; }

        body {
            margin: 0;
            min-height: 100vh;
            color: var(--text);
            font-family: \"IBM Plex Sans\", sans-serif;
            background:
                radial-gradient(circle at 10% 14%, #1b4051 0%, transparent 42%),
                radial-gradient(circle at 84% 14%, #274553 0%, transparent 44%),
                linear-gradient(165deg, var(--bg-a), var(--bg-b));
        }

        .container {
            width: min(1020px, 96vw);
            margin: 20px auto 28px;
        }

        .panel {
            background: var(--panel);
            border: 1px solid var(--border);
            border-radius: 14px;
            padding: 16px;
            box-shadow: 0 14px 36px rgba(0, 0, 0, 0.33);
        }

        h1 {
            margin: 0;
            font-family: \"Space Grotesk\", sans-serif;
            font-size: clamp(1.25rem, 2.8vw, 2rem);
            letter-spacing: 0.01em;
        }

        p.subtitle {
            margin: 8px 0 14px;
            color: var(--muted);
        }

        h2.section-title {
            margin: 18px 0 10px;
            font-size: 1rem;
            font-family: \"Space Grotesk\", sans-serif;
            color: #d8edf4;
        }

        .text-input {
            width: 100%;
            border-radius: 10px;
            border: 1px solid rgba(164, 216, 229, 0.3);
            background: rgba(5, 14, 20, 0.9);
            color: var(--text);
            padding: 13px 12px;
            font-size: 1rem;
        }

        .text-input:focus {
            outline: none;
            border-color: var(--accent-2);
            box-shadow: 0 0 0 3px rgba(87, 221, 210, 0.18);
        }

        .toolbar {
            margin-top: 12px;
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            align-items: center;
        }

        button {
            border: none;
            border-radius: 10px;
            padding: 10px 14px;
            font-weight: 600;
            cursor: pointer;
            color: #0d2530;
            background: linear-gradient(140deg, var(--accent), #e3a638);
            transition: transform 160ms ease, filter 160ms ease;
        }

        button:hover {
            transform: translateY(-1px);
            filter: brightness(1.06);
        }

        button:disabled {
            cursor: wait;
            filter: grayscale(0.35) brightness(0.88);
        }

        label.auto {
            color: var(--muted);
            font-size: 0.94rem;
        }

        .status {
            margin-left: auto;
            border: 1px solid rgba(164, 216, 229, 0.35);
            border-radius: 999px;
            padding: 7px 11px;
            color: var(--muted);
            background: rgba(8, 18, 24, 0.84);
            font-size: 0.86rem;
        }

        .status.busy { color: #ffe5b3; border-color: rgba(255, 201, 95, 0.45); }
        .status.ok { color: #d6f8c2; border-color: rgba(138, 232, 99, 0.48); }
        .status.err { color: #ffd1c9; border-color: rgba(255, 143, 126, 0.48); }

        .quick {
            margin-top: 13px;
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }

        .quick button {
            color: var(--text);
            background: rgba(16, 37, 48, 0.95);
            border: 1px solid rgba(158, 214, 226, 0.26);
            font-weight: 500;
            padding: 8px 11px;
        }

        .quick button.not-ready {
            border-color: rgba(255, 143, 126, 0.48);
            color: #ffd8d0;
        }

        .grid {
            margin-top: 14px;
            display: grid;
            grid-template-columns: 1fr;
            gap: 14px;
        }

        .meta, .video-wrap {
            border: 1px solid rgba(158, 214, 226, 0.2);
            background: rgba(4, 11, 15, 0.86);
            border-radius: 12px;
            padding: 12px;
        }

        .meta h2, .video-wrap h2 {
            margin: 0 0 8px;
            font-size: 1rem;
            font-family: \"Space Grotesk\", sans-serif;
        }

        pre {
            margin: 0;
            color: #c5e5ed;
            font-size: 0.9rem;
            white-space: pre-wrap;
            word-break: break-word;
        }

        video {
            width: 100%;
            border-radius: 10px;
            background: #000;
            min-height: 260px;
        }

        @media (min-width: 980px) {
            .grid {
                grid-template-columns: 0.9fr 1.3fr;
            }
        }
    </style>
</head>
<body>
    <div class=\"container\">
        <section class=\"panel\">
            <h1>Instant Phrase Library + Live Custom Render</h1>
            <p class=\"subtitle\">Use pre-rendered hardcoded sentences for instant playback. Keep the live renderer for one custom sentence only.</p>

            <div class=\"toolbar\">
                <button id=\"buildLibraryBtn\">Build Missing Instant Clips</button>
                <div id=\"libraryStatus\" class=\"status\">Library loading...</div>
            </div>

            <div id=\"quickButtons\" class=\"quick\"></div>

            <h2 class=\"section-title\">Live Render (single custom sentence)</h2>

            <input id=\"textInput\" class=\"text-input\" type=\"text\" placeholder=\"Type message. Example: I am sick doctor appointment today\" />

            <div class=\"toolbar\">
                <button id=\"animateBtn\">Render Live Sentence</button>
                <div id=\"status\" class=\"status\">Idle</div>
            </div>

            <div class=\"grid\">
                <div class=\"meta\">
                    <h2>Current Glosses (English)</h2>
                    <pre id=\"glossOut\">-</pre>
                </div>

                <div class=\"video-wrap\">
                    <h2>Rendered Video Preview</h2>
                    <video id=\"videoOut\" controls autoplay loop muted playsinline></video>
                </div>
            </div>
        </section>
    </div>

    <script>
        const presetPhrases = __PRESET_PHRASES__;
        const textInput = document.getElementById("textInput");
        const animateBtn = document.getElementById("animateBtn");
        const buildLibraryBtn = document.getElementById("buildLibraryBtn");
        const statusEl = document.getElementById("status");
        const libraryStatusEl = document.getElementById("libraryStatus");
        const glossOut = document.getElementById("glossOut");
        const videoOut = document.getElementById("videoOut");
        const quickButtons = document.getElementById("quickButtons");

        let activeRequest = 0;

        function setStatus(text, kind) {
            statusEl.textContent = text;
            statusEl.className = "status";
            if (kind) {
                statusEl.classList.add(kind);
            }
        }

        function setLibraryStatus(text, kind) {
            libraryStatusEl.textContent = text;
            libraryStatusEl.className = "status";
            if (kind) {
                libraryStatusEl.classList.add(kind);
            }
        }

        function updateLibraryStatus() {
            const readyCount = presetPhrases.filter((item) => item.ready).length;
            const kind = readyCount === presetPhrases.length ? "ok" : "busy";
            setLibraryStatus(`Library ${readyCount}/${presetPhrases.length} ready`, kind);
        }

        function getPresetById(presetId) {
            return presetPhrases.find((item) => item.id === presetId);
        }

        async function playVideo(url) {
            videoOut.src = url + "?t=" + Date.now();
            try {
                await videoOut.play();
            } catch (_ignored) {
                // Autoplay can fail on some browsers, controls stay available.
            }
        }

        function renderQuickButtons() {
            quickButtons.innerHTML = "";
            for (const phrase of presetPhrases) {
                const btn = document.createElement("button");
                btn.type = "button";
                const category = String(phrase.category || "general").toUpperCase();
                btn.textContent = `[${category}] ${phrase.english}`;
                if (!phrase.ready) {
                    btn.classList.add("not-ready");
                }
                btn.addEventListener("click", () => {
                    runPresetAnimation(phrase.id);
                });
                quickButtons.appendChild(btn);
            }
        }

        async function refreshPresetStatus() {
            try {
                const response = await fetch("/api/presets");
                const data = await response.json();
                if (!response.ok) {
                    throw new Error(data.error || "Preset list failed");
                }

                const byId = new Map((data.presets || []).map((item) => [item.id, item]));
                for (const phrase of presetPhrases) {
                    const fresh = byId.get(phrase.id);
                    if (!fresh) {
                        continue;
                    }
                    phrase.ready = !!fresh.ready;
                    phrase.videoUrl = fresh.videoUrl;
                    phrase.glosses = fresh.glosses || phrase.glosses;
                    phrase.englishGlosses = fresh.englishGlosses || phrase.englishGlosses;
                }

                renderQuickButtons();
                updateLibraryStatus();
            } catch (error) {
                setLibraryStatus(String(error.message || error), "err");
            }
        }

        async function runPresetAnimation(presetId) {
            const phrase = getPresetById(presetId);
            const current = ++activeRequest;
            setStatus("Loading instant clip...", "busy");

            try {
                const response = await fetch(`/api/presets/${encodeURIComponent(presetId)}/mp4`, {
                    method: "POST",
                });

                const data = await response.json();
                if (current !== activeRequest) {
                    return;
                }

                if (!response.ok) {
                    throw new Error(data.error || "Preset render failed");
                }

                if (phrase) {
                    phrase.ready = true;
                    phrase.videoUrl = data.videoUrl;
                }
                renderQuickButtons();
                updateLibraryStatus();

                glossOut.textContent = (data.englishGlosses || data.glosses || []).join(" ");
                await playVideo(data.videoUrl);
                setStatus("Instant clip ready", "ok");
            } catch (error) {
                setStatus("Error", "err");
                glossOut.textContent = String(error.message || error);
            }
        }

        async function buildLibrary() {
            buildLibraryBtn.disabled = true;
            setLibraryStatus("Building missing clips...", "busy");

            try {
                const response = await fetch("/api/presets/build", { method: "POST" });
                const data = await response.json();
                if (!response.ok) {
                    throw new Error(data.error || "Build failed");
                }

                await refreshPresetStatus();
                const kind = data.ready === data.total ? "ok" : "busy";
                setLibraryStatus(`Library ${data.ready}/${data.total} ready`, kind);
            } catch (error) {
                setLibraryStatus(String(error.message || error), "err");
            } finally {
                buildLibraryBtn.disabled = false;
            }
        }

        async function runAnimation() {
            const text = textInput.value.trim();
            if (!text) {
                setStatus("Type some text", "err");
                glossOut.textContent = "-";
                return;
            }

            const current = ++activeRequest;
            animateBtn.disabled = true;
            setStatus("Rendering...", "busy");

            try {
                const response = await fetch("/api/text/mp4", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ text }),
                });

                const data = await response.json();
                if (current !== activeRequest) {
                    return;
                }

                if (!response.ok) {
                    throw new Error(data.error || "Render failed");
                }

                glossOut.textContent = (data.englishGlosses || data.glosses || []).join(" ");
                await playVideo(data.videoUrl);
                setStatus("Done", "ok");
            } catch (error) {
                setStatus("Error", "err");
                glossOut.textContent = String(error.message || error);
            } finally {
                animateBtn.disabled = false;
            }
        }

        animateBtn.addEventListener("click", runAnimation);
        buildLibraryBtn.addEventListener("click", buildLibrary);
        textInput.addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
                runAnimation();
            }
        });

        renderQuickButtons();
        updateLibraryStatus();
        refreshPresetStatus().then(() => {
            const readyPreset = presetPhrases.find((item) => item.ready);
            if (readyPreset) {
                runPresetAnimation(readyPreset.id);
            }
        });
    </script>
</body>
</html>
"""


#
# Initialize the Flask server
print("Creating server...")
app = Flask(__name__)


@app.route("/")
def home():
    return (
        '<p>MMS-Player rendering service is running.</p>'
        '<p>Open <a href="/demo">/demo</a> for text-to-animation interface.</p>'
    )


@app.route("/demo")
def demo_page():
    html = DEMO_HTML_TEMPLATE.replace(
        "__PRESET_PHRASES__",
        json.dumps([preset_payload(item) for item in PRESET_PHRASE_LIBRARY], ensure_ascii=False),
    )
    return Response(html, mimetype="text/html")


@app.route("/api/presets", methods=["GET"])
def list_presets():
    presets = [preset_payload(item) for item in PRESET_PHRASE_LIBRARY]
    ready = sum(1 for item in presets if item["ready"])
    return jsonify({
        "total": len(presets),
        "ready": ready,
        "presets": presets,
    })


@app.route("/api/presets/<preset_id>/mp4", methods=["POST"])
def fetch_preset_mp4(preset_id):
    phrase = PRESET_PHRASES_BY_ID.get(preset_id)
    if phrase is None:
        return jsonify({"error": f"Unknown preset id '{preset_id}'."}), 404

    ensure_preset_video(phrase)
    return jsonify({
        "id": phrase["id"],
        "english": phrase["english"],
        "category": phrase["category"],
        "glosses": phrase["glosses"],
        "englishGlosses": phrase.get("englishGlosses", glosses_to_english(phrase["glosses"])),
        "videoUrl": phrase["videoUrl"],
        "preset": True,
    })


@app.route("/api/presets/build", methods=["POST"])
def build_presets():
    if len(PRESET_PHRASE_LIBRARY) == 0:
        return jsonify({"error": "No preset phrases available."}), 400

    summary = build_missing_preset_videos()
    return jsonify(summary)


# @app.route("/api/<filename>/<is_complete>/<is_custom>")
@app.route("/api/corpus/sentence/animation/<sentence_number>/")
# (Deprecated) Kept for backwards compatibility
@app.route("/api/<filename>", defaults={"is_complete": True, "is_custom": False})
def animation_get(sentence_number):
    filepath = GENERATED_CORPUS_PATH / "mms" / (sentence_number + ".mms")
    print(f"GET: Generating for file '{filepath}'")
    return generate_json_animation_data(mms_filepath=filepath, use_relative_time=False)


@app.route("/api/mms/animation", methods=["POST"])
def fetch_json_from_file():

    # print("Files in request: ", len(request.files))
    # for i, fname in enumerate(request.files):
    #     print(i, fname)

    file = request.files["file"]
    filename = file.filename

    tmp_dir = TemporaryDirectory(prefix="MMSserver", suffix="MMSfile")
    tmp_path = Path(tmp_dir.name)
    save_path = tmp_path / filename
    print("Saving file..... {}".format(save_path))
    file.save(save_path)
    print("File saved. Converting ...")

    return generate_json_animation_data(save_path, True)


def generate_json_animation_data(mms_filepath, use_relative_time):

    tmp_dir = TemporaryDirectory(prefix="MMSserver")
    tmp_path = Path(tmp_dir.name)

    # Convert /path/to/file.mms --> /tmp/path/to/file.blend
    export_blend_path = tmp_path / mms_filepath.with_suffix(".blend").name

    # Synthesize the MMS and save it into a temporary .blend scene file.
    print("Exporting to", export_blend_path)
    args = [
        BLENDER_EXE,
        "--background",
        "--python",
        str(MAIN_SCRIPT),
        "--",
        "--source-mms-file",
        str(mms_filepath),
        "--corpus-generated-directory",
        str(GENERATED_CORPUS_PATH),
        "--export-blend",
        str(export_blend_path),
    ]
    if use_relative_time:
        args.extend(["--use-relative-time"])

    p = subprocess.Popen(args, cwd=str(PROJECT_ROOT))
    p.wait()

    if p.returncode != 0:
        raise Exception(f"MMS synthesis failed for '{str(mms_filepath)}'.")

    if not export_blend_path.exists():
        raise Exception(f"File '{str(export_blend_path)} was not generated.")

    # Export the blend scene as JSON animation data
    json_path = export_blend_path.with_suffix(".json")  # f"/tmp/sentence_{str(mms_filepath)}.json"
    p = subprocess.Popen(
        [
            BLENDER_EXE,
            "--background",
            "--python",
            str(JSON_EXPORTER_SCRIPT),
            "--",
            "--blend-path",
            str(export_blend_path),
            "--json-path",
            str(json_path),
        ],
        cwd=str(PROJECT_ROOT),
    )
    # print the stdout from the process
    p.wait()

    if p.returncode != 0:
        raise Exception(f"JSON export failed for '{str(export_blend_path)}'.")

    with open(json_path, "r") as fstream:
        string = json.load(fstream)

    return string


def generate_mp4_animation_data(mms_filepath: Path, use_relative_time: bool, mp4_path: Path):
    """Run the Blender pipeline and directly export an MP4 file."""
    args = [
        BLENDER_EXE,
        "--background",
        "--python",
        str(MAIN_SCRIPT),
        "--",
        "--source-mms-file",
        str(mms_filepath),
        "--corpus-generated-directory",
        str(GENERATED_CORPUS_PATH),
        "--export-mp4",
        str(mp4_path),
    ]

    if use_relative_time:
        args.extend(["--use-relative-time"])

    p = subprocess.Popen(args, cwd=str(PROJECT_ROOT))
    p.wait()

    if p.returncode != 0 or not mp4_path.exists():
        raise Exception(f"Video render failed for '{str(mms_filepath)}'.")


def render_custom_text_to_live_response(text: str):
    """Render custom text through live cache path only (no preset shortcut)."""
    glosses = text_to_glosses(text)
    if len(glosses) == 0:
        return jsonify({
            "error": "No supported words found in text.",
            "hint": "Use basic communication words like hello, please, doctor, appointment, time, reply, today.",
        }), 400

    output_path, request_id, video_url = live_cache_output_path(glosses)
    cache_hit = output_path.exists()

    if not cache_hit:
        # Blender background rendering can conflict when many requests arrive at once.
        with RENDER_LOCK:
            if not output_path.exists():
                render_glosses_to_mp4(glosses, output_path)
            cache_hit = output_path.exists()

    return jsonify({
        "id": request_id,
        "text": text,
        "glosses": glosses,
        "englishGlosses": glosses_to_english(glosses),
        "videoUrl": video_url,
        "preset": False,
        "cached": cache_hit,
        "live": True,
    })


@app.route("/api/text/mp4", methods=["POST"])
def fetch_mp4_from_text():
    """Accept plain text, map to glosses, and return rendered MP4 URL."""
    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text", "")).strip()

    if text == "":
        return jsonify({"error": "The 'text' field is required."}), 400

    normalized_text = normalize_text(text).rstrip(".")
    preset_phrase = PRESET_PHRASE_BY_TEXT.get(normalized_text)
    if preset_phrase is not None:
        ensure_preset_video(preset_phrase)
        return jsonify({
            "id": preset_phrase["id"],
            "text": text,
            "glosses": preset_phrase["glosses"],
            "englishGlosses": preset_phrase.get("englishGlosses", glosses_to_english(preset_phrase["glosses"])),
            "videoUrl": preset_phrase["videoUrl"],
            "preset": True,
            "cached": True,
        })

    return render_custom_text_to_live_response(text)


@app.route("/api/text/live/mp4", methods=["POST"])
def fetch_live_mp4_from_text():
    """Accept custom plain text and force the live-render path."""
    payload = request.get_json(silent=True) or {}
    text = str(payload.get("text", "")).strip()

    if text == "":
        return jsonify({"error": "The 'text' field is required."}), 400

    return render_custom_text_to_live_response(text)


@app.route("/renders/<path:filename>")
def serve_render_file(filename):
    return send_from_directory(str(RENDER_OUTPUT_DIR), filename)


#
# MAIN
#
if __name__ == '__main__':
    if OPENROUTER_API_KEY:
        print("OpenRouter key detected in OPENROUTER_API_KEY.")

    auto_prebuild = os.getenv("MMS_PREBUILD_LIBRARY", "1") == "1"
    if auto_prebuild:
        start_background_preset_build()

    mms_port = int(os.getenv("MMS_PORT", "5000"))
    app.run(debug=False, port=mms_port)
