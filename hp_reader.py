"""
Pixel Hearts Overlay - HP reader
Reads the player's own HP/shield from the VALORANT HUD on screen (pixels only;
never touches game memory or the game process) and sends it to an OBS overlay.

Usage:
  python hp_reader.py              -> live mode
  python hp_reader.py --demo       -> fake damage, to test the overlay without the game
  python hp_reader.py --test a.png -> read HP from saved screenshots
  python hp_reader.py --diag       -> 10-second diagnostic, saves diag_area.png
  python hp_reader.py --debug      -> live mode + saves roi_debug.png
"""
import sys, time, os, glob, json, asyncio
from collections import Counter, deque
import cv2
import numpy as np

# ===== الإعدادات (مبنية على 1920x1080) =====
BASE_W, BASE_H = 1920, 1080
# منطقة الرقم: من x=520 إلى 720، ومن أسفل الشاشة بـ 85 إلى 25 بكسل
ROI = (520, BASE_H - 85, 200, 60)        # x, y, w, h
MIN_GLYPH_H = 28                          # الأرقام الكبيرة طولها ~33px (أرقام الشيلد أصغر وتنفلتر)
DIGIT_BAND = (13, 51)                     # الصفوف اللي فيها الأرقام داخل المنطقة
MIN_GLYPH_AREA = 100                      # يستبعد الخط المائل جنب الرقم
MATCH_THRESHOLD = 0.70                    # أقل من كذا = رقم مو معروف
MATCH_MARGIN = 0.10                       # لازم يكون أوضح من ثاني أقرب رقم بهالفرق
FPS = 10
HOLD_NONE_SEC = 1.5                       # كم ثانية نصبر لو الرقم ما انقرى قبل ما نخفي القلوب

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(HERE, "templates")
UNKNOWN_DIR = os.path.join(HERE, "unknown")
NORM = (20, 33)                           # كل رقم يتصغّر/يتكبر لهالمقاس قبل المقارنة


def load_templates():
    t = {}
    for f in glob.glob(os.path.join(TEMPLATE_DIR, "*.png")):
        d = os.path.splitext(os.path.basename(f))[0][0]
        if d.isdigit():
            g = cv2.imread(f, cv2.IMREAD_GRAYSCALE)
            t.setdefault(d, []).append(cv2.resize(g, NORM).astype(np.float32) / 255)
    return t


def white_mask(bgr):
    # يلقط الأبيض العادي + الوردي اللي يطلع لما تكون الصحة قليلة
    b, g, r = bgr[..., 0].astype(int), bgr[..., 1].astype(int), bgr[..., 2].astype(int)
    # r > 200 يستبعد التركوازي (أيقونات الشيلد) لأن الأحمر فيه قليل
    return ((r > 200) & (np.minimum(b, g) > 140) & (np.maximum(np.maximum(b, g), r) > 230)).astype(np.uint8) * 255


def segment(roi_bgr, with_flag=False):
    """يرجع قائمة صور الأرقام مرتبة من اليسار لليمين
    with_flag=True يرجع كمان هل فيه شي مشكوك فيه جنب الأرقام (رقم ممسوح أو ملتصق بالخلفية)"""
    m = white_mask(roi_bgr)
    # الأرقام دايم في نفس الشريط الأفقي (تقريبًا من صف 15 لـ 48)، فنقص اللي فوقه وتحته
    # عشان لو الخلفية بيضا (سما، جدار) ما تلتصق بالأرقام
    m[:DIGIT_BAND[0]] = 0
    m[DIGIT_BAND[1]:] = 0
    n, lab, st, _ = cv2.connectedComponentsWithStats(m)
    out, rejected = [], []
    for k in range(1, n):
        x, y, w, h, a = st[k]
        if MIN_GLYPH_H <= h <= 40 and a >= MIN_GLYPH_AREA and w < h and w <= 26:
            out.append((x, w, (lab[y:y + h, x:x + w] == k).astype(np.uint8) * 255))
        elif a >= 80 and h >= 15:
            rejected.append((x, w))
    out.sort(key=lambda t: t[0])
    glyphs = [g for _, _, g in out]
    if not with_flag:
        return glyphs
    suspicious = False
    if out:
        left, right = out[0][0] - 25, out[-1][0] + out[-1][1] + 25
        # قطعة كبيرة مرفوضة قريبة من الأرقام = غالبًا رقم انمسح أو التصق بالخلفية
        if any(x < right and x + w > left for x, w in rejected):
            suspicious = True
        # المسافة بين بداية كل رقم والثاني دايم تقريبًا 21-24 بكسل
        for (x1, _, _), (x2, _, _) in zip(out, out[1:]):
            if not 17 <= x2 - x1 <= 30:
                suspicious = True
    return glyphs, suspicious


