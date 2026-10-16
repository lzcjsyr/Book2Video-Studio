"""
项目路径管理器 - 集中管理所有项目相关的文件路径
避免在多个地方重复构建路径逻辑
"""

import os
from typing import Optional


class ProjectPaths:
    """项目路径管理器，统一管理项目文件结构"""
    
    def __init__(self, project_dir: str):
        """
        初始化项目路径管理器
        
        Args:
            project_dir: 项目根目录路径
        """
        self.root = project_dir
        self.text = os.path.join(project_dir, "text")
        self.images = os.path.join(project_dir, "images")
        self.voice = os.path.join(project_dir, "voice")
    
    # ==================== Text 目录相关路径 ====================
    
    def raw_json(self) -> str:
        """原始数据JSON文件路径"""
        return os.path.join(self.text, "raw.json")
    
    def script_json(self) -> str:
        """脚本JSON文件路径"""
        return os.path.join(self.text, "script.json")
    
    def mini_summary_json(self) -> str:
        """步骤2描述小结JSON文件路径"""
        return os.path.join(self.text, "mini_summary.json")
    
    # ==================== Images 目录相关路径 ====================
    
    def opening_image(self) -> str:
        """开场素材路径（自动检测图片或视频格式）"""
        # 按优先级检查：视频格式优先，然后是图片格式
        for ext in ['.mp4', '.mov', '.avi', '.mkv', '.webm', '.png', '.jpg']:
            path = os.path.join(self.images, f"opening{ext}")
            if os.path.exists(path):
                return path
        # 默认返回 PNG 路径（向后兼容）
        return os.path.join(self.images, "opening.png")
    
    def segment_image(self, index: int) -> str:
        """
        段落图片路径
        
        Args:
            index: 段落索引（从1开始）
        """
        return os.path.join(self.images, f"segment_{index}.png")
    
    # ==================== Voice 目录相关路径 ====================
    
    def opening_audio(self) -> str:
        """开场音频路径"""
        return os.path.join(self.voice, "opening.wav")
    
    def segment_audio(self, index: int) -> str:
        """段落音频路径（index 从 1 开始）"""
        return os.path.join(self.voice, f"voice_{index}.wav")
    
    # ==================== 其他文件路径 ====================
    
    def final_video(self) -> str:
        """最终视频文件路径"""
        return os.path.join(self.root, "final_video.mp4")
    
    # ==================== 工具方法 ====================
    
    def ensure_dirs_exist(self) -> None:
        """确保所有必要的子目录都存在"""
        os.makedirs(self.text, exist_ok=True)
        os.makedirs(self.images, exist_ok=True)
        os.makedirs(self.voice, exist_ok=True)
    
    def segment_audio_exists(self, index: int) -> Optional[str]:
        """段落音频存在时返回其路径，否则返回 None。"""
        path = self.segment_audio(index)
        return path if os.path.exists(path) else None


__all__ = ['ProjectPaths']
