#!/bin/sh
set -eu

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"
ui_repo=${SUBLIME_CHAT_UI_REPO:-"https://github.com/yaroslavyaroslav/sublime-chat-ui.git"}
ref=${1:-main}

git subtree pull \
    --prefix=plugin/vendor/sublime_chat_ui \
    "$ui_repo" \
    "$ref" \
    --squash
