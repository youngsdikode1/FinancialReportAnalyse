from typing import Dict, List, Optional
import threading
import time
import io
import os
from datetime import datetime
import traceback
# ====================== CLog: 日志文件对象 ======================
class CLog:
    """
    日志文件对象类，管理单个日志文件的写入。
    特性:
    - append 模式：按日期分割文件 {name}-YYYY‑MM‑DD.log，Flush时检测跨天自动切换文件
    - overwrite 模式：每次启动时清空本地同一个文件（覆盖），文件名固定 {name}.log
    - 内存缓冲机制，批量刷新提升性能
    - 可配置时间戳格式
    """
    def __init__(
        self,
        file_name: str,
        open_type: str = "append",  # "append" or "overwrite"
        add_time: bool = False
    ):
        """
        初始化日志对象。
        Args:
            file_name: 日志文件名（不含扩展名）
            open_type: 打开类型："append"(追加按日期分文件) / "overwrite"(启动覆盖固定文件名)
            add_time: 是否每行添加时间戳，默认 False
        """
        self.m_file_name = file_name
        self.m_open_type = open_type
        self.m_add_time = add_time
        self.m_logs: List[str] = []
        self.m_file_handler: Optional[io.TextIOWrapper] = None  # 保存文件句柄
        self.m_last_open_date: Optional[str] = None  # 记录上次打开文件的日期 "2026‑10‑17"，仅append生效

        # 内部局部导入，避免顶层循环依赖
        from utils.config import CConfigManager
        data_dir = CConfigManager.GetInstance().GetDataDir()
        self.m_log_dir = os.path.join(data_dir, "logs")
        os.makedirs(self.m_log_dir, exist_ok=True)

    def SetLogTime(self):
        self.m_add_time = True

    def _OpenFile(self) -> None:
        """内部方法：打开或创建日志文件"""
        if self.m_open_type == "append":
            # append模式：带日期后缀
            filename = os.path.join(self.m_log_dir, f"{self.m_file_name}-{self.m_last_open_date}.log")
            mode = "a"
        else:
            # overwrite模式：固定文件名
            filename = os.path.join(self.m_log_dir, f"{self.m_file_name}.log")
            mode = "w"
        try:
            self.m_file_handler = open(filename, mode, encoding="utf-8")
        except Exception as e:
            print(f"[CLog] 打开/创建文件失败：{filename}, 错误：{e}")

    def Log(self, input: str) -> None:
        """
        添加一行日志到内存缓冲。仅写内存，不做时间判断。
        Args:
            input: 日志内容（不包含时间戳）
        """
        if self.m_add_time:
            timestamp = datetime.now().strftime("%H:%M:%S")
            log_line = f"[{timestamp}] {input}"
        else:
            log_line = input
        self.m_logs.append(log_line)
        return log_line

    def Flush(self) -> None:
        """
        将内存中收集的日志统一刷新到文件中。
        append模式：在此处检测跨天，检测到日期变化则切换日志文件。
        """
        if not self.m_logs:
            return

        # append模式下，Flush时检测是否跨天
        if self.m_open_type == "append":
            today_str = datetime.now().strftime("%Y-%m-%d")
            if self.m_last_open_date is None:
                # 首次打开
                self.m_last_open_date = today_str
            elif today_str != self.m_last_open_date:
                # 跨天：关闭旧句柄，下次_OpenFile打开新日期文件
                self._CloseFile()
                self.m_file_handler = None
                self.m_last_open_date = today_str

        if not self.m_file_handler:
            self._OpenFile()

        try:
            logs = self.m_logs
            self.m_logs = []
            for log_line in logs:
                self.m_file_handler.write(log_line + "\n")
            self.m_file_handler.flush()
        except Exception as e:
            print(f"[CLog] 刷新日志到文件失败：{e}")

    def _CloseFile(self) -> None:
        """内部方法：关闭文件句柄"""
        try:
            if self.m_file_handler is not None:
                self.m_file_handler.close()
                self.m_file_handler = None
        except Exception as e:
            print(f"[CLog] 关闭文件失败：{e}")

    def __del__(self) -> None:
        """析构函数：对象销毁时关闭文件"""
        self._CloseFile()

