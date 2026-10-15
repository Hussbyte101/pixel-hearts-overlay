# Pixel Hearts Overlay

A free stream overlay for VALORANT streamers. It shows your **health and shield as animated pixel-art hearts** on your OBS stream, so viewers can see every hit you take.



https://github.com/user-attachments/assets/a45b8168-c927-4b97-92bf-43fd23dc9314


- ❤️ **10 hearts = 100 HP**, with half hearts
- 🛡️ **Light / Heavy shield** shown as a row of shields above the hearts
- 💛 **Regen shield** shown as golden "overheal" hearts
- 💥 Hearts shake and flash on damage, and tremble when you're at 30 HP or less
- 👀 **Viewers only**: the hearts appear on the stream, never on your game screen

---

## How it works

```
Your screen  →  hp_reader.py (reads the HP number)  →  local WebSocket  →  OBS browser source (draws the hearts)
```

1. `hp_reader.py` takes a screenshot of a **small area** of your own screen: the HP and shield numbers in the bottom-left HUD. This is the same way OBS captures your game.
2. It reads the numbers with image processing (OpenCV template matching).
3. It sends the values to an overlay page in OBS over a local WebSocket (`localhost` only).

### What it does NOT do

- ❌ Does **not** read game memory, inject code, or interact with the VALORANT process
- ❌ Does **not** send any keyboard or mouse input
- ❌ Does **not** reveal hidden information or give any gameplay advantage: it only reads your own HP, which is already on your screen
- ❌ Does **not** use the Riot API, collect data, or send anything off your computer

---

## Requirements

- Windows 10 / 11
- [Python 3.10+](https://www.python.org/downloads/) (tick **"Add Python to PATH"** during install)
- [OBS Studio](https://obsproject.com/) 28 or newer
- VALORANT in **Windowed** or **Windowed Fullscreen** mode at **1920×1080**

---

## Setup

### 1. Download

Click the green **Code** button → **Download ZIP**, then extract it to a folder with an English-only path, for example:

```
C:\Overlays\pixel-hearts-overlay
```

> Paths with non-English characters (or inside OneDrive) can stop OpenCV from reading files.

### 2. Install the dependencies

Open a terminal inside the folder (type `cmd` in the File Explorer address bar and press Enter), then run:

```
pip install -r requirements.txt
```

### 3. Test it without the game

```
python hp_reader.py --demo
```

This sends fake damage so you can set up OBS without launching VALORANT.

### 4. Add the overlay to OBS

1. In **Sources**, click **+** → **Browser**
2. Tick **Local file** and choose **`obs.html`** from the folder
3. Set **Width = 420** and **Height = 100**
4. Click OK and move the hearts where you want them

You only need to do this once. `obs.html` waits for the script and loads the hearts automatically, so you can start OBS and the script in any order.

### 5. Play

Stop the demo with **Ctrl + C**, then run:

```
python hp_reader.py
```

---

## Customize

| Change | File | What to edit |
|---|---|---|
| Heart size | `obs.html` | `const SCALE = 3;` |
| Low-HP tremble threshold | `overlay.html` | `LOW_HP` |
| Heart colors | `overlay.html` | `FULL`, `EMPTY` |
| Heart shape | `overlay.html` | `SPRITE` (each letter is one pixel) |
| Golden heart colors | `overlay.html` | `GOLD_FULL` |
| Read speed | `hp_reader.py` | `FPS` |
| Delay before hiding the hearts | `hp_reader.py` | `HOLD_NONE_SEC` |

---

## Troubleshooting

**The hearts never appear**
Run `python hp_reader.py --diag` while standing in the Range. It prints what the script sees for 10 seconds and saves `diag_area.png`. Check that the image shows your HP number.

**The hearts disappear sometimes**
That's expected when the HUD is hidden (buy menu, death, spectating). The script waits 1.5 seconds before hiding them.

**A number is not recognized**
Unknown digit shapes are saved in the `unknown` folder (max 50 images). If one is a clean digit, copy it into `templates` and name it after the digit, e.g. `7_new.png`.

**"Port is in use"**
Another copy of the script is already running. Close it first.

---

## Known limitations

- Built for **1920×1080**. Other resolutions and stretched 4:3 need recalibration.
- When spectating a teammate, the HUD shows *their* HP, so the hearts follow them.
- A VALORANT HUD update (font, colors, position) may require new digit templates.

---

## Disclaimer

Pixel Hearts Overlay was created under Riot Games' "Legal Jibber Jabber" policy using assets owned by Riot Games. Riot Games does not endorse or sponsor this project.

This is an independent, fan-made tool. It is not affiliated with Riot Games or Mojang. Use it at your own risk.

## License

[MIT](LICENSE)
