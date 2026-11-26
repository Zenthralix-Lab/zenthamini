#     ███████╗███████╗███╗   ██╗████████╗██╗  ██╗██████╗  █████╗ ██╗     ██╗██╗  ██╗
#     ╚══███╔╝██╔════╝████╗  ██║╚══██╔══╝██║  ██║██╔══██╗██╔══██╗██║     ██║╚██╗██╔╝
#       ███╔╝ █████╗  ██╔██╗ ██║   ██║   ███████║██████╔╝███████║██║     ██║ ╚███╔╝
#      ███╔╝  ██╔══╝  ██║╚██╗██║   ██║   ██╔══██║██╔══██╗██╔══██║██║     ██║ ██╔██╗
#     ███████╗███████╗██║ ╚████║   ██║   ██║  ██║██║  ██║██║  ██║███████╗██║██╔╝ ██╗
#     ╚══════╝╚══════╝╚═╝  ╚═══╝   ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝╚═╝╚═╝  ╚═╝
import curses
import random
import time
import math

# ─── KONFIGURASI ───
TARGET_TEXT       = "ZENTHRALIX"
MATRIX_CHARS      = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*()_+-=[]{}|;:,.<>?"
FPS               = 30
PARTICLES_PER_PIXEL = 4        # partikel per piksel target
LETTER_SPACING    = 0          # 0 = huruf rapat (seperti contoh), 1 = renggang