# ====================== CLogManager: 日志管理器 ======================
class CLogManager:
    """
    日志管理器（单例类），统一管理所有日志对象。
    设计模式：单例模式 + 工厂模式
    - 通过 GetInstance() 获取唯一实例
    - GetLogObj() 根据名称获取或创建对应的 CLog 对象
    - 后台线程处理：每 0.01 秒自动 flush 所有日志到文件
    """
    __instance = None
    m_log_objects: Dict[str, CLog] = {}
    m_flush_thread: Optional[threading.Thread] = None

    def __init__(self):
        pass

    @classmethod
    def GetInstance(cls) -> "CLogManager":
        if cls.__instance is None:
            cls.__instance = cls()
            cls.__instance._StartFlushThread()
        return cls.__instance

    def _StartFlushThread(self) -> None:
        if self.m_flush_thread and self.m_flush_thread.is_alive():
            return
        self.m_flush_thread = threading.Thread(
            target=self._FlushLoop,
            daemon=True
        )
        self.m_flush_thread.start()

    def _FlushLoop(self) -> None:
        while True:
            time.sleep(0.01)
            for name, log_obj in list(self.m_log_objects.items()):
                if log_obj.m_logs:
                    log_obj.Flush()

    def GetLogObj(self, name: str) -> CLog:
        if name not in self.m_log_objects:
            self.m_log_objects[name] = CLog(file_name=name)
        return self.m_log_objects[name]

    def CloseAll(self) -> None:
        for name, log_obj in self.m_log_objects.items():
            log_obj._CloseFile()

def LogInfo(string):
    log1 = CLogManager.GetInstance().GetLogObj("info")
    log1.SetLogTime()
    log_line = log1.Log(string)
    print(log_line)

def LogFatal(string):
    log1 = CLogManager.GetInstance().GetLogObj("fatal")
    log1.SetLogTime()
    log_line = log1.Log(string)    
    LogInfo(string)


def PrintLog(string):
    print(string)
    log1 = CLogManager.GetInstance().GetLogObj("print")
    log1.m_open_type = "overwrite"
    log1.Log(string)

def LLMRequestLog(string):
    log1 = CLogManager.GetInstance().GetLogObj("llm_request")
    log1.Log(string)

def LogStack(ex: Exception, desc: str):
    """
    记录异常堆栈，err_stack 使用 append模式，自动按日期分割 err_stack‑2026‑10‑17.log
    """
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        stack_text = traceback.format_exc()
        content = (
            f"\n===== {now} | {desc} =====\n"
            f"Exception: {repr(ex)}\n"
            f"Stack:\n{stack_text}\n"
        )
        print(content)
        mgr = CLogManager.GetInstance()
        err_log = mgr.GetLogObj("err_stack")
        err_log.Log(content)
    except Exception as e:
        print(f"LogStack inner error: {e}")

def Main() -> None:
    """单元测试"""
    print("=== CLogManager 自测开始 ===\n")
    mgr = CLogManager.GetInstance()
    print("✓ CLogManager 实例已创建，后台 flush 线程已启动\n")

    # append测试，会生成 test‑2026‑xx‑xx.log
    log2 = mgr.GetLogObj("test_append")
    log2.m_open_type = "append"
    log2.Log("append 测试行1")
    log2.Log("append 测试行2")

    LogInfo("hahaha")
    PrintLog("hehehehe")

    # 模拟跨天测试（仅本地调试，实际运行靠系统时间）
    # log2.m_last_open_date = "2026‑10‑16"

    time.sleep(0.3)
    mgr.CloseAll()
    print("\n✓ 程序退出，测试结束！")

if __name__ == "__main__":
    Main()