import ctypes
import json
import math
import re
import sys
import time
import tkinter as tk
from ctypes import byref, windll, wintypes
from pathlib import Path
from tkinter import filedialog

try:
    import winsound
except ImportError:  # pragma: no cover - Windows-only nicety.
    winsound = None


APP_DIR = Path(__file__).resolve().parent
ASSET_DIR = APP_DIR / "assets"
LOGO_FILE = ASSET_DIR / "notoow_logo.png"
STATE_FILE = APP_DIR / "timer_widget_state.json"
WIDGET_WIDTH = 360
FULL_HEIGHT = 270
COMPACT_HEIGHT = 184
QUICK_MINUTES = (5, 10, 25, 50, 60)
MAX_MINUTES = 999
MAX_TOTAL_SECONDS = MAX_MINUTES * 60 + 59
IS_WINDOWS = sys.platform.startswith("win")
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


class RECT(wintypes.RECT):
    pass


USER32 = windll.user32
USER32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
USER32.GetWindowRect.restype = wintypes.BOOL
USER32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_uint,
]
USER32.SetWindowPos.restype = wintypes.BOOL
USER32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
USER32.GetCursorPos.restype = wintypes.BOOL
USER32.GetSystemMetrics.argtypes = [ctypes.c_int]
USER32.GetSystemMetrics.restype = ctypes.c_int