def classify(glyph, templates):
    g = cv2.resize(glyph, NORM).astype(np.float32) / 255
    best, score = None, -1.0
    for d, ts in templates.items():
        for t in ts:
            # IoU بين الرقم والقالب
            inter = np.minimum(g, t).sum()
            union = np.maximum(g, t).sum() + 1e-6
            s = inter / union
            if s > score:
                best, score = d, s
    return best, score


def classify_safe(glyph, templates):
    """يقبل الرقم بس لو كان واضح: نتيجة عالية + بعيد كفاية عن ثاني أقرب رقم"""
    g = cv2.resize(glyph, NORM).astype(np.float32) / 255
    best = {}
    for d, ts in templates.items():
        best[d] = max(np.minimum(g, t).sum() / (np.maximum(g, t).sum() + 1e-6) for t in ts)
    ranked = sorted(best.items(), key=lambda x: x[1], reverse=True)
    (d1, s1), (_, s2) = ranked[0], ranked[1]
    if s1 >= MATCH_THRESHOLD and s1 - s2 >= MATCH_MARGIN:
        return d1, s1
    return None, s1


MAX_UNKNOWN = 50                          # حد أعلى عشان المجلد ما يمتلي


def save_unknown(glyph, folder=UNKNOWN_DIR):
    os.makedirs(folder, exist_ok=True)
    files = glob.glob(os.path.join(folder, "*.png"))
    if len(files) >= MAX_UNKNOWN:
        return
    # ما نحفظ نفس الشكل مرتين
    for f in files:
        old = cv2.imread(f, cv2.IMREAD_GRAYSCALE)
        a = cv2.resize(old, NORM).astype(np.float32) / 255
        b = cv2.resize(glyph, NORM).astype(np.float32) / 255
        if np.minimum(a, b).sum() / (np.maximum(a, b).sum() + 1e-6) > 0.9:
            return
    cv2.imwrite(os.path.join(folder, f"{int(time.time() * 1000)}.png"), glyph)


def read_hp(roi_bgr, templates, collect=True):
    """يرجع رقم الـ HP أو None لو ما فيه رقم واضح"""
    glyphs, suspicious = segment(roi_bgr, with_flag=True)
    if not glyphs or len(glyphs) > 3 or suspicious:
        # أحسن ما نقرأ نص الرقم (مثلًا "10" بدل "100")، نقول ما نعرف
        return None
    digits = ""
    for g in glyphs:
        d, s = classify_safe(g, templates)
        if d is None:
            if collect:
                save_unknown(g)
            return None
        digits += d
    if len(digits) > 1 and digits[0] == "0":   # "05" مستحيل، أكيد فيه غلط
        return None
    hp = int(digits)
    return hp if 0 <= hp <= 150 else None


# ===== الشيلد (الرقم الصغير داخل أيقونة الدرع) =====
SHIELD_ROI = (515, BASE_H - 75, 70, 40)   # x, y, w, h
SMALL_TEMPLATE_DIR = os.path.join(HERE, "templates_small")
UNKNOWN_SHIELD_DIR = os.path.join(HERE, "unknown_shield")


