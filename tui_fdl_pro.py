import os
import sys
import argparse
import datetime
import threading
from typing import List, Dict, Optional, Tuple

import blessed
import pyperclip

# --- 全局常量 ---
FILE_MARKER = "$$FILE"
ENCODING = 'utf-8'

# --- 辅助函数 ---
def is_encodable(filepath: str) -> bool:
    try:
        with open(filepath, 'r', encoding=ENCODING) as f:
            f.read(1024)
        return True
    except (UnicodeDecodeError, IOError):
        return False

def format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"

# --- 线程安全进度追踪器 ---
class ProgressTracker:
    def __init__(self):
        self.count = 0
        self.total = 0
        self.current_path = ""
        self.lock = threading.Lock()

    def update(self, count_increment, current_path=""):
        with self.lock:
            self.count += count_increment
            if current_path:
                self.current_path = current_path

    def set_total(self, total):
        with self.lock:
            self.total = total

    def get_state(self) -> Tuple[int, int, str]:
        with self.lock:
            return self.count, self.total, self.current_path

# --- TUI 核心应用类 ---
class FdlTuiApp:
    def __init__(self, root_dir: str):
        self.term = blessed.Terminal()
        self.root_dir = os.path.abspath(root_dir)
        
        self.mode = 'loading'
        self.sort_by = 'name'
        self.last_drawn_lines = []

        self.tree: Optional[Dict] = None
        self.flat_list: List[Dict] = []
        self.cursor_pos = 0
        self.top_line = 0
        self.running = True
        self.message = ""

        self.selected_size = 0
        self.selected_count = 0
        self.total_encodable_size = 0
        self.total_encodable_count = 0

        self.progress = ProgressTracker()
        self.tree_result: Optional[Dict] = None
        self.loader_thread = threading.Thread(target=self._build_tree_worker)
        self.loader_thread.daemon = True
        self.loader_thread.start()

        self.preview_content = []
        self.preview_scroll = 0
        self.preview_node = None

    def _build_tree_worker(self):
        total_files = sum(len(files) for _, _, files in os.walk(self.root_dir))
        self.progress.set_total(total_files)
        self.tree_result, self.total_encodable_count, self.total_encodable_size = self._build_tree(self.root_dir)
        self.selected_count = self.total_encodable_count
        self.selected_size = self.total_encodable_size

    def _build_tree(self, path: str, depth: int = 0) -> Tuple[Dict, int, int]:
        self.progress.update(1, current_path=path)
        name = os.path.basename(path)
        node = {"name": name, "path": path, "depth": depth, "selected": True, "children": []}
        
        node_encodable_count = 0
        node_encodable_size = 0

        if os.path.isdir(path):
            node["type"] = "dir"
            node["expanded"] = False
            try:
                entries = sorted(os.scandir(path), key=lambda e: (e.is_file(), e.name.lower()))
                for entry in entries:
                    child_node, child_count, child_size = self._build_tree(entry.path, depth + 1)
                    node["children"].append(child_node)
                    node_encodable_count += child_count
                    node_encodable_size += child_size
            except OSError:
                pass
            node["size"] = sum(c.get("size", 0) for c in node["children"])
        else:
            node["type"] = "file"
            node["size"] = os.path.getsize(path) if os.path.exists(path) else 0
            if is_encodable(path):
                node_encodable_count = 1
                node_encodable_size = node["size"]
            else:
                node["selected"] = False

        node['encodable_count'] = node_encodable_count
        node['encodable_size'] = node_encodable_size
        return node, node_encodable_count, node_encodable_size

    def _toggle_selection(self, node: Dict, select_state: Optional[bool] = None):
        is_selectable = node["type"] == "dir" or is_encodable(node["path"])
        if not is_selectable: return
        target_state = select_state if select_state is not None else not node["selected"]
        
        # 递归地检查实际发生变化的总量
        delta_count, delta_size = self._calculate_selection_delta(node, target_state)
        
        # 应用选择
        self._apply_selection_state(node, target_state)

        self.selected_count += delta_count
        self.selected_size += delta_size

    def _calculate_selection_delta(self, node: Dict, target_state: bool) -> Tuple[int, int]:
        """递归计算如果状态改变, count 和 size 的总变化量"""
        if node['selected'] == target_state:
            return 0, 0 # 状态未变, 无变化
        
        is_selectable = node['type'] == 'dir' or is_encodable(node['path'])
        if not is_selectable: return 0, 0

        if node['type'] == 'file':
            change = 1 if target_state else -1
            return change, change * node['size']

        # 对于目录, 聚合所有子项的变化
        total_delta_count = 0
        total_delta_size = 0
        for child in node['children']:
            d_count, d_size = self._calculate_selection_delta(child, target_state)
            total_delta_count += d_count
            total_delta_size += d_size
            
        return total_delta_count, total_delta_size
    
    def _apply_selection_state(self, node: Dict, state: bool):
        """仅递归应用选择状态, 不返回任何值"""
        is_selectable = node['type'] == 'dir' or is_encodable(node['path'])
        if is_selectable and node['selected'] != state:
            node['selected'] = state
            for child in node.get('children', []):
                self._apply_selection_state(child, state)

    def _sort_children(self, node: Dict):
        if node["type"] == 'dir' and node["children"]:
            if self.sort_by == 'name':
                node["children"].sort(key=lambda n: (n['type'] == 'file', n['name'].lower()))
            elif self.sort_by == 'size':
                node["children"].sort(key=lambda n: (n['type'] == 'file', -n['size'], n['name'].lower()))
            for child in node["children"]: self._sort_children(child)
    
    def _update_flat_list(self):
        self.flat_list = []
        def recurse(node: Dict):
            self.flat_list.append(node)
            if node.get("expanded", False):
                for child in node["children"]:
                    recurse(child)
        if self.tree: recurse(self.tree)
        if self.cursor_pos >= len(self.flat_list):
            self.cursor_pos = max(0, len(self.flat_list) - 1)

    def _render(self, new_lines: List[str]):
        for i in range(self.term.height):
            line = new_lines[i] if i < len(new_lines) else ""
            line = line.ljust(self.term.width)
            last_line = self.last_drawn_lines[i] if i < len(self.last_drawn_lines) else None
            if line != last_line:
                print(self.term.move(i, 0) + line, end="")
        self.last_drawn_lines = new_lines
        sys.stdout.flush()

    def _draw_loading_screen(self):
        count, total, path = self.progress.get_state()
        lines = [""] * self.term.height
        
        title = "Scanning project..."
        lines[self.term.height // 2 - 2] = self.term.center(self.term.bold(title))
        
        if total > 0:
            percentage = count / total if total > 0 else 0
            bar_width = self.term.width - 20
            filled_len = int(bar_width * percentage)
            bar = '█' * filled_len + '─' * (bar_width - filled_len)
            progress_bar = f"[{bar}] {percentage:.1%}"
            lines[self.term.height // 2] = self.term.center(progress_bar)

        display_path = path
        if len(path) > self.term.width - 4:
            display_path = "..." + path[-(self.term.width - 7):]
        
        # --- FIX: 使用安全的方式应用 dim ---
        dimmed_path = f"{self.term.dim}{display_path}{self.term.normal}"
        lines[self.term.height // 2 + 2] = self.term.center(dimmed_path)
        
        self._render(lines)
        
    def _draw_browse_mode(self):
        lines = []
        height = self.term.height
        sort_mode_str = f"Sort: {self.sort_by.capitalize()}"
        header1 = (
            f"FDL Exporter | "
            f"Selected: {format_size(self.selected_size)} ({self.selected_count}) | "
            f"Total: {format_size(self.total_encodable_size)} ({self.total_encodable_count}) | "
            f"{sort_mode_str}"
        )
        lines.append(self.term.bold_black_on_lightgray(header1.ljust(self.term.width)))
        if self.cursor_pos < self.top_line: self.top_line = self.cursor_pos
        if self.cursor_pos >= self.top_line + height - 2: self.top_line = self.cursor_pos - height + 3
        visible_items = self.flat_list[self.top_line : self.top_line + height - 2]
        for i, node in enumerate(visible_items):
            line_idx = self.top_line + i
            indent, sel_char = "  " * node["depth"], ""
            if node["type"] == "file" and not is_encodable(node["path"]):
                sel_char = f"{self.term.dim}[ ]{self.term.normal}"
            else:
                sel_char = self.term.green("[✓]") if node["selected"] else "[ ]"
            icon = "▾" if node.get("expanded") else "▸" if node["type"] == "dir" else " "
            display_name = f"{icon} {node['name']}{'/' if node['type'] == 'dir' else ''}"
            size_str = f"({format_size(node['size'])})" if node['size'] > 0 else ""
            line_str = f"{indent}{sel_char} {display_name}"
            
            stripped_line_len = len(self.term.strip_seqs(line_str))
            space_for_size = self.term.width - stripped_line_len - 1
            dimmed_size = f"{self.term.dim}{size_str}{self.term.normal}"
            
            # 动态计算右对齐所需的padding
            padding = space_for_size + len(dimmed_size) - len(size_str)
            line = f"{line_str}{dimmed_size:>{padding}}"
            
            lines.append(self.term.black_on_green(line) if line_idx == self.cursor_pos else line)

        while len(lines) < height - 1: lines.append("")
        footer = "↑↓ Move | ←→ Expand/Collapse | Tab Sort | p Preview | +/-/Spc Toggle | s Save | c Copy | q Quit"
        if self.message:
            footer = self.term.bold_yellow(self.message.ljust(self.term.width))
            self.message = ""
        lines.append(self.term.bold_black_on_lightgray(footer.ljust(self.term.width)))
        self._render(lines)

    def _draw_preview_mode(self):
        w, h = self.term.width, self.term.height
        p_w, p_h = max(w - 10, 20), max(h - 6, 10)
        p_x, p_y = (w - p_w) // 2, (h - p_h) // 2
        content_h = p_h - 4
        lines = list(self.last_drawn_lines)
        lines[p_y] = self.term.move(p_y, p_x) + '╭' + '─' * (p_w - 2) + '╮'
        for i in range(p_h - 2):
            lines[p_y + 1 + i] = self.term.move(p_y + 1 + i, p_x) + '│' + ' ' * (p_w - 2) + '│'
        lines[p_y + p_h - 1] = self.term.move(p_y + p_h - 1, p_x) + '╰' + '─' * (p_w - 2) + '╯'
        title = f" Preview: {os.path.basename(self.preview_node['path'])} ({format_size(self.preview_node['size'])}) "
        lines[p_y] = self.term.move(p_y, p_x + 1) + self.term.bold(title)
        for i in range(content_h):
            content_idx = self.preview_scroll + i
            if content_idx < len(self.preview_content):
                content_line = self.preview_content[content_idx].replace('\t', '    ')[:p_w - 4]
                lines[p_y + 2 + i] = self.term.move(p_y + 2 + i, p_x + 2) + content_line
        scroll_info = f"Ln {self.preview_scroll+1}/{len(self.preview_content)}"
        help_info = "[↑↓ Scroll, p/q/Esc Close]"
        footer_text = f"{scroll_info.ljust(p_w - 2 - len(help_info))}{help_info}"
        lines[p_y + p_h - 2] = self.term.move(p_y + p_h - 2, p_x + 1) + self.term.reverse(footer_text)
        self._render(lines)

    def run(self):
        with self.term.cbreak(), self.term.hidden_cursor(), self.term.fullscreen():
            while self.running:
                if self.mode == 'loading':
                    self._draw_loading_screen()
                    if not self.loader_thread.is_alive():
                        self.tree = self.tree_result
                        self.mode = 'browse'
                        self._update_flat_list()
                        self.last_drawn_lines = []
                elif self.mode == 'browse': self._draw_browse_mode()
                elif self.mode == 'preview': self._draw_preview_mode()

                key = self.term.inkey(timeout=0.1)
                if not key: continue

                if self.mode == 'browse':
                    if not self.flat_list: continue
                    current_node = self.flat_list[self.cursor_pos]
                    if key.code == self.term.KEY_UP: self.cursor_pos = max(0, self.cursor_pos - 1)
                    elif key.code == self.term.KEY_DOWN: self.cursor_pos = min(len(self.flat_list) - 1, self.cursor_pos + 1)
                    elif key.code == self.term.KEY_LEFT:
                        if current_node["type"] == "dir" and current_node["expanded"]:
                            current_node["expanded"] = False; self._update_flat_list()
                    elif key.code == self.term.KEY_RIGHT:
                        if current_node["type"] == "dir" and not current_node["expanded"]:
                            current_node["expanded"] = True; self._update_flat_list()
                    elif key in ('+', '='): self._toggle_selection(current_node, select_state=True)
                    elif key == '-': self._toggle_selection(current_node, select_state=False)
                    elif key == ' ': self._toggle_selection(current_node)
                    elif key.lower() == 'p':
                        if current_node['type'] == 'file' and is_encodable(current_node['path']):
                            self.mode = 'preview'; self.preview_node = current_node; self.preview_scroll = 0
                            try:
                                with open(current_node['path'], 'r', encoding=ENCODING, errors='ignore') as f:
                                    self.preview_content = f.read().splitlines()
                            except IOError:
                                self.preview_content = ["Error reading file."]
                    elif key == '\t':
                        self.sort_by = 'size' if self.sort_by == 'name' else 'name'
                        self._sort_children(self.tree); self._update_flat_list()
                        self.message = f"Sorted by {self.sort_by.capitalize()}"
                    elif key.lower() == 'c':
                        content = self._generate_fdl_string(); pyperclip.copy(content)
                        self.message = f"Copied {format_size(len(content.encode(ENCODING)))} to clipboard!"
                    elif key.lower() == 's':
                        content = self._generate_fdl_string()
                        filename = f"fdl_output_{datetime.datetime.now():%Y%m%d_%H%M%S}.txt"
                        with open(filename, 'w', encoding=ENCODING) as f: f.write(content)
                        self.message = f"Saved to {filename}!"
                    elif key.lower() == 'q': self.running = False
                elif self.mode == 'preview':
                    content_h = max(1, self.term.height - 10)
                    if key.code == self.term.KEY_UP: self.preview_scroll = max(0, self.preview_scroll-1)
                    elif key.code == self.term.KEY_DOWN: self.preview_scroll = min(max(0, len(self.preview_content)-content_h), self.preview_scroll+1)
                    elif key.code == self.term.KEY_PGUP: self.preview_scroll = max(0, self.preview_scroll - content_h)
                    elif key.code == self.term.KEY_PGDOWN: self.preview_scroll = min(max(0, len(self.preview_content)-content_h), self.preview_scroll + content_h)
                    elif key.code == self.term.KEY_HOME: self.preview_scroll = 0
                    elif key.code == self.term.KEY_END: self.preview_scroll = max(0, len(self.preview_content)-content_h)
                    elif key.lower() in ('p', 'q') or key.code == self.term.KEY_ESCAPE:
                        self.mode = 'browse'; self.last_drawn_lines = []

                if not self.running:
                    prompt = self.term.move(self.term.height - 1, 0) + self.term.bold_red("Are you sure you want to quit? [y/N] ".ljust(self.term.width))
                    print(prompt, end="", flush=True)
                    confirm_key = self.term.inkey()
                    if confirm_key.lower() == 'y': break
                    else: self.running = True; self.last_drawn_lines = []

    def _generate_fdl_string(self) -> str:
        fdl_parts = []
        def recurse(node: Dict):
            if node.get("selected", False):
                if node["type"] == "file" and is_encodable(node["path"]):
                    relative_path = os.path.relpath(node["path"], self.root_dir).replace(os.sep, '/')
                    fdl_parts.append(f"{FILE_MARKER} {relative_path}")
                    try:
                        with open(node["path"], 'r', encoding=ENCODING) as f:
                            fdl_parts.append(f.read())
                    except Exception:
                        fdl_parts.append(f"ERROR: Could not read file {relative_path}")
                elif node["type"] == "dir":
                    for child in node["children"]: recurse(child)
        if self.tree: recurse(self.tree)
        return "\n".join(fdl_parts)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TUI tool to pack file contents.")
    parser.add_argument("directory", nargs='?', default='.', help="Source directory.")
    args = parser.parse_args()
    if not os.path.isdir(args.directory):
        print(f"Error: Directory '{args.directory}' not found.", file=sys.stderr); sys.exit(1)
    
    app = FdlTuiApp(args.directory)
    try:
        app.run()
    except (KeyboardInterrupt, Exception):
        # 确保在任何情况下都能正常退出 TUI 模式
        print(app.term.normal + app.term.clear, end="")
        if not isinstance(e, KeyboardInterrupt):
            print(f"\nAn unexpected error occurred: {e}", file=sys.stderr)
            import traceback; traceback.print_exc()
        sys.exit(1)

