import os
import sys
import argparse
import pyperclip

# FDL 标记
FILE_MARKER = "$$FILE"

def is_text_file(filepath):
    """
    判断一个文件是否可能为文本文件。
    尝试用 UTF-8 解码文件的前1024字节，如果成功则认为是文本文件。
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            f.read(1024)
        return True
    except (UnicodeDecodeError, IOError):
        return False

def dir_to_fdl(source_dir):
    """
    将目录结构和文本文件内容转换为FDL字符串。
    """
    if not os.path.isdir(source_dir):
        raise ValueError(f"错误: 提供的路径 '{source_dir}' 不是一个有效的目录。")

    fdl_parts = []
    base_dir = os.path.abspath(source_dir)

    for dirpath, _, filenames in os.walk(base_dir):
        for filename in filenames:
            full_path = os.path.join(dirpath, filename)
            
            # 仅处理文本文件
            if is_text_file(full_path):
                # 获取相对于源目录的路径
                relative_path = os.path.relpath(full_path, base_dir)
                
                # 添加文件标记和路径
                fdl_parts.append(f"{FILE_MARKER} {relative_path.replace(os.sep, '/')}")
                
                # 读取并添加文件内容
                with open(full_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    fdl_parts.append(content)

    return "\n".join(fdl_parts)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="将目录内容编码为FDL并复制到剪切板。")
    parser.add_argument("directory", help="要编码的源目录路径。")
    args = parser.parse_args()

    try:
        fdl_string = dir_to_fdl(args.directory)
        if fdl_string:
            pyperclip.copy(fdl_string)
            print(f"成功将目录 '{args.directory}' 的FDL表示复制到剪切板。")
        else:
            print(f"目录 '{args.directory}' 中没有找到可处理的文本文件。")

    except Exception as e:
        print(f"发生错误: {e}", file=sys.stderr)
        sys.exit(1)

