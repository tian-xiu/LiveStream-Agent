"""
音频播放器 — 跨平台音频播放，多后端自动降级。

支持的播放后端（按优先级）：
  Windows:  mpv > ffplay > winsound (仅 WAV) > PowerShell
  macOS:    afplay > mpv > ffplay
  Linux:    mpv > ffplay > aplay > paplay

若所有后端均不可用，播放器会记录错误但不抛异常，
确保 Agent 在无声卡环境下仍可正常运行。
"""

import asyncio
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from utils.logger import logger


class AudioPlayer:
    """
    音频播放器。

    用法：
        player = AudioPlayer()
        await player.play("data/audio/speech.mp3")
    """

    # 各平台可用的播放后端（按优先级排列）
    _BACKENDS_WINDOWS = ["mpv", "ffplay", "winsound", "powershell"]
    _BACKENDS_MACOS = ["afplay", "mpv", "ffplay"]
    _BACKENDS_LINUX = ["mpv", "ffplay", "aplay", "paplay"]

    def __init__(self):
        self._system = platform.system()
        self._backends = self._get_backends()
        self._available_backend: Optional[str] = None  # 缓存的可用后端

    def _get_backends(self) -> list[str]:
        """根据操作系统返回可用后端列表。"""
        if self._system == "Windows":
            return self._BACKENDS_WINDOWS
        elif self._system == "Darwin":
            return self._BACKENDS_MACOS
        else:
            return self._BACKENDS_LINUX

    async def play(self, audio_path: str) -> bool:
        """
        播放音频文件。

        参数：
            audio_path: 音频文件路径

        返回：
            True 表示播放成功
        """
        path = Path(audio_path)
        if not path.exists():
            logger.error(f"音频文件不存在：{audio_path}")
            return False

        if path.stat().st_size == 0:
            logger.error(f"音频文件为空：{audio_path}")
            return False

        # 如果已有缓存的可用后端，直接使用
        if self._available_backend:
            return await self._play_with_backend(
                self._available_backend, str(path)
            )

        # 否则按优先级尝试所有后端
        for backend in self._backends:
            if await self._play_with_backend(backend, str(path)):
                self._available_backend = backend
                logger.info(f"音频播放后端：{backend}")
                return True

        logger.error("所有播放后端均不可用，请安装 mpv 或 ffmpeg")
        return False

    async def _play_with_backend(self, backend: str, path: str) -> bool:
        """用指定后端播放音频。返回 True 表示成功。"""
        method = getattr(self, f"_play_{backend}", None)
        if method is None:
            return False

        try:
            return await method(path)
        except Exception as e:
            logger.debug(f"播放后端 {backend} 失败：{e}")
            return False

    # ── 后端实现 ──────────────────────────────────

    async def _play_mpv(self, path: str) -> bool:
        """mpv 播放器（Windows/Linux/macOS 通用）。"""
        if not shutil.which("mpv"):
            return False

        cmd = ["mpv", "--no-terminal", "--no-video", path]
        return await self._run_async(cmd)

    async def _play_ffplay(self, path: str) -> bool:
        """ffplay 播放器（ffmpeg 附带）。"""
        if not shutil.which("ffplay"):
            return False

        cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path]
        return await self._run_async(cmd)

    async def _play_afplay(self, path: str) -> bool:
        """macOS 内置 afplay。"""
        cmd = ["afplay", path]
        return await self._run_async(cmd)

    async def _play_aplay(self, path: str) -> bool:
        """Linux ALSA aplay（仅 WAV）。"""
        if not shutil.which("aplay"):
            return False
        if not path.lower().endswith(".wav"):
            return False  # aplay 不支持 MP3

        cmd = ["aplay", "-q", path]
        return await self._run_async(cmd)

    async def _play_paplay(self, path: str) -> bool:
        """Linux PulseAudio paplay。"""
        if not shutil.which("paplay"):
            return False

        cmd = ["paplay", path]
        return await self._run_async(cmd)

    async def _play_winsound(self, path: str) -> bool:
        """Windows winsound（仅 WAV）。"""
        if not path.lower().endswith(".wav"):
            return False

        import winsound

        try:
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            # winsound 是同步的，我们用小延迟让声音开始播放
            await asyncio.sleep(0.1)
            return True
        except Exception:
            return False

    async def _play_powershell(self, path: str) -> bool:
        """Windows PowerShell 音频播放（最后的兜底方案）。"""
        if self._system != "Windows":
            return False

        ps_script = (
            f'Add-Type -AssemblyName PresentationCore; '
            f'$player = New-Object System.Windows.Media.MediaPlayer; '
            f'$player.Open("{path}"); '
            f'$player.Play(); '
            f'Start-Sleep -Seconds 2'
        )

        cmd = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            ps_script,
        ]
        result = await self._run_async(cmd)
        if result:
            logger.debug("使用 PowerShell 播放音频")
        return result

    # ── 工具方法 ──────────────────────────────────

    @staticmethod
    async def _run_async(cmd: list[str], timeout: float = 30.0) -> bool:
        """
        异步执行外部命令。

        返回 True 表示命令成功（返回码为 0）。
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(proc.wait(), timeout=timeout)
                return proc.returncode == 0
            except asyncio.TimeoutError:
                logger.debug(f"播放命令超时：{' '.join(cmd)}")
                proc.kill()
                return False
        except FileNotFoundError:
            return False
        except Exception as e:
            logger.debug(f"执行命令失败 {' '.join(cmd)}：{e}")
            return False


# ── 便捷函数 ──────────────────────────────────────


async def play_audio(file_path: str) -> bool:
    """快捷播放音频文件。"""
    player = AudioPlayer()
    return await player.play(file_path)
