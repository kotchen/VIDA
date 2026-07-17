#!/usr/bin/env python3
"""
AI视频转录器启动脚本
"""

import os
import sys
import subprocess
import argparse
from dataclasses import dataclass
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


@dataclass(frozen=True)
class StartupOptions:
    production_mode: bool
    port: int


def parse_startup_options(argv=None, environ=None):
    if argv is None:
        argv = sys.argv[1:]
    if environ is None:
        environ = os.environ

    parser = argparse.ArgumentParser(description="Start AI Video Transcriber")
    parser.add_argument("--prod", action="store_true", help="Disable hot reload")
    parser.add_argument("--port", type=int, help="Server port")
    args = parser.parse_args(argv)

    return StartupOptions(
        production_mode=args.prod or environ.get("PRODUCTION_MODE") == "true",
        port=args.port if args.port is not None else int(environ.get("PORT", 8000)),
    )


def configure_ffmpeg_path(environ=None, project_root=None):
    if environ is None:
        environ = os.environ
    if project_root is None:
        project_root = Path(__file__).parent

    explicit_location = environ.get("FFMPEG_LOCATION")
    candidates = []
    if explicit_location:
        explicit_path = Path(explicit_location)
        candidates.append(explicit_path.parent if explicit_path.is_file() else explicit_path)
    candidates.append(Path(project_root) / "tools" / "ffmpeg" / "bin")

    for candidate in candidates:
        if (candidate / "ffmpeg.exe").exists() and (candidate / "ffprobe.exe").exists():
            current_path = environ.get("PATH", "")
            path_parts = [part for part in current_path.split(os.pathsep) if part]
            candidate_text = str(candidate)
            if candidate_text not in path_parts:
                environ["PATH"] = os.pathsep.join([candidate_text] + path_parts)
            environ["FFMPEG_LOCATION"] = candidate_text
            return candidate

    return None

def check_dependencies():
    """检查依赖是否安装"""
    import sys
    required_packages = {
        "fastapi": "fastapi",
        "uvicorn": "uvicorn", 
        "yt-dlp": "yt_dlp",
        "faster-whisper": "faster_whisper",
        "openai": "openai"
    }
    
    missing_packages = []
    for display_name, import_name in required_packages.items():
        try:
            __import__(import_name)
        except ImportError:
            missing_packages.append(display_name)
    
    if missing_packages:
        print("❌ 缺少以下依赖包:")
        for package in missing_packages:
            print(f"   - {package}")
        print("\n请运行以下命令安装依赖:")
        print("source venv/bin/activate && pip install -r requirements.txt")
        return False
    
    print("✅ 所有依赖已安装")
    return True

def check_ffmpeg():
    """检查FFmpeg是否安装"""
    try:
        subprocess.run(["ffmpeg", "-version"], 
                      stdout=subprocess.DEVNULL, 
                      stderr=subprocess.DEVNULL, 
                      check=True)
        print("✅ FFmpeg已安装")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("❌ 未找到FFmpeg")
        print("请安装FFmpeg:")
        print("  macOS: brew install ffmpeg")
        print("  Ubuntu: sudo apt install ffmpeg")
        print("  Windows: 从官网下载 https://ffmpeg.org/download.html")
        return False

def setup_environment():
    """设置环境变量"""
    # 设置OpenAI配置
    if not os.getenv("OPENAI_API_KEY"):
        print("⚠️  警告: 未设置OPENAI_API_KEY环境变量")
        print("请设置环境变量: export OPENAI_API_KEY=your_api_key_here")
        return False
    
    print("✅ 已设置OpenAI API Key")
    
    if not os.getenv("OPENAI_BASE_URL"):
        os.environ["OPENAI_BASE_URL"] = "https://oneapi.basevec.com/v1"
        print("✅ 已设置OpenAI Base URL")
    
    # 设置其他默认配置
    if not os.getenv("WHISPER_MODEL_SIZE"):
        os.environ["WHISPER_MODEL_SIZE"] = "base"
    
    print("🔑 OpenAI API已配置，摘要功能可用")
    return True

def main():
    """主函数"""
    # 检查是否使用生产模式（禁用热重载）
    options = parse_startup_options()
    production_mode = options.production_mode
    configure_ffmpeg_path()
    
    print("🚀 AI视频转录器启动检查")
    if production_mode:
        print("🔒 生产模式 - 热重载已禁用")
    else:
        print("🔧 开发模式 - 热重载已启用")
    print("=" * 50)
    
    # 检查依赖
    if not check_dependencies():
        sys.exit(1)
    
    # 检查FFmpeg
    if not check_ffmpeg():
        print("⚠️  FFmpeg未安装，可能影响某些视频格式的处理")
    
    # 设置环境
    setup_environment()
    
    print("\n🎉 启动检查完成!")
    print("=" * 50)
    
    # 启动服务器
    host = os.getenv("HOST", "0.0.0.0")
    port = options.port
    
    print(f"\n🌐 启动服务器...")
    print(f"   地址: http://localhost:{port}")
    print(f"   按 Ctrl+C 停止服务")
    print("=" * 50)
    
    try:
        # 切换到backend目录并启动服务
        backend_dir = Path(__file__).parent / "backend"
        os.chdir(backend_dir)
        
        cmd = [
            sys.executable, "-m", "uvicorn", "main:app",
            "--host", host,
            "--port", str(port)
        ]
        
        # 只在开发模式下启用热重载
        if not production_mode:
            cmd.append("--reload")
        
        subprocess.run(cmd, env=os.environ.copy())
        
    except KeyboardInterrupt:
        print("\n\n👋 服务已停止")
    except Exception as e:
        print(f"\n❌ 启动失败: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
