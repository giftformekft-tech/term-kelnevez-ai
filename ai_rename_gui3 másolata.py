import re, json, base64, threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, simpledialog

import requests

try:
    from PIL import Image, ImageFilter
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# =========================
# 1) IDE ÍRD BE A KULCSOD
# =========================
API_KEY = ""  # <-- ide tedd az sk-... kulcsot (NE oszd meg)

MODEL = "gpt-5-mini"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

DEFAULT_MAIN_CATS = ["Vicces", "Horgászat", "Család", "Ajándék", "Egyéb"]
DEFAULT_SUB_CATS  = ["Festő", "Szakmák / Mesterek", "Autós", "Apák napi", "Anyák napi", "Egyéb"]

WINDOWS_FORBIDDEN = r'<>:"/\\|?*'

# Fájlnévből tiltott szavak (ékezetfüggetlenül is)
BANNED_WORDS = ["póló", "polo", "poló", "pólós", "polos", "pólókat", "polokat"]

# Fájlnév max hossza (karakter)
FILENAME_MAX_CHARS = 55


def normalize_hu_basic(s: str) -> str:
    s = (s or "").lower()
    repl = {"á":"a","é":"e","í":"i","ó":"o","ö":"o","ő":"o","ú":"u","ü":"u","ű":"u"}
    for k, v in repl.items():
        s = s.replace(k, v)
    return s


def strip_banned_words(title: str, banned_words: list[str]) -> str:
    """Tiltott szavak eltávolítása (szóként), ékezetfüggetlen ellenőrzéssel."""
    if not title:
        return title

    parts = re.split(r"(\s+)", title)  # whitespace megőrzés
    banned_norm = {normalize_hu_basic(w) for w in banned_words}

    out = []
    for part in parts:
        if part.isspace():
            out.append(part)
            continue

        token = re.sub(r"[^\wáéíóöőúüűÁÉÍÓÖŐÚÜŰ-]", "", part)
        if not token:
            out.append(part)
            continue

        if normalize_hu_basic(token) in banned_norm:
            continue

        out.append(part)

    res = "".join(out)
    res = re.sub(r"\s+", " ", res).strip()
    return res


def shorten_title_no_word_cut(text: str, max_chars: int = 55) -> str:
    """
    Rövidítés úgy, hogy SOHA nem vág szót.
    Csak teljes szavakat hagy meg.
    Ha az első szó önmagában hosszabb, akkor azt meghagyja teljesen.
    """
    t = (text or "").strip()
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return "kep"

    if len(t) <= max_chars:
        return t

    words = t.split(" ")
    out = []
    cur_len = 0

    for w in words:
        if not out and len(w) > max_chars:
            return w

        add_len = len(w) if not out else (1 + len(w))  # space + word
        if cur_len + add_len > max_chars:
            break

        out.append(w)
        cur_len += add_len

    res = " ".join(out).rstrip(" ,.-;:!?.")
    return res if res else words[0]


