from typing import Dict, List, Optional
import threading
import time
import io
from datetime import datetime
import traceback

# ====================== CLog: 日志文件对象 ======================
class CLog:
    """
    日志文件对象类，管理单个日志文件的写入。

    特性:
    - append 模式：每次 CLog 对象生成时保留原有文件内容，在末尾追加
      （对象生成时保持文件打开状态，通过 m_file_handler 直接写入）
    - overwrite 模式：每次启动时清空本地同一个文件（覆盖），Flush 时再打开写入
    - 内存缓冲机制，批量刷新提升性能
    - 可配置时间戳格式
    """
    def __init__(
        self,
        file_name: str,
        open_type: str = "overwrite",  # "append" or "overwrite"
        add_time: bool = False
    ):
        """
        初始化日志对象。

        Args:
            file_name: 日志文件名（不含扩展名）
            open_type: 打开类型："append"(追加保留原有内容) 或 "overwrite"(启动时清空文件)，默认"overwrite"
            add_time: 是否每行添加时间戳，默认 False
        """
        self.m_file_name = file_name
        self.m_open_type = open_type
        self.m_add_time = add_time
        self.m_logs: List[str] = []
        self.m_file_handler: Optional[io.TextIOWrapper] = None  # 保存文件句柄

    def _OpenFile(self) -> None:
        """内部方法：打开或创建日志文件"""
        filename = f"{self.m_file_name}.log"

        try:
            if self.m_open_type == "append":
                # append 模式：保留原有文件内容，打开并保持文件句柄
                mode = "a"
            else:  # overwrite 模式
                # overwrite 模式：启动时清空并创建新文件
                mode = "w"

            # 打开文件
            self.m_file_handler = open(filename, mode, encoding="utf-8")

        except Exception as e:
            print(f"[CLog] 打开/创建文件失败：{filename}, 错误：{e}")

    def Log(self, input: str) -> None:
        """
        添加一行日志到内存缓冲。

        Args:
            input: 日志内容（不包含时间戳）
        """
        if self.m_add_time:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_line = f"[{timestamp}] {input}"
        else:
            log_line = input

        self.m_logs.append(log_line)

    def Flush(self) -> None:
        """
        将内存中收集的日志统一刷新到文件中。

        注意：
        - append 模式：通过 m_file_handler 直接追加写入（文件保持打开状态）
        - overwrite 模式：先关闭再打开，清空后写入当前内存数据
        - 此方法确保写入操作安全
        """
        if not self.m_logs:
            return

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

    特性:
    - 启动时自动生成后台守护线程
    - 单例模式确保全局唯一实例
    """
    # 类变量初始化（在类定义时创建）
    __instance = None
    m_log_objects: Dict[str, CLog] = {}
    m_flush_thread: Optional[threading.Thread] = None

    def __init__(self):
        """构造函数：无实际操作，类变量在模块加载时已初始化"""
        pass

    @classmethod
    def GetInstance(cls) -> "CLogManager":
        """
        单例模式：获取或创建唯一的 CLogManager 实例。

        Returns:
            CLogManager 的唯一实例
        """
        if cls.__instance is None:
            cls.__instance = cls()
            # 在第一次初始化时立即启动后台线程
            cls.__instance._StartFlushThread()
        return cls.__instance

    def _StartFlushThread(self) -> None:
        """启动后台 flush 线程（内部方法，仅在第一次初始化时调用）"""
        if self.m_flush_thread and self.m_flush_thread.is_alive():
            return  # 线程已在运行

        self.m_flush_thread = threading.Thread(
            target=self._FlushLoop,
            daemon=True  # 设置为守护线程，主程序退出时自动终止
        )
        self.m_flush_thread.start()

    def _FlushLoop(self) -> None:
        """后台线程主循环：每 0.01 秒 flush 所有日志对象"""
        while True:
            time.sleep(0.01)
            # flush 所有已创建的日志对象的内存日志到文件
            for name, log_obj in list(self.m_log_objects.items()):
                if log_obj.m_logs:  # 只有有日志时才刷新
                    log_obj.Flush()

    def GetLogObj(self, name: str) -> CLog:
        """
        获取指定的日志对象，如果未创建则自动新建。

        Args:
            name: 日志文件名（作为标识符）

        Returns:
            对应的 CLog 对象

        行为说明:
        - 首次调用时会自动创建该名称的 CLog 对象，默认模式为 "overwrite"
        - 后台 flush 线程会自动处理所有日志对象的刷新
        """
        if name not in self.m_log_objects:
            # 首次遇到该名称，自动创建 CLog 对象
            self.m_log_objects[name] = CLog(file_name=name)

        return self.m_log_objects[name]

    def CloseAll(self) -> None:
        """
        关闭所有已创建的日志文件句柄。

        注意：此方法不会清空内存中的日志，建议调用后显式调用每个 CLog.Flush() 来刷新剩余日志。
        """
        for name, log_obj in self.m_log_objects.items():
            log_obj._CloseFile()
        # 保留在字典中，避免重新打开时文件被覆盖（如果是 append 模式）


def LogInfo(string):
    log1 = CLogManager.GetInstance().GetLogObj("info")
    log1.Log(string)
    print("LogInfo: " + string)


def PrintLog(string):
    print(string)
    log1 = CLogManager.GetInstance().GetLogObj("print")
    log1.Log(string)


def LLMRequestLog(string):
    log1 = CLogManager.GetInstance().GetLogObj("llm_request")
    log1.Log(string)


def LogStack(ex: Exception, desc: str):
    """
    记录异常堆栈，复用CLogManager，输出控制台 + err_stack.log(append模式)
    内部全包异常捕获，本函数绝不向外抛出异常
    """
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        stack_text = traceback.format_exc()
        content = (
            f"\n===== {now} | {desc} =====\n"
            f"Exception: {repr(ex)}\n"
            f"Stack:\n{stack_text}\n"
        )
        # 控制台打印
        print(content)
        # 获取err_stack日志对象，设置为append模式
        mgr = CLogManager.GetInstance()
        err_log = mgr.GetLogObj("err_stack")
        err_log.m_open_type = "append"
        err_log.Log(content)
    except Exception as e:
        print(f"LogStack inner error: {e}")


def Main() -> None:
    """主函数：运行单元测试"""

    print("=== CLogManager 自测开始 ===\n")

    # 1. 获取单例管理器（这会启动后台 flush 线程）
    mgr = CLogManager.GetInstance()
    print("✓ CLogManager 实例已创建，后台 flush 线程已启动\n")

    # 2. 通过名称自动创建第一个日志对象
    log1 = mgr.GetLogObj("test")
    print(f"✓ 获取/创建日志对象：{log1.m_file_name}.log")

    # 3. 写几行日志进去（不会立即刷新，由后台线程处理）
    log1.Log("这是测试日志第 1 行")
    log1.Log("这是测试日志第 2 行")
    log1.Log("这是测试日志第 3 行")

    # 4. 再创建第二个日志对象进行追加测试
    log2 = mgr.GetLogObj("test_append")
    log2.m_open_type = "append"  # 切换到追加模式
    log2.Log("追加测试 2")

    print("\n✓ 日志已写入内存，等待后台线程 flush...\n")

    LogInfo("hahaha")
    PrintLog("hehehehe")
    # 测试LogStack
    try:
        1 / 0
    except Exception as te:
        LogStack(te, "测试LogStack")

    # 5. 稍等一会，让后台线程处理
    time.sleep(0.3)  # 等待 0.1 秒，后台线程会自动 flush
    # 6. 手动关闭所有文件句柄
    mgr.CloseAll()

    print("\n✓ 程序退出，测试结束！")


# ====================== 测试入口点 ======================
if __name__ == "__main__":
    Main()