def load_small_templates():
    t = {}
    for f in glob.glob(os.path.join(SMALL_TEMPLATE_DIR, "*.png")):
        d = os.path.splitext(os.path.basename(f))[0][0]
        if d.isdigit():
            g = cv2.imread(f, cv2.IMREAD_GRAYSCALE)
            t.setdefault(d, []).append(cv2.resize(g, NORM).astype(np.float32) / 255)
    return t


def segment_small(roi_bgr):
    # أرقام الشيلد بيضا صافية (الأيقونة نفسها لونها تركوازي فتنفلتر)
    m = (roi_bgr.min(axis=2) > 170).astype(np.uint8) * 255
    n, lab, st, _ = cv2.connectedComponentsWithStats(m)
    out = []
    for k in range(1, n):
        x, y, w, h, a = st[k]
        if 10 <= h <= 16 and a > 20 and w < h:
            out.append((x, (lab[y:y + h, x:x + w] == k).astype(np.uint8) * 255))
    return [g for _, g in sorted(out, key=lambda t: t[0])]


def classify_small(glyph, small_t, big_t):
    """يجرب القوالب الصغيرة أول، ولو ما لقى يرجع للكبيرة (نفس الخط بس أصغر)"""
    d, s = classify(glyph, small_t) if small_t else (None, 0)
    if s >= 0.80:
        return d
    g = cv2.resize(glyph, NORM).astype(np.float32) / 255
    scores = sorted(((np.minimum(g, t).sum() / (np.maximum(g, t).sum() + 1e-6), dg)
                     for dg, ts in big_t.items() for t in ts), reverse=True)
    best, second = scores[0], next(x for x in scores if x[1] != scores[0][1])
    if best[0] >= 0.65 and best[0] - second[0] >= 0.10:
        return best[1]
    return None


def save_unknown_shield(glyph):
    save_unknown(glyph, UNKNOWN_SHIELD_DIR)


def read_shield(roi_bgr, small_t, big_t, collect=True):
    """يرجع الشيلد (0 لو ما فيه درع) أو None لو القراءة مو واضحة"""
    glyphs = segment_small(roi_bgr)
    if not glyphs:
        return 0
    if len(glyphs) > 2:
        return None
    digits = ""
    for g in glyphs:
        d = classify_small(g, small_t, big_t)
        if d is None:
            if collect:
                save_unknown_shield(g)
            return None
        digits += d
    v = int(digits)
    return v if v <= 50 else None


def is_regen(roi_bgr):
    """الـ Regen Shield أيقونته دائرية وسميكة، فيها بكسلات تركوازية أكثر بكثير من الدرع العادي"""
    r = roi_bgr.astype(int)
    teal = (r[..., 1] > 180) & (r[..., 0] > 140) & (r[..., 2] < 120)
    return bool(teal.sum() > 300)


def crop(frame, box):
    h, w = frame.shape[:2]
    sx, sy = w / BASE_W, h / BASE_H
    x, y, rw, rh = box
    roi = frame[int(y * sy):int((y + rh) * sy), int(x * sx):int((x + rw) * sx)]
    if (sx, sy) != (1, 1):
        roi = cv2.resize(roi, (rw, rh))
    return roi


def crop_roi(frame):
    """يقص المنطقة بنسبة لحجم الصورة"""
    h, w = frame.shape[:2]
    sx, sy = w / BASE_W, h / BASE_H
    x, y, rw, rh = ROI
    roi = frame[int(y * sy):int((y + rh) * sy), int(x * sx):int((x + rw) * sx)]
    if (sx, sy) != (1, 1):
        roi = cv2.resize(roi, (rw, rh))
    return roi


# ===== إيجاد نافذة فالورانت =====
def list_valorant_windows():
    """كل النوافذ الظاهرة اللي في عنوانها VALORANT: [(hwnd, العنوان, النوع)]"""
    import win32gui
    found = []
    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if "VALORANT" in title.upper():
                found.append((hwnd, title, win32gui.GetClassName(hwnd)))
    win32gui.EnumWindows(cb, None)
    return found


