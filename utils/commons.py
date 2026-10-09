import inspect

def GetCaller():
    """获取上层业务调用者信息，用于日志，仅调试模式使用"""
    stack = inspect.stack()
    # stack[0]:GetCaller
    # stack[1]:调用函数
    # stack[2]:业务上层调用函数
    if len(stack) >=3:
        #frame.filename
        #frame.function
        #frame.lineno
        frame = stack[2]
        return frame
    return None


EMOJI = "✅ ❌ ⚠️ 🚧 ⏳ 🚫 🟢 🟡 🔴 ✨ 🐛 ♻️ ⚡ 🔥 🔧 🧪 📝 🚀 🎨 ℹ️ 💡 ❓ 🚨 🔔 📄 📊 📁 💾 🌐 🔄 🔒 🗃️"    