import os
import sys
import argparse
import datetime
import threading
import fnmatch
import difflib
from typing import List, Dict, Optional, Tuple, Set

import blessed
import pyperclip

# --- 全局常量 ---
DIFF_MARKER = "$$DIFF"
ENCODING = 'utf-8'

# --- 辅助函数 ---
def is_encodable(filepath: str) -> bool:
    if not os.path.exists(filepath):
        return False
    try:
        with open(filepath, 'r', encoding=ENCODING) as f:
            f.read(1024)
        return True
    except (UnicodeDecodeError, IOError):
        return False

def get_file_lines(filepath: str) -> List[str]:
    if not os.path.exists(filepath) or not is_encodable(filepath):
        return []
    try:
        with open(filepath, 'r', encoding=ENCODING, errors='ignore') as f:
            return f.readlines()
    except IOError:
        return []

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
class FdlDiffTuiApp:
    def __init__(self, dir1: str, dir2: str, exclude_patterns: Optional[List[str]] = None):
        self.term = blessed.Terminal()
        self.dir1 = os.path.abspath(dir1) # 旧版本 (Base)
        self.dir2 = os.path.abspath(dir2) # 新版本 (Target)
        self.exclude_patterns = exclude_patterns or []
        
        self.mode = 'loading'
        self.sort_by = 'name'
        self.last_drawn_lines = []

        self.tree: Optional[Dict] = None
        self.flat_list: List[Dict] = []
        self.cursor_pos = 0
        self.top_line = 0
        self.running = True
        self.message = ""

        self.selected_count = 0
        self.total_diff_count = 0

        self.progress = ProgressTracker()
        self.tree_result: Optional[Dict] = None
        self.loader_thread = threading.Thread(target=self._build_tree_worker)
        self.loader_thread.daemon = True
        self.loader_thread.start()

        self.preview_content = []
        self.preview_scroll = 0
        self.preview_node = None

    def _build_tree_worker(self):
        # 估算总数 (两个文件夹的文件总数之和的近似值)
        total_files = sum(len(f) for _, _, f in os.walk(self.dir1)) + \
                      sum(len(f) for _, _, f in os.walk(self.dir2))
        self.progress.set_total(total_files // 2) # 粗略折半作为进度总量估算
        
        self.tree_result, self.total_diff_count = self._build_diff_tree("")
        if not self.tree_result:
            # 如果没有差异，创建一个空的根节点
            self.tree_result = {"name": "No Differences Found", "rel_path": "", "depth": 0, "type": "dir", "expanded": True, "selected": False, "children": [], "diff_count": 0}
            self.total_diff_count = 0
            
        self.selected_count = self.total_diff_count

    def _build_diff_tree(self, rel_path: str, depth: int = 0) -> Tuple[Optional[Dict], int]:
        self.progress.update(1, current_path=rel_path if rel_path else "Root")
        
        abs1 = os.path.join(self.dir1, rel_path) if rel_path else self.dir1
        abs2 = os.path.join(self.dir2, rel_path) if rel_path else self.dir2

        name = os.path.basename(rel_path) if rel_path else "ROOT"
        node = {
            "name": name, "rel_path": rel_path, "depth": depth, 
            "selected": True, "children": [], "type": "dir", "expanded": depth == 0
        }
        
        node_diff_count = 0

        # 获取两个目录下的所有条目
        entries1 = set(os.listdir(abs1)) if os.path.isdir(abs1) else set()
        entries2 = set(os.listdir(abs2)) if os.path.isdir(abs2) else set()
        all_entries = sorted(list(entries1.union(entries2)))

        for entry_name in all_entries:
            # --- 排除逻辑 ---
            if depth == 0 and any(fnmatch.fnmatch(entry_name, p) for p in self.exclude_patterns):
                continue

            child_rel_path = os.path.join(rel_path, entry_name) if rel_path else entry_name
            child_abs1 = os.path.join(self.dir1, child_rel_path)
            child_abs2 = os.path.join(self.dir2, child_rel_path)

            is_dir1 = os.path.isdir(child_abs1)
            is_dir2 = os.path.isdir(child_abs2)

            # 如果在两边中任意一边是目录，则按目录处理（处理成目录意味着递归进去对比）
            if is_dir1 or is_dir2:
                child_node, child_count = self._build_diff_tree(child_rel_path, depth + 1)
                if child_node is not None: # 如果子目录里面有差异，才添加到树中
                    node["children"].append(child_node)
                    node_diff_count += child_count
            else:
                # 是文件
                exists1 = os.path.exists(child_abs1)
                exists2 = os.path.exists(child_abs2)
                
                status = None
                if exists1 and exists2:
                    # 检查是否都是可编码的文本文件，如果不是，我们在此版本中略过对比（或者可以标记为二进制差异）
                    if is_encodable(child_abs1) and is_encodable(child_abs2):
                        lines1 = get_file_lines(child_abs1)
                        lines2 = get_file_lines(child_abs2)
                        if lines1 != lines2:
                            status = "modified"
                elif exists2:
                    if is_encodable(child_abs2): status = "added"
                elif exists1:
                    if is_encodable(child_abs1): status = "removed"

                if status:
                    file_node = {
                        "name": entry_name, "rel_path": child_rel_path, "depth": depth + 1,
                        "selected": True, "type": "file", "status": status, "children": []
                    }
                    node["children"].append(file_node)
                    node_diff_count += 1

        # 如果这个目录是空的（里面没有发生变化的文件），则将其从树中裁剪掉 (返回 None)
        if depth > 0 and not node["children"]:
            return None, 0

        node['diff_count'] = node_diff_count
        return node, node_diff_count

    def _generate_diff_lines(self, rel_path: str) -> List[str]:
        p1 = os.path.join(self.dir1, rel_path)
        p2 = os.path.join(self.dir2, rel_path)
        
        lines1 = get_file_lines(p1)
        lines2 = get_file_lines(p2)
        
        diff = list(difflib.unified_diff(
            lines1, lines2,
            fromfile=f"a/{rel_path}", tofile=f"b/{rel_path}",
            n=3 # 上下文行数
        ))
        return [line.replace('\n', '') for line in diff]

    def _toggle_selection(self, node: Dict, select_state: Optional[bool] = None):
        target_state = select_state if select_state is not None else not node["selected"]
        
        delta_count = self._calculate_selection_delta(node, target_state)
        self._apply_selection_state(node, target_state)
        self.selected_count += delta_count

    def _calculate_selection_delta(self, node: Dict, target_state: bool) -> int:
        if node['selected'] == target_state: return 0
        if node['type'] == 'file':
            return 1 if target_state else -1
        
        total_delta = 0
        for child in node['children']:
            total_delta += self._calculate_selection_delta(child, target_state)
        return total_delta
    
    def _apply_selection_state(self, node: Dict, state: bool):
        if node['selected'] != state:
            node['selected'] = state
            for child in node.get('children', []):
                self._apply_selection_state(child, state)

    def _sort_children(self, node: Dict):
        if node["type"] == 'dir' and node["children"]:
            # 对于Diff模式，按 状态(增改删) 排序 或 名称排序
            if self.sort_by == 'status':
                status_order = {'modified': 0, 'added': 1, 'removed': 2, 'dir': 3}
                key_func = lambda n: (status_order.get(n.get('status', 'dir'), 3), n['type'] == 'file', n['name'].lower())
            else:
                key_func = lambda n: (n['type'] == 'file', n['name'].lower())
                
            node["children"].sort(key=key_func)
            for child in node["children"]: self._sort_children(child)
    
    def _update_flat_list(self):
        self.flat_list = []
        def recurse(node: Dict):
            if node["depth"] > 0 or node["name"] == "No Differences Found": # 隐藏根节点，除非它是空提示
                self.flat_list.append(node)
            if node.get("expanded", False) or node["depth"] == 0:
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
        title = "Comparing directories..."
        lines[self.term.height // 2 - 2] = self.term.center(self.term.bold(title))
        
        if total > 0:
            percentage = min(1.0, count / total) if total > 0 else 0
            bar_width = self.term.width - 20
            filled_len = int(bar_width * percentage)
            bar = '█' * filled_len + '─' * (bar_width - filled_len)
            progress_bar = f"[{bar}] {percentage:.1%}"
            lines[self.term.height // 2] = self.term.center(progress_bar)

        display_path = path
        if len(path) > self.term.width - 4:
            display_path = "..." + path[-(self.term.width - 7):]
        dimmed_path = f"{self.term.dim}{display_path}{self.term.normal}"
        lines[self.term.height // 2 + 2] = self.term.center(dimmed_path)
        self._render(lines)
        
    def _draw_browse_mode(self):
        lines = []
        height = self.term.height
        sort_mode_str = f"Sort: {self.sort_by.capitalize()}"
        header1 = (f"FDL Diff | Selected Diffs: {self.selected_count} | "
                   f"Total Diffs: {self.total_diff_count} | {sort_mode_str}")
        lines.append(self.term.bold_white_on_royalblue(header1.ljust(self.term.width)))
        
        if self.cursor_pos < self.top_line: self.top_line = self.cursor_pos
        if self.cursor_pos >= self.top_line + height - 2: self.top_line = self.cursor_pos - height + 3
        
        visible_items = self.flat_list[self.top_line : self.top_line + height - 2]
        for i, node in enumerate(visible_items):
            line_idx = self.top_line + i
            sel_char = self.term.green("[✓]") if node.get("selected") else "[ ]"
            
            icon = "▾" if node.get("expanded") else "▸" if node["type"] == "dir" else " "
            
            # 状态颜色标识
            status_tag = ""
            if node["type"] == "file":
                if node["status"] == "added": status_tag = self.term.green(" [+]")
                elif node["status"] == "removed": status_tag = self.term.red(" [-]")
                elif node["status"] == "modified": status_tag = self.term.yellow(" [M]")
            
            display_name = f"{icon} {node['name']}{'/' if node['type'] == 'dir' else ''}"
            
            # 根据层级缩进 (depth - 1 因为我们隐藏了 Root)
            indent = max(0, node['depth'] - 1)
            line_str = f"{'  ' * indent}{sel_char}{status_tag} {display_name}"
            
            line = line_str
            if line_idx == self.cursor_pos:
                line = self.term.black_on_cyan(line.ljust(self.term.width))

            lines.append(line)

        while len(lines) < height - 1: lines.append("")
        footer = "↑↓ Move | ←→ Expand/Collapse | Tab Sort | p Preview Diff | +/-/Spc Toggle | s Save | c Copy | q Quit"
        if self.message:
            footer = self.term.bold_yellow(self.message.ljust(self.term.width))
            self.message = ""
        lines.append(self.term.bold_black_on_lightgray(footer.ljust(self.term.width)))
        self._render(lines)

    def _draw_preview_mode(self):
        w, h = self.term.width, self.term.height
        p_w, p_h = max(w - 10, 40), max(h - 6, 10)
        p_x, p_y = (w - p_w) // 2, (h - p_h) // 2
        content_h = p_h - 4
        lines = list(self.last_drawn_lines)
        lines[p_y] = self.term.move(p_y, p_x) + '╭' + '─' * (p_w - 2) + '╮'
        for i in range(p_h - 2): lines[p_y + 1 + i] = self.term.move(p_y + 1 + i, p_x) + '│' + ' ' * (p_w - 2) + '│'
        lines[p_y + p_h - 1] = self.term.move(p_y + p_h - 1, p_x) + '╰' + '─' * (p_w - 2) + '╯'
        
        title = f" Diff: {self.preview_node['rel_path']} "
        lines[p_y] = self.term.move(p_y, p_x + 1) + self.term.bold(title)
        
        for i in range(content_h):
            content_idx = self.preview_scroll + i
            if content_idx < len(self.preview_content):
                raw_text = self.preview_content[content_idx].replace('\t', '    ')[:p_w - 4]
                # 对 diff 内容进行简单的语法高亮
                if raw_text.startswith('+') and not raw_text.startswith('+++'):
                    colored_text = self.term.green(raw_text)
                elif raw_text.startswith('-') and not raw_text.startswith('---'):
                    colored_text = self.term.red(raw_text)
                elif raw_text.startswith('@@'):
                    colored_text = self.term.cyan(raw_text)
                else:
                    colored_text = raw_text
                lines[p_y + 2 + i] = self.term.move(p_y + 2 + i, p_x + 2) + colored_text
                
        scroll_info = f"Ln {self.preview_scroll+1}/{max(1, len(self.preview_content))}"
        help_info = "[↑↓/PgUp/PgDn Scroll, p/q/Esc Close]"
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

                if key and self.mode == 'browse':
                    if not self.flat_list: continue
                    current_node = self.flat_list[self.cursor_pos]
                    if key.code == self.term.KEY_UP: self.cursor_pos = max(0, self.cursor_pos - 1)
                    elif key.code == self.term.KEY_DOWN: self.cursor_pos = min(len(self.flat_list) - 1, self.cursor_pos + 1)
                    elif key.code == self.term.KEY_LEFT:
                        if current_node["type"] == "dir" and current_node.get("expanded"):
                            current_node["expanded"] = False; self._update_flat_list()
                    elif key.code == self.term.KEY_RIGHT:
                        if current_node["type"] == "dir" and not current_node.get("expanded"):
                            current_node["expanded"] = True; self._update_flat_list()
                    elif key in ('+', '='): self._toggle_selection(current_node, select_state=True)
                    elif key == '-': self._toggle_selection(current_node, select_state=False)
                    elif key == ' ': self._toggle_selection(current_node)
                    elif key.lower() == 'p':
                        if current_node['type'] == 'file':
                            self.mode = 'preview'; self.preview_node = current_node; self.preview_scroll = 0
                            self.preview_content = self._generate_diff_lines(current_node['rel_path'])
                            if not self.preview_content:
                                self.preview_content = ["(No textual difference or binary file)"]
                    elif key == '\t':
                        self.sort_by = 'status' if self.sort_by == 'name' else 'name'
                        self._sort_children(self.tree); self._update_flat_list()
                        self.message = f"Sorted by {self.sort_by.capitalize()}"
                    elif key.lower() == 'c':
                        content = self._generate_fdl_string(); pyperclip.copy(content)
                        self.message = f"Copied diff to clipboard!"
                    elif key.lower() == 's':
                        content = self._generate_fdl_string()
                        filename = f"fdl_diff_{datetime.datetime.now():%Y%m%d_%H%M%S}.diff"
                        with open(filename, 'w', encoding=ENCODING) as f: f.write(content)
                        self.message = f"Saved to {filename}!"
                    elif key.lower() == 'q': self.running = False
                elif key and self.mode == 'preview':
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
                    if self.term.inkey().lower() == 'y': break
                    else: self.running = True; self.last_drawn_lines = []

    def _generate_fdl_string(self) -> str:
        fdl_parts = []
        def recurse(node: Dict):
            if node.get("selected", False):
                if node["type"] == "file":
                    diff_lines = self._generate_diff_lines(node["rel_path"])
                    if diff_lines:
                        fdl_parts.append(f"{DIFF_MARKER} {node['rel_path'].replace(os.sep, '/')}")
                        fdl_parts.append("\n".join(diff_lines))
                elif node["type"] == "dir":
                    for child in node.get("children", []): 
                        recurse(child)
        if self.tree: recurse(self.tree)
        return "\n\n".join(fdl_parts)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TUI tool to generate diffs between two directories.")
    parser.add_argument("dir1", help="Base directory (Old version).")
    parser.add_argument("dir2", help="Target directory (New version).")
    parser.add_argument("--exclude", type=str, help='Comma/semicolon-separated list of file/dir patterns to exclude from the root (e.g., "node_modules,.git,*.log").')
    args = parser.parse_args()
    
    if not os.path.isdir(args.dir1):
        print(f"Error: Base Directory '{args.dir1}' not found.", file=sys.stderr); sys.exit(1)
    if not os.path.isdir(args.dir2):
        print(f"Error: Target Directory '{args.dir2}' not found.", file=sys.stderr); sys.exit(1)
    
    exclude_patterns = []
    if args.exclude:
        exclude_patterns = [p.strip().rstrip('/\\') for p in args.exclude.replace(';',',').split(',') if p.strip()]

    app = FdlDiffTuiApp(args.dir1, args.dir2, exclude_patterns=exclude_patterns)
    try:
        app.run()
    except (KeyboardInterrupt, Exception) as e:
        print(app.term.normal + app.term.clear, end="")
        if not isinstance(e, KeyboardInterrupt):
            print(f"\nAn unexpected error occurred: {e}", file=sys.stderr)
            import traceback; traceback.print_exc()
        sys.exit(1)