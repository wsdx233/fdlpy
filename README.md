# fdlpy

## 简介
`fdlpy` 是一个用于将文件目录结构和文本内容编码为单一切片文本格式（称作 `FDL`，即 File Description Language）的 Python 工具集。它能够方便地通过剪切板或文件进行代码、配置或项目片段的分享和传输，支持跨平台复制和恢复。无论是快速分享代码片段，还是在不同环境间同步小型项目，`fdlpy` 都提供了高效的解决方案。

## 功能特性

### 交互式目录选择与导出 (TUI)
`/tui_fdl_pro.py` 提供了一个强大的终端用户界面 (TUI)，允许您：
- **直观浏览**: 以树状结构清晰展示项目目录。
- **自定义选择**: 交互式地选择或取消选择要包含在 FDL 中的文件和文件夹。
- **内容预览**: 直接在 TUI 中预览选定的文本文件内容。
- **灵活排序**: 支持按文件名称或大小对显示项目进行排序。
- **统计信息**: 实时显示当前选中文件的总大小和数量。
- **便捷操作**: 将生成的 FDL 直接复制到剪切板，或保存到本地文件。

**使用:**
```bash
python tui_fdl_pro.py [目录路径]
# 示例：在当前目录启动 TUI
python tui_fdl_pro.py .
# 示例：在指定目录启动 TUI
python tui_fdl_pro.py /path/to/my_project
```
**TUI 快捷键:**
- `↑↓`: 移动光标选择文件/目录
- `←→`: 展开/折叠目录
- `Tab`: 切换文件/目录的排序方式（按名称 / 按大小）
- `P`: 预览当前选中文本文件的内容 (在预览模式下: `↑↓` 滚动, `PageUp/PageDown` 快速滚动, `Home/End` 跳到开头/结尾, `P/Q/Esc` 退出预览)
- `Space` / `+` / `-`: 切换当前文件/目录的选中状态（`+` 全选子项，`-` 取消全选子项）
- `C`: 复制生成的 FDL 文本到剪切板
- `S`: 将生成的 FDL 文本保存到 `fdl_output_YYYYMMDD_HHMMSS.txt` 文件
- `Q`: 退出 `fdlpy`

### 命令行工具：目录到 FDL (复制)
`/fdl_copy.py` 是一个简单的命令行工具，用于将指定目录下的所有**文本文件**（可编码为 UTF-8 的文件）打包成 FDL 格式，并自动复制到剪切板。

**使用:**
```bash
python fdl_copy.py <源目录>
# 示例：将 'my_project_folder' 目录内容复制到剪切板
python fdl_copy.py my_project_folder
```

### 命令行工具：FDL 到目录 (粘贴)
`/fdl_paste.py` 是另一个命令行工具，它从剪切板读取 FDL 格式的文本，并在指定的目标目录中自动创建相应的文件和目录结构。它智能地处理不同操作系统间的换行符差异。

**使用:**
```bash
python fdl_paste.py <目标目录>
# 示例：将剪切板中的 FDL 内容还原到 'new_project_folder'
python fdl_paste.py new_project_folder
```

## FDL 格式
FDL（File Description Language）是一种简洁的纯文本格式，用于表示文件内容和它们在项目中的相对路径。每个文件块都以 `$$FILE <相对路径>` 开头，紧随其后的是该文件的全部内容。

**FDL 格式示例:**
```
$$FILE main.py
def main():
    print("Hello from fdlpy!")


if __name__ == "__main__":
    main()
$$FILE config/settings.py
DEBUG = True
DATABASE = "sqlite:///db.sqlite"
```

## 安装

### 先决条件
- Python 3.12 或更高版本。
- 推荐使用 `uv` 作为包管理器，以确保一致且快速的依赖安装。如果您尚未安装 `uv`，可以通过 `pip install uv` 安装。

### 使用 `uv` 安装
1.  **克隆项目仓库**:
    ```bash
    git clone [您的项目仓库地址，例如：https://github.com/YourUsername/fdlpy.git]
    cd fdlpy
    ```
2.  **安装依赖**: `uv` 会根据 `uv.lock` 文件为您安装所有必要的依赖。
    ```bash
    uv sync
    ```
3.  **直接运行**: 您现在可以直接通过 `python` 命令运行 `fdl_copy.py`, `fdl_paste.py` 或 `tui_fdl_pro.py`。

### 使用 `pip` 安装 (备选)
如果您不使用 `uv`，可以通过 `pip` 手动安装依赖：
```bash
pip install blessed pyperclip
```
然后同样直接运行脚本。

## 依赖
- `blessed`: 用于 TUI 界面的强大终端操作库。
- `pyperclip`: 提供跨平台的剪切板操作能力。

## 许可证
本项目采用 [Apache 许可证 2.0](https://www.apache.org/licenses/LICENSE-2.0) 授权。

