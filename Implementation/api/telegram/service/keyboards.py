from __future__ import annotations

MAIN_MENU_KEYBOARD = {
    "keyboard": [
        [{"text": "/login"}, {"text": "/status"}],
        [{"text": "/help"}, {"text": "/logout"}],
    ],
    "resize_keyboard": True,
    "one_time_keyboard": False,
}

LOGIN_KEYBOARD = {
    "keyboard": [[{"text": "/login"}]],
    "resize_keyboard": True,
    "one_time_keyboard": True,
}

STATUS_KEYBOARD = {
    "keyboard": [
        [{"text": "/status"}, {"text": "/logout"}],
    ],
    "resize_keyboard": True,
    "one_time_keyboard": False,
}