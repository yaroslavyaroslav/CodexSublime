#!/bin/sh
set -eu

repo_root=$(git rev-parse --show-toplevel)
ui_repo=${SUBLIME_CHAT_UI_REPO:-"$repo_root/../sublime-chat-ui"}
ref=${1:-main}

git subtree pull \
    --prefix=plugin/vendor/sublime_chat_ui \
    "$ui_repo" \
    "$ref" \
    --squash

