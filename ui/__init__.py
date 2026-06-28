"""
字幕叠加窗口 — 在桌面上实时显示 AI 语音播报内容。

使用 Tkinter 实现，在独立线程中运行，通过线程安全队列接收消息。
窗口置于屏幕底部居中，始终置顶，半透明背景。

用法：
    overlay = SubtitleOverlay()
    overlay.start()
    overlay.show("你好，欢迎来到直播间！", nickname="天***", action="gift")
    # ... 运行中 ...
    overlay.stop()
"""

import queue
import threading
from typing import Optional, Tuple

from utils.logger import logger


class SubtitleOverlay:
    """桌面字幕叠加窗口。"""

    # 窗口外观参数
    WINDOW_WIDTH_RATIO = 0.80      # 占屏幕宽度的比例
    WINDOW_HEIGHT = 130            # 窗口高度
    WINDOW_BOTTOM_MARGIN = 60      # 距屏幕底部距离（像素）
    WINDOW_ALPHA = 0.88            # 窗口不透明度
    BG_COLOR = "#1a1a1a"           # 背景色（深灰）
    FG_ACTION = "#aaaaaa"          # 来源行文字色
    FG_TEXT = "#ffffff"            # 正文行文字色
    FONT = ("Microsoft YaHei", 14)  # 来源行字体
    FONT_TEXT = ("Microsoft YaHei", 22)  # 正文行字体

    # 事件类型 → 中文描述
    ACTION_LABELS = {
        "danmaku": "弹幕",
        "gift": "礼物",
        "enter_room": "进入直播间",
        "like": "点赞",
    }

    def __init__(self):
        self._queue: queue.Queue = queue.Queue(maxsize=32)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()  # 窗口创建完成信号

        # tkinter 对象（在线程中初始化）
        self._tk = None
        self._window = None
        self._action_label = None
        self._text_label = None

    @property
    def is_ready(self) -> bool:
        """窗口是否已创建并显示。"""
        return self._ready.is_set()

    # ── 生命周期 ──────────────────────────────────────────────

    def start(self):
        """启动字幕窗口（在独立线程中运行 tkinter 主循环）。"""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._ready.clear()
        self._thread = threading.Thread(target=self._run_tkinter, daemon=True)
        self._thread.start()
        # 等待窗口创建完成（最多 3 秒）
        self._ready.wait(timeout=3)
        if self._ready.is_set():
            logger.info("字幕窗口已启动")
        else:
            logger.warning("字幕窗口启动超时")

    def show(self, text: str, nickname: str = "", action: str = ""):
        """
        显示字幕文本。

        参数：
            text: AI 回复文本
            nickname: 触发回复的用户昵称
            action: 事件类型（danmaku / gift / enter_room / like）
        """
        if not self._ready.is_set():
            return
        try:
            self._queue.put_nowait((text, nickname, action))
        except queue.Full:
            # 队列满时丢弃旧消息，保留最新的
            try:
                self._queue.get_nowait()
                self._queue.put_nowait((text, nickname, action))
            except queue.Empty:
                pass

    def stop(self):
        """停止字幕窗口。"""
        self._stop_event.set()
        # 发送停止信号
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        logger.info("字幕窗口已停止")

    # ── Tkinter 线程 ─────────────────────────────────────────

    def _run_tkinter(self):
        """在独立线程中创建并运行 tkinter 窗口。"""
        import tkinter as tk

        self._tk = tk.Tk()
        self._tk.withdraw()  # 隐藏根窗口，只显示顶层

        screen_w = self._tk.winfo_screenwidth()
        screen_h = self._tk.winfo_screenheight()

        win_w = int(screen_w * self.WINDOW_WIDTH_RATIO)
        win_h = self.WINDOW_HEIGHT
        win_x = (screen_w - win_w) // 2
        win_y = screen_h - win_h - self.WINDOW_BOTTOM_MARGIN

        # 创建顶层窗口
        self._window = tk.Toplevel(self._tk)
        self._window.overrideredirect(True)          # 无边框
        self._window.attributes("-topmost", True)     # 始终置顶
        self._window.attributes("-alpha", self.WINDOW_ALPHA)
        self._window.geometry(f"{win_w}x{win_h}+{win_x}+{win_y}")
        self._window.configure(bg=self.BG_COLOR)

        # 构建 UI
        self._build_ui(win_w)

        # 让窗口可以拖拽移动
        self._make_draggable(self._window)

        # 窗口就绪
        self._ready.set()

        # 开始轮询消息队列
        self._poll_queue()

        self._tk.mainloop()

    def _build_ui(self, win_w: int):
        """构建窗口内的文本标签。"""
        import tkinter as tk

        # 内边距框架
        frame = tk.Frame(self._window, bg=self.BG_COLOR)
        frame.pack(fill=tk.BOTH, expand=True, padx=24, pady=(12, 10))

        # 第一行：来源信息（用户 + 事件类型）
        self._action_label = tk.Label(
            frame,
            text="",
            font=self.FONT,
            fg=self.FG_ACTION,
            bg=self.BG_COLOR,
            anchor="w",
        )
        self._action_label.pack(fill=tk.X, anchor="w")

        # 第二行：AI 正文
        self._text_label = tk.Label(
            frame,
            text="",
            font=self.FONT_TEXT,
            fg=self.FG_TEXT,
            bg=self.BG_COLOR,
            anchor="w",
            wraplength=win_w - 48,
        )
        self._text_label.pack(fill=tk.X, anchor="w", pady=(4, 0))

    def _make_draggable(self, widget):
        """让窗口可通过鼠标拖拽移动。"""
        start_x, start_y = 0, 0

        def on_press(e):
            nonlocal start_x, start_y
            start_x = e.x_root
            start_y = e.y_root

        def on_drag(e):
            nonlocal start_x, start_y
            dx = e.x_root - start_x
            dy = e.y_root - start_y
            x = self._window.winfo_x() + dx
            y = self._window.winfo_y() + dy
            self._window.geometry(f"+{x}+{y}")
            start_x = e.x_root
            start_y = e.y_root

        widget.bind("<ButtonPress-1>", on_press)
        widget.bind("<B1-Motion>", on_drag)

    # ── 消息处理 ─────────────────────────────────────────────

    def _poll_queue(self):
        """定期从队列取消息并更新显示（由 tkinter after() 驱动）。"""
        try:
            while True:  # 一次性尽量多处理，避免积压
                msg = self._queue.get_nowait()
                if msg is None:
                    self._window.quit()
                    return
                text, nickname, action = msg
                self._update_display(text, nickname, action)
        except queue.Empty:
            pass

        if not self._stop_event.is_set():
            self._window.after(100, self._poll_queue)

    def _update_display(self, text: str, nickname: str, action: str):
        """更新字幕显示。"""
        # 构建来源行文本
        action_desc = self.ACTION_LABELS.get(action, action)
        if nickname and action:
            action_text = f"来自 {nickname} 的{action_desc}"
        elif nickname:
            action_text = f"{nickname}"
        else:
            action_text = ""

        self._action_label.config(text=action_text)
        self._text_label.config(text=text)