def clean_filename_title(title: str, max_len: int = 120) -> str:
    """
    Fájlnévbarát (szóközös):
    - tiltott Windows karakterek ki
    - vezérlő karakterek ki
    - végén pont/szóköz le
    """
    t = (title or "").strip()
    t = t.translate({ord(ch): "" for ch in WINDOWS_FORBIDDEN})
    t = re.sub(r"[\x00-\x1f]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = t.rstrip(" .")
    if not t:
        t = "kep"
    if len(t) > max_len:
        t = t[:max_len].rstrip(" .")
    return t


def build_filename_from_title(title_hu: str) -> str:
    """
    Fájlnév pipeline:
    1) tiltott szavak törlése (póló)
    2) rövidítés (max 55 char, csak teljes szavak)
    3) Windows-safe tisztítás, szóközökkel
    """
    t = strip_banned_words(title_hu or "", BANNED_WORDS)
    t = shorten_title_no_word_cut(t, FILENAME_MAX_CHARS)
    t = clean_filename_title(t, max_len=FILENAME_MAX_CHARS)
    return t or "kep"


def image_to_data_url(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def unique_path_windows_style(target: Path) -> Path:
    """Ütközés esetén: 'Név (2).ext'"""
    if not target.exists():
        return target
    stem, suf = target.stem, target.suffix
    i = 2
    while True:
        cand = target.with_name(f"{stem} ({i}){suf}")
        if not cand.exists():
            return cand
        i += 1


def extract_output_text(out: dict) -> str:
    t = out.get("output_text")
    if isinstance(t, str) and t.strip():
        return t

    for o in out.get("output", []) or []:
        if not isinstance(o, dict):
            continue
        content = o.get("content")
        if content is None:
            continue
        if isinstance(content, dict):
            content = [content]
        if not isinstance(content, list):
            continue
        for c in content:
            if not isinstance(c, dict):
                continue
            if isinstance(c.get("text"), str) and c["text"].strip():
                return c["text"]
            if c.get("type") == "output_text" and isinstance(c.get("text"), str) and c["text"].strip():
                return c["text"]
    return ""


def safe_json_loads(s: str) -> dict:
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, flags=re.S)
        if m:
            return json.loads(m.group(0))
        raise


def build_schema(
    main_to_subs: dict,
    want_desc: bool,
    want_short: bool,
    want_main: bool,
    want_sub: bool,
    want_tags: bool,
    fixed_main: str | None = None
):
    """
    response_format json_schema: oneOf/anyOf nem mehet, ezért enumok.
    FIX FŐ eset:
      - categories.main mezőt nem kérjük (kód állítja be)
      - categories.sub enumját leszűkítjük a fixed_main-hez tartozó alkategóriákra
    """
    props = {
        "title_hu": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    }
    required = ["title_hu", "confidence"]

    if want_desc:
        props["description"] = {"type": "string"}
        required.append("description")

    if want_short:
        props["short_description"] = {"type": "string"}
        required.append("short_description")

    if want_tags:
        props["tags"] = {"type": "array", "items": {"type": "string"}}
        required.append("tags")

    if want_main or want_sub:
        mains = sorted([m for m in (main_to_subs or {}).keys() if (m or "").strip()]) or ["Egyéb"]
        all_subs = sorted({s for subs in (main_to_subs or {}).values() for s in (subs or []) if (s or "").strip()}) or ["Egyéb"]

        fixed_main = (fixed_main or "").strip() or None
        if fixed_main and fixed_main in main_to_subs:
            subs = [s.strip() for s in (main_to_subs.get(fixed_main) or []) if (s or "").strip()]
            # ha nincs al hozzá, fallback az összesre, de ez UI hibára utal
            sub_enum = subs or all_subs
        else:
            fixed_main = None
            sub_enum = all_subs

        cat_props = {}
        cat_req = []

        # FIX FŐ esetben nem kérünk main-t
        if want_main and not fixed_main:
            cat_props["main"] = {"type": "string", "enum": mains}
            cat_req.append("main")

        if want_sub:
            cat_props["sub"] = {"type": "string", "enum": sub_enum}
            cat_req.append("sub")

        props["categories"] = {
            "type": "object",
            "additionalProperties": False,
            "properties": cat_props,
            "required": cat_req
        }
        required.append("categories")

    return {
        "name": "img_meta_v1",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": props,
            "required": required,
        }
    }


def enforce_category_mapping(meta: dict, main_to_subs: dict) -> tuple[dict, str]:
    """
    Javítja a meta['categories'] main/sub párost a mapping alapján.
    """
    note = ""
    cats = meta.get("categories")
    if not isinstance(cats, dict):
        return meta, note

    main = (cats.get("main") or "").strip()
    sub = (cats.get("sub") or "").strip()
    if not sub:
        return meta, note

    sub_to_mains: dict[str, list[str]] = {}
    for m, subs in (main_to_subs or {}).items():
        m = (m or "").strip()
        if not m:
            continue
        for s in (subs or []):
            s = (s or "").strip()
            if not s:
                continue
            sub_to_mains.setdefault(s, []).append(m)

    possible = sub_to_mains.get(sub, [])
    if not possible:
        note = f"⚠ sub nincs a mappingben: {sub}"
        return meta, note

    if len(possible) == 1:
        fixed_main = possible[0]
        if main != fixed_main and fixed_main:
            cats["main"] = fixed_main
            meta["categories"] = cats
            note = f"ℹ main javítva: '{main}' → '{fixed_main}' (sub='{sub}')"
        return meta, note

    if main in possible:
        return meta, note

    fixed_main = possible[0]
    cats["main"] = fixed_main
    meta["categories"] = cats
    note = f"⚠ main ütközés: '{main}' nem illik sub='{sub}'-hoz. Beállítva: '{fixed_main}' (lehetséges: {possible})"
    return meta, note