def find_valorant_hwnd():
    found = list_valorant_windows()
    # الأفضل: نافذة اللعبة نفسها (نوعها UnrealWindow)
    for hwnd, title, cls in found:
        if cls == "UnrealWindow":
            return hwnd
    # احتياط: نافذة عنوانها "VALORANT" بالضبط (مو تبويب كروم ولا مشهد OBS)
    for hwnd, title, cls in found:
        if title.strip().upper() == "VALORANT":
            return hwnd
    return None


def find_valorant_rect():
    """يرجع (left, top, width, height) لمساحة اللعب داخل نافذة فالورانت"""
    try:
        import win32gui
    except ImportError:
        return None
    hwnd = find_valorant_hwnd()
    if hwnd is None:
        return None
    if win32gui.IsIconic(hwnd):                 # اللعبة مصغّرة
        return "minimized"
    l, t, r, b = win32gui.GetClientRect(hwnd)
    if r - l < 400 or b - t < 300:              # نافذة صغيرة أو غريبة
        return "minimized"
    x, y = win32gui.ClientToScreen(hwnd, (0, 0))
    return x, y, r - l, b - t


def set_dpi_aware():
    """لو ويندوز على تكبير 125% أو 150%، بدون هذا الإحداثيات تطلع غلط"""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


# ===== WebSocket: يرسل الـ HP للـ overlay =====
WS_PORT = 8765
clients = set()
current = {"hp": None, "shield": 0, "regen": False}


async def ws_handler(ws):
    clients.add(ws)
    try:
        await ws.send(json.dumps(current))      # أول ما يتصل يعطيه آخر قيمة
        await ws.wait_closed()
    finally:
        clients.discard(ws)


async def broadcast(hp, shield=0, regen=False):
    current["hp"] = hp
    current["shield"] = shield or 0
    current["regen"] = bool(regen and shield)
    msg = json.dumps(current)
    for ws in list(clients):
        try:
            await ws.send(msg)
        except Exception:
            clients.discard(ws)