class DanmakuFeed:
    """屏幕左侧弹幕字幕窗口 — 实时滚动显示直播间弹幕消息。

    每个弹幕以「昵称: 内容」的形式堆叠显示，
    新消息出现在底部，旧消息自动上滚并在超时后淡出移除。
    """

    # ── 窗口外观参数 ────────────────────────────────
    WINDOW_WIDTH = 320            # 窗口宽度（像素）
    WINDOW_HEIGHT_RATIO = 0.60    # 占屏幕高度的比例
    WINDOW_LEFT_MARGIN = 20       # 距屏幕左侧距离
    WINDOW_TOP_MARGIN = 80        # 距屏幕顶部最小距离
    WINDOW_ALPHA = 0.85           # 窗口不透明度
    TITLE_BAR_HEIGHT = 28         # 标题栏高度（px）
    CANVAS_BOTTOM_PAD = 8         # Canvas 底部留白（px）
    BG_COLOR = "#0d0d0d"          # 背景色（近黑）
    FG_NICKNAME = "#ffaa00"       # 昵称色（金色）
    FG_CONTENT = "#cccccc"        # 内容色（浅灰）
    FG_SEPARATOR = "#ffffff"      # 分隔符颜色
    FONT_NICKNAME = ("Microsoft YaHei", 10, "bold")
    FONT_CONTENT = ("Microsoft YaHei", 10)
    MAX_VISIBLE_MIN = 15           # 最少同时显示的消息条数
    MAX_VISIBLE_FALLBACK = 20     # 测量失败时的回退值
    MESSAGE_LIFETIME = 600.0      # 消息存活时间（秒），10分钟后才消失
    LINE_SPACING = 4              # 消息行间距
    FADE_DURATION_MS = 300        # 淡出动画时长（毫秒）
    FADE_STEPS = 6                # 淡出动画分步步数

    def __init__(self):
        self._queue: queue.Queue = queue.Queue(maxsize=64)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()

        # tkinter 对象（在线程中初始化）
        self._tk = None
        self._window = None
        self._container = None           # 滚动容器 Frame
        self._canvas = None              # 用于滚动的 Canvas
        self._inner_frame = None         # Canvas 内部的 Frame
        self._scrollbar = None

        # 消息追踪
        self._msg_labels: list = []      # [(label_frame, expiry_time), ...]
        self._max_visible: int = 0       # 动态计算的最大可见行数（窗口创建后赋值）
        self._fading_rows: set = set()   # 正在淡出动画中的行
        self._win_height: int = 0        # 窗口高度（_run_tkinter 中赋值）

    @property
    def is_ready(self) -> bool:
        """窗口是否已创建并显示。"""
        return self._ready.is_set()

    # ── 生命周期 ──────────────────────────────────────────────

    def start(self):
        """启动弹幕窗口（独立线程中运行 tkinter 主循环）。"""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._ready.clear()
        self._thread = threading.Thread(target=self._run_tkinter, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3)
        if self._ready.is_set():
            logger.info("弹幕字幕窗口已启动")
        else:
            logger.warning("弹幕字幕窗口启动超时")

    def show(self, nickname: str, content: str):
        """
        添加一条弹幕到滚动窗口。

        参数：
            nickname: 发送弹幕的用户昵称
            content: 弹幕文本内容
        """
        if not self._ready.is_set():
            return
        try:
            self._queue.put_nowait((nickname, content))
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait((nickname, content))
            except queue.Empty:
                pass

    def stop(self):
        """停止弹幕窗口。"""
        self._stop_event.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        logger.info("弹幕字幕窗口已停止")

    # ── Tkinter 线程 ─────────────────────────────────────────

    def _run_tkinter(self):
        """在独立线程中创建并运行 tkinter 窗口。"""
        import tkinter as tk
        from tkinter import ttk

        self._tk = tk.Tk()
        self._tk.withdraw()

        screen_w = self._tk.winfo_screenwidth()
        screen_h = self._tk.winfo_screenheight()

        win_w = self.WINDOW_WIDTH
        win_h = int(screen_h * self.WINDOW_HEIGHT_RATIO)
        win_x = self.WINDOW_LEFT_MARGIN
        win_y = max(self.WINDOW_TOP_MARGIN, (screen_h - win_h) // 2)
        self._win_height = win_h  # 供 _calculate_max_visible 使用

        # 创建顶层窗口
        self._window = tk.Toplevel(self._tk)
        self._window.overrideredirect(True)
        self._window.attributes("-topmost", True)
        self._window.attributes("-alpha", self.WINDOW_ALPHA)
        self._window.geometry(f"{win_w}x{win_h}+{win_x}+{win_y}")
        self._window.configure(bg=self.BG_COLOR)

        # 标题栏
        title_bar = tk.Frame(self._window, bg="#1a1a1a", height=28)
        title_bar.pack(fill=tk.X, side=tk.TOP)
        title_bar.pack_propagate(False)
        tk.Label(
            title_bar, text="💬 实时弹幕", font=("Microsoft YaHei", 9, "bold"),
            fg="#888888", bg="#1a1a1a",
        ).pack(side=tk.LEFT, padx=10, pady=2)

        # Canvas + Scrollbar 滚动区域
        self._canvas = tk.Canvas(
            self._window, bg=self.BG_COLOR,
            highlightthickness=0, bd=0,
        )
        self._scrollbar = ttk.Scrollbar(
            self._window, orient=tk.VERTICAL, command=self._canvas.yview,
        )
        self._inner_frame = tk.Frame(self._canvas, bg=self.BG_COLOR)

        self._inner_frame.bind(
            "<Configure>",
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")),
        )
        self._canvas.create_window((0, 0), window=self._inner_frame, anchor="nw")
        self._canvas.configure(yscrollcommand=self._scrollbar.set)

        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0), pady=(0, 6))
        self._scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 鼠标滚轮绑定
        self._canvas.bind("<Enter>", lambda e: self._canvas.bind_all("<MouseWheel>", self._on_mousewheel))
        self._canvas.bind("<Leave>", lambda e: self._canvas.unbind_all("<MouseWheel>"))

        # 可拖拽
        self._make_draggable(self._window)

        # 动态计算最大可见行数（基于窗口高度 + 实际行高）
        self._window.update_idletasks()
        self._max_visible = self._calculate_max_visible()
        logger.debug(f"弹幕最大可见行数：{self._max_visible}")

        self._ready.set()
        self._poll_queue()
        self._tk.mainloop()

    def _on_mousewheel(self, event):
        """鼠标滚轮滚动 Canvas。"""
        self._canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _make_draggable(self, widget):
        """让窗口可通过鼠标拖拽移动。"""
        start_x, start_y = 0, 0

        def on_press(e):
            nonlocal start_x, start_y
            start_x = e.x_root
            start_y = e.y_root

        def on_drag(e):
            nonlocal start_x, start_y
            dx = e.x_root - start_x
            dy = e.y_root - start_y
            x = self._window.winfo_x() + dx
            y = self._window.winfo_y() + dy
            self._window.geometry(f"+{x}+{y}")
            start_x = e.x_root
            start_y = e.y_root

        widget.bind("<ButtonPress-1>", on_press)
        widget.bind("<B1-Motion>", on_drag)

    # ── 消息处理 ─────────────────────────────────────────────

    def _poll_queue(self):
        """定期从队列取消息并更新显示。"""
        import time as _time

        try:
            while True:
                msg = self._queue.get_nowait()
                if msg is None:
                    self._window.quit()
                    return
                nickname, content = msg
                self._add_message(nickname, content, _time.time())
        except queue.Empty:
            pass

        # 移除过期消息
        self._remove_expired(_time.time())

        if not self._stop_event.is_set():
            self._window.after(100, self._poll_queue)

    def _add_message(self, nickname: str, content: str, now: float):
        """在窗口底部添加一条弹幕消息。"""
        import tkinter as tk

        trunc_content = content[:80] + "..." if len(content) > 80 else content

        # 消息行容器
        row = tk.Frame(self._inner_frame, bg=self.BG_COLOR)
        row.pack(fill=tk.X, pady=(0, self.LINE_SPACING), padx=(2, 8))

        # 昵称 + 分隔符 + 内容，用普通 Label 拼接
        name_label = tk.Label(
            row, text=nickname, font=self.FONT_NICKNAME,
            fg=self.FG_NICKNAME, bg=self.BG_COLOR, anchor="w",
        )
        name_label.pack(side=tk.LEFT)

        sep_label = tk.Label(
            row, text=": ", font=self.FONT_CONTENT,
            fg=self.FG_SEPARATOR, bg=self.BG_COLOR,
        )
        sep_label.pack(side=tk.LEFT)

        text_label = tk.Label(
            row, text=trunc_content, font=self.FONT_CONTENT,
            fg=self.FG_CONTENT, bg=self.BG_COLOR, anchor="w",
            wraplength=self.WINDOW_WIDTH - 120,  # 窗口宽 320，减去昵称+分隔符+内边距
        )
        text_label.pack(side=tk.LEFT)

        self._msg_labels.append((row, now + self.MESSAGE_LIFETIME))

        # 超出动态最大可见数量时淡出移除最旧的一条
        if len(self._msg_labels) > self._max_visible:
            oldest_row, _ = self._msg_labels.pop(0)
            self._fade_out_row(oldest_row)

        # 自动滚动到底部
        self._canvas.yview_moveto(1.0)

    def _remove_expired(self, now: float):
        """移除已过期的消息行（带淡出动画）。"""
        while self._msg_labels and self._msg_labels[0][1] < now:
            row, _ = self._msg_labels.pop(0)
            self._fade_out_row(row)

    # ── 动态布局 & 淡出动画 ──────────────────────────────────

    def _calculate_max_visible(self) -> int:
        """
        根据窗口高度动态计算最大可见消息行数。

        使用已知的窗口高度（self._win_height）减去标题栏和底部留白
        得到 Canvas 可用高度，除以测量出的单行实际高度。
        结果限制在 [MAX_VISIBLE_MIN, 200] 范围内。
        """
        import tkinter as tk

        try:
            # 创建测试行测量单行高度
            test_row = tk.Frame(self._inner_frame, bg=self.BG_COLOR)
            test_label = tk.Label(
                test_row, text="测试", font=self.FONT_CONTENT, bg=self.BG_COLOR,
            )
            test_label.pack(side=tk.LEFT)
            test_row.pack(pady=(0, self.LINE_SPACING))
            self._window.update_idletasks()
            row_height = test_row.winfo_reqheight()
            test_row.destroy()

            if row_height <= 0:
                row_height = 20  # 兜底估算

            # 用已知窗口几何计算可用高度（不依赖 canvas.winfo_height 的时机问题）
            canvas_height = self._win_height - self.TITLE_BAR_HEIGHT - self.CANVAS_BOTTOM_PAD
            available = max(canvas_height, 60)
            max_visible = available // row_height
            logger.debug(
                f"弹幕布局：win_h={self._win_height}, row_h={row_height}, "
                f"canvas_h={canvas_height}, max_visible={max_visible}"
            )
            return max(self.MAX_VISIBLE_MIN, min(max_visible, 200))
        except Exception:
            return self.MAX_VISIBLE_FALLBACK

    def _fade_out_row(self, row):
        """
        对指定消息行执行淡出动画（前景色渐变至背景色），动画结束后 destroy。

        在 tkinter 线程中通过 after() 分步驱动，每步间隔均匀，
        动画期间该行保留在 _fading_rows 中防止重复淡出。
        """
        import tkinter as tk

        if row in self._fading_rows:
            return
        self._fading_rows.add(row)

        # 收集行内所有 Label 及其原始前景色
        label_colors: list[tuple[tk.Label, str]] = []
        for child in row.winfo_children():
            if isinstance(child, tk.Label):
                orig = child.cget("fg")
                child._fade_orig = orig
                label_colors.append((child, orig))

        if not label_colors:
            # 没有可淡出的 Label，直接销毁
            row.destroy()
            self._fading_rows.discard(row)
            return

        step_ms = self.FADE_DURATION_MS // self.FADE_STEPS
        total_steps = self.FADE_STEPS

        def _step(remaining: int):
            if remaining <= 0:
                try:
                    row.destroy()
                except tk.TclError:
                    pass
                self._fading_rows.discard(row)
                return

            t = 1.0 - (remaining / total_steps)  # 0 → 1
            for label, orig_color in label_colors:
                faded = self._interpolate_color(orig_color, self.BG_COLOR, t)
                try:
                    label.config(fg=faded)
                except tk.TclError:
                    pass

            self._window.after(step_ms, lambda: _step(remaining - 1))

        _step(total_steps)

    @staticmethod
    def _interpolate_color(c1: str, c2: str, t: float) -> str:
        """
        在两种十六进制颜色 #RRGGBB 之间线性插值，t=0 返回 c1，t=1 返回 c2。
        """
        r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
        r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
        r = round(r1 + (r2 - r1) * t)
        g = round(g1 + (g2 - g1) * t)
        b = round(b1 + (b2 - b1) * t)
        return f"#{r:02x}{g:02x}{b:02x}"
