from dataclasses import dataclass
import time

class CMessageType:
    # 左侧聊天面板
    TYPE_CHAT_USER = "TYPE_CHAT_USER"
    TYPE_CHAT_BOT = "TYPE_CHAT_BOT"
    # 右侧状态监控面板（财报任务日志、解析进度、报错）
    TYPE_CHAT_MONITOR = "TYPE_CHAT_MONITOR"


@dataclass
class CMessage:
    m_type: str
    m_content: str
    m_color: str
    m_time: int   # unix 毫秒时间戳

    def ToDict(self) -> dict:
        """序列化给前端websocket、持久化存档使用"""
        return {
            "m_type": self.m_type,
            "m_content": self.m_content,
            "m_color": self.m_color,
            "m_time": self.m_time
        }

    @staticmethod
    def Load(d: dict) -> "CMessage":
        """
        静态方法：从字典反序列化回CMessage对象
        解析逻辑收拢在此处，存档加载统一调用该方法
        """
        return CMessage(
            m_type=d["m_type"],
            m_content=d["m_content"],
            m_color=d["m_color"],
            m_time=d["m_time"]
        )

    @staticmethod
    def NewMessage(msg_type: str, content: str, color: str) -> "CMessage":
        """
        工厂方法：生成一条新消息，自动填充当前毫秒时间戳
        """
        ts = int(time.time() * 1000)
        return CMessage(
            m_type=msg_type,
            m_content=content,
            m_color=color,
            m_time=ts
        )