def enforce_fixed_main(meta: dict, main_to_subs: dict, fixed_main: str) -> tuple[dict, str]:
    """
    Fix fő esetben:
      - main = fixed_main
      - sub legyen a fixed_main aljai közül; ha nem, fallback
    """
    note = ""
    fixed_main = (fixed_main or "").strip()
    if not fixed_main:
        return meta, note

    cats = meta.get("categories")
    if not isinstance(cats, dict):
        cats = {}
        meta["categories"] = cats

    cats["main"] = fixed_main

    allowed = [s.strip() for s in (main_to_subs.get(fixed_main) or []) if (s or "").strip()]
    sub = (cats.get("sub") or "").strip()

    if allowed:
        if sub not in allowed:
            # Fallback: "Egyéb" ha létezik, különben első
            fallback = "Egyéb" if "Egyéb" in allowed else allowed[0]
            cats["sub"] = fallback
            note = f"⚠ sub kívül esett a fix fő listáján: '{sub}' → '{fallback}' (fix main='{fixed_main}')"
    else:
        # nincs allowed lista -> csak beállítjuk a main-t
        note = f"⚠ fix main '{fixed_main}' alatt nincs alkategória a mappingben"

    meta["categories"] = cats
    return meta, note


def call_openai_image_meta(data_url: str, schema: dict, instructions: str) -> dict:
    body = {
        "model": MODEL,
        "instructions": instructions,
        "input": [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Elemezd a képet és add vissza a kért sémát."},
                {"type": "input_image", "image_url": data_url}
            ]
        }],
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema["name"],
                "schema": schema["schema"]
            }
        },
        "store": False
    }

    r = requests.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        data=json.dumps(body),
        timeout=120
    )

    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}\n{r.text}")

    out = r.json()
    text = extract_output_text(out)
    if not text:
        raise RuntimeError("Nem találtam kimeneti szöveget:\n" + json.dumps(out, ensure_ascii=False)[:2000])

    meta = safe_json_loads(text)
    meta["version"] = 1
    meta["model"] = MODEL
    return meta


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AI Képleíró + Átnevező – GPT-5 mini")
        self.geometry("1140x860")

        self.folder = tk.StringVar()
        self.dry_run = tk.BooleanVar(value=True)
        self.make_json = tk.BooleanVar(value=True)

        # Képnév megtartása (csak JSON)
        self.keep_original_name = tk.BooleanVar(value=False)

        # Pixeles peremezés
        self.do_choke = tk.BooleanVar(value=False)
        self.choke_px = tk.IntVar(value=2)

        # JSON mezők
        self.want_desc = tk.BooleanVar(value=True)
        self.want_short = tk.BooleanVar(value=True)
        self.want_main = tk.BooleanVar(value=True)
        self.want_sub = tk.BooleanVar(value=True)
        self.want_tags = tk.BooleanVar(value=True)

        # kreatív + brand
        self.creative_mode = tk.BooleanVar(value=True)
        self.allow_brands = tk.BooleanVar(value=True)

        # ÚJ: fix fő kategória
        self.force_main = tk.BooleanVar(value=False)
        self.force_main_value = tk.StringVar(value="")

        # ---- Folder
        top = tk.Frame(self)
        top.pack(fill="x", padx=10, pady=(10, 6))
        tk.Label(top, text="Mappa:").pack(side="left")
        tk.Entry(top, textvariable=self.folder).pack(side="left", fill="x", expand=True, padx=8)
        tk.Button(top, text="Tallózás", command=self.pick_folder).pack(side="left")

        # ---- Options
        opt = tk.Frame(self)
        opt.pack(fill="x", padx=10, pady=6)
        tk.Checkbutton(opt, text="Dry run (ne nevezze át, csak mutassa)", variable=self.dry_run).pack(side="left")
        tk.Checkbutton(opt, text="JSON mentése (.json)", variable=self.make_json).pack(side="left", padx=18)
        tk.Checkbutton(opt, text="Kép nevének megtartása (csak JSON készül)", variable=self.keep_original_name).pack(side="left", padx=18)

        # ---- Fix main UI
        fix = tk.LabelFrame(self, text="Fix fő kategória (opcionális)")
        fix.pack(fill="x", padx=10, pady=8)
        row = tk.Frame(fix); row.pack(fill="x", padx=8, pady=8)
        tk.Checkbutton(
            row,
            text="Fő kategória fixálása",
            variable=self.force_main,
            command=self.on_force_main_toggle
        ).pack(side="left")

        tk.Label(row, text="Fix fő:").pack(side="left", padx=(18, 6))
        self.force_main_combo = ttk.Combobox(row, textvariable=self.force_main_value, state="disabled", width=30)
        self.force_main_combo.pack(side="left")

        tk.Label(row, text="(Ha be van kapcsolva: csak ehhez a főhöz tartozó alkategóriát választ.)").pack(side="left", padx=10)

        # ---- Naming behavior
        namebox = tk.LabelFrame(self, text="Név-képzés beállítások")
        namebox.pack(fill="x", padx=10, pady=8)
        r = tk.Frame(namebox); r.pack(fill="x", padx=8, pady=6)
        tk.Checkbutton(r, text="Kreatív mód (2-4 extra szó a fő felirat mellé)", variable=self.creative_mode).pack(side="left")
        tk.Checkbutton(r, text="Brand nevek engedése", variable=self.allow_brands).pack(side="left", padx=18)

        # ---- JSON toggles
        togg = tk.LabelFrame(self, text="Mi kerüljön a JSON-ba?")
        togg.pack(fill="x", padx=10, pady=8)
        row1 = tk.Frame(togg); row1.pack(fill="x", padx=8, pady=6)
        tk.Checkbutton(row1, text="Leírás", variable=self.want_desc).pack(side="left")
        tk.Checkbutton(row1, text="Rövid leírás", variable=self.want_short).pack(side="left", padx=14)
        tk.Checkbutton(row1, text="Fő kategória", variable=self.want_main).pack(side="left", padx=14)
        tk.Checkbutton(row1, text="Alkategória", variable=self.want_sub).pack(side="left", padx=14)
        tk.Checkbutton(row1, text="Címkék (tags)", variable=self.want_tags).pack(side="left", padx=14)

        # ---- Képfeldolgozás
        imgbox = tk.LabelFrame(self, text="Képfeldolgozás (PNG)")
        imgbox.pack(fill="x", padx=10, pady=8)
        ri = tk.Frame(imgbox); ri.pack(fill="x", padx=8, pady=6)
        tk.Checkbutton(ri, text="Pink szegély levágása (Choke PNG Alpha)", variable=self.do_choke).pack(side="left")
        tk.Label(ri, text="   Mérték (pixel):").pack(side="left")
        ttk.Spinbox(ri, from_=1, to=10, textvariable=self.choke_px, width=3).pack(side="left", padx=5)

        # ---- Category relationship editor (UI)
        rel = tk.LabelFrame(self, text="Kategória kapcsolatok (Fő → Alkategóriák)")
        rel.pack(fill="x", padx=10, pady=8)

        self.cat_map_file = Path("category_map.json")
        self.main_to_subs: dict[str, list[str]] = {}

        wrap = tk.Frame(rel)
        wrap.pack(fill="both", expand=True, padx=8, pady=8)

        left = tk.Frame(wrap)
        left.pack(side="left", fill="both", expand=True)

        tk.Label(left, text="Fő kategóriák:").pack(anchor="w")
        self.main_list = tk.Listbox(left, height=10)
        self.main_list.pack(fill="both", expand=True)

        btns_main = tk.Frame(left)
        btns_main.pack(fill="x", pady=(6, 0))
        tk.Button(btns_main, text="+ Fő", command=self.add_main).pack(side="left")
        tk.Button(btns_main, text="Átnevez", command=self.rename_main).pack(side="left", padx=6)
        tk.Button(btns_main, text="Törlés", command=self.delete_main).pack(side="left")

        right = tk.Frame(wrap)
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))

        tk.Label(right, text="Alkategóriák (kijelölt főhöz):").pack(anchor="w")
        self.sub_list = tk.Listbox(right, height=10)
        self.sub_list.pack(fill="both", expand=True)

        btns_sub = tk.Frame(right)
        btns_sub.pack(fill="x", pady=(6, 0))
        tk.Button(btns_sub, text="+ Al", command=self.add_sub).pack(side="left")
        tk.Button(btns_sub, text="Átnevez", command=self.rename_sub).pack(side="left", padx=6)
        tk.Button(btns_sub, text="Törlés", command=self.delete_sub).pack(side="left")

        self.main_list.bind("<<ListboxSelect>>", lambda e: self.refresh_subs())
        self.load_category_map_or_defaults()

        # ---- Controls
        ctl = tk.Frame(self)
        ctl.pack(fill="x", padx=10, pady=8)

        self.start_btn = tk.Button(ctl, text="Start", command=self.start)
        self.start_btn.pack(side="left")

        self.pb_var = tk.IntVar(value=0)
        self.pb = ttk.Progressbar(ctl, maximum=100, variable=self.pb_var)
        self.pb.pack(side="left", fill="x", expand=True, padx=10)

        # ---- Log
        self.log = tk.Text(self, height=18)
        self.log.pack(fill="both", expand=True, padx=10, pady=(8, 10))

        tk.Label(
            self,
            text=f"Fájlnév: képen lévő fő szöveg rövidítve (max {FILENAME_MAX_CHARS} karakter), 'póló' nélkül, szóközökkel. Szót nem vágunk el.",
        ).pack(anchor="w", padx=10, pady=(0, 10))

    # ---------------- FIX MAIN helpers ----------------

    def on_force_main_toggle(self):
        if self.force_main.get():
            self.force_main_combo.configure(state="readonly")
            # ha üres, állítsuk az elsőre
            vals = list(self.force_main_combo["values"])
            if vals and not self.force_main_value.get():
                self.force_main_value.set(vals[0])
        else:
            self.force_main_combo.configure(state="disabled")

    def refresh_force_main_values(self):
        mains = sorted([m for m in self.main_to_subs.keys() if (m or "").strip()])
        self.force_main_combo["values"] = mains
        # ha a kiválasztott már nem létezik -> ürítés/első
        cur = (self.force_main_value.get() or "").strip()
        if cur and cur not in mains:
            self.force_main_value.set("")
        if self.force_main.get() and mains and not self.force_main_value.get():
            self.force_main_value.set(mains[0])

    # ---------------- UI helpers: category mapping ----------------

    def load_category_map_or_defaults(self):
        if self.cat_map_file.exists():
            try:
                data = json.loads(self.cat_map_file.read_text(encoding="utf-8"))
                self.main_to_subs = {str(k): [str(x) for x in (v or [])] for k, v in data.items()}
            except Exception:
                self.main_to_subs = {}

        if not self.main_to_subs:
            self.main_to_subs = {m: list(DEFAULT_SUB_CATS) for m in DEFAULT_MAIN_CATS}

        self.refresh_mains()
        self.refresh_force_main_values()
        if self.main_list.size() > 0:
            self.main_list.selection_set(0)
            self.refresh_subs()

    def save_category_map(self):
        try:
            self.cat_map_file.write_text(json.dumps(self.main_to_subs, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def refresh_mains(self):
        self.main_list.delete(0, "end")
        for m in sorted(self.main_to_subs.keys()):
            self.main_list.insert("end", m)

    def get_selected_main(self):
        sel = self.main_list.curselection()
        if not sel:
            return None
        return self.main_list.get(sel[0])

    def refresh_subs(self):
        m = self.get_selected_main()
        self.sub_list.delete(0, "end")
        if not m:
            return
        for s in self.main_to_subs.get(m, []):
            self.sub_list.insert("end", s)

    def add_main(self):
        name = simpledialog.askstring("Új fő kategória", "Fő kategória neve:")
        if not name:
            return
        name = name.strip()
        if not name:
            return
        if name in self.main_to_subs:
            messagebox.showerror("Hiba", "Ilyen fő kategória már létezik.")
            return
        self.main_to_subs[name] = []
        self.save_category_map()
        self.refresh_mains()
        self.refresh_force_main_values()

    def rename_main(self):
        old = self.get_selected_main()
        if not old:
            return
        new = simpledialog.askstring("Fő kategória átnevezése", "Új név:", initialvalue=old)
        if not new:
            return
        new = new.strip()
        if not new or new == old:
            return
        if new in self.main_to_subs:
            messagebox.showerror("Hiba", "Ilyen fő kategória már létezik.")
            return
        self.main_to_subs[new] = self.main_to_subs.pop(old)
        self.save_category_map()
        self.refresh_mains()
        self.refresh_force_main_values()

    def delete_main(self):
        m = self.get_selected_main()
        if not m:
            return
        if messagebox.askyesno("Törlés", f"Törlöd ezt a fő kategóriát?\n\n{m}"):
            self.main_to_subs.pop(m, None)
            self.save_category_map()
            self.refresh_mains()
            self.refresh_subs()
            self.refresh_force_main_values()

    def add_sub(self):
        m = self.get_selected_main()
        if not m:
            messagebox.showerror("Hiba", "Előbb válassz fő kategóriát.")
            return
        name = simpledialog.askstring("Új alkategória", f"Alkategória neve ({m} alatt):")
        if not name:
            return
        name = name.strip()
        if not name:
            return
        subs = self.main_to_subs.setdefault(m, [])
        if name in subs:
            messagebox.showerror("Hiba", "Ilyen alkategória már van ennél a főnél.")
            return
        subs.append(name)
        self.save_category_map()
        self.refresh_subs()
        self.refresh_force_main_values()

    def rename_sub(self):
        m = self.get_selected_main()
        if not m:
            return
        sel = self.sub_list.curselection()
        if not sel:
            return
        old = self.sub_list.get(sel[0])
        new = simpledialog.askstring("Alkategória átnevezése", "Új név:", initialvalue=old)
        if not new:
            return
        new = new.strip()
        if not new or new == old:
            return
        subs = self.main_to_subs.get(m, [])
        if new in subs:
            messagebox.showerror("Hiba", "Ilyen alkategória már van ennél a főnél.")
            return
        subs[subs.index(old)] = new
        self.save_category_map()
        self.refresh_subs()
        self.refresh_force_main_values()

    def delete_sub(self):
        m = self.get_selected_main()
        if not m:
            return
        sel = self.sub_list.curselection()
        if not sel:
            return
        s = self.sub_list.get(sel[0])
        if messagebox.askyesno("Törlés", f"Törlöd ezt az alkategóriát?\n\n{m} → {s}"):
            subs = self.main_to_subs.get(m, [])
            if s in subs:
                subs.remove(s)
            self.save_category_map()
            self.refresh_subs()
            self.refresh_force_main_values()

    def get_category_map(self) -> dict:
        out = {}
        for m, subs in self.main_to_subs.items():
            m2 = (m or "").strip()
            if not m2:
                continue
            out[m2] = [s.strip() for s in (subs or []) if (s or "").strip()]
        return out

    # ---------------- main app flow ----------------

    def pick_folder(self):
        p = filedialog.askdirectory()
        if p:
            self.folder.set(p)

    def logline(self, s: str):
        self.log.insert("end", s + "\n")
        self.log.see("end")

    def start(self):
        if not API_KEY.startswith("sk-"):
            messagebox.showerror("Hiba", "Írd be a kulcsot a fájl tetején az API_KEY változóba (sk-... legyen).")
            return
        folder = self.folder.get().strip()
        if not folder:
            messagebox.showerror("Hiba", "Nincs kiválasztott mappa.")
            return

        cat_map = self.get_category_map()

        if self.want_main.get() or self.want_sub.get():
            if not cat_map:
                messagebox.showerror("Hiba", "Adj meg legalább 1 fő kategóriát.")
                return
            if self.want_sub.get():
                has_any_pair = any(len(v) > 0 for v in cat_map.values())
                if not has_any_pair:
                    messagebox.showerror("Hiba", "Adj meg legalább 1 alkategóriát valamelyik fő alá.")
                    return

        if self.force_main.get():
            fixed = (self.force_main_value.get() or "").strip()
            if not fixed:
                messagebox.showerror("Hiba", "Fix fő kategória be van kapcsolva, de nincs kiválasztva.")
                return
            if fixed not in cat_map:
                messagebox.showerror("Hiba", f"A fix fő kategória nem létezik a listában: {fixed}")
                return

        self.start_btn.config(state="disabled")
        self.pb_var.set(0)
        self.log.delete("1.0", "end")

        self.logline(f"Modell: {MODEL}")
        self.logline(f"Mappa: {folder}")
        self.logline(f"Dry run: {self.dry_run.get()} | JSON: {self.make_json.get()} | Keep original name: {self.keep_original_name.get()}")
        self.logline(f"Képfeldolgozás: {'Igen (Szűkítés: '+str(self.choke_px.get())+'px)' if self.do_choke.get() else 'Nem'}")
        self.logline(f"Kreatív mód: {self.creative_mode.get()} | Brand: {self.allow_brands.get()}")
        self.logline(f"JSON mezők: desc={self.want_desc.get()}, short={self.want_short.get()}, main={self.want_main.get()}, sub={self.want_sub.get()}, tags={self.want_tags.get()}")
        if self.force_main.get():
            self.logline(f"FIX FŐ KATEGÓRIA: {self.force_main_value.get().strip()}")
        self.logline("-" * 90)

        threading.Thread(target=self.run_batch, daemon=True).start()

    def run_batch(self):
        folder = Path(self.folder.get().strip())
        files = sorted([p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS])

        if not files:
            self.after(0, lambda: messagebox.showinfo("Info", "Nem találtam képfájlt a mappában."))
            self.after(0, lambda: self.start_btn.config(state="normal"))
            return

        cat_map = self.get_category_map()
        fixed_main = (self.force_main_value.get() or "").strip() if self.force_main.get() else None

        schema = build_schema(
            cat_map,
            self.want_desc.get(), self.want_short.get(),
            self.want_main.get(), self.want_sub.get(),
            self.want_tags.get(),
            fixed_main=fixed_main
        )

        instructions = (
            "A title_hu alapja a képen látható DOMINÁNS felirat legyen (tartalmilag maradjon meg). "
            f"A title_hu legyen rövid (max {FILENAME_MAX_CHARS} karakter), és kizárólag teljes szavakat használj, Csak a kezdő betű lehet nagy nem lehet benne végíg nagybetűs szó "
            "(nem vághatsz el szót, nem rövidíthetsz). "
            "Ha van szám (pl. 18), maradjon benne. "
            "A title_hu NE tartalmazza a 'póló' szót. "
        )

        if self.creative_mode.get():
            instructions += (
                "A domináns felirat mellé adhatsz 2-4 kreatív szót (hangulat/stílus), "
                "de a fő felirat maradjon felismerhetően jelen. "
            )
        else:
            instructions += "Ne adj hozzá kreatív szavakat, csak a felirat rövid, hű kivonatát add. "

        if self.allow_brands.get():
            instructions += "Brand/márkaneveket nyugodtan használhatsz, ha a képen szerepelnek vagy egyértelműen kapcsolódnak. "
        else:
            instructions += "Ne használj brand/márkaneveket. "

        if self.want_sub.get() or self.want_main.get():
            if fixed_main:
                instructions += (
                    f"A fő kategória FIX: '{fixed_main}'. "
                    "Csak ehhez a főhöz tartozó alkategóriát válassz a megadott listából. "
                )
            else:
                instructions += (
                    "A categories mezőket a megadott listákból válaszd. "
                    "Ha az alkategória több főhöz is illik, válaszd a leginkább passzoló főt. "
                )

        instructions += "A mezőket pontosan a megadott JSON sémában add vissza."

        total = len(files)
        for i, img in enumerate(files, start=1):
            try:
                self.after(0, lambda n=img.name, a=i, t=total: self.logline(f"[{a}/{t}] Elemzés: {n}"))

                meta = call_openai_image_meta(image_to_data_url(img), schema, instructions)

                # ✅ Fix fő: main beégetése + sub ellenőrzés
                if fixed_main and self.want_sub.get():
                    meta, note = enforce_fixed_main(meta, cat_map, fixed_main)
                    if note:
                        self.after(0, lambda msg=note: self.logline("   " + msg))
                # ✅ Nem fix: main/sub mapping javítás (ha mindkettő kell)
                elif (self.want_main.get() and self.want_sub.get()):
                    meta, note = enforce_category_mapping(meta, cat_map)
                    if note:
                        self.after(0, lambda msg=note: self.logline("   " + msg))

                nice_title = build_filename_from_title(meta.get("title_hu", ""))

                # ---- NÉV LOGIKA
                if self.keep_original_name.get():
                    target_img = img
                    target_json = unique_path_windows_style(img.with_suffix(".json"))
                    meta["filename_base"] = img.stem
                else:
                    target_img = unique_path_windows_style(img.with_name(nice_title + img.suffix))
                    target_json = unique_path_windows_style(img.with_name(nice_title + ".json"))
                    meta["filename_base"] = target_img.stem

                meta["title_hu_short_for_filename"] = nice_title

                # JSON mentés
                if self.make_json.get():
                    target_json.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

                # Átnevezés és Képfeldolgozás (Choke)
                if not self.dry_run.get():
                    if self.do_choke.get() and img.suffix.lower() == ".png":
                        if not HAS_PIL:
                            self.after(0, lambda: self.logline("   ✖ Hiba: Pillow modul hiányzik a peremlevágáshoz! Telepítsd: pip install Pillow"))
                            if not self.keep_original_name.get() and img != target_img:
                                img.rename(target_img)
                        else:
                            try:
                                with Image.open(img) as im:
                                    out_im = im.convert("RGBA")
                                    r, g, b, a = out_im.split()
                                    filter_size = self.choke_px.get() * 2 + 1
                                    a = a.filter(ImageFilter.MinFilter(filter_size))
                                    out_im = Image.merge("RGBA", (r, g, b, a))
                                    out_im.save(target_img)
                                
                                # Ha újat hoztunk létre és az eredeti már nem kell
                                if not self.keep_original_name.get() and img != target_img:
                                    img.unlink()
                            except Exception as ex:
                                self.after(0, lambda e=str(ex): self.logline(f"   ✖ Képfeldolgozási hiba: {e}"))
                                if not self.keep_original_name.get() and img != target_img:
                                    img.rename(target_img)
                    else:
                        if not self.keep_original_name.get() and img != target_img:
                            img.rename(target_img)

                conf = float(meta.get("confidence", 0))
                cat_info = ""
                if isinstance(meta.get("categories"), dict):
                    m = meta["categories"].get("main")
                    s = meta["categories"].get("sub")
                    parts = [x for x in [m, s] if x]
                    if parts:
                        cat_info = " | " + " / ".join(parts)

                shown_name = (target_img.name if not self.keep_original_name.get() else img.name)
                self.after(0, lambda b=shown_name, c=conf, ci=cat_info:
                           self.logline(f"   ✔ {b}{ci} | conf={c:.2f}"))

            except Exception as e:
                self.after(0, lambda err=str(e): self.logline(f"   ✖ Hiba: {err}"))

            self.after(0, lambda v=int(i / total * 100): self.pb_var.set(v))

        self.after(0, lambda: self.start_btn.config(state="normal"))
        self.after(0, lambda: messagebox.showinfo("Kész", "Feldolgozás kész."))


if __name__ == "__main__":
    App().mainloop()