async def live(debug=False):
    import mss
    templates = load_templates()
    small_t = load_small_templates()
    print(f"Templates loaded: {sorted(templates)} | small: {sorted(small_t)}")
    print(f"WebSocket running on ws://localhost:{WS_PORT}")
    # منطقة وحدة تغطي الشيلد والـ HP
    X0, Y0 = SHIELD_ROI[0], min(SHIELD_ROI[1], ROI[1])
    X1 = ROI[0] + ROI[2]
    Y1 = max(SHIELD_ROI[1] + SHIELD_ROI[3], ROI[1] + ROI[3])
    last_sent = "init"
    history = deque(maxlen=5)
    none_streak = 0
    shown_shield = (0, False)                   # آخر شيلد انرسل فعلًا
    errors = 0
    with mss.mss() as sct:
        while True:
            try:
                rect = find_valorant_rect()
                if rect == "minimized":
                    state = (None, None, False)
                else:
                    if rect is None:
                        mon = sct.monitors[1]
                        rect = (mon["left"], mon["top"], mon["width"], mon["height"])
                    gx, gy, gw, gh = rect
                    sx, sy = gw / BASE_W, gh / BASE_H
                    box = {"left": gx + int(X0 * sx), "top": gy + int(Y0 * sy),
                           "width": int((X1 - X0) * sx), "height": int((Y1 - Y0) * sy)}
                    area = np.array(sct.grab(box))[:, :, :3]
                    if (sx, sy) != (1, 1):
                        area = cv2.resize(area, (X1 - X0, Y1 - Y0))

                    def sub(r):
                        x, y, w, h = r
                        return np.ascontiguousarray(area[y - Y0:y - Y0 + h, x - X0:x - X0 + w])
                    roi, sroi = sub(ROI), sub(SHIELD_ROI)
                    if debug:
                        cv2.imwrite(os.path.join(HERE, "roi_debug.png"), area)

                    hp = read_hp(roi, templates)
                    shield = read_shield(sroi, small_t, templates) if hp is not None else None
                    regen = is_regen(sroi) if shield else False
                    state = (hp, shield, regen)
                errors = 0
            except Exception as e:
                # أي خطأ مؤقت (شاشة مقفلة، نافذة طالعة برا الشاشة...) ما يطيّح السكربت
                errors += 1
                if errors == 1 or errors % 50 == 0:
                    print("Warning: temporary capture error -", e, flush=True)
                state = (None, None, False)

            # فلتر الضجيج بالتصويت: نشوف آخر 5 قراءات، والقيمة اللي تتكرر أكثر تفوز.
            # كذا لو القراءة "ترمش" (مرة رقم ومرة لا) ما تتجمد القلوب
            history.append(state)
            none_streak = none_streak + 1 if state[0] is None else 0
            pending = None
            if none_streak >= int(HOLD_NONE_SEC * FPS):
                pending = (None, None, False)
            else:
                votes = Counter(st for st in history if st[0] is not None)
                if votes:
                    best, count = votes.most_common(1)[0]
                    # لازم القيمة تطلع 3 مرات على الأقل من آخر 5 قراءات.
                    # قراءة غلط وحدة أو ثنتين ما تقدر تغيّر القلوب
                    if count >= 3:
                        pending = best
            if pending is not None and pending != last_sent:
                last_sent = pending
                hp, shield, regen = pending
                if hp is None:
                    shown_shield = (0, False)
                elif shield is None:
                    # الشيلد بس هو اللي ما انقرى: نخلي آخر قيمة معروفة
                    shield, regen = shown_shield
                else:
                    shown_shield = (shield, regen)
                print("HP:", "--" if hp is None else hp, "| Shield:", "--" if hp is None else shield,
                      "(regen)" if hp is not None and regen else "", flush=True)
                await broadcast(hp, shield, regen)
            await asyncio.sleep(1 / FPS)


async def demo():
    """يجرب الـ overlay بدون اللعبة: يسوي ضرر بشكل عشوائي (الشيلد ياخذ أول)"""
    import random
    print(f"Demo mode - WebSocket on ws://localhost:{WS_PORT}")
    while True:
        hp, shield = 100, random.choice([0, 25, 50])
        regen = shield == 25 and random.random() < 0.5
        await broadcast(hp, shield, regen); print("HP:", hp, "| Shield:", shield, "(regen)" if regen else "", flush=True)
        await asyncio.sleep(2)
        while hp > 0:
            dmg = random.choice([10, 15, 26, 40])
            absorbed = min(shield, round(dmg * 0.66))
            shield -= absorbed
            hp = max(0, hp - (dmg - absorbed))
            await broadcast(hp, shield, regen); print("HP:", hp, "| Shield:", shield, flush=True)
            await asyncio.sleep(random.uniform(1.2, 2.5))
        await asyncio.sleep(3)
        await broadcast(None); print("HP: --", flush=True)
        await asyncio.sleep(3)


