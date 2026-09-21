from .awareness import register as register_awareness_tools
from .home_automation import register as register_home_automation_tools
from .sleep_mode import register as register_sleep_mode_tools
from .smart_plugs import register as register_smart_plug_tools
from .tv_control import register as register_tv_control_tools

__all__ = [
    "register_awareness_tools",
    "register_home_automation_tools",
    "register_sleep_mode_tools",
    "register_smart_plug_tools",
    "register_tv_control_tools",
]
