"""Readout — the main window."""

import bisect
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image, ImageChops, ImageOps, ImageTk

from .document import PdfDocument, count_words
from .speech import SpeechWorker
from .store import Store

BG = "#0D0E12"
SURFACE = "#14161C"
SURFACE_2 = "#1B1E26"
SURFACE_3 = "#262A35"
BORDER = "#323746"
TEXT = "#ECEDF2"
MUTED = "#8A8FA0"
FAINT = "#5C6172"
ACCENT = "#8B7CFF"
ACCENT_HOVER = "#7867F2"
AMBER = "#FFD45A"
CANVAS_BG = "#0A0B0E"

# (tint, alpha) for the word and line highlights; alpha None = multiply blend
PAPER_WORD = ((255, 212, 90), None)
PAPER_LINE = ((238, 234, 255), None)
NIGHT_WORD = ((255, 196, 64), 0.45)
NIGHT_LINE = ((120, 104, 230), 0.22)

PAD = 28
SLEEP_CHOICES = ["Off", "End of page", "5 min", "15 min", "30 min", "60 min"]


def _resource(*parts):
    """Path to a bundled file, both from source and inside a PyInstaller build."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, *parts)


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _night_paper(img):
    """Dark page that keeps colours recognisable: invert brightness, not hue."""
    h, s, v = ImageOps.invert(img).convert("HSV").split()
    h = h.point(lambda x: (x + 128) % 256)
    img = Image.merge("HSV", (h, s, v)).convert("RGB")
    return img.point(lambda x: 24 + x * 196 // 255)  # charcoal paper, soft white ink


def _short_voice(name):
    return name.replace("Microsoft ", "").replace(" Desktop", "")


class ReadoutApp(ctk.CTk):
    def __init__(self, initial_path=None):
        super().__init__(fg_color=BG)
        self.title("Readout")
        self.geometry("1320x860")
        self.minsize(980, 640)
        try:
            self.iconbitmap(_resource("assets", "readout.ico"))
        except tk.TclError:
            pass

        self.store = Store()
        self.events = queue.Queue()
        self.speech = SpeechWorker(self.events)
        self.speech.start()

        self.voices = {}  # display name -> voice id
        self.doc = None
        self.page = 0
        self.page_text = None
        self.cursor = 0  # token the voice is on (or will start from)
        self.playing = False
        self.session = 0  # tags speech jobs so stale events are ignored
        self.export_tag = None
        self.zoom = 1.0
        self.fit_width = True
        self.word_counts = None
        self.sleep_job = None
        self.sleep_end_of_page = False

        self._page_pil = None
        self._page_photo = None
        self._overlays = []  # (canvas item, PhotoImage) pairs
        self._origin = (PAD, PAD)
        self._scroll_h = 1
        self._scroll_anim = None
        self._resize_job = None
        self._restart_job = None
        self._caption_sentence = None
        self._caption_spans = {}
        self._last_save = 0.0

        self.f_brand = ctk.CTkFont("Segoe UI", 19, "bold")
        self.f_body = ctk.CTkFont("Segoe UI", 13)
        self.f_small = ctk.CTkFont("Segoe UI", 12)
        self.f_caps = ctk.CTkFont("Segoe UI", 11, "bold")
        self.f_caption = ctk.CTkFont("Georgia", 19)
        self.f_icon = ctk.CTkFont("Segoe UI Symbol", 17)
        self.f_play = ctk.CTkFont("Segoe UI Symbol", 22)

        self._build_topbar()
        self._build_sidebar()
        self._build_viewer()
        self._build_deck()
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._bind_keys()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(30, self._poll_events)
        self.after(50, self._show_empty_state)
        self._refresh_library()
        if initial_path:
            self.after(150, lambda: self.open_pdf(initial_path))

    # ------------------------------------------------------------------ UI

    def _build_topbar(self):
        bar = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, height=62)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(1, weight=1)
        bar.grid_rowconfigure(0, weight=1)

        brand = ctk.CTkFrame(bar, fg_color="transparent")
        brand.grid(row=0, column=0, padx=(22, 0), sticky="w")
        ctk.CTkLabel(brand, text="◉", font=ctk.CTkFont("Segoe UI Symbol", 22),
                     text_color=ACCENT).pack(side="left")
        ctk.CTkLabel(brand, text=" Readout", font=self.f_brand, text_color=TEXT).pack(side="left")

        self.title_label = ctk.CTkLabel(bar, text="", font=self.f_body, text_color=MUTED, anchor="w")
        self.title_label.grid(row=0, column=1, padx=(54, 12), sticky="ew")

        tools = ctk.CTkFrame(bar, fg_color="transparent")
        tools.grid(row=0, column=2, padx=(0, 18), sticky="e")
        self.status_label = ctk.CTkLabel(tools, text="", font=self.f_small, text_color=MUTED)
        self.status_label.pack(side="left", padx=(0, 16))

        zoom = ctk.CTkFrame(tools, fg_color=SURFACE_2, corner_radius=10)
        zoom.pack(side="left", padx=(0, 10))
        self._icon_button(zoom, "−", lambda: self.zoom_by(1 / 1.15), size=32).pack(side="left", padx=2, pady=2)
        self.zoom_label = ctk.CTkButton(zoom, text="Fit", width=52, height=32, font=self.f_small,
                                        fg_color="transparent", hover_color=SURFACE_3,
                                        text_color=TEXT, command=self.zoom_fit)
        self.zoom_label.pack(side="left")
        self._icon_button(zoom, "+", lambda: self.zoom_by(1.15), size=32).pack(side="left", padx=2, pady=2)

        self.export_btn = ctk.CTkButton(tools, text="Export audio", width=118, height=36,
                                        font=self.f_body, fg_color="transparent",
                                        border_width=1, border_color=BORDER, hover_color=SURFACE_2,
                                        text_color=TEXT, corner_radius=10, command=self.export_audio)
        self.export_btn.pack(side="left", padx=(0, 10))
        ctk.CTkButton(tools, text="Open PDF", width=108, height=36, font=self.f_body,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#FFFFFF",
                      corner_radius=10, command=self.open_dialog).pack(side="left")

    def _build_sidebar(self):
        s = self.store.settings
        side = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0, width=290)
        side.grid(row=1, column=0, sticky="ns")
        side.pack_propagate(False)

        self._section(side, "VOICE")
        self.voice_menu = ctk.CTkOptionMenu(
            side, values=["Loading voices…"], command=self._on_voice, height=34,
            font=self.f_body, dropdown_font=self.f_body, fg_color=SURFACE_2,
            button_color=SURFACE_3, button_hover_color=BORDER, dropdown_fg_color=SURFACE_2,
            dropdown_hover_color=SURFACE_3, text_color=TEXT, corner_radius=10, dynamic_resizing=False)
        self.voice_menu.pack(fill="x", padx=20)

        self.rate_value = self._section(side, "SPEED")
        self.rate_slider = self._slider(side, 80, 400, 64, self._on_rate)
        self.rate_slider.set(s["rate"])
        self.rate_value.configure(text=f"{s['rate']} wpm")

        self.volume_value = self._section(side, "VOLUME")
        self.volume_slider = self._slider(side, 0, 1, 20, self._on_volume)
        self.volume_slider.set(s["volume"])
        self.volume_value.configure(text=f"{round(s['volume'] * 100)}%")

        self._section(side, "READING")
        self.follow_switch = self._switch(side, "Follow along", "follow", None)
        self.margin_switch = self._switch(side, "Skip headers & footers", "skip_margins", self._on_margins)
        self.night_switch = self._switch(side, "Night paper", "night", self._on_night)

        self._section(side, "SLEEP TIMER")
        self.sleep_menu = ctk.CTkOptionMenu(
            side, values=SLEEP_CHOICES, command=self._on_sleep, height=34, font=self.f_body,
            dropdown_font=self.f_body, fg_color=SURFACE_2, button_color=SURFACE_3,
            button_hover_color=BORDER, dropdown_fg_color=SURFACE_2, dropdown_hover_color=SURFACE_3,
            text_color=TEXT, corner_radius=10, dynamic_resizing=False)
        self.sleep_menu.pack(fill="x", padx=20)

        self._section(side, "LIBRARY")
        self.library = ctk.CTkScrollableFrame(side, fg_color="transparent",
                                              scrollbar_button_color=SURFACE_3,
                                              scrollbar_button_hover_color=BORDER)
        self.library.pack(fill="both", expand=True, padx=8, pady=(0, 12))

    def _build_viewer(self):
        wrap = ctk.CTkFrame(self, fg_color=CANVAS_BG, corner_radius=0)
        wrap.grid(row=1, column=1, sticky="nsew")
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(wrap, bg=CANVAS_BG, highlightthickness=0, bd=0, cursor="xterm")
        vbar = ctk.CTkScrollbar(wrap, orientation="vertical", command=self.canvas.yview,
                                fg_color="transparent", button_color=SURFACE_3,
                                button_hover_color=BORDER)
        hbar = ctk.CTkScrollbar(wrap, orientation="horizontal", command=self.canvas.xview,
                                fg_color="transparent", button_color=SURFACE_3,
                                button_hover_color=BORDER)
        self.canvas.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vbar.grid(row=0, column=1, sticky="ns")
        hbar.grid(row=1, column=0, sticky="ew")

        self.canvas.bind("<Configure>", self._on_canvas_resize)
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        self.canvas.bind("<Double-Button-1>", self._on_canvas_double)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", lambda e: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind("<Button-5>", lambda e: self.canvas.yview_scroll(3, "units"))

    def _build_deck(self):
        deck = ctk.CTkFrame(self, fg_color=SURFACE, corner_radius=0)
        deck.grid(row=2, column=0, columnspan=2, sticky="ew")
        deck.grid_columnconfigure(0, weight=1)

        self.progress = ctk.CTkProgressBar(deck, height=5, corner_radius=0,
                                           progress_color=ACCENT, fg_color=SURFACE_3)
        self.progress.grid(row=0, column=0, sticky="ew")
        self.progress.set(0)
        self.progress.bind("<Button-1>", self._on_progress_click)

        self.caption = ctk.CTkTextbox(deck, height=66, wrap="word", font=self.f_caption,
                                      fg_color="transparent", text_color=TEXT, border_width=0,
                                      activate_scrollbars=False)
        self.caption.grid(row=1, column=0, sticky="ew", padx=40, pady=(14, 2))
        self.caption.tag_config("hint", foreground=FAINT)
        self.caption.tag_config("read", foreground=FAINT)
        self.caption.tag_config("cur", foreground=BG, background=AMBER)
        self._set_caption_hint("Captions follow the voice here, word by word.")

        controls = ctk.CTkFrame(deck, fg_color="transparent")
        controls.grid(row=2, column=0, sticky="ew", padx=24, pady=(4, 16))
        controls.grid_columnconfigure((0, 2), weight=1, uniform="side")

        left = ctk.CTkFrame(controls, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(left, text="Page", font=self.f_small, text_color=MUTED).pack(side="left", padx=(0, 8))
        self.page_entry = ctk.CTkEntry(left, width=56, height=32, justify="center", font=self.f_body,
                                       fg_color=SURFACE_2, border_color=BORDER, text_color=TEXT,
                                       corner_radius=8)
        self.page_entry.pack(side="left")
        self.page_entry.bind("<Return>", self._on_page_entry)
        self.page_total = ctk.CTkLabel(left, text="/ –", font=self.f_small, text_color=MUTED)
        self.page_total.pack(side="left", padx=8)

        center = ctk.CTkFrame(controls, fg_color="transparent")
        center.grid(row=0, column=1)
        self._icon_button(center, "⏮", lambda: self.step_page(-1)).pack(side="left", padx=4)
        self._icon_button(center, "⏪", lambda: self.skip_sentence(-1)).pack(side="left", padx=4)
        self.play_btn = ctk.CTkButton(center, text="▶", width=58, height=58, corner_radius=29,
                                      font=self.f_play, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                      text_color="#FFFFFF", command=self.toggle_play)
        self.play_btn.pack(side="left", padx=14)
        self._icon_button(center, "⏩", lambda: self.skip_sentence(1)).pack(side="left", padx=4)
        self._icon_button(center, "⏭", lambda: self.step_page(1)).pack(side="left", padx=4)

        right = ctk.CTkFrame(controls, fg_color="transparent")
        right.grid(row=0, column=2, sticky="e")
        self.pct_label = ctk.CTkLabel(right, text="", font=self.f_small, text_color=MUTED)
        self.pct_label.pack(side="right")
        self.time_label = ctk.CTkLabel(right, text="", font=self.f_small, text_color=TEXT)
        self.time_label.pack(side="right", padx=(0, 14))

    def _section(self, parent, title):
        row = ctk.CTkFrame(parent, fg_color="transparent", height=18)
        row.pack(fill="x", padx=20, pady=(20, 8))
        ctk.CTkLabel(row, text=title, font=self.f_caps, text_color=FAINT, height=16).pack(side="left")
        value = ctk.CTkLabel(row, text="", font=self.f_small, text_color=MUTED, height=16)
        value.pack(side="right")
        return value

    def _slider(self, parent, lo, hi, steps, command):
        slider = ctk.CTkSlider(parent, from_=lo, to=hi, number_of_steps=steps, command=command,
                               fg_color=SURFACE_3, progress_color=ACCENT, button_color=ACCENT,
                               button_hover_color=ACCENT_HOVER)
        slider.pack(fill="x", padx=14)
        return slider

    def _switch(self, parent, text, key, command):
        def changed():
            self.store.settings[key] = bool(switch.get())
            if command:
                command()

        switch = ctk.CTkSwitch(parent, text=text, font=self.f_body, text_color=TEXT,
                               progress_color=ACCENT, button_color="#D9DAE3",
                               button_hover_color="#FFFFFF", fg_color=SURFACE_3, command=changed)
        switch.pack(anchor="w", padx=20, pady=5)
        if self.store.settings[key]:
            switch.select()
        return switch

    def _icon_button(self, parent, glyph, command, size=42):
        return ctk.CTkButton(parent, text=glyph, width=size, height=size, corner_radius=size // 2,
                             font=self.f_icon, fg_color="transparent", hover_color=SURFACE_3,
                             text_color=TEXT, command=command)

    def _set_status(self, text):
        self.status_label.configure(text=text)

    # ------------------------------------------------------------ library

    def _refresh_library(self):
        for child in self.library.winfo_children():
            child.destroy()
        entries = self.store.recent()
        if not entries:
            ctk.CTkLabel(self.library, text="PDFs you open show up here,\nready to resume where you stopped.",
                         font=self.f_small, text_color=FAINT, justify="left").pack(anchor="w", padx=12, pady=6)
            return
        current = os.path.normcase(self.doc.path) if self.doc else None
        for entry in entries:
            self._library_card(entry, os.path.normcase(entry["path"]) == current)

    def _library_card(self, entry, active):
        pages = max(entry.get("pages", 1), 1)
        frac = (entry.get("page", 0) + 1) / pages
        base = SURFACE_3 if active else "transparent"
        card = ctk.CTkFrame(self.library, fg_color=base, corner_radius=10)
        card.pack(fill="x", padx=4, pady=3)
        name = entry.get("title") or os.path.basename(entry["path"])
        if len(name) > 30:
            name = name[:29] + "…"
        title = ctk.CTkLabel(card, text=name, font=self.f_body, text_color=TEXT, anchor="w", height=20)
        title.pack(fill="x", padx=12, pady=(9, 0))
        meta = ctk.CTkLabel(card, text=f"Page {entry.get('page', 0) + 1} of {pages}  ·  {round(frac * 100)}%",
                            font=self.f_small, text_color=MUTED, anchor="w", height=18)
        meta.pack(fill="x", padx=12)
        bar = ctk.CTkProgressBar(card, height=3, progress_color=ACCENT if active else MUTED,
                                 fg_color=SURFACE_2 if active else SURFACE_3)
        bar.pack(fill="x", padx=12, pady=(5, 10))
        bar.set(frac)

        def open_it(_e=None, path=entry["path"]):
            self.open_pdf(path)

        def hover(on):
            if not active:
                card.configure(fg_color=SURFACE_2 if on else base)

        for w in (card, title, meta, bar):
            w.bind("<Button-1>", open_it)
            w.bind("<Enter>", lambda e: hover(True))
            w.bind("<Leave>", lambda e: hover(False))

    def _remember(self, save=False):
        if not self.doc:
            return
        self.store.remember(self.doc.path, page=self.page, token=self.cursor,
                            pages=self.doc.page_count, title=self.doc.title)
        if save:
            self.store.save()
            self._last_save = time.monotonic()

    # ------------------------------------------------------------ documents

    def open_dialog(self):
        path = filedialog.askopenfilename(title="Open a PDF",
                                          filetypes=[("PDF documents", "*.pdf"), ("All files", "*.*")])
        if path:
            self.open_pdf(path)

    def open_pdf(self, path, resume=True):
        if self.doc and os.path.normcase(os.path.abspath(path)) == os.path.normcase(self.doc.path):
            return
        try:
            doc = PdfDocument(path, skip_margins=self.store.settings["skip_margins"])
        except Exception as exc:
            messagebox.showerror("Readout", f"Couldn't open that file.\n\n{exc}")
            return
        if doc.needs_pass:
            pw = ctk.CTkInputDialog(title="Password required",
                                    text="This PDF is password-protected.\nEnter its password:").get_input()
            if not pw or not doc.authenticate(pw):
                doc.close()
                messagebox.showerror("Readout", "That password didn't unlock the PDF.")
                return
        if doc.page_count == 0:
            doc.close()
            messagebox.showerror("Readout", "That PDF has no pages.")
            return

        self.pause()
        if self.doc:
            self._remember()
            self.doc.close()
        self.doc = doc
        self.word_counts = None
        self.fit_width = True
        page, token = (self.store.position(path) if resume else None) or (0, 0)
        page = _clamp(page, 0, doc.page_count - 1)

        title = doc.title if len(doc.title) <= 70 else doc.title[:69] + "…"
        self.title_label.configure(text=title)
        self.page_total.configure(text=f"/ {doc.page_count}")
        self._start_word_count(doc)
        self.update_idletasks()
        self.go_to_page(page, token)
        self._remember(save=True)
        self._refresh_library()
        count = f"{doc.page_count} page" + ("s" if doc.page_count != 1 else "")
        self._set_status("Resumed where you left off" if (page or token) else count)

    def _start_word_count(self, doc):
        path, password = doc.path, doc.password

        def work():
            try:
                counts = count_words(path, password)
            except Exception:
                return
            self.events.put(("counts", path, counts))

        threading.Thread(target=work, daemon=True).start()

    def go_to_page(self, index, token=0, keep_playing=None):
        if not self.doc:
            return
        playing = self.playing if keep_playing is None else keep_playing
        self._halt()
        self.playing = playing
        self.page = _clamp(index, 0, self.doc.page_count - 1)
        self.page_text = self.doc.text(self.page)
        self.cursor = _clamp(token, 0, max(len(self.page_text.tokens) - 1, 0))
        self._caption_sentence = None
        self._render_page()
        self.canvas.yview_moveto(0)
        self.page_entry.delete(0, "end")
        self.page_entry.insert(0, str(self.page + 1))
        if self.page_text.tokens:
            self._highlight(self.cursor)
        else:
            self._set_caption_hint("No readable text on this page.")
            self._update_progress()
        self._sync_play_button()
        self._remember()
        self._refresh_library()
        if self.playing:
            self._speak_from(self.cursor)

    def step_page(self, direction):
        if self.doc:
            self.go_to_page(self.page + direction)

    def _on_page_entry(self, _e=None):
        try:
            self.go_to_page(int(self.page_entry.get()) - 1)
        except ValueError:
            pass
        self.canvas.focus_set()

    def _on_progress_click(self, e):
        if self.doc:
            frac = e.x / max(self.progress.winfo_width(), 1)
            self.go_to_page(int(_clamp(frac, 0, 0.9999) * self.doc.page_count))

    # ------------------------------------------------------------ rendering

    def _show_empty_state(self):
        if self.doc:
            return
        c = self.canvas
        c.delete("all")
        w, h = max(c.winfo_width(), 400), max(c.winfo_height(), 300)
        cx, cy = w / 2, h / 2 - 30
        c.create_oval(cx - 46, cy - 120, cx + 46, cy - 28, outline=ACCENT, width=2)
        c.create_text(cx, cy - 74, text="▶", fill=ACCENT, font=("Segoe UI Symbol", 26))
        c.create_text(cx, cy + 6, text="Open a PDF and press play", fill=TEXT, font=("Segoe UI", 20, "bold"))
        c.create_text(cx, cy + 56, fill=MUTED, font=("Segoe UI", 12),
                      text="Readout highlights every word on the page as it reads.\n"
                           "Click any word to jump there. Double-click to start reading from it.",
                      justify="center")
        c.create_text(cx, cy + 112, fill=FAINT, font=("Segoe UI", 11),
                      text="Ctrl+O open   ·   Space play/pause   ·   ← → sentence   ·   PgUp PgDn page")
        c.configure(scrollregion=(0, 0, w, h))

    def _render_page(self):
        cw = max(self.canvas.winfo_width(), 200)
        pw, _ph = self.doc.page_size(self.page)
        if self.fit_width:
            self.zoom = _clamp((cw - 2 * PAD) / pw, 0.25, 5.0)
        img = self.doc.render(self.page, self.zoom)
        if self.store.settings["night"]:
            img = _night_paper(img)
        self._page_pil = img
        self._page_photo = ImageTk.PhotoImage(img)
        self._overlays.clear()

        c = self.canvas
        c.delete("all")
        iw, ih = img.size
        ox, oy = max(PAD, (cw - iw) // 2), PAD
        self._origin = (ox, oy)
        self._scroll_h = ih + 2 * PAD
        c.create_rectangle(ox + 3, oy + 6, ox + iw + 3, oy + ih + 6, fill="#000000", outline="")
        c.create_image(ox, oy, image=self._page_photo, anchor="nw")
        if not self.page_text.tokens:
            c.create_text(ox + iw / 2, oy + 30, fill=MUTED, font=("Segoe UI", 12),
                          text="No text layer on this page — it may be a scanned image.")
        c.configure(scrollregion=(0, 0, max(cw, iw + 2 * PAD), self._scroll_h))
        self.zoom_label.configure(text="Fit" if self.fit_width else f"{round(self.zoom * 100)}%")

    def _rerender(self):
        if not self.doc:
            return
        top = self.canvas.yview()[0]
        self._render_page()
        self.canvas.yview_moveto(top)
        if self.page_text.tokens:
            self._highlight(self.cursor, scroll=False)

    def _on_canvas_resize(self, _e):
        if self._resize_job:
            self.after_cancel(self._resize_job)
        if not self.doc:
            self._resize_job = self.after(60, self._show_empty_state)
        elif self.fit_width:
            self._resize_job = self.after(120, self._rerender)

    def zoom_by(self, factor):
        if self.doc:
            self.fit_width = False
            self.zoom = _clamp(self.zoom * factor, 0.25, 5.0)
            self._rerender()

    def zoom_fit(self):
        if self.doc:
            self.fit_width = True
            self._rerender()

    def _on_wheel(self, e):
        if e.state & 0x0004:  # Ctrl held
            self.zoom_by(1.1 if e.delta > 0 else 1 / 1.1)
        else:
            self.canvas.yview_scroll(int(-e.delta / 40), "units")

    def _highlight(self, idx, scroll=True):
        pt = self.page_text
        for item, _photo in self._overlays:
            self.canvas.delete(item)
        self._overlays.clear()
        tok = pt.tokens[idx]
        night = self.store.settings["night"]
        line = pt.line_rects.get(tok.line_key)
        if line:
            self._paint(line, NIGHT_LINE if night else PAPER_LINE, 3)
        for rect in tok.rects:
            self._paint(rect, NIGHT_WORD if night else PAPER_WORD, 2)
        if scroll and self.store.settings["follow"]:
            self._scroll_into_view(tok.rects[0])
        self._update_caption(idx)
        self._update_progress()

    def _paint(self, rect, style, pad):
        """Overlay a highlighter-style tint on part of the rendered page."""
        tint, alpha = style
        img, z = self._page_pil, self.zoom
        x0 = _clamp(int(rect[0] * z) - pad, 0, img.width)
        y0 = _clamp(int(rect[1] * z) - pad, 0, img.height)
        x1 = _clamp(int(rect[2] * z) + pad, 0, img.width)
        y1 = _clamp(int(rect[3] * z) + pad, 0, img.height)
        if x1 <= x0 or y1 <= y0:
            return
        crop = img.crop((x0, y0, x1, y1))
        layer = Image.new("RGB", crop.size, tint)
        out = ImageChops.multiply(crop, layer) if alpha is None else Image.blend(crop, layer, alpha)
        photo = ImageTk.PhotoImage(out)
        ox, oy = self._origin
        item = self.canvas.create_image(ox + x0, oy + y0, image=photo, anchor="nw")
        self._overlays.append((item, photo))

    def _scroll_into_view(self, rect):
        y = self._origin[1] + rect[1] * self.zoom
        top, h = self.canvas.canvasy(0), self.canvas.winfo_height()
        if top + h * 0.15 < y < top + h * 0.75:
            return
        target = _clamp((y - h * 0.35) / self._scroll_h, 0.0, 1.0)
        self._animate_scroll(self.canvas.yview()[0], target, 0)

    def _animate_scroll(self, start, end, step, steps=10):
        if self._scroll_anim:
            self.after_cancel(self._scroll_anim)
            self._scroll_anim = None
        t = (step + 1) / steps
        eased = 1 - (1 - t) ** 3
        self.canvas.yview_moveto(start + (end - start) * eased)
        if step + 1 < steps:
            self._scroll_anim = self.after(16, self._animate_scroll, start, end, step + 1, steps)

    # ------------------------------------------------------------ captions & progress

    def _set_caption_hint(self, text):
        self._caption_sentence = None
        box = self.caption
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("1.0", text, "hint")
        box.configure(state="disabled")

    def _update_caption(self, idx):
        pt = self.page_text
        box = self.caption
        sentence = pt.tokens[idx].sentence
        if sentence != self._caption_sentence:
            first, last = pt.sentences[sentence]
            spans, words, pos = {}, [], 0
            for i in range(first, last + 1):
                word = pt.tokens[i].text
                spans[i] = (pos, pos + len(word))
                words.append(word)
                pos += len(word) + 1
            box.configure(state="normal")
            box.delete("1.0", "end")
            box.insert("1.0", " ".join(words))
            box.configure(state="disabled")
            self._caption_sentence, self._caption_spans = sentence, spans
        a, b = self._caption_spans[idx]
        box.tag_remove("read", "1.0", "end")
        box.tag_remove("cur", "1.0", "end")
        box.tag_add("read", "1.0", f"1.0+{a}c")
        box.tag_add("cur", f"1.0+{a}c", f"1.0+{b}c")
        box.see(f"1.0+{b}c")

    def _update_progress(self):
        if not self.doc:
            return
        n = self.doc.page_count
        tokens = len(self.page_text.tokens)
        frac = (self.page + (self.cursor / tokens if tokens else 0)) / n
        self.progress.set(frac)
        self.pct_label.configure(text=f"{frac * 100:.0f}%")
        if self.word_counts:
            remaining = sum(self.word_counts[self.page + 1:]) + max(tokens - self.cursor, 0)
            minutes = round(remaining / max(self.rate_slider.get(), 1))
            if minutes < 1:
                text = "< 1 min left"
            elif minutes < 60:
                text = f"≈ {minutes} min left"
            else:
                text = f"≈ {minutes // 60} h {minutes % 60:02d} min left"
            self.time_label.configure(text=text)

    # ------------------------------------------------------------ playback

    def toggle_play(self):
        if self.export_tag:
            return
        if not self.doc:
            self.open_dialog()
            return
        if self.playing:
            self.pause()
        else:
            self.playing = True
            self._sync_play_button()
            self._speak_from(self.cursor)

    def pause(self):
        self.playing = False
        self._halt()
        self._sync_play_button()
        self._remember(save=True)

    def _halt(self):
        self.session += 1
        if not self.export_tag:  # stopping the worker would cancel the export
            self.speech.stop()

    def _sync_play_button(self):
        self.play_btn.configure(text="⏸" if self.playing else "▶")

    def _voice_id(self):
        return self.voices.get(self.voice_menu.get())

    def _speak_from(self, idx):
        pt = self.page_text
        self.session += 1
        if not pt.tokens:
            session = self.session
            self.after(400, lambda: self.playing and self.session == session and self._page_finished())
            return
        start = pt.starts[idx]
        self.speech.speak(pt.speech[start:], base=start, tag=self.session,
                          rate=int(self.rate_slider.get()), volume=float(self.volume_slider.get()),
                          voice=self._voice_id())

    def _seek(self, idx):
        self.cursor = idx
        self._highlight(idx)
        if self.playing:
            self._speak_from(idx)

    def skip_sentence(self, direction):
        if not self.doc:
            return
        pt = self.page_text
        if not pt.tokens:
            self.step_page(direction)
            return
        s = pt.tokens[self.cursor].sentence
        first = pt.sentences[s][0]
        if direction < 0:
            if self.cursor - first > 2:
                self._seek(first)  # restart the current sentence first, like a podcast player
            elif s > 0:
                self._seek(pt.sentences[s - 1][0])
            elif self.page > 0:
                prev = self.doc.text(self.page - 1)
                self.go_to_page(self.page - 1, prev.sentences[-1][0] if prev.sentences else 0)
        elif s + 1 < len(pt.sentences):
            self._seek(pt.sentences[s + 1][0])
        else:
            self.step_page(1)

    def _page_finished(self):
        last = self.page >= self.doc.page_count - 1
        if self.sleep_end_of_page:
            self.sleep_end_of_page = False
            self.sleep_menu.set("Off")
            self.pause()
            if not last:
                self.go_to_page(self.page + 1, keep_playing=False)
            self._set_status("Sleep timer — paused at end of page")
        elif not last:
            self.go_to_page(self.page + 1, keep_playing=True)
        else:
            self.pause()
            self._set_status("Finished the document ✓")

    def _restart_soon(self):
        """Debounced: re-speak from the current word so setting changes apply live."""
        if self._restart_job:
            self.after_cancel(self._restart_job)
        self._restart_job = self.after(350, self._restart_now)

    def _restart_now(self):
        self._restart_job = None
        if self.playing:
            self._speak_from(self.cursor)

    # ------------------------------------------------------------ canvas input

    def _token_at(self, e):
        pt = self.page_text
        if not pt or not pt.tokens:
            return None
        ox, oy = self._origin
        x = (self.canvas.canvasx(e.x) - ox) / self.zoom
        y = (self.canvas.canvasy(e.y) - oy) / self.zoom
        for i, tok in enumerate(pt.tokens):
            for r in tok.rects:
                if r[0] - 2 <= x <= r[2] + 2 and r[1] - 2 <= y <= r[3] + 2:
                    return i
        return None

    def _on_canvas_click(self, e):
        self.canvas.focus_set()
        idx = self._token_at(e)
        if idx is not None:
            self._seek(idx)

    def _on_canvas_double(self, e):
        idx = self._token_at(e)
        if idx is not None and not self.playing and not self.export_tag:
            self.cursor = idx
            self.toggle_play()

    # ------------------------------------------------------------ settings

    def _on_voice(self, name):
        self.store.settings["voice"] = self.voices.get(name)
        self._restart_soon()

    def _on_rate(self, value):
        self.store.settings["rate"] = int(value)
        self.rate_value.configure(text=f"{int(value)} wpm")
        self._update_progress()
        self._restart_soon()

    def _on_volume(self, value):
        self.store.settings["volume"] = round(float(value), 2)
        self.volume_value.configure(text=f"{round(value * 100)}%")
        self._restart_soon()

    def _on_margins(self):
        if not self.doc:
            return
        self.doc.set_skip_margins(self.store.settings["skip_margins"])
        self.go_to_page(self.page, 0)

    def _on_night(self):
        self._rerender()

    def _on_sleep(self, choice):
        if self.sleep_job:
            self.after_cancel(self.sleep_job)
            self.sleep_job = None
        self.sleep_end_of_page = choice == "End of page"
        if choice.endswith("min"):
            minutes = int(choice.split()[0])
            self.sleep_job = self.after(minutes * 60_000, self._sleep_fire)
            self._set_status(f"Sleeping in {minutes} min")
        elif self.sleep_end_of_page:
            self._set_status("Will pause at the end of this page")
        else:
            self._set_status("Sleep timer off")

    def _sleep_fire(self):
        self.sleep_job = None
        self.sleep_menu.set("Off")
        if self.playing:
            self.pause()
            self._set_status("Sleep timer — playback paused")

    # ------------------------------------------------------------ export

    def export_audio(self):
        if self.export_tag:
            self.speech.stop()  # cancel the running export
            return
        if not self.doc:
            self.open_dialog()
            return
        dialog = ExportDialog(self, self.doc.page_count, self.page + 1)
        self.wait_window(dialog)
        if not dialog.result:
            return
        first, last = dialog.result
        suffix = f" (p{first})" if first == last else f" (p{first}-{last})"
        path = filedialog.asksaveasfilename(title="Save audiobook", defaultextension=".wav",
                                            initialfile=self.doc.name + suffix + ".wav",
                                            filetypes=[("WAV audio", "*.wav")])
        if not path:
            return
        self.pause()
        chunks = [self.doc.text(i).speech for i in range(first - 1, last)]
        self.session += 1
        self.export_tag = self.session
        self.speech.export(chunks, path, tag=self.export_tag, rate=int(self.rate_slider.get()),
                           volume=float(self.volume_slider.get()), voice=self._voice_id())
        self.export_btn.configure(text="Cancel export", border_color=AMBER, text_color=AMBER)
        self.play_btn.configure(state="disabled")
        self._set_status("Exporting audio…")

    def _export_finished(self, path):
        self.export_tag = None
        self.export_btn.configure(text="Export audio", border_color=BORDER, text_color=TEXT)
        self.play_btn.configure(state="normal")
        if path:
            self._set_status("Audio exported ✓")
            messagebox.showinfo("Readout", f"Saved your audiobook to:\n\n{path}")
        else:
            self._set_status("Export cancelled")

    # ------------------------------------------------------------ events & keys

    def _poll_events(self):
        try:
            while True:
                self._handle(self.events.get_nowait())
        except queue.Empty:
            pass
        self.after(30, self._poll_events)

    def _handle(self, event):
        kind = event[0]
        if kind == "word":
            _, tag, location = event
            if tag != self.session or not self.playing or not self.page_text.tokens:
                return
            idx = _clamp(bisect.bisect_right(self.page_text.starts, location) - 1,
                         0, len(self.page_text.tokens) - 1)
            self.cursor = idx
            self._highlight(idx)
            if time.monotonic() - self._last_save > 5:
                self._remember(save=True)
        elif kind == "done":
            _, tag, completed = event
            if tag == self.session and self.playing and completed:
                self._page_finished()
        elif kind == "voices":
            self._load_voices(event[1])
        elif kind == "counts":
            _, path, counts = event
            if self.doc and self.doc.path == path:
                self.word_counts = counts
                self._update_progress()
        elif kind == "export_progress":
            _, tag, done, total = event
            if tag == self.export_tag:
                self._set_status(f"Exporting audio… page {done} of {total}")
        elif kind == "export_done":
            _, tag, path = event
            if tag == self.export_tag:
                self._export_finished(path)
        elif kind == "error":
            if self.playing:
                self.pause()
            messagebox.showerror("Readout", event[1])

    def _load_voices(self, voices):
        self.voices = {}
        for vid, name in voices:
            label = _short_voice(name)
            while label in self.voices:
                label += " "
            self.voices[label] = vid
        names = list(self.voices) or ["System default"]
        self.voice_menu.configure(values=names)
        saved = self.store.settings.get("voice")
        chosen = next((n for n, v in self.voices.items() if v == saved), names[0])
        self.voice_menu.set(chosen)

    def _bind_keys(self):
        def key(fn):
            def handler(e):
                if isinstance(e.widget, (tk.Entry, tk.Text)):
                    return None
                fn()
                return "break"
            return handler

        self.bind_all("<space>", key(self.toggle_play))
        self.bind_all("<Left>", key(lambda: self.skip_sentence(-1)))
        self.bind_all("<Right>", key(lambda: self.skip_sentence(1)))
        self.bind_all("<Prior>", key(lambda: self.step_page(-1)))
        self.bind_all("<Next>", key(lambda: self.step_page(1)))
        self.bind_all("<Control-o>", lambda e: self.open_dialog())
        self.bind_all("<Control-e>", lambda e: self.export_audio())
        self.bind_all("<Control-equal>", lambda e: self.zoom_by(1.15))
        self.bind_all("<Control-plus>", lambda e: self.zoom_by(1.15))
        self.bind_all("<Control-minus>", lambda e: self.zoom_by(1 / 1.15))
        self.bind_all("<Control-0>", lambda e: self.zoom_fit())

    def _on_close(self):
        self.playing = False
        self._remember()
        self.store.save()
        self.speech.shutdown()
        self.destroy()


class ExportDialog(ctk.CTkToplevel):
    """Pick the page range to turn into an audio file."""

    def __init__(self, master, page_count, current):
        super().__init__(master, fg_color=SURFACE)
        self.result = None
        self.page_count = page_count
        self.title("Export audiobook")
        self.geometry("380x214")
        self.resizable(False, False)
        self.transient(master)

        ctk.CTkLabel(self, text="Export audiobook", font=ctk.CTkFont("Segoe UI", 17, "bold"),
                     text_color=TEXT).pack(anchor="w", padx=24, pady=(20, 2))
        ctk.CTkLabel(self, text="Saves the selected pages as a single WAV file\nusing the current voice and speed.",
                     font=ctk.CTkFont("Segoe UI", 12), text_color=MUTED, justify="left").pack(anchor="w", padx=24)

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=24, pady=16)
        self.first = self._entry(row, "From", "1")
        self.last = self._entry(row, "To", str(page_count))
        ctk.CTkButton(row, text="This page", width=86, height=30, fg_color=SURFACE_3,
                      hover_color=BORDER, text_color=TEXT,
                      command=lambda: self._set(current, current)).pack(side="right")
        ctk.CTkButton(row, text="All", width=48, height=30, fg_color=SURFACE_3, hover_color=BORDER,
                      text_color=TEXT, command=lambda: self._set(1, page_count)).pack(side="right", padx=6)

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(fill="x", padx=24, pady=(4, 20), side="bottom")
        ctk.CTkButton(buttons, text="Export", width=110, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      command=self._ok).pack(side="right")
        ctk.CTkButton(buttons, text="Cancel", width=90, fg_color="transparent", border_width=1,
                      border_color=BORDER, hover_color=SURFACE_2, text_color=TEXT,
                      command=self.destroy).pack(side="right", padx=8)
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self.destroy())
        self.after(150, self._focus)

    def _focus(self):
        self.lift()
        self.focus_force()
        self.grab_set()

    def _entry(self, parent, label, value):
        ctk.CTkLabel(parent, text=label, text_color=MUTED).pack(side="left", padx=(0, 6))
        entry = ctk.CTkEntry(parent, width=54, justify="center", fg_color=SURFACE_2, border_color=BORDER)
        entry.insert(0, value)
        entry.pack(side="left", padx=(0, 12))
        return entry

    def _set(self, first, last):
        for entry, value in ((self.first, first), (self.last, last)):
            entry.delete(0, "end")
            entry.insert(0, str(value))

    def _ok(self):
        try:
            first, last = int(self.first.get()), int(self.last.get())
        except ValueError:
            return
        first = _clamp(first, 1, self.page_count)
        last = _clamp(last, first, self.page_count)
        self.result = (first, last)
        self.destroy()


def run(path=None):
    ctk.set_appearance_mode("dark")
    try:  # own taskbar icon/group on Windows instead of python.exe's
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NestureX.Readout")
    except (AttributeError, OSError):
        pass
    ReadoutApp(path).mainloop()