# ===== وضع التشخيص: يطبع كل شي يشوفه السكربت =====
def diag(seconds=10):
    import mss
    set_dpi_aware()
    print("=== DIAG ===")
    try:
        wins = list_valorant_windows()
    except ImportError:
        print("!! pywin32 not installed -> run: pip install pywin32"); wins = []
    print("windows with VALORANT in title:")
    for hwnd, title, cls in wins:
        print(f"   title={title!r}  class={cls!r}")
    if not wins:
        print("   (none found - is the game open?)")
    templates, small_t = load_templates(), load_small_templates()
    X0, Y0 = SHIELD_ROI[0], min(SHIELD_ROI[1], ROI[1])
    X1 = ROI[0] + ROI[2]
    Y1 = max(SHIELD_ROI[1] + SHIELD_ROI[3], ROI[1] + ROI[3])
    stats = Counter()
    with mss.mss() as sct:
        t_end = time.time() + seconds
        i = 0
        while time.time() < t_end:
            rect = find_valorant_rect()
            if rect in (None, "minimized"):
                print(f"[{i}] window: {rect}  -> using full monitor" if rect is None else f"[{i}] minimized")
                if rect == "minimized":
                    time.sleep(0.5); i += 1; continue
                mon = sct.monitors[1]
                rect = (mon["left"], mon["top"], mon["width"], mon["height"])
            gx, gy, gw, gh = rect
            sx, sy = gw / BASE_W, gh / BASE_H
            box = {"left": gx + int(X0 * sx), "top": gy + int(Y0 * sy),
                   "width": int((X1 - X0) * sx), "height": int((Y1 - Y0) * sy)}
            area = np.array(sct.grab(box))[:, :, :3]
            if (sx, sy) != (1, 1):
                area = cv2.resize(area, (X1 - X0, Y1 - Y0))
            roi = crop_box(area, X0, Y0, ROI)
            glyphs, sus = segment(roi, with_flag=True)
            hp = read_hp(roi, templates, collect=False)
            sh = read_shield(crop_box(area, X0, Y0, SHIELD_ROI), small_t, templates, collect=False)
            stats["ok" if hp is not None else "none"] += 1
            if i % 5 == 0:
                print(f"[{i}] rect={rect} glyphs={len(glyphs)} suspicious={sus} -> HP={hp} shield={sh}")
            if i == 0 or i % 10 == 0:
                cv2.imwrite(os.path.join(HERE, "diag_area.png"),
                            cv2.resize(area, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST))
            i += 1
            time.sleep(1 / FPS)
    print(f"=== result: read OK {stats['ok']} times, failed {stats['none']} times ===")
    print("saved diag_area.png (what the script sees, 3x zoom)")


def crop_box(area, X0, Y0, r):
    x, y, w, h = r
    return np.ascontiguousarray(area[y - Y0:y - Y0 + h, x - X0:x - X0 + w])


# ===== سيرفر صغير يقدّم صفحة الـ overlay لـ OBS (بدون كاش) =====
HTTP_PORT = 8766


def start_http():
    import threading
    from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

    class NoCache(SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=HERE, **k)

        def end_headers(self):
            self.send_header("Cache-Control", "no-store, must-revalidate")
            super().end_headers()

        def log_message(self, *a):
            pass

    try:
        srv = ThreadingHTTPServer(("localhost", HTTP_PORT), NoCache)
    except OSError:
        raise SystemExit(f"Port {HTTP_PORT} is in use - another copy of the script is probably running. Close it and try again.")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"Overlay URL: http://localhost:{HTTP_PORT}/overlay.html  (in OBS, use obs.html as a Local file)")


async def main(mode):
    import websockets
    set_dpi_aware()
    start_http()
    try:
        server = await websockets.serve(ws_handler, "localhost", WS_PORT)
    except OSError:
        raise SystemExit(f"Port {WS_PORT} is in use - another copy of the script is probably running. Close it and try again.")
    async with server:
        await (demo() if mode == "demo" else live(debug=(mode == "debug")))


def test(paths):
    templates = load_templates()
    small_t = load_small_templates()
    for p in paths:
        frame = cv2.imread(p)
        hp = read_hp(crop(frame, ROI), templates, collect=False)
        sroi = crop(frame, SHIELD_ROI)
        sh = read_shield(sroi, small_t, templates, collect=False)
        print(os.path.basename(p), "-> HP:", hp, "| Shield:", sh, "(regen)" if sh and is_regen(sroi) else "")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--test":
        test(sys.argv[2:])
    elif "--diag" in sys.argv:
        diag()
    else:
        mode = "demo" if "--demo" in sys.argv else "debug" if "--debug" in sys.argv else "live"
        try:
            asyncio.run(main(mode))
        except KeyboardInterrupt:
            pass