# ─── FONT ANSI SHADOW (6 baris) ───
FONT = {
    'Z': ["███████╗", "╚══███╔╝", "  ███╔╝ ", " ███╔╝  ", "███████╗", "╚══════╝"],
    'E': ["███████╗", "██╔════╝", "█████╗  ", "██╔══╝  ", "███████╗", "╚══════╝"],
    'N': ["███╗   ██╗", "████╗  ██║", "██╔██╗ ██║", "██║╚██╗██║", "██║ ╚████║", "╚═╝  ╚═══╝"],
    'T': ["████████╗", "╚══██╔══╝", "   ██║   ", "   ██║   ", "   ██║   ", "   ╚═╝   "],
    'H': ["██╗  ██╗", "██║  ██║", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
    'R': ["██████╗ ", "██╔══██╗", "██████╔╝", "██╔══██╗", "██║  ██║", "╚═╝  ╚═╝"],
    'A': [" █████╗ ", "██╔══██╗", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
    'L': ["██╗     ", "██║     ", "██║     ", "██║     ", "███████╗", "╚══════╝"],
    'I': ["██╗", "██║", "██║", "██║", "██║", "╚═╝"],
    'X': ["██╗  ██╗", "╚██╗██╔╝", " ╚███╔╝ ", " ██╔██╗ ", "██╔╝ ██╗", "╚═╝  ╚═╝"],
}

# ─── UTILITAS FONT ───
def text_width(text, spacing=LETTER_SPACING):
    total = 0
    for c in text:
        if c in FONT:
            total += max(len(r) for r in FONT[c]) + spacing
    return max(0, total - spacing)

def build_target_map(text, start_y, start_x, max_y, max_x, spacing=LETTER_SPACING):
    """Kembalikan dict {(y,x): karakter} dari pola teks besar."""
    out, cx = {}, start_x
    for c in text:
        if c not in FONT:
            cx += spacing + 1
            continue
        pat = FONT[c]
        w = max(len(r) for r in pat)
        for ry, row in enumerate(pat):
            for rx in range(w):
                ch = row[rx] if rx < len(row) else ' '
                if ch == ' ':
                    continue
                ty, tx = start_y + ry, cx + rx
                if 0 <= ty < max_y - 1 and 0 <= tx < max_x - 1:
                    out[(ty, tx)] = ch
        cx += w + spacing
    return out

# ─── HUJAN MATRIX ───
class MatrixRain:
    def __init__(self, x, max_y):
        self.x, self.max_y = x, max_y
        self.reset(initial=True)

    def reset(self, initial=False):
        self.y      = random.uniform(-self.max_y, 0) if initial else random.uniform(-30, -5)
        self.speed  = random.uniform(0.25, 0.8)
        self.length = random.randint(4, 16)
        self.chars  = [random.choice(MATRIX_CHARS) for _ in range(self.length)]

    def update(self):
        self.y += self.speed
        if self.y - self.length > self.max_y:
            self.reset()
        if random.random() < 0.06:
            self.chars[random.randint(0, self.length - 1)] = random.choice(MATRIX_CHARS)

# ─── PARTIKEL PENCARI TARGET ───
class SeekerParticle:
    def __init__(self, tx, ty, max_x, max_y):
        self.target_x, self.target_y = tx, ty
        self.x = random.uniform(0, max_x - 1)
        self.y = random.uniform(-60, -5)
        self.char       = random.choice(MATRIX_CHARS)
        self.state      = "falling"
        self.speed_y    = random.uniform(0.4, 1.4)
        self.pull_rate  = random.uniform(0.06, 0.14)
        self.trigger    = random.randint(-10, 10)   # variasi mulai tertarik

    def update(self):
        if self.state == "falling":
            self.y += self.speed_y
            if self.y > self.target_y - 8 + self.trigger or self.y > 1:
                self.state = "pulling"
        elif self.state == "pulling":
            dx = self.target_x - self.x
            dy = self.target_y - self.y
            d  = math.hypot(dx, dy)
            if d < 0.4:
                self.state = "settled"
                self.x, self.y = float(self.target_x), float(self.target_y)
            else:
                self.x += dx * self.pull_rate
                self.y += dy * self.pull_rate
        if self.state != "settled" and random.random() < 0.02:
            self.char = random.choice(MATRIX_CHARS)

# ─── PALET WARNA ───
def init_colors():
    curses.start_color()
    try:
        curses.use_default_colors()
    except Exception:
        pass
    pairs = [(1, 46), (2, 82), (3, 46), (4, 34), (5, 118)]
    if curses.COLORS >= 256:
        for pid, fg in pairs:
            try:
                curses.init_pair(pid, fg, -1)
            except Exception:
                try:
                    curses.init_pair(pid, curses.COLOR_GREEN, curses.COLOR_BLACK)
                except Exception:
                    pass
    else:
        for i in range(1, 6):
            try:
                curses.init_pair(i, curses.COLOR_GREEN, curses.COLOR_BLACK)
            except Exception:
                pass

# ─── UTILITAS ANIMASI SELAMAT DATANG ───
def welcome_animation(stdscr, target_text="ZENTHRALIX", duration=3):
    """Animasi welcome bertahap sebelum hujan matrix dimulai."""
    curses.curs_set(0)
    
    max_y, max_x = stdscr.getmaxyx()
    
    # Hitung posisi tengah
    text_w = text_width(target_text)
    text_h = 6
    ox = (max_x - text_w) // 2
    oy = (max_y - text_h) // 2
    
    # Warna untuk welcome
    for pid, fg in ((99, 46), (100, 196), (101, 226)):
        try:
            if curses.COLORS >= 256:
                curses.init_pair(pid, fg, -1)
            else:
                curses.init_pair(pid, curses.COLOR_GREEN, curses.COLOR_BLACK)
        except Exception:
            try:
                curses.init_pair(pid, curses.COLOR_GREEN, curses.COLOR_BLACK)
            except Exception:
                pass
    
    frame = 0
    max_frames = duration * FPS
    
    while frame < max_frames:
        stdscr.erase()
        frame += 1
        
        # Hitap progress animasi (0.0 - 1.0)
        progress = frame / max_frames
        
        # Efek fade-in untuk teks
        alpha = int(progress * 3)  # 0, 1, 2 atau 3 (kekuatan warna)
        
        # Gambar teks dengan ukuran yang bertambah
        scale = 1.0 + (progress * 0.5)  # dari 1.0 hingga 1.5
        
        # Header glow effect
        try:
            # Teks utama dengan efek mengecil/bertambah
            for c in target_text:
                if c in FONT:
                    pat = FONT[c]
                    w = max(len(r) for r in pat)
                    h = len(pat)
                    
                    # Posisi berdasarkan progress
                    pos_x = ox
                    pos_y = oy + int(progress * 2)
                    
                    # Gambar setiap baris font
                    for ry in range(h):
                        row = pat[ry]
                        for rx in range(w):
                            ch = row[rx] if rx < len(row) else ' '
                            if ch == ' ':
                                continue
                            tx = pos_x + rx
                            ty = pos_y + ry
                            
                            if 0 <= ty < max_y - 1 and 0 <= tx < max_x - 1:
                                # Warna berganti-ganti berdasarkan progress
                                color_id = min(99 + int(progress * 2), 101)
                                attr = curses.color_pair(color_id)
                                
                                # Efek glow: gambar beberapa kali dengan offset
                                for offset in range(-1, 2):
                                    try:
                                        stdscr.addstr(ty + offset, tx + offset, ch, attr)
                                    except curses.error:
                                        pass
        except Exception:
            pass
        
        # Teks subtitle/slogan
        subtitle = "Zentha AI · Maintenance Cross-Platform"
        try:
            sw = text_width(subtitle)
            sox = (max_x - sw) // 2
            soy = oy + h + 4
            for c in subtitle:
                if c in FONT:
                    pat = FONT[c]
                    w = max(len(r) for r in pat)
                    for ry in range(len(pat)):
                        row = pat[ry]
                        for rx in range(w):
                            ch = row[rx] if rx < len(row) else ' '
                            if ch == ' ':
                                continue
                            tx = sox + rx
                            ty = soy + ry
                            if 0 <= ty < max_y - 1 and 0 <= tx < max_x - 1:
                                try:
                                    stdscr.addstr(ty, tx, ch, curses.color_pair(99) | curses.A_BOLD)
                                except curses.error:
                                    pass
        except Exception:
            pass
        
        # Garis pembatas progres
        try:
            bar_len = int(progress * (max_x - 10))
            bar_x = 5
            bar_y = max_y - 2
            for i in range(bar_len):
                if 0 <= bar_y < max_y - 1 and 0 <= bar_x + i < max_x - 1:
                    stdscr.addstr(bar_y, bar_x + i, "▏", curses.color_pair(100))
        except Exception:
            pass
        
        stdscr.refresh()
        time.sleep(1.0 / FPS)
    
    # Bersihkan layar setelah animasi selesai
    stdscr.clear()
    time.sleep(0.5)

# ─── PRELOADER ───
def preloader_animation(stdscr, duration=2):
    """Preloader: loading bar + matrix flicker before welcome animation."""
    curses.curs_set(0)
    max_y, max_x = stdscr.getmaxyx()
    frames = max(1, duration * FPS)
    for f in range(frames):
        stdscr.erase()
        title = "ZENTHRALIX"
        try:
            stdscr.addstr(max_y // 2 - 2, max(0, (max_x - len(title)) // 2),
                          title, curses.A_BOLD)
        except curses.error:
            pass
        bar_w = min(40, max(10, max_x - 4))
        bx = max(0, (max_x - bar_w) // 2)
        by = max_y // 2
        progress = (f + 1) / frames
        filled = int(bar_w * progress)
        try:
            stdscr.addstr(by, bx, "[" + "#" * filled + "-" * (bar_w - filled) + "]")
            stdscr.addstr(by + 1, max(0, (max_x - 12) // 2),
                          "Loading {:3d}%".format(int(progress * 100)))
        except curses.error:
            pass
        for _ in range(5):
            rx = random.randrange(max(1, max_x))
            ry = random.randrange(max(1, max_y))
            try:
                stdscr.addstr(ry, rx, random.choice(MATRIX_CHARS), curses.A_DIM)
            except curses.error:
                pass
        stdscr.refresh()
        time.sleep(1.0 / FPS)
    stdscr.clear()
    time.sleep(0.3)

# ─── MAIN ───
def main(stdscr):
    # Preloader terlebih dahulu
    preloader_animation(stdscr, duration=2)
    # Jalankan animasi welcome terlebih dahulu
    welcome_animation(stdscr, TARGET_TEXT, duration=3)
    
    curses.curs_set(0)
    stdscr.nodelay(1)
    init_colors()

    max_y, max_x = stdscr.getmaxyx()
    tw = text_width(TARGET_TEXT)
    th = 6
    need_x, need_y = tw + 6, th + 6

    if max_x < need_x or max_y < need_y:
        stdscr.clear()
        try:
            stdscr.addstr(0, 0, "⚠  TERMINAL TERLALU KECIL", curses.A_BOLD)
            stdscr.addstr(1, 0, f"   Butuh: {need_x}x{need_y}  |  Anda: {max_x}x{max_y}")
            stdscr.addstr(2, 0, "   Zoom-out (Ctrl+Minus) atau perkecil font.")
        except curses.error:
            pass
        stdscr.refresh(); stdscr.getch(); return

    # Posisi tengah
    oy = (max_y - th) // 2
    ox = (max_x - tw) // 2
    target_map = build_target_map(TARGET_TEXT, oy, ox, max_y, max_x)

    # Latar hujan
    rain = [MatrixRain(x, max_y - 1) for x in range(0, max_x, 2)]

    # Partikel
    particles = []
    for _ in range(PARTICLES_PER_PIXEL):
        for (ty, tx) in target_map:
            particles.append(SeekerParticle(tx, ty, max_x, max_y))

    settled   = set()
    frame     = 0
    done_at   = None

    while True:
        stdscr.erase()
        frame += 1

        # ── 1. HUJAN ──
        for drop in rain:
            drop.update()
            for i in range(drop.length):
                ry = int(drop.y) - i
                if not (0 <= ry < max_y - 1):
                    continue
                try:
                    if i == 0:
                        stdscr.addstr(ry, drop.x, drop.chars[i],
                                      curses.color_pair(1) | curses.A_BOLD)
                    elif i < 3:
                        stdscr.addstr(ry, drop.x, drop.chars[i],
                                      curses.color_pair(1))
                    else:
                        stdscr.addstr(ry, drop.x, drop.chars[i],
                                      curses.color_pair(2) | curses.A_DIM)
                except curses.error:
                    pass

        # ── 2. PARTIKEL ──
        for p in particles:
            p.update()
            ix, iy = int(p.x), int(p.y)
            if not (0 <= iy < max_y - 1 and 0 <= ix < max_x - 1):
                continue
            try:
                if p.state == "settled":
                    if (iy, ix) in target_map:
                        settled.add((iy, ix))
                elif p.state == "pulling":
                    stdscr.addstr(iy, ix, p.char,
                                  curses.color_pair(5) | curses.A_BOLD)
                else:
                    stdscr.addstr(iy, ix, p.char,
                                  curses.color_pair(2))
            except curses.error:
                pass

        # ── 3. TEKS BESAR (reveal bertahap + pulse) ──
        for (ty, tx), ch in target_map.items():
            if (ty, tx) not in settled:
                continue
            try:
                if done_at is not None:
                    pulse = math.sin((frame - done_at) * 0.10)
                    if pulse > 0:
                        stdscr.addstr(ty, tx, ch,
                                      curses.color_pair(3) | curses.A_BOLD)
                    else:
                        stdscr.addstr(ty, tx, ch,
                                      curses.color_pair(4))
                else:
                    stdscr.addstr(ty, tx, ch,
                                  curses.color_pair(3) | curses.A_BOLD)
            except curses.error:
                pass

        # Cek selesai
        if done_at is None and len(settled) >= len(target_map):
            done_at = frame

        stdscr.refresh()
        time.sleep(1.0 / FPS)

        if stdscr.getch() in (ord('q'), ord('Q')):
            break

# ─── ENTRY ───
if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"Error: {e}")