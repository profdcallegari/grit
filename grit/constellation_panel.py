import math
from datetime import timezone
from typing import Callable, Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import ttk

from grit.core.models import CommitSummary


class ConstellationPanel(ttk.Frame):
    """Chronological commit graph rendered as an interactive star field."""

    AUTHOR_COLORS = (
        "#67e8f9", "#f9a8d4", "#86efac", "#c4b5fd",
        "#fcd34d", "#93c5fd", "#fda4af", "#a3e635",
    )
    LEFT_GUTTER = 190
    TOP_GUTTER = 28
    COLUMN_GAP = 38
    ROW_HEIGHT = 48
    STAR_RADIUS = 6
    PLAY_INTERVAL_MS = 650
    EDGE_COLOR = "#334155"
    EDGE_DIM = "#172235"
    ACTIVE_EDGE_COLOR = "#f8c15c"
    STAR_DIM = "#253449"

    def __init__(self, parent: tk.Misc, on_select: Optional[Callable[[str], None]] = None):
        super().__init__(parent)
        self._on_select = on_select
        self._commits: List[CommitSummary] = []
        self._index_by_hash: Dict[str, int] = {}
        self._author_colors: Dict[str, str] = {}
        self._positions: Dict[str, Tuple[float, float]] = {}
        self._star_items: Dict[str, int] = {}
        self._edge_items: Dict[str, List[int]] = {}
        self._selected_hash: Optional[str] = None
        self._highlighted_child: Optional[str] = None
        self._current_index = -1
        self._after_id = None
        self._playing = False
        self._syncing_scale = False

        controls = ttk.Frame(self, padding=(0, 0, 0, 6))
        controls.pack(fill=tk.X)
        self.play_button = ttk.Button(controls, text="Reproduzir", command=self._toggle_play)
        self.play_button.pack(side=tk.LEFT, padx=(0, 8))
        self.position_var = tk.StringVar(value="Nenhum commit")
        self.seek_scale = ttk.Scale(
            controls, from_=0, to=0, orient=tk.HORIZONTAL, command=self._on_seek,
            state=tk.DISABLED,
        )
        self.seek_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        ttk.Label(controls, textvariable=self.position_var, style="StatTitle.TLabel").pack(side=tk.RIGHT)

        scene = ttk.Frame(self)
        scene.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(
            scene, background="#0b1422", highlightthickness=0,
        )
        scroll_x = ttk.Scrollbar(scene, orient=tk.HORIZONTAL, command=self.canvas.xview)
        scroll_y = ttk.Scrollbar(scene, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=scroll_x.set, yscrollcommand=scroll_y.set)
        scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.details_var = tk.StringVar(value="Selecione um commit para ver os detalhes.")
        ttk.Label(
            self, textvariable=self.details_var, style="StatTitle.TLabel",
            anchor=tk.W, wraplength=780,
        ).pack(fill=tk.X, pady=(6, 0))
        self.canvas.bind("<Destroy>", self._on_canvas_destroy, add="+")
        self.show([])

    @staticmethod
    def _timestamp_key(commit: CommitSummary):
        timestamp = commit.timestamp
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.timestamp(), commit.hash

    def show(self, commits: List[CommitSummary]):
        self.stop()
        self._commits = sorted(commits, key=self._timestamp_key)
        self._index_by_hash = {commit.hash: index for index, commit in enumerate(self._commits)}
        self._selected_hash = None
        self._highlighted_child = None
        self._current_index = -1
        self._positions.clear()
        self._star_items.clear()
        self._edge_items.clear()
        self.canvas.delete("all")

        if not self._commits:
            self.seek_scale.configure(from_=0, to=0, state=tk.DISABLED)
            self.position_var.set("Nenhum commit")
            self.details_var.set("Nenhum commit disponível para a constelação.")
            self.canvas.create_text(
                12, 12, anchor=tk.NW, fill="#aab8ca",
                text="Execute uma análise para ver a constelação de commits.",
            )
            self.canvas.configure(scrollregion=(0, 0, 0, 0))
            return

        authors = sorted({commit.author or "(autor desconhecido)" for commit in self._commits}, key=str.casefold)
        self._author_colors = {
            author: self.AUTHOR_COLORS[index % len(self.AUTHOR_COLORS)]
            for index, author in enumerate(authors)
        }
        self.seek_scale.configure(from_=0, to=max(0, len(self._commits) - 1), state=tk.NORMAL)
        self._draw_scene(authors)
        self._select_index(0, notify=True, scroll=False)

    def _draw_scene(self, authors: List[str]):
        author_rows = {author: index for index, author in enumerate(authors)}
        for index, commit in enumerate(self._commits):
            author = commit.author or "(autor desconhecido)"
            self._positions[commit.hash] = (
                self.LEFT_GUTTER + index * self.COLUMN_GAP,
                self.TOP_GUTTER + author_rows[author] * self.ROW_HEIGHT + self.ROW_HEIGHT // 2,
            )

        for author, row in author_rows.items():
            y = self.TOP_GUTTER + row * self.ROW_HEIGHT + self.ROW_HEIGHT // 2
            label = author if len(author) <= 22 else author[:21] + "..."
            self.canvas.create_text(
                self.LEFT_GUTTER - 14, y, anchor=tk.E, text=label,
                fill=self._author_colors[author], font=("TkDefaultFont", 9, "bold"),
            )

        for commit in self._commits:
            child_x, child_y = self._positions[commit.hash]
            for parent_hash in commit.parents:
                parent_position = self._positions.get(parent_hash)
                if parent_position is None:
                    continue
                edge = self.canvas.create_line(
                    parent_position[0], parent_position[1], child_x, child_y,
                    fill=self.EDGE_DIM, width=1, smooth=True, tags=("parent-edge",),
                )
                self._edge_items.setdefault(commit.hash, []).append(edge)

        for commit in self._commits:
            x, y = self._positions[commit.hash]
            color = self._author_colors[commit.author or "(autor desconhecido)"]
            if commit.is_merge:
                points = (x, y - 9, x + 9, y, x, y + 9, x - 9, y)
            else:
                points_list = []
                for point in range(10):
                    angle = -math.pi / 2 + point * math.pi / 5
                    radius = self.STAR_RADIUS if point % 2 == 0 else self.STAR_RADIUS * 0.45
                    points_list.extend((x + math.cos(angle) * radius, y + math.sin(angle) * radius))
                points = tuple(points_list)
            item = self.canvas.create_polygon(
                *points, fill=self.STAR_DIM, outline=self.STAR_DIM, width=1, tags=("commit-star",),
            )
            self._star_items[commit.hash] = item
            self.canvas.tag_bind(
                item, "<Button-1>",
                lambda _event, commit_hash=commit.hash: self._select_hash(commit_hash, notify=True),
            )

        self._selection_ring = self.canvas.create_oval(
            0, 0, 0, 0, outline="#ffffff", width=2, state=tk.HIDDEN,
        )
        self.canvas.tag_raise(self._selection_ring)

        axis_y = self.TOP_GUTTER + len(authors) * self.ROW_HEIGHT + 12
        first_x = self._positions[self._commits[0].hash][0]
        last_x = self._positions[self._commits[-1].hash][0]
        self.canvas.create_line(first_x, axis_y, last_x, axis_y, fill="#52647a", width=1)
        self.canvas.create_text(
            first_x, axis_y + 12, anchor=tk.NW,
            text=self._commits[0].timestamp.strftime("%Y-%m-%d"), fill="#aab8ca",
        )
        if last_x != first_x:
            self.canvas.create_text(
                last_x, axis_y + 12, anchor=tk.NE,
                text=self._commits[-1].timestamp.strftime("%Y-%m-%d"), fill="#aab8ca",
            )

        width = max(560, self.LEFT_GUTTER + len(self._commits) * self.COLUMN_GAP + 40)
        height = max(220, axis_y + 44)
        self.canvas.configure(scrollregion=(0, 0, width, height))

    def _select_hash(self, commit_hash: str, notify: bool):
        index = self._index_by_hash.get(commit_hash)
        if index is not None:
            self._select_index(index, notify=notify, scroll=True)

    def select_commit(self, commit_hash: str):
        self._select_hash(commit_hash, notify=False)

    def _select_index(self, index: int, notify: bool, scroll: bool):
        if not self._commits:
            return
        index = max(0, min(index, len(self._commits) - 1))
        commit = self._commits[index]
        previous_index = self._current_index
        previous_hash = self._selected_hash
        self._current_index = index
        self._selected_hash = commit.hash

        if index > previous_index:
            for commit_index in range(previous_index + 1, index + 1):
                self._set_commit_visibility(commit_index, True)
        elif index < previous_index:
            for commit_index in range(index + 1, previous_index + 1):
                self._set_commit_visibility(commit_index, False)

        self._syncing_scale = True
        self.seek_scale.set(index)
        self._syncing_scale = False
        self.position_var.set(
            f"{index + 1} / {len(self._commits)}  |  {commit.timestamp.strftime('%Y-%m-%d')}"
        )
        self.details_var.set(
            f"{commit.message}  |  {commit.author}  |  "
            f"{commit.timestamp.strftime('%Y-%m-%d %H:%M')}  |  {commit.hash[:8]}"
        )

        if previous_hash in self._star_items:
            previous_index = self._index_by_hash[previous_hash]
            previous = self._commits[previous_index]
            color = (
                self._author_colors[previous.author or "(autor desconhecido)"]
                if previous_index <= index else self.STAR_DIM
            )
            self.canvas.itemconfigure(self._star_items[previous_hash], outline=color, width=1)
            edge_color = self.EDGE_COLOR if previous_index <= index else self.EDGE_DIM
            for edge in self._edge_items.get(previous_hash, []):
                self.canvas.itemconfigure(edge, fill=edge_color, width=1)
        self.canvas.itemconfigure(self._star_items[commit.hash], outline="#ffffff", width=2)
        x, y = self._positions[commit.hash]
        self.canvas.coords(
            self._selection_ring, x - 11, y - 11, x + 11, y + 11,
        )
        self.canvas.itemconfigure(self._selection_ring, state=tk.NORMAL)

        if self._highlighted_child in self._edge_items:
            for edge in self._edge_items[self._highlighted_child]:
                self.canvas.itemconfigure(edge, fill=self.EDGE_COLOR, width=1)
        for edge in self._edge_items.get(commit.hash, []):
            self.canvas.itemconfigure(edge, fill=self.ACTIVE_EDGE_COLOR, width=2)
        self._highlighted_child = commit.hash

        if scroll:
            self._ensure_visible(x, y)
        if notify and self._on_select:
            self._on_select(commit.hash)

    def _set_commit_visibility(self, index: int, visible: bool):
        commit = self._commits[index]
        color = self._author_colors[commit.author or "(autor desconhecido)"] if visible else self.STAR_DIM
        self.canvas.itemconfigure(self._star_items[commit.hash], fill=color, outline=color)
        edge_color = self.EDGE_COLOR if visible else self.EDGE_DIM
        for edge in self._edge_items.get(commit.hash, []):
            self.canvas.itemconfigure(edge, fill=edge_color, width=1)

    def _ensure_visible(self, x: float, y: float):
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        region = self.canvas.cget("scrollregion")
        if width <= 1 or height <= 1 or not region:
            return
        _, _, scene_width, scene_height = map(float, self.canvas.tk.splitlist(region))
        left, top = self.canvas.canvasx(0), self.canvas.canvasy(0)
        right, bottom = left + width, top + height
        if x < left + 24 or x > right - 24:
            self.canvas.xview_moveto(max(0, min(1, (x - width / 2) / scene_width)))
        if y < top + 24 or y > bottom - 24:
            self.canvas.yview_moveto(max(0, min(1, (y - height / 2) / scene_height)))

    def _on_seek(self, value: str):
        if not self._syncing_scale and self._commits:
            self._select_index(int(round(float(value))), notify=True, scroll=False)

    def _toggle_play(self):
        if self._playing:
            self.stop()
            return
        if not self._commits:
            return
        if self._current_index >= len(self._commits) - 1:
            self._select_index(0, notify=True, scroll=True)
        self._playing = True
        self.play_button.configure(text="Pausar")
        self._after_id = self.after(self.PLAY_INTERVAL_MS, self._advance)

    def _advance(self):
        self._after_id = None
        if not self._playing:
            return
        if self._current_index >= len(self._commits) - 1:
            self.stop()
            return
        self._select_index(self._current_index + 1, notify=True, scroll=True)
        self._after_id = self.after(self.PLAY_INTERVAL_MS, self._advance)

    def stop(self):
        self._playing = False
        if self._after_id is not None:
            self.after_cancel(self._after_id)
            self._after_id = None
        if self.winfo_exists():
            self.play_button.configure(text="Reproduzir")

    def _on_canvas_destroy(self, event):
        if event.widget is self.canvas and self._after_id is not None:
            self.after_cancel(self._after_id)
            self._after_id = None
            self._playing = False
