# users/message.py
from dataclasses import dataclass

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

    def ToDict(self) -> dict:
        """序列化给前端websocket使用"""
        return {
            "m_type": self.m_type,
            "m_content": self.m_content,
            "m_color": self.m_color
        }