class TimerWidget(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Timer Widget")
        self.configure(bg="#161616")
        self.overrideredirect(True)
        self.resizable(False, False)

        self.duration_minutes = tk.StringVar(value="25")
        self.duration_seconds = tk.StringVar(value="0")
        self.status_text = tk.StringVar(value="준비")
        self.pin_text = tk.StringVar(value="Pin")
        self.compact_button_text = tk.StringVar(value="Mini")
        self.sound_mode = tk.StringVar(value="default")
        self.sound_repeat = tk.IntVar(value=3)
        self.custom_sound_path = ""
        self.custom_sound_label_text = tk.StringVar(value="선택된 WAV 없음")
        self.time_text = tk.StringVar(value="25:00")

        self.remaining_seconds = 25 * 60
        self.running = False
        self.finished = False
        self.deadline = None
        self.drag_start_pointer_x = 0
        self.drag_start_pointer_y = 0
        self.drag_start_window_x = 0
        self.drag_start_window_y = 0
        self.alpha = 0.94
        self.always_on_top = True
        self.compact = False
        self.initial_x = 0
        self.initial_y = 0
        self.initial_height = FULL_HEIGHT
        self.settings_window = None
        self.completion_window = None
        self.sound_after_ids = []
        self.logo_source_image = None
        self.logo_image = None

        self._load_state()
        self._configure_window()
        self.pin_menu_var = tk.BooleanVar(value=self.always_on_top)
        self.compact_menu_var = tk.BooleanVar(value=self.compact)
        self._build_ui()
        self._build_context_menu()
        self._apply_pin()
        self._apply_alpha()
        self._update_display()

        self.bind("<space>", self.shortcut_toggle_timer)
        self.bind("<r>", self.shortcut_reset_timer)
        self.bind("<R>", self.shortcut_reset_timer)
        self.bind("<s>", self.shortcut_stop_timer)
        self.bind("<S>", self.shortcut_stop_timer)
        self.bind("<Escape>", self.shortcut_escape)
        self.protocol("WM_DELETE_WINDOW", self.close)

    def _configure_window(self):
        self.alpha = float(self._state.get("alpha", self.alpha))
        self.always_on_top = bool(self._state.get("always_on_top", self.always_on_top))
        self.compact = bool(self._state.get("compact", self.compact))
        self.sound_mode.set(str(self._state.get("sound_mode", self.sound_mode.get())))
        self.custom_sound_path = str(self._state.get("custom_sound_path", ""))
        if self.custom_sound_path:
            self.custom_sound_label_text.set(Path(self.custom_sound_path).name)
        try:
            self.sound_repeat.set(max(1, min(10, int(self._state.get("sound_repeat", self.sound_repeat.get())))))
        except (TypeError, ValueError):
            self.sound_repeat.set(3)
        minutes = str(self._state.get("minutes", self.duration_minutes.get()))
        seconds = str(self._state.get("seconds", self.duration_seconds.get()))
        self.duration_minutes.set(minutes)
        self.duration_seconds.set(seconds)
        self.remaining_seconds = self._current_total_seconds()
        height = COMPACT_HEIGHT if self.compact else FULL_HEIGHT

        x, y = self._saved_position()
        x, y = self._keep_window_visible(x, y, WIDGET_WIDTH, height)
        self.initial_x = x
        self.initial_y = y
        self.initial_height = height
        self.geometry(f"{WIDGET_WIDTH}x{height}")

    def _saved_position(self):
        if "x" in self._state and "y" in self._state:
            try:
                return int(self._state["x"]), int(self._state["y"])
            except (TypeError, ValueError):
                pass

        geometry = self._state.get("geometry")
        if geometry:
            match = re.match(r"\d+x\d+(.+)$", str(geometry))
            offsets = re.findall(r"[+-]?\d+", match.group(1) if match else "")
            if len(offsets) >= 2:
                return int(offsets[0]), int(offsets[1])

        screen_w = self.winfo_screenwidth()
        return max(20, screen_w - WIDGET_WIDTH - 56), 80

    def _keep_window_visible(self, x, y, width, height):
        if not IS_WINDOWS:
            return x, y

        virtual_x = USER32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        virtual_y = USER32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        virtual_w = USER32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        virtual_h = USER32.GetSystemMetrics(SM_CYVIRTUALSCREEN)

        min_visible = 80
        min_x = virtual_x - width + min_visible
        max_x = virtual_x + virtual_w - min_visible
        min_y = virtual_y
        max_y = virtual_y + virtual_h - min_visible

        x = min(max(int(x), min_x), max_x)
        y = min(max(int(y), min_y), max_y)
        return x, y

    def _build_ui(self):
        self.shell = tk.Frame(self, bg="#161616", highlightthickness=1, highlightbackground="#343434")
        self.shell.pack(fill="both", expand=True)

        self.titlebar = tk.Frame(self.shell, bg="#161616")
        self.titlebar.pack(fill="x", padx=10, pady=(8, 0))
        self._make_draggable(self.titlebar)

        self.logo_image = self._load_logo_image()
        if self.logo_image is not None:
            logo = tk.Label(self.titlebar, image=self.logo_image, bg="#161616")
            logo.pack(side="left", padx=(0, 6))
            self._make_draggable(logo)

        title = tk.Label(
            self.titlebar,
            text="Timer",
            bg="#161616",
            fg="#e8e3d8",
            font=("Segoe UI Semibold", 10),
        )
        title.pack(side="left")
        self._make_draggable(title)

        close_button = self._small_button(self.titlebar, "x", self.close, "#2a2a2a", "#f4b8b8")
        close_button.pack(side="right")

        settings_button = self._small_button(self.titlebar, "⚙", self.open_settings)
        settings_button.pack(side="right", padx=(0, 6))

        pin_button = self._small_button(self.titlebar, textvariable=self.pin_text, command=self.toggle_pin)
        pin_button.pack(side="right", padx=(0, 6))

        compact_button = self._small_button(
            self.titlebar,
            textvariable=self.compact_button_text,
            command=self.toggle_compact,
        )
        compact_button.pack(side="right", padx=(0, 6))

        self.time_label = tk.Label(
            self.shell,
            textvariable=self.time_text,
            bg="#161616",
            fg="#f7f0df",
            font=("Segoe UI Semibold", 38),
        )
        self.time_label.pack(fill="x", padx=14, pady=(8, 0))
        self._make_draggable(self.time_label)

        self.status_label = tk.Label(
            self.shell,
            textvariable=self.status_text,
            bg="#161616",
            fg="#8bd3c7",
            font=("Segoe UI", 9),
        )
        self.status_label.pack(fill="x", padx=14)
        self._make_draggable(self.status_label)

        self.progress = tk.Canvas(self.shell, height=6, bg="#242424", highlightthickness=0)
        self.progress.pack(fill="x", padx=16, pady=(8, 8))

        self.actions = tk.Frame(self.shell, bg="#161616")
        self.actions.pack(fill="x", padx=12, pady=(0, 8))

        start_button = tk.Button(
            self.actions,
            text="시작",
            command=self.start_timer,
            bg="#f28c38",
            fg="#171717",
            activebackground="#ffad5f",
            activeforeground="#171717",
            relief="flat",
            bd=0,
            padx=8,
            pady=7,
            font=("Malgun Gothic", 9, "bold"),
            cursor="hand2",
        )
        start_button.pack(side="left", expand=True, fill="x", padx=3)

        pause_button = tk.Button(
            self.actions,
            text="일시정지",
            command=self.pause_timer,
            bg="#2b2b2b",
            fg="#f7f0df",
            activebackground="#3c3c3c",
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            padx=8,
            pady=7,
            font=("Malgun Gothic", 9),
            cursor="hand2",
        )
        pause_button.pack(side="left", expand=True, fill="x", padx=3)

        stop_button = tk.Button(
            self.actions,
            text="정지",
            command=self.stop_timer,
            bg="#2b2b2b",
            fg="#f7f0df",
            activebackground="#3c3c3c",
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            padx=8,
            pady=7,
            font=("Malgun Gothic", 9),
            cursor="hand2",
        )
        stop_button.pack(side="left", expand=True, fill="x", padx=3)

        self.controls = tk.Frame(self.shell, bg="#161616")
        self.controls.pack(fill="x", padx=12)

        quick_row = tk.Frame(self.controls, bg="#161616")
        quick_row.pack(fill="x", pady=(0, 7))
        for column, minutes in enumerate(QUICK_MINUTES):
            quick_row.grid_columnconfigure(column, weight=1, uniform="quick-minutes")
            button = self._split_minute_button(quick_row, minutes)
            button.grid(row=0, column=column, sticky="ew", padx=2)

        custom_row = tk.Frame(
            self.controls,
            bg="#202020",
            highlightthickness=1,
            highlightbackground="#343434",
        )
        custom_row.pack(fill="x", pady=(2, 8), ipady=5)

        minus_button = self._chip_button(custom_row, "-1", lambda: self.bump_minutes(-1))
        minus_button.pack(side="left", padx=(6, 5))

        minute_validation = (self.register(self.validate_minutes_input), "%P")
        second_validation = (self.register(self.validate_seconds_input), "%P")
        self.minutes_entry = tk.Entry(
            custom_row,
            textvariable=self.duration_minutes,
            width=4,
            justify="center",
            bg="#151515",
            fg="#f7f0df",
            insertbackground="#f7f0df",
            relief="flat",
            font=("Segoe UI", 10),
            validate="key",
            validatecommand=minute_validation,
        )
        self.minutes_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.minutes_entry.bind("<Button-1>", self.select_minutes_text)
        self.minutes_entry.bind("<FocusIn>", self.select_minutes_text)
        self.minutes_entry.bind("<KeyRelease>", self.apply_custom_minutes_live)
        self.minutes_entry.bind("<Return>", lambda _event: self.apply_custom_minutes())
        self.minutes_entry.bind("<FocusOut>", lambda _event: self.apply_custom_minutes())

        label = tk.Label(custom_row, text="min", bg="#161616", fg="#aaa395", font=("Segoe UI", 9))
        label.configure(bg="#202020")
        label.pack(side="left", padx=(0, 6))

        self.seconds_entry = tk.Entry(
            custom_row,
            textvariable=self.duration_seconds,
            width=3,
            justify="center",
            bg="#151515",
            fg="#f7f0df",
            insertbackground="#f7f0df",
            relief="flat",
            font=("Segoe UI", 10),
            validate="key",
            validatecommand=second_validation,
        )
        self.seconds_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.seconds_entry.bind("<Button-1>", self.select_seconds_text)
        self.seconds_entry.bind("<FocusIn>", self.select_seconds_text)
        self.seconds_entry.bind("<KeyRelease>", self.apply_custom_minutes_live)
        self.seconds_entry.bind("<Return>", lambda _event: self.apply_custom_minutes())
        self.seconds_entry.bind("<FocusOut>", lambda _event: self.apply_custom_minutes())

        seconds_label = tk.Label(custom_row, text="sec", bg="#202020", fg="#aaa395", font=("Segoe UI", 9))
        seconds_label.pack(side="left", padx=(0, 6))

        plus_button = self._chip_button(custom_row, "+1", lambda: self.bump_minutes(1))
        plus_button.pack(side="left", padx=(0, 6))

        self.bind("<Button-3>", self.show_menu)
        self.shell.bind("<Button-3>", self.show_menu)
        self.titlebar.bind("<Button-3>", self.show_menu)
        self.time_label.bind("<Button-3>", self.show_menu)
        self.status_label.bind("<Button-3>", self.show_menu)
        self.actions.bind("<Button-3>", self.show_menu)

        self._make_draggable(self.shell)
        self._make_draggable(self.progress)

        self._sync_compact_state()
        self.after_idle(lambda: self._set_window_bounds(self.initial_x, self.initial_y, WIDGET_WIDTH, self.initial_height))

    def _build_context_menu(self):
        self.menu = tk.Menu(self, tearoff=0, bg="#202020", fg="#f7f0df", activebackground="#3a3a3a")
        self.menu.add_command(label="Start", command=self.start_timer)
        self.menu.add_command(label="Pause", command=self.pause_timer)
        self.menu.add_command(label="Stop", command=self.stop_timer)
        self.menu.add_command(label="Reset", command=self.reset_timer)
        self.menu.add_separator()
        self.menu.add_checkbutton(
            label="Always on top",
            variable=self.pin_menu_var,
            command=self.toggle_pin,
        )
        self.menu.add_command(label="Opacity 100%", command=lambda: self.set_opacity(1.0))
        self.menu.add_command(label="Opacity 90%", command=lambda: self.set_opacity(0.90))
        self.menu.add_command(label="Opacity 75%", command=lambda: self.set_opacity(0.75))
        self.menu.add_checkbutton(
            label="Mini mode",
            variable=self.compact_menu_var,
            command=self.toggle_compact,
        )
        self.menu.add_separator()
        self.menu.add_command(label="Exit", command=self.close)

    def _widget_exists(self, widget):
        return widget is not None and widget.winfo_exists()

    def _focus_is_text_input(self):
        focused = self.focus_get()
        if focused is None:
            return False
        return focused.winfo_class() in {"Entry", "TEntry", "Text", "Spinbox", "TSpinbox"}

    def _modal_window_open(self):
        return self._widget_exists(self.settings_window) or self._widget_exists(self.completion_window)

    def _can_use_main_shortcut(self):
        return not self._focus_is_text_input() and not self._modal_window_open()

    def shortcut_toggle_timer(self, _event=None):
        if self._can_use_main_shortcut():
            self.toggle_timer()
        return "break"

    def shortcut_reset_timer(self, _event=None):
        if self._can_use_main_shortcut():
            self.reset_timer()
        return "break"

    def shortcut_stop_timer(self, _event=None):
        if self._can_use_main_shortcut():
            self.stop_timer()
        return "break"

    def shortcut_escape(self, _event=None):
        if self._widget_exists(self.completion_window):
            self.dismiss_completion_popup()
        elif self._widget_exists(self.settings_window):
            self.close_settings()
        elif not self._focus_is_text_input():
            self.close()
        return "break"

    def _small_button(self, parent, text=None, command=None, bg="#252525", fg="#d8d1c4", textvariable=None):
        return tk.Button(
            parent,
            text=text,
            textvariable=textvariable,
            command=command,
            bg=bg,
            fg=fg,
            activebackground="#3a3a3a",
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            padx=8,
            pady=3,
            font=("Segoe UI", 8),
            cursor="hand2",
        )

    def _load_logo_image(self):
        if not LOGO_FILE.exists():
            return None
        try:
            self.logo_source_image = tk.PhotoImage(file=str(LOGO_FILE))
        except tk.TclError:
            return None

        max_size = max(self.logo_source_image.width(), self.logo_source_image.height())
        scale = max(1, math.ceil(max_size / 18))
        return self.logo_source_image.subsample(scale, scale)

    def _chip_button(self, parent, text, command):
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg="#242424",
            fg="#e9dfcc",
            activebackground="#383838",
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            padx=8,
            pady=5,
            font=("Segoe UI", 9),
            cursor="hand2",
        )

    def _split_minute_button(self, parent, minutes):
        button = tk.Canvas(
            parent,
            width=1,
            height=29,
            bg="#242424",
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        button.minutes = minutes
        button.hover_side = None
        button.bind("<Configure>", lambda event, widget=button: self._draw_split_minute_button(widget))
        button.bind("<Motion>", lambda event, widget=button: self._hover_split_minute_button(widget, event.x))
        button.bind("<Leave>", lambda _event, widget=button: self._leave_split_minute_button(widget))
        button.bind("<Button-1>", lambda event, widget=button: self._click_split_minute_button(widget, event.x))
        return button

    def _draw_split_minute_button(self, button):
        button.delete("all")
        width = max(1, button.winfo_width())
        height = max(1, button.winfo_height())
        mid = width / 2
        arrow_zone = min(20, max(16, width * 0.28))

        button.create_rectangle(0, 0, width, height, fill="#242424", width=0)
        if button.hover_side == "left":
            button.create_rectangle(0, 0, arrow_zone, height, fill="#303030", width=0)
        elif button.hover_side == "right":
            button.create_rectangle(width - arrow_zone, 0, width, height, fill="#303030", width=0)

        button.create_line(mid, 6, mid, height - 6, fill="#363636")
        left_arrow_color = "#e9dfcc" if button.hover_side == "left" else "#8e887d"
        right_arrow_color = "#e9dfcc" if button.hover_side == "right" else "#8e887d"
        button.create_text(9, height / 2, text="<", fill=left_arrow_color, font=("Segoe UI", 8))
        button.create_text(width - 9, height / 2, text=">", fill=right_arrow_color, font=("Segoe UI", 8))
        button.create_text(
            width / 2,
            height / 2,
            text=f"{button.minutes}m",
            fill="#e9dfcc",
            font=("Segoe UI", 9),
        )

    def _hover_split_minute_button(self, button, x):
        side = "left" if x < button.winfo_width() / 2 else "right"
        if button.hover_side != side:
            button.hover_side = side
            self._draw_split_minute_button(button)

    def _leave_split_minute_button(self, button):
        button.hover_side = None
        self._draw_split_minute_button(button)

    def _click_split_minute_button(self, button, x):
        if x < button.winfo_width() / 2:
            self.subtract_minutes(button.minutes)
        else:
            self.add_minutes(button.minutes)

    def open_settings(self):
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.lift()
            self.settings_window.focus_force()
            return

        window = tk.Toplevel(self)
        self.settings_window = window
        window.title("Timer Settings")
        window.configure(bg="#161616")
        window.resizable(False, False)
        window.transient(self)
        window.attributes("-topmost", self.always_on_top)
        window.protocol("WM_DELETE_WINDOW", self.close_settings)
        window.bind("<Escape>", lambda _event: self.close_settings())

        panel = tk.Frame(window, bg="#161616", padx=14, pady=12)
        panel.pack(fill="both", expand=True)

        tk.Label(
            panel,
            text="완료 소리",
            bg="#161616",
            fg="#f7f0df",
            font=("Malgun Gothic", 10, "bold"),
        ).pack(anchor="w")

        sound_options = (
            ("기본음", "default"),
            ("짧은 알림음", "short"),
            ("긴 알람", "long"),
            ("무음", "silent"),
            ("사용자 WAV", "custom"),
        )
        for label, value in sound_options:
            self._settings_radio(panel, label, self.sound_mode, value).pack(anchor="w", pady=(5, 0))

        file_row = tk.Frame(panel, bg="#161616")
        file_row.pack(fill="x", pady=(8, 4))
        self._settings_button(file_row, "WAV 선택", self.choose_custom_sound).pack(side="left")
        tk.Label(
            file_row,
            textvariable=self.custom_sound_label_text,
            bg="#161616",
            fg="#aaa395",
            font=("Malgun Gothic", 8),
        ).pack(side="left", padx=(8, 0))

        tk.Label(
            panel,
            text="반복",
            bg="#161616",
            fg="#f7f0df",
            font=("Malgun Gothic", 10, "bold"),
        ).pack(anchor="w", pady=(12, 0))

        repeat_row = tk.Frame(panel, bg="#161616")
        repeat_row.pack(fill="x", pady=(4, 0))
        for count in (1, 3, 5):
            self._settings_radio(repeat_row, f"{count}회", self.sound_repeat, count).pack(side="left", padx=(0, 10))

        tk.Label(
            panel,
            text="투명도",
            bg="#161616",
            fg="#f7f0df",
            font=("Malgun Gothic", 10, "bold"),
        ).pack(anchor="w", pady=(12, 0))
        opacity = tk.Scale(
            panel,
            from_=35,
            to=100,
            orient="horizontal",
            bg="#161616",
            fg="#f7f0df",
            troughcolor="#242424",
            highlightthickness=0,
            activebackground="#f28c38",
            command=lambda value: self.set_opacity(float(value) / 100),
        )
        opacity.set(int(self.alpha * 100))
        opacity.pack(fill="x")

        action_row = tk.Frame(panel, bg="#161616")
        action_row.pack(fill="x", pady=(12, 0))
        self._settings_button(action_row, "테스트", self.play_completion_sound).pack(side="left")
        self._settings_button(action_row, "위치 초기화", self.reset_window_position).pack(side="left", padx=(8, 0))
        self._settings_button(action_row, "닫기", self.close_settings).pack(side="right")

        self._place_settings_window(window)

    def _settings_radio(self, parent, label, variable, value):
        return tk.Radiobutton(
            parent,
            text=label,
            variable=variable,
            value=value,
            command=self._save_state,
            bg="#161616",
            fg="#e9dfcc",
            activebackground="#161616",
            activeforeground="#ffffff",
            selectcolor="#242424",
            font=("Malgun Gothic", 9),
        )

    def _settings_button(self, parent, text, command):
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg="#242424",
            fg="#f7f0df",
            activebackground="#383838",
            activeforeground="#ffffff",
            relief="flat",
            bd=0,
            padx=9,
            pady=5,
            font=("Malgun Gothic", 9),
            cursor="hand2",
        )

    def _place_settings_window(self, window):
        self.update_idletasks()
        x, y, width, _height = self._window_bounds()
        window.update_idletasks()
        settings_width = max(280, window.winfo_width())
        settings_height = max(320, window.winfo_height())
        x, y = self._keep_window_visible(x + width + 10, y, settings_width, settings_height)
        window.geometry(f"{settings_width}x{settings_height}{x:+d}{y:+d}")

    def choose_custom_sound(self):
        path = filedialog.askopenfilename(
            parent=self.settings_window or self,
            title="완료 소리 WAV 선택",
            filetypes=(("WAV files", "*.wav"), ("All files", "*.*")),
        )
        if not path:
            return
        self.custom_sound_path = path
        self.custom_sound_label_text.set(Path(path).name)
        self.sound_mode.set("custom")
        self._save_state()

    def close_settings(self):
        self._save_state()
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.destroy()
        self.settings_window = None

    def reset_window_position(self):
        height = COMPACT_HEIGHT if self.compact else FULL_HEIGHT
        self._set_window_bounds(80, 80, WIDGET_WIDTH, height)
        self.status_text.set("위치 초기화")
        self._save_state()

    def _make_draggable(self, widget):
        widget.bind("<ButtonPress-1>", self.start_drag)
        widget.bind("<B1-Motion>", self.drag)

    def select_minutes_text(self, _event=None):
        self.after_idle(lambda: self.minutes_entry.selection_range(0, tk.END))

    def select_seconds_text(self, _event=None):
        self.after_idle(lambda: self.seconds_entry.selection_range(0, tk.END))

    def validate_minutes_input(self, value):
        return value == "" or (value.isdigit() and len(value) <= 3)

    def validate_seconds_input(self, value):
        return value == "" or (value.isdigit() and len(value) <= 2 and int(value) <= 59)

    def _window_handle(self):
        if not IS_WINDOWS:
            return None
        try:
            handle = self.wm_frame()
        except tk.TclError:
            handle = self.winfo_id()
        if isinstance(handle, str):
            return wintypes.HWND(int(handle, 0))
        return wintypes.HWND(int(handle))

    def _window_bounds(self):
        self.update_idletasks()
        if IS_WINDOWS:
            rect = RECT()
            handle = self._window_handle()
            if handle and USER32.GetWindowRect(handle, byref(rect)):
                return (
                    int(rect.left),
                    int(rect.top),
                    int(rect.right - rect.left),
                    int(rect.bottom - rect.top),
                )
        return self.winfo_x(), self.winfo_y(), self.winfo_width(), self.winfo_height()

    def _set_window_bounds(self, x, y, width, height):
        x, y, width, height = int(x), int(y), int(width), int(height)
        self.update_idletasks()
        if IS_WINDOWS:
            handle = self._window_handle()
            moved = handle and USER32.SetWindowPos(
                handle,
                wintypes.HWND(0),
                x,
                y,
                width,
                height,
                SWP_NOZORDER | SWP_NOACTIVATE,
            )
            if moved:
                return
        self.geometry(f"{width}x{height}{x:+d}{y:+d}")

    def _pointer_position(self):
        if IS_WINDOWS:
            point = wintypes.POINT()
            if USER32.GetCursorPos(byref(point)):
                return int(point.x), int(point.y)
        return self.winfo_pointerx(), self.winfo_pointery()

    def _load_state(self):
        self._state = {}
        if not STATE_FILE.exists():
            return
        try:
            self._state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._state = {}

    def _save_state(self):
        x, y, width, height = self._window_bounds()
        state = {
            "geometry": f"{width}x{height}{x:+d}{y:+d}",
            "x": x,
            "y": y,
            "alpha": self.alpha,
            "always_on_top": self.always_on_top,
            "compact": self.compact,
            "minutes": self.duration_minutes.get().strip() or "0",
            "seconds": self.duration_seconds.get().strip() or "0",
            "sound_mode": self.sound_mode.get(),
            "sound_repeat": self.sound_repeat.get(),
            "custom_sound_path": self.custom_sound_path,
        }
        try:
            STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _entry_number(self, value, default=0):
        try:
            text = str(value).strip()
            if text == "":
                return default
            return int(text)
        except ValueError:
            return default

    def _current_total_seconds(self):
        minutes = max(0, min(MAX_MINUTES, self._entry_number(self.duration_minutes.get())))
        seconds = max(0, min(59, self._entry_number(self.duration_seconds.get())))
        return min(MAX_TOTAL_SECONDS, minutes * 60 + seconds)

    def _set_input_from_seconds(self, total_seconds):
        total_seconds = max(0, min(MAX_TOTAL_SECONDS, int(math.ceil(total_seconds))))
        minutes, seconds = divmod(total_seconds, 60)
        self.duration_minutes.set(str(minutes))
        self.duration_seconds.set(str(seconds))

    def _format_time(self, seconds):
        seconds = max(0, int(math.ceil(seconds)))
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def _progress_ratio(self):
        total = max(1, self._current_total_seconds())
        elapsed = total - self.remaining_seconds
        return max(0.0, min(1.0, elapsed / total))

    def _draw_progress(self):
        self.progress.delete("all")
        width = self.progress.winfo_width()
        height = self.progress.winfo_height()
        if width <= 1:
            self.after(50, self._draw_progress)
            return
        fill_width = int(width * self._progress_ratio())
        self.progress.create_rectangle(0, 0, width, height, fill="#2b2b2b", width=0)
        if fill_width > 0:
            color = "#d66b40" if self.finished else "#4bb39f"
            self.progress.create_rectangle(0, 0, fill_width, height, fill=color, width=0)

    def _update_display(self):
        self.time_text.set(self._format_time(self.remaining_seconds))
        self.pin_text.set("Top" if self.always_on_top else "Pin")
        self._draw_progress()

    def _tick(self):
        if not self.running or self.deadline is None:
            return

        self.remaining_seconds = max(0, self.deadline - time.monotonic())
        self._update_display()

        if self.remaining_seconds <= 0:
            self.finish_timer()
            return

        self.after(180, self._tick)

    def _sync_remaining_from_deadline(self):
        if self.running:
            self.remaining_seconds = max(0, (self.deadline or time.monotonic()) - time.monotonic())

    def set_duration(self, minutes):
        if self.running:
            self.pause_timer()
        minutes = max(0, min(MAX_MINUTES, int(minutes)))
        total_seconds = minutes * 60
        self.finished = False
        self._set_input_from_seconds(total_seconds)
        self.remaining_seconds = total_seconds
        self.status_text.set(f"{minutes}분 설정")
        self._update_display()

    def add_minutes(self, minutes):
        self._sync_remaining_from_deadline()
        add_seconds = minutes * 60

        if self.finished or self.remaining_seconds <= 0:
            new_total = min(MAX_TOTAL_SECONDS, add_seconds)
            self.remaining_seconds = new_total
        else:
            current_total = self._current_total_seconds()
            new_total = min(MAX_TOTAL_SECONDS, current_total + add_seconds)
            self.remaining_seconds = min(new_total, self.remaining_seconds + add_seconds)

        self._set_input_from_seconds(new_total)
        if self.running:
            self.deadline = time.monotonic() + self.remaining_seconds
        self.finished = False
        self.status_text.set(f"+{minutes}분 추가")
        self._update_display()

    def subtract_minutes(self, minutes):
        self._sync_remaining_from_deadline()
        subtract_seconds = minutes * 60

        if self.remaining_seconds <= 0:
            new_remaining = 0
            new_total = 0
        else:
            current_total = self._current_total_seconds()
            new_remaining = max(0, self.remaining_seconds - subtract_seconds)
            if new_remaining <= 0:
                new_total = 0
            else:
                new_total = max(new_remaining, current_total - subtract_seconds)

        self.remaining_seconds = new_remaining
        self._set_input_from_seconds(new_total)
        self.finished = False

        if self.running and self.remaining_seconds > 0:
            self.deadline = time.monotonic() + self.remaining_seconds
            self.status_text.set("진행 중")
        else:
            self.running = False
            self.deadline = None
            self.status_text.set(f"-{minutes}분")

        self._update_display()

    def apply_custom_minutes(self):
        minute_value = self.duration_minutes.get().strip()
        second_value = self.duration_seconds.get().strip()
        if minute_value == "" and second_value == "":
            self.remaining_seconds = 0
            self.deadline = None
            self.finished = False
            self.running = False
            self.status_text.set("입력 대기")
            self._update_display()
            return

        seconds = self._current_total_seconds()
        minutes, seconds_part = divmod(seconds, 60)
        if minute_value == "":
            self.duration_minutes.set("0")
        if second_value == "":
            self.duration_seconds.set("0")
        self.finished = False
        self.remaining_seconds = seconds
        if seconds <= 0:
            self.running = False
            self.deadline = None
            self.status_text.set("0초 설정")
        elif self.running:
            self.deadline = time.monotonic() + self.remaining_seconds
            self.status_text.set("진행 중")
        else:
            if seconds_part:
                self.status_text.set(f"{minutes}분 {seconds_part}초 설정")
            else:
                self.status_text.set(f"{minutes}분 설정")
        self._update_display()

    def apply_custom_minutes_live(self, _event=None):
        self.after_idle(self.apply_custom_minutes)

    def bump_minutes(self, delta):
        if delta > 0:
            self.add_minutes(delta)
            return
        self.subtract_minutes(abs(delta))

    def toggle_timer(self):
        if self.running:
            self.pause_timer()
        else:
            self.start_timer()

    def start_timer(self):
        if self.running:
            return
        self.apply_custom_minutes() if self.remaining_seconds <= 0 else None
        if self.remaining_seconds <= 0:
            self.remaining_seconds = self._current_total_seconds()
        if self.remaining_seconds <= 0:
            self.status_text.set("시간 설정 필요")
            self._update_display()
            return
        self.finished = False
        self.running = True
        self.deadline = time.monotonic() + self.remaining_seconds
        self.status_text.set("진행 중")
        self._update_display()
        self._tick()

    def pause_timer(self):
        if not self.running:
            return
        if self.deadline is not None:
            self.remaining_seconds = max(0, self.deadline - time.monotonic())
        self.running = False
        self.deadline = None
        self.status_text.set("일시정지")
        self._update_display()

    def stop_timer(self):
        self.running = False
        self.finished = False
        self.deadline = None
        self.remaining_seconds = 0
        self.status_text.set("정지")
        self._update_display()

    def reset_timer(self):
        self.running = False
        self.finished = False
        self.deadline = None
        self.remaining_seconds = self._current_total_seconds()
        self.status_text.set("준비")
        self._update_display()

    def finish_timer(self):
        self.running = False
        self.finished = True
        self.deadline = None
        self.remaining_seconds = 0
        self.status_text.set("시간 끝")
        self._update_display()
        self._alert()

    def _alert(self):
        self.attributes("-topmost", True)
        self.lift()
        self.play_completion_sound()
        self.after(100, self._flash_once)
        self.after(260, self.show_completion_popup)

    def show_completion_popup(self):
        if self.completion_window is not None and self.completion_window.winfo_exists():
            self.completion_window.lift()
            return

        popup = tk.Toplevel(self)
        self.completion_window = popup
        popup.title("Timer Complete")
        popup.configure(bg="#161616")
        popup.resizable(False, False)
        popup.transient(self)
        popup.attributes("-topmost", True)
        popup.protocol("WM_DELETE_WINDOW", self.dismiss_completion_popup)
        popup.bind("<Escape>", lambda _event: self.dismiss_completion_popup())

        panel = tk.Frame(popup, bg="#161616", padx=16, pady=14)
        panel.pack(fill="both", expand=True)

        tk.Label(
            panel,
            text="시간이 다 됐습니다.",
            bg="#161616",
            fg="#f7f0df",
            font=("Malgun Gothic", 11, "bold"),
        ).pack(anchor="w")
        tk.Label(
            panel,
            text=self._format_time(self._current_total_seconds()),
            bg="#161616",
            fg="#8bd3c7",
            font=("Segoe UI Semibold", 24),
        ).pack(anchor="w", pady=(4, 10))

        action_row = tk.Frame(panel, bg="#161616")
        action_row.pack(fill="x")
        self._settings_button(action_row, "5분 더", self.snooze_five_minutes).pack(side="left")
        self._settings_button(action_row, "다시 시작", self.restart_timer_from_popup).pack(side="left", padx=(8, 0))
        self._settings_button(action_row, "닫기", self.dismiss_completion_popup).pack(side="right")

        self._place_completion_popup(popup)

    def _place_completion_popup(self, popup):
        self.update_idletasks()
        x, y, width, height = self._window_bounds()
        popup.update_idletasks()
        popup_width = max(280, popup.winfo_width())
        popup_height = max(130, popup.winfo_height())
        popup_x = x + max(0, (width - popup_width) // 2)
        popup_y = y + max(0, (height - popup_height) // 2)
        popup_x, popup_y = self._keep_window_visible(popup_x, popup_y, popup_width, popup_height)
        popup.geometry(f"{popup_width}x{popup_height}{popup_x:+d}{popup_y:+d}")

    def snooze_five_minutes(self):
        self.dismiss_completion_popup()
        self.set_duration(5)
        self.start_timer()

    def restart_timer_from_popup(self):
        self.dismiss_completion_popup()
        self.reset_timer()
        self.start_timer()

    def dismiss_completion_popup(self):
        self.stop_completion_sound()
        if self.completion_window is not None and self.completion_window.winfo_exists():
            self.completion_window.destroy()
        self.completion_window = None

    def stop_completion_sound(self):
        for after_id in self.sound_after_ids:
            try:
                self.after_cancel(after_id)
            except tk.TclError:
                pass
        self.sound_after_ids.clear()
        if winsound is not None:
            try:
                winsound.PlaySound(None, 0)
            except RuntimeError:
                pass

    def play_completion_sound(self):
        self.stop_completion_sound()
        mode = self.sound_mode.get()
        if mode == "silent":
            return

        if mode == "custom" and self.custom_sound_path and winsound is not None:
            sound_path = Path(self.custom_sound_path)
            if sound_path.exists():
                try:
                    winsound.PlaySound(
                        str(sound_path),
                        winsound.SND_FILENAME | winsound.SND_ASYNC,
                    )
                    return
                except RuntimeError:
                    pass

        try:
            repeat = max(1, min(10, int(self.sound_repeat.get())))
        except (TypeError, ValueError, tk.TclError):
            repeat = 3

        if mode == "short":
            delays = (0,)
        elif mode == "long":
            delays = tuple(range(0, 1500, 250))
        else:
            delays = tuple(index * 260 for index in range(repeat))

        for delay in delays:
            self.sound_after_ids.append(self.after(delay, self._beep))

    def _beep(self):
        if winsound is None:
            self.bell()
            return
        try:
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except RuntimeError:
            self.bell()

    def _flash_once(self, step=0):
        colors = ("#47251c", "#161616", "#47251c", "#161616")
        if step >= len(colors):
            return
        self.shell.configure(bg=colors[step])
        self.titlebar.configure(bg=colors[step])
        self.time_label.configure(bg=colors[step])
        self.status_label.configure(bg=colors[step])
        self.actions.configure(bg=colors[step])
        self.controls.configure(bg=colors[step])
        self.after(170, lambda: self._flash_once(step + 1))

    def toggle_pin(self):
        self.always_on_top = not self.always_on_top
        self.pin_menu_var.set(self.always_on_top)
        self._apply_pin()
        self._update_display()

    def _apply_pin(self):
        self.attributes("-topmost", self.always_on_top)

    def set_opacity(self, value):
        self.alpha = max(0.35, min(1.0, float(value)))
        self._apply_alpha()

    def _apply_alpha(self):
        self.attributes("-alpha", self.alpha)

    def toggle_compact(self):
        self.compact = not self.compact
        self.compact_menu_var.set(self.compact)
        self._sync_compact_state()

    def _sync_compact_state(self):
        if not hasattr(self, "controls"):
            return
        self.compact_button_text.set("Full" if self.compact else "Mini")
        if self.compact:
            self.controls.pack_forget()
            self._resize_at_current_position(WIDGET_WIDTH, COMPACT_HEIGHT)
        else:
            self.controls.pack(fill="x", padx=12)
            self._resize_at_current_position(WIDGET_WIDTH, FULL_HEIGHT)

    def _resize_at_current_position(self, width, height):
        x, y, _current_width, _current_height = self._window_bounds()
        self._set_window_bounds(x, y, width, height)

    def start_drag(self, event):
        self.drag_start_pointer_x, self.drag_start_pointer_y = self._pointer_position()
        self.drag_start_window_x, self.drag_start_window_y, _width, _height = self._window_bounds()

    def drag(self, event):
        pointer_x, pointer_y = self._pointer_position()
        _x, _y, width, height = self._window_bounds()
        x = self.drag_start_window_x + pointer_x - self.drag_start_pointer_x
        y = self.drag_start_window_y + pointer_y - self.drag_start_pointer_y
        self._set_window_bounds(x, y, width, height)

    def show_menu(self, event):
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def close(self):
        self._save_state()
        self.destroy()


def main():
    try:
        app = TimerWidget()
        app.mainloop()
    except tk.TclError as exc:
        print(f"Unable to start Timer Widget: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
