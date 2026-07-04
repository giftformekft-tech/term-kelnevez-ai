# EXE készítése (Windows)

Ez az útmutató lépésről lépésre elmagyarázza, hogyan készíts a Python programból
önálló Windows `.exe` fájlt, amit utána **Python nélkül** futtathatsz bármelyik
Windows gépen.

## Fontos

- Az `.exe`-t **Windows gépen** kell elkészíteni (a PyInstaller nem tud másik
  rendszerre fordítani).
- Ahol az exe-t **készíted**, ott kell Python + PyInstaller.
- Ahol az exe-t **futtatod**, ott **semmit** nem kell telepíteni.

---

## Gyors út (ajánlott)

1. **Telepítsd a Pythont** (ha még nincs): https://www.python.org/downloads/
   - A telepítő első képernyőjén **pipáld be: „Add Python to PATH”**.
2. Töltsd le / másold ki ezt a mappát a gépedre (benne a `.py` fájllal,
   a `build.bat`-tal és a `requirements.txt`-tel).
3. **Dupla katt a `build.bat` fájlra.**
4. Várd meg, amíg lefut (letölti a függőségeket, majd buildel).
5. Kész! Az exe itt lesz:  `dist\AI_Kepleiro.exe`

Ezután a `dist\AI_Kepleiro.exe` fájlt átviheted bármelyik Windows gépre
(pendrive, letöltés stb.), és ott Python nélkül elindul.

---

## Kézi út (ha nem a .bat-ot használod)

Nyiss egy parancssort (CMD) ebben a mappában, és futtasd:

```bat
python -m pip install -r requirements.txt
python -m PyInstaller --onefile --windowed --collect-all certifi --name "AI_Kepleiro" "ai_rename_gui3 másolata.py"
```

Az eredmény: `dist\AI_Kepleiro.exe`

### A kapcsolók jelentése

- `--onefile` – egyetlen, önálló `.exe` fájl (minden bele van csomagolva)
- `--windowed` – ne nyíljon fekete konzolablak a GUI mellé
- `--collect-all certifi` – az SSL tanúsítványok becsomagolása (az OpenAI API
  hívásokhoz kell, különben SSL hiba lehet)
- `--name "AI_Kepleiro"` – az exe neve

---

## Futtatáskor (a cél gépen)

- **Nem kell Python.**
- Kell **internet** (az OpenAI API-t hívja).
- A `category_map.json` az exe **melletti mappába** íródik/olvasódik – ez normális.
- Az API kulcsot a program tetején lévő `API_KEY` mezőben kell megadni **build
  előtt** (mert az a `.py` forrásba van beírva). Ha később változtatod a kulcsot,
  újra kell buildelni.

---

## Gyakori hibák

| Hiba | Megoldás |
|------|----------|
| „python nem parancs” / nem található | Nincs telepítve a Python, vagy nincs a PATH-ban. Telepítsd újra, és pipáld be az „Add Python to PATH”-ot. |
| SSL / tanúsítvány hiba futtatáskor | Használd a `--collect-all certifi` kapcsolót (a `build.bat` már tartalmazza). |
| „Pillow modul hiányzik” | A `requirements.txt` telepíti; futtasd újra a `build.bat`-ot. |
| A Windows Defender/SmartScreen figyelmeztet | Ez normális aláíratlan exe-nél: „További információ” → „Futtatás mindenképp”. |
