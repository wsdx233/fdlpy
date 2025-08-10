import os
import sys
import argparse
import datetime
from typing import List, Dict, Optional

import blessed
import pyperclip

# --- 全局常量 ---
FILE_MARKER = "$$FILE"
ENCODING = 'utf-8'

# --- 辅助函数 (无变动) ---
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

# --- TUI 核心应用类 ---
class FdlTuiApp:
    def __init__(self, root_dir: str):
        self.term = blessed.Terminal()
        self.root_dir = os.path.abspath(root_dir)
        
        # --- 新增状态 ---
        self.mode = 'browse'  # 'browse' 或 'preview'
        self.sort_by = 'name' # 'name' 或 'size'
        self.last_drawn_lines = []

        self.tree = self._build_tree(self.root_dir)
        self.flat_list: List[Dict] = []
        self.cursor_pos = 0
        self.top_line = 0
        self.running = True
        self.message = ""

        # --- 预览窗口专用状态 ---
        self.preview_content = []
        self.preview_scroll = 0
        self.preview_node = None
        
        self._update_flat_list()
        self._update_selection_stats()

    def _build_tree(self, path: str, depth: int = 0) -> Dict:
        name = os.path.basename(path)
        node = {"name": name, "path": path, "depth": depth, "selected": True, "children": []}

        if os.path.isdir(path):
            node["type"] = "dir"
            node["expanded"] = False
            try:
                children = []
                for entry in sorted(os.scandir(path), key=lambda e: e.name.lower()):
                     child_node = self._build_tree(entry.path, depth + 1)
                     children.append(child_node)
                node["children"] = children
                node["size"] = sum(c.get("size", 0) for c in node["children"])
            except OSError:
                node["size"] = 0
        else:
            node["type"] = "file"
            if is_encodable(path):
                node["size"] = os.path.getsize(path)
            else:
                node["size"] = 0
                node["selected"] = False
        
        # --- 新增: 子节点排序 ---
        if node["type"] == "dir":
            self._sort_children(node)
            
        return node
    
    def _sort_children(self, node: Dict):
        """递归地对节点的所有子节点进行排序"""
        if node["type"] == 'dir' and node["children"]:
            if self.sort_by == 'name':
                # 文件夹优先，然后按名称
                node["children"].sort(key=lambda n: (n['type'] == 'file', n['name'].lower()))
            elif self.sort_by == 'size':
                # 文件夹优先，然后按大小（降序），同大小按名称
                node["children"].sort(key=lambda n: (n['type'] == 'file', -n['size'], n['name'].lower()))
            
            # 递归排序
            for child in node["children"]:
                self._sort_children(child)

    def _update_flat_list(self):
        self.flat_list = []
        def recurse(node: Dict):
            self.flat_list.append(node)
            if node.get("expanded", False):
                for child in node["children"]:
                    recurse(child)
        recurse(self.tree)
        if self.cursor_pos >= len(self.flat_list):
            self.cursor_pos = max(0, len(self.flat_list) - 1)

    def _update_selection_stats(self):
        self.total_encodable_size, self.total_encodable_count = 0, 0
        self.selected_size, self.selected_count = 0, 0
        def recurse(node: Dict):
            if node["type"] == "file" and is_encodable(node["path"]):
                self.total_encodable_size += node["size"]
                self.total_encodable_count += 1
                if node["selected"]:
                    self.selected_size += node["size"]
                    self.selected_count += 1
            elif node["type"] == "dir":
                for child in node["children"]:
                    recurse(child)
        recurse(self.tree)

    def _toggle_selection(self, node: Dict, select_state: Optional[bool] = None):
        is_selectable = node["type"] == "dir" or is_encodable(node["path"])
        if not is_selectable: return
        
        current_select = select_state if select_state is not None else not node["selected"]
        node["selected"] = current_select
        
        if node["type"] == "dir":
            for child in node["children"]:
                self._toggle_selection(child, current_select)
        
        self._update_selection_stats()

    # --- 渲染与绘制 ---
    def _render(self, new_lines: List[str]):
        """高效渲染，只重绘变化的行，消除闪烁"""
        for i in range(self.term.height):
            line = new_lines[i] if i < len(new_lines) else ""
            # 用 ljust 确保清除旧行内容
            line = line.ljust(self.term.width)
            last_line = self.last_drawn_lines[i] if i < len(self.last_drawn_lines) else None
            
            if line != last_line:
                print(self.term.move(i, 0) + line, end="")
        
        self.last_drawn_lines = new_lines
        sys.stdout.flush()

    def _draw_browse_mode(self):
        """绘制主浏览界面"""
        lines = []
        height = self.term.height

        # 1. 头部状态栏
        sort_mode_str = f"Sort: {self.sort_by.capitalize()}"
        header1 = (
            f"FDL Exporter | "
            f"Selected: {format_size(self.selected_size)} ({self.selected_count}) | "
            f"Total: {format_size(self.total_encodable_size)} ({self.total_encodable_count}) | "
            f"{sort_mode_str}"
        )
        lines.append(self.term.bold_black_on_lightgray(header1.ljust(self.term.width)))

        # 2. 文件树
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
            
            # 组合行，确保size在右侧对齐
            space_for_size = self.term.width - len(line_str) - 1
            line = f"{line_str}{f'{self.term.dim}{size_str}{self.term.normal}':>{space_for_size}}"

            lines.append(self.term.black_on_green(line) if line_idx == self.cursor_pos else line)

        # 3. 底部指令栏
        while len(lines) < height - 1: lines.append("") # 填充空白行
        footer = "↑↓ Move | ←→ Expand/Collapse | Tab Sort | p Preview | +/-/Spc Toggle | s Save | c Copy | q Quit"
        if self.message:
            footer = self.term.bold_yellow(self.message.ljust(len(footer)))
            self.message = ""
        lines.append(self.term.bold_black_on_lightgray(footer.ljust(self.term.width)))

        self._render(lines)

    def _draw_preview_mode(self):
        """绘制文件预览窗口"""
        w, h = self.term.width, self.term.height
        # 窗口尺寸与位置
        p_w, p_h = max(w - 10, 20), max(h - 6, 10)
        p_x, p_y = (w - p_w) // 2, (h - p_h) // 2
        content_h = p_h - 4 # 边框和标题占4行

        lines = list(self.last_drawn_lines) # 从背景开始绘制
        
        # 绘制边框
        lines[p_y] = self.term.move(p_y, p_x) + '╭' + '─' * (p_w - 2) + '╮'
        for i in range(p_h - 2):
            lines[p_y + 1 + i] = self.term.move(p_y + 1 + i, p_x) + '│' + ' ' * (p_w - 2) + '│'
        lines[p_y + p_h - 1] = self.term.move(p_y + p_h - 1, p_x) + '╰' + '─' * (p_w - 2) + '╯'

        # 绘制标题
        title = f" Preview: {os.path.basename(self.preview_node['path'])} ({format_size(self.preview_node['size'])}) "
        lines[p_y] = self.term.move(p_y, p_x + 1) + self.term.bold(title)

        # 绘制文件内容
        for i in range(content_h):
            content_idx = self.preview_scroll + i
            if content_idx < len(self.preview_content):
                content_line = self.preview_content[content_idx].replace('\t', '    ')[:p_w - 4]
                lines[p_y + 2 + i] = self.term.move(p_y + 2 + i, p_x + 2) + content_line

        # 绘制滚动条和帮助
        scroll_info = f"Ln {self.preview_scroll+1}/{len(self.preview_content)}"
        help_info = "[↑↓ Scroll, p/q/Esc Close]"
        footer_text = f"{scroll_info.ljust(p_w - 2 - len(help_info))}{help_info}"
        lines[p_y + p_h - 2] = self.term.move(p_y + p_h - 2, p_x + 1) + self.term.reverse(footer_text)

        self._render(lines)

    # --- 输入处理 ---
    def _handle_input_browse(self, key):
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
                self.mode = 'preview'; self.preview_node = current_node
                self.preview_scroll = 0
                with open(current_node['path'], 'r', encoding=ENCODING, errors='ignore') as f:
                    self.preview_content = f.read().splitlines()
        elif key == '\t':
            self.sort_by = 'size' if self.sort_by == 'name' else 'name'
            self._sort_children(self.tree)
            self._update_flat_list()
            self.message = f"Sorted by {self.sort_by.capitalize()}"
        elif key.lower() == 'c':
            content = self._generate_fdl_string(); pyperclip.copy(content)
            self.message = f"Copied {format_size(len(content.encode(ENCODING)))} to clipboard!"
        elif key.lower() == 's':
            content = self._generate_fdl_string()
            filename = f"fdl_output_{datetime.datetime.now():%Y%m%d_%H%M%S}.txt"
            with open(filename, 'w', encoding=ENCODING) as f: f.write(content)
            self.message = f"Saved to {filename}!"
        elif key.lower() == 'q':
            self.running = False # 退出逻辑移至主循环，避免嵌套输入

    def _handle_input_preview(self, key):
        content_h = self.term.height - 10
        if key.code == self.term.KEY_UP: self.preview_scroll = max(0, self.preview_scroll-1)
        elif key.code == self.term.KEY_DOWN: self.preview_scroll = min(max(0, len(self.preview_content)-content_h), self.preview_scroll+1)
        elif key.code == self.term.KEY_PGUP: self.preview_scroll = max(0, self.preview_scroll - content_h)
        elif key.code == self.term.KEY_PGDOWN: self.preview_scroll = min(max(0, len(self.preview_content)-content_h), self.preview_scroll + content_h)
        elif key.code == self.term.KEY_HOME: self.preview_scroll = 0
        elif key.code == self.term.KEY_END: self.preview_scroll = max(0, len(self.preview_content)-content_h)
        elif key.lower() in ('p', 'q') or key.code == self.term.KEY_ESCAPE:
            self.mode = 'browse'
            self.last_drawn_lines = [] # 强制重绘背景

    def _generate_fdl_string(self) -> str:
        fdl_parts = []
        def recurse(node: Dict):
            if node["type"] == "file" and node["selected"] and is_encodable(node["path"]):
                relative_path = os.path.relpath(node["path"], self.root_dir).replace(os.sep, '/')
                fdl_parts.append(f"{FILE_MARKER} {relative_path}")
                try:
                    with open(node["path"], 'r', encoding=ENCODING) as f:
                        fdl_parts.append(f.read())
                except Exception:
                    fdl_parts.append(f"ERROR: Could not read file {relative_path}")
            elif node["type"] == "dir":
                for child in node["children"]: recurse(child)
        recurse(self.tree)
        return "\n".join(fdl_parts)
    
    def run(self):
        with self.term.cbreak(), self.term.hidden_cursor(), self.term.fullscreen():
            while self.running:
                if self.mode == 'browse': self._draw_browse_mode()
                elif self.mode == 'preview': self._draw_preview_mode()

                key = self.term.inkey(timeout=3)
                if not key: continue

                if self.mode == 'browse': self._handle_input_browse(key)
                elif self.mode == 'preview': self._handle_input_preview(key)

                if not self.running: # 处理退出确认
                    prompt = "Are you sure you want to quit? [y/N] "
                    print(self.term.move(self.term.height - 1, 0) + self.term.bold_red(prompt.ljust(self.term.width)), end="")
                    confirm_key = self.term.inkey()
                    if confirm_key.lower() == 'y':
                        break
                    else:
                        self.running = True # 取消退出
                        self.last_drawn_lines = [] # 强制重绘

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TUI tool to pack file contents into a text block.")
    parser.add_argument("directory", nargs='?', default='.', help="Source directory. Defaults to current directory.")
    args = parser.parse_args()

    if not os.path.isdir(args.directory):
        print(f"Error: Directory '{args.directory}' not found.", file=sys.stderr); sys.exit(1)

    try:
        app = FdlTuiApp(args.directory)
        app.run()
    except Exception as e:
        print(f"An unexpected error occurred: {e}", file=sys.stderr)
        import traceback; traceback.print_exc()
        sys.exit(1)

