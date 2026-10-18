from typing import List, Dict, Optional
# 全局不要import psutil，放到函数内部懒加载

from users.user_manager import CUserManager
from rag.rag_manager import CRAGManager
from utils.logs import LogStack, LogInfo


class CMonitorManager:
    __instance: Optional["CMonitorManager"] = None

    def __init__(self):
        if CMonitorManager.__instance is not None:
            raise RuntimeError("请使用 CMonitorManager.GetInstance() 获取单例，不要直接实例化")

    @classmethod
    def GetInstance(cls) -> "CMonitorManager":
        if cls.__instance is None:
            cls.__instance = cls()
        return cls.__instance

    def GetUserInfo(self) -> str:
        """获取在线用户信息，返回格式化文本"""
        try:
            user_manager = CUserManager()
            count = user_manager.GetOnlineUserCount()
            lines = [
                "==== 用户在线统计 ====",
                f"在线用户数量：{count}"
            ]
            return "\n".join(lines)
        except Exception as e:
            LogStack(e, "CMonitorManager.GetUserInfo 异常")
            return f"==== 用户在线统计 ====\n获取在线用户信息失败：{str(e)}"

    def GetRAGInfo(self) -> str:
        """获取全部RAG collection信息，返回格式化文本"""
        try:
            coll_list: List[Dict] = CRAGManager.GetInstance().GetCollectionInfos()
            lines = ["==== RAG向量库集合信息 ===="]
            for item in coll_list:
                name = item.get("name", "unknown")
                cnt = item.get("count", "-")
                status = item.get("status", "-")
                lines.append(f"集合名:{name}，文档数:{cnt}，状态:{status}")
            lines.append(f"总集合数量：{len(coll_list)}")
            return "\n".join(lines)
        except Exception as e:
            LogStack(e, "CMonitorManager.GetRAGInfo 异常")
            return f"==== RAG向量库集合信息 ====\n获取RAG集合信息失败：{str(e)}"

    def GetHardwareInfo(self) -> str:
        """
        获取CPU、内存硬件占用信息
        psutil 函数内部局部import；未安装psutil时返回提示，不影响其他业务
        """
        try:
            # 局部懒加载，模块级别不依赖psutil
            import psutil
        except ImportError:
            return "==== 系统硬件资源 ====\n⚠️未安装psutil，无法采集硬件信息\n执行 pip install psutil 开启硬件监控"

        try:
            cpu_percent = psutil.cpu_percent(interval=0.4)
            mem = psutil.virtual_memory()
            total_gb = round(mem.total / (1024 ** 3), 2)
            used_gb = round(mem.used / (1024 ** 3), 2)
            mem_percent = mem.percent

            lines = [
                "==== 系统硬件资源 ====",
                f"CPU使用率：{cpu_percent} %",
                f"内存使用率：{mem_percent} %",
                f"内存总大小：{total_gb} GB",
                f"内存已使用：{used_gb} GB"
            ]
            return "\n".join(lines)
        except Exception as e:
            LogStack(e, "CMonitorManager.GetHardwareInfo 采集硬件异常")
            return f"==== 系统硬件资源 ====\n读取硬件资源失败：{str(e)}"


def TestMonitor():
    """本地自测"""
    mgr = CMonitorManager.GetInstance()
    print(mgr.GetUserInfo())
    print("\n")
    print(mgr.GetRAGInfo())
    print("\n")
    print(mgr.GetHardwareInfo())


if __name__ == "__main__":
    TestMonitor()