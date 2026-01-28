"""
User management for session isolation.

This module provides utilities for creating and managing isolated Linux users
for each session, ensuring complete filesystem and process isolation.
"""

import asyncio
import logging
import os
import pwd
import grp
import shutil
import shlex
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, List, Tuple
from fastapi import HTTPException

logger = logging.getLogger(__name__)

# 存储session到用户名的映射
session_users: Dict[str, str] = {}

# 后台进程注册表：session_id -> {process_id -> BackgroundProcessEntry}
_background_processes: Dict[str, Dict[str, "BackgroundProcessEntry"]] = {}

# 用户ID范围（从10000开始，避免与系统用户冲突）
USER_ID_START = 10000
USER_ID_COUNTER = USER_ID_START

# 用户组名
USER_GROUP = "shipyard_users"
USER_GROUP_ID = 9999

# 元数据文件路径
METADATA_DIR = Path("/app/metadata")
SESSION_USERS_FILE = METADATA_DIR / "session_users.json"
USERS_INFO_FILE = METADATA_DIR / "users_info.json"


@dataclass
class ProcessResult:
    success: bool
    stdout: str
    stderr: str
    return_code: Optional[int] = None
    pid: Optional[int] = None
    process_id: Optional[str] = None
    error: Optional[str] = None


class BackgroundProcessEntry:
    """后台进程条目"""

    def __init__(
        self,
        process_id: str,
        pid: int,
        command: str,
        process: asyncio.subprocess.Process,
    ):
        self.process_id = process_id
        self.pid = pid
        self.command = command
        self.process = process

    @property
    def status(self) -> str:
        """获取进程状态"""
        if self.process.returncode is None:
            return "running"
        elif self.process.returncode == 0:
            return "completed"
        else:
            return "failed"


def generate_process_id() -> str:
    """生成进程ID"""
    return str(uuid.uuid4())[:8]


def register_background_process(
    session_id: str,
    process_id: str,
    pid: int,
    command: str,
    process: asyncio.subprocess.Process,
) -> None:
    """注册后台进程"""
    if session_id not in _background_processes:
        _background_processes[session_id] = {}
    _background_processes[session_id][process_id] = BackgroundProcessEntry(
        process_id=process_id,
        pid=pid,
        command=command,
        process=process,
    )
    logger.info(
        "Registered background process: session=%s process_id=%s pid=%s",
        session_id,
        process_id,
        pid,
    )


def get_session_background_processes(session_id: str) -> List[Dict]:
    """获取指定 session 的所有后台进程"""
    if session_id not in _background_processes:
        return []

    processes = []
    for entry in _background_processes[session_id].values():
        processes.append(
            {
                "process_id": entry.process_id,
                "pid": entry.pid,
                "command": entry.command,
                "status": entry.status,
            }
        )
    return processes


def save_session_users():
    """保存 session 到用户的映射关系到磁盘"""
    try:
        METADATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(SESSION_USERS_FILE, "w") as f:
            json.dump(session_users, f, indent=2)
        logger.info(f"Saved session_users mapping: {len(session_users)} sessions")
    except Exception as e:
        logger.error(f"Failed to save session_users: {e}")


def load_session_users():
    """从磁盘加载 session 到用户的映射关系"""
    global session_users
    try:
        if SESSION_USERS_FILE.exists():
            with open(SESSION_USERS_FILE, "r") as f:
                session_users = json.load(f)
            logger.info(f"Loaded session_users mapping: {len(session_users)} sessions")
        else:
            logger.info("No existing session_users file found, starting fresh")
    except Exception as e:
        logger.error(f"Failed to load session_users: {e}")
        session_users = {}


def save_user_info(username: str, user_id: int, group_id: int, home_dir: str):
    """保存用户信息到磁盘"""
    try:
        METADATA_DIR.mkdir(parents=True, exist_ok=True)

        # 读取现有数据
        users_info = {}
        if USERS_INFO_FILE.exists():
            with open(USERS_INFO_FILE, "r") as f:
                users_info = json.load(f)

        # 更新用户信息
        users_info[username] = {
            "uid": user_id,
            "gid": group_id,
            "home_dir": home_dir,
        }

        # 保存到文件
        with open(USERS_INFO_FILE, "w") as f:
            json.dump(users_info, f, indent=2)

        logger.info(f"Saved user info for {username}")
    except Exception as e:
        logger.error(f"Failed to save user info for {username}: {e}")


def load_users_info() -> Dict:
    """从磁盘加载所有用户信息"""
    try:
        if USERS_INFO_FILE.exists():
            with open(USERS_INFO_FILE, "r") as f:
                users_info = json.load(f)
            logger.info(f"Loaded users info: {len(users_info)} users")
            return users_info
        else:
            logger.info("No existing users_info file found")
            return {}
    except Exception as e:
        logger.error(f"Failed to load users info: {e}")
        return {}


class UserManager:
    """管理session用户的类"""

    @staticmethod
    async def ensure_shipyard_group():
        """确保shipyard用户组存在"""
        try:
            grp.getgrnam(USER_GROUP)
            logger.info(f"Group {USER_GROUP} already exists")
        except KeyError:
            # 创建用户组
            process = await asyncio.create_subprocess_exec(
                "groupadd",
                "-g",
                str(USER_GROUP_ID),
                USER_GROUP,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to create group {USER_GROUP}: {stderr.decode()}",
                )
            logger.info(f"Created group {USER_GROUP} with GID {USER_GROUP_ID}")

    @staticmethod
    async def create_session_user(session_id: str) -> str:
        """为session创建独立的Linux用户"""
        global USER_ID_COUNTER

        if session_id in session_users:
            return session_users[session_id]

        # 确保用户组存在
        await UserManager.ensure_shipyard_group()

        # 生成用户名（基于session_id，但限制长度和字符）
        username = f"ship_{session_id[:8]}"
        user_id = USER_ID_COUNTER
        USER_ID_COUNTER += 1

        # 检查用户是否已存在
        try:
            pwd.getpwnam(username)
            logger.info(f"User {username} already exists")
            session_users[session_id] = username
            save_session_users()
            return username
        except KeyError:
            pass

        # 创建用户主目录
        home_dir = f"/home/{username}"

        # 先手动创建home目录（因为/home是挂载的卷，useradd -m可能失败）
        home_path = Path(home_dir)
        home_path.mkdir(parents=True, exist_ok=True)

        # 创建用户（不使用-m选项，因为我们已经手动创建了目录）
        process = await asyncio.create_subprocess_exec(
            "useradd",
            "-u",
            str(user_id),  # 用户ID
            "-g",
            USER_GROUP,  # 主用户组
            "-d",
            home_dir,  # 主目录
            "-s",
            "/bin/bash",  # 默认shell
            username,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            error_msg = stderr.decode().strip()
            if "already exists" in error_msg:
                logger.info(f"User {username} already exists")
                session_users[session_id] = username
                save_session_users()
                # 即使用户已存在，也确保workspace正确设置
                await UserManager.setup_user_workspace(username, home_dir)
                return username
            else:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to create user {username}: {error_msg}",
                )

        logger.info(f"Created user {username} with UID {user_id}")

        # 设置用户主目录和workspace权限
        await UserManager.setup_user_workspace(username, home_dir)

        session_users[session_id] = username

        # 保存用户信息和映射关系到磁盘
        save_user_info(username, user_id, USER_GROUP_ID, home_dir)
        save_session_users()

        return username

    @staticmethod
    async def setup_user_workspace(username: str, home_dir: str):
        """设置用户的工作空间"""
        home_path = Path(home_dir)
        home_path.mkdir(parents=True, exist_ok=True)
        workspace_dir = home_path / "workspace"
        workspace_dir.mkdir(exist_ok=True)

        # 获取用户信息
        user_info = pwd.getpwnam(username)
        user_id = user_info.pw_uid
        group_id = user_info.pw_gid

        # 设置权限
        shutil.chown(home_path, user=user_id, group=group_id)
        shutil.chown(workspace_dir, user=user_id, group=group_id)
        os.chmod(home_path, 0o755)
        os.chmod(workspace_dir, 0o755)

        # 创建基本的shell配置文件
        bashrc_path = home_path / ".bashrc"
        if not bashrc_path.exists():
            bashrc_content = """
# Basic shell configuration for shipyard user
export PS1='\\u@shipyard:\\w\\$ '
export PATH=/usr/local/bin:/usr/bin:/bin
cd ~/workspace
"""
            bashrc_path.write_text(bashrc_content)
            shutil.chown(bashrc_path, user=user_id, group=group_id)
            os.chmod(bashrc_path, 0o644)

        logger.info(f"Set up workspace for user {username} at {workspace_dir}")

    @staticmethod
    def get_session_user(session_id: str) -> Optional[str]:
        """获取session对应的用户名"""
        return session_users.get(session_id)

    @staticmethod
    async def get_user_info(username: str) -> Dict:
        """获取用户信息"""
        try:
            user_info = pwd.getpwnam(username)
            return {
                "username": username,
                "uid": user_info.pw_uid,
                "gid": user_info.pw_gid,
                "home_dir": user_info.pw_dir,
                "shell": user_info.pw_shell,
            }
        except KeyError:
            raise HTTPException(status_code=404, detail=f"User {username} not found")

    @staticmethod
    async def recreate_user_from_metadata(username: str, user_info: Dict) -> bool:
        """从元数据重建 Linux 用户账户"""
        try:
            # 检查用户是否已存在
            try:
                pwd.getpwnam(username)
                logger.info(f"User {username} already exists, skipping recreation")
                return True
            except KeyError:
                pass

            # 确保用户组存在
            await UserManager.ensure_shipyard_group()

            user_id = user_info.get("uid")
            home_dir = user_info.get("home_dir")

            if not user_id or not home_dir:
                logger.error(f"Missing uid or home_dir for user {username}")
                return False

            # 重建用户账户
            process = await asyncio.create_subprocess_exec(
                "useradd",
                "-u",
                str(user_id),
                "-g",
                USER_GROUP,
                "-d",
                home_dir,
                "-M",  # 不创建主目录（已存在）
                "-s",
                "/bin/bash",
                username,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                error_msg = stderr.decode().strip()
                if "already exists" in error_msg:
                    logger.info(f"User {username} already exists")
                    return True
                else:
                    logger.error(f"Failed to recreate user {username}: {error_msg}")
                    return False

            logger.info(f"Recreated user {username} with UID {user_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to recreate user {username}: {e}")
            return False

    @staticmethod
    async def restore_all_users() -> int:
        """启动时恢复所有用户账户"""
        try:
            # 加载映射关系
            load_session_users()

            # 加载用户信息
            users_info = load_users_info()

            if not users_info:
                logger.info("No users to restore")
                return 0

            # 恢复用户账户
            restored_count = 0
            for username, user_info in users_info.items():
                if await UserManager.recreate_user_from_metadata(username, user_info):
                    restored_count += 1

            logger.info(f"Restored {restored_count}/{len(users_info)} users")
            return restored_count

        except Exception as e:
            logger.error(f"Failed to restore users: {e}")
            return 0

    @staticmethod
    async def start_interactive_shell(
        session_id: str,
        cols: int = 80,
        rows: int = 24,
        env: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, int]:
        """
        启动交互式 shell (PTY)

        Returns:
            (master_fd, pid)
        """
        try:
            import pty
            import tty

            username = await get_or_create_session_user(session_id)
            user_info = await UserManager.get_user_info(username)
            user_home = user_info["home_dir"]
            working_dir = Path(user_home) / "workspace"

            # 准备环境变量
            process_env = {
                "HOME": user_home,
                "USER": username,
                "LOGNAME": username,
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "SHELL": "/bin/bash",
                "TERM": "xterm-256color",
                "LANG": "en_US.UTF-8",
            }
            if env:
                process_env.update(env)

            pid, master_fd = pty.fork()

            if pid == 0:  # Child process
                try:
                    # 设置工作目录
                    os.chdir(str(working_dir))

                    # 准备 sudo 命令参数
                    sudo_cmd = "/usr/bin/sudo"
                    sudo_args = [
                        sudo_cmd,
                        "-u",
                        username,
                        "-H",
                        "bash",  # 显式运行 bash
                        "-l",  # login shell
                    ]

                    os.execvpe(sudo_cmd, sudo_args, process_env)

                except Exception as e:
                    print(f"Error starting shell: {e}")
                    os._exit(1)

            # Parent process
            # 设置窗口大小
            import termios
            import struct
            import fcntl

            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)

            logger.info(f"Started interactive shell for {username} (PID {pid})")
            return master_fd, pid

        except Exception as e:
            logger.error(f"Failed to start interactive shell for {session_id}: {e}")
            raise

    @staticmethod
    async def cleanup_session_user(session_id: str) -> bool:
        """清理session用户"""
        username = session_users.get(session_id)
        if not username:
            return False

        try:
            # 终止用户的所有进程
            process = await asyncio.create_subprocess_exec(
                "pkill",
                "-u",
                username,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await process.communicate()

            if session_id in session_users:
                del session_users[session_id]
                save_session_users()

            logger.info(f"Cleaned up session user {username} for session {session_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to cleanup user {username}: {e}")
            return False


async def get_or_create_session_user(session_id: str) -> str:
    """获取或创建session对应的用户"""
    username = UserManager.get_session_user(session_id)
    if username:
        return username

    return await UserManager.create_session_user(session_id)


async def run_as_user(
    session_id: str,
    command: str,
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    timeout: Optional[int] = None,
    shell: bool = True,
    background: bool = False,
) -> ProcessResult:
    """以指定用户身份运行命令"""
    try:
        username = await get_or_create_session_user(session_id)
        user_info = await UserManager.get_user_info(username)
        user_home = user_info["home_dir"]

        # 准备环境变量
        process_env = {
            "HOME": user_home,
            "USER": username,
            "LOGNAME": username,
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "SHELL": "/bin/bash",
        }
        if env:
            process_env.update(env)

        working_dir = Path(user_info["home_dir"]) / "workspace"
        if cwd:
            if not os.path.isabs(cwd):
                working_dir = working_dir / cwd
            else:
                working_dir = Path(cwd)
            # resolve working dir
            working_dir = working_dir.resolve()
            try:
                working_dir.relative_to(Path(user_info["home_dir"]) / "workspace")
            except ValueError:
                raise HTTPException(
                    status_code=403,
                    detail=f"Access denied: path must be within user workspace: {user_info['home_dir']}/workspace",
                )

        # 使用 sudo 切换用户执行命令
        # sudo_command = f"sudo -u {username} -H bash -c 'cd {working_dir} && {command}'"

        env_args = []
        if env:
            for key, value in env.items():
                env_args.append(f"{key}={value}")

        if shell:
            sudo_args = [
                "sudo",
                "-u",
                username,
                "-H",
            ]
            if env_args:
                sudo_args.extend(["env", *env_args])
            sudo_args.extend(
                [
                    "bash",
                    "-lc",
                    f"cd {shlex.quote(str(working_dir))} && {command}",
                ]
            )
            logger.debug(
                "Shell exec args: %s env_keys=%s",
                sudo_args,
                list(env.keys()) if env else [],
            )
            process = await asyncio.create_subprocess_exec(
                *sudo_args,
                env=process_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            args = shlex.split(command)
            sudo_args = [
                "sudo",
                "-u",
                username,
                "-H",
            ]
            if env_args:
                sudo_args.extend(["env", *env_args])
            sudo_args.extend(args)
            logger.debug(
                "Exec args: %s env_keys=%s",
                sudo_args,
                list(env.keys()) if env else [],
            )
            process = await asyncio.create_subprocess_exec(
                *sudo_args,
                env=process_env,
                cwd=str(working_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

        if background:
            process_id = generate_process_id()
            # 注册后台进程（使用原始的 session_id，即调用时传入的 username 参数值）
            # 注意：run_as_user 的第一个参数实际上是 session_id
            register_background_process(
                session_id=session_id,
                process_id=process_id,
                pid=process.pid,
                command=command,
                process=process,
            )
            logger.info(
                "Background shell exec started: user=%s pid=%s process_id=%s cmd=%s",
                username,
                process.pid,
                process_id,
                command,
            )
            return ProcessResult(
                success=True,
                return_code=0,
                stdout="",
                stderr="",
                pid=process.pid,
                process_id=process_id,
            )
        else:
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
                return ProcessResult(
                    success=process.returncode == 0,
                    return_code=process.returncode,
                    stdout=stdout.decode().strip(),
                    stderr=stderr.decode().strip(),
                    pid=process.pid,
                    process_id=None,
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.communicate()
                return ProcessResult(
                    success=False,
                    return_code=-1,
                    stdout="",
                    stderr="",
                    pid=process.pid,
                    process_id=None,
                    error="Command timed out",
                )

    except Exception as e:
        logger.exception(
            "Shell exec failed: session_id=%s cmd=%s cwd=%s env_keys=%s",
            session_id,
            command,
            cwd,
            list(env.keys()) if env else [],
        )
        return ProcessResult(
            success=False,
            return_code=-1,
            stdout="",
            stderr="",
            error=str(e),
            pid=None,
            process_id=None,
        )
