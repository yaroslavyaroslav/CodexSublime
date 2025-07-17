import sys

# clear modules cache if package is reloaded (after update?)
prefix = __package__ + '.plugins'  # type: ignore # don't clear the base package
for module_name in [module_name for module_name in sys.modules if module_name.startswith(prefix)]:
    del sys.modules[module_name]
del prefix

from .plugin.commands import (  # noqa: E402
    CodexPromptCommand,  # noqa: E402, F401
    CodexSubmitInputPanelCommand,  # noqa: E402, F401
    CodexOpenTranscriptCommand,  # noqa: E402, F401
    CodexResetChatCommand,  # noqa: E402, F401
)
from .plugin.lifecycle import CodexWindowEventListener  # noqa: E402, F401
