import os
import sys
import argparse
import pyperclip

# FDL 标记
FILE_MARKER = "$$FILE"

def fdl_to_dir(fdl_string, target_dir):
    """
    解析FDL字符串并在目标目录中创建文件和文件夹。
    此函数设计为可处理来自不同操作系统（Windows, macOS, Linux）的文本。
    """
    # 核心修改：统一处理不同平台的换行符 (\n, \r\n, \r)
    # splitlines() 会智能地按所有类型的换行符分割，然后用 '\n' 重新连接，实现标准化。
    normalized_fdl = "\n".join(fdl_string.splitlines())

    if not normalized_fdl.strip().startswith(FILE_MARKER):
        raise ValueError("剪切板内容不是有效的FDL格式（未找到 $$FILE 标记）。")

    # 优化解析逻辑：通过在开头添加换行符，让所有文件块的分割方式保持一致。
    # 这样可以避免对第一个文件块进行特殊处理。
    if not normalized_fdl.startswith("\n"):
        # 确保第一个$$FILE前有换行符，以便统一分割
        temp_fdl = "\n" + normalized_fdl
    else:
        temp_fdl = normalized_fdl

    # 按“换行+标记”进行分割，第一个元素会是空字符串，直接忽略。
    parts = temp_fdl.split(f"\n{FILE_MARKER} ")

    if not os.path.exists(target_dir):
        os.makedirs(target_dir)
        print(f"已创建目标目录: {target_dir}")

    created_count = 0
    # 从第二个元素开始遍历 (parts[0] 是空字符串)
    for part in parts[1:]:
        if not part.strip():
            continue

        # 按第一个换行符分割路径和内容。由于已标准化，这里可以安全使用'\n'。
        try:
            path_part, content = part.split('\n', 1)
        except ValueError:  # 如果文件为空，没有换行符
            path_part = part
            content = ""

        # 清理路径字符串，防止因空格或制表符导致问题
        relative_path = path_part.strip()
        if not relative_path:
            print(f"警告：检测到空的相对路径，已跳过。", file=sys.stderr)
            continue

        # 构建完整的目标文件路径
        full_path = os.path.join(target_dir, relative_path)

        # 创建父目录
        parent_dir = os.path.dirname(full_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir)

        # 写入文件。Python的文本模式('w')默认会使用系统的标准换行符。
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)

        print(f"已创建文件: {full_path}")
        created_count += 1

    return created_count

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="从剪切板读取FDL并在指定目录创建文件。")
    parser.add_argument("directory", help="要写入文件的目标目录路径。")
    args = parser.parse_args()

    try:
        fdl_content = pyperclip.paste()
        if not fdl_content or not fdl_content.strip():
            print("剪切板为空或只包含空白字符。", file=sys.stderr)
            sys.exit(1)

        count = fdl_to_dir(fdl_content, args.directory)
        print(f"\n操作完成。共创建了 {count} 个文件。")

    except Exception as e:
        print(f"发生错误: {e}", file=sys.stderr)
        sys.exit(1)

