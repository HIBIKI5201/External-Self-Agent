"""Automatic pet hiding is independent of enabled settings and foreground focus (#35)."""
from unittest.mock import Mock, patch

import pet
import winfocus


def test_chat_window_visibility_handles_minimized_multiple_and_other_windows():
    windows = {1: ['E.S. Agent', True, True, 'Chrome_WidgetWin_1'],
               2: ['12:34 集中 – E.S. Agent', True, False, 'Chrome_WidgetWin_1'],
               3: ['Other app', True, False, 'Chrome_WidgetWin_1']}
    def enum(callback, data):
        for hwnd in windows:
            if not callback(hwnd, data):
                break
    api = Mock()
    api.EnumWindows.side_effect = enum
    api.IsWindowVisible.side_effect = lambda hwnd: windows[hwnd][1]
    api.IsIconic.side_effect = lambda hwnd: windows[hwnd][2]
    with patch.object(winfocus, 'user32', api), \
            patch.object(winfocus, '_title', lambda hwnd: windows[hwnd][0]), \
            patch.object(winfocus, '_class', lambda hwnd: windows[hwnd][3]):
        assert winfocus.find_chat_window() == 1  # focusing can restore a minimized window
        assert winfocus.chat_window_visible()  # second non-minimized chat window
        windows[2][2] = True
        assert not winfocus.chat_window_visible()
        windows[2][2], windows[2][1] = False, False
        assert not winfocus.chat_window_visible()
        windows[1][2], windows[1][3] = False, 'OtherClass'
        assert not winfocus.chat_window_visible()
        windows[1][3] = 'Chrome_WidgetWin_1'
        assert winfocus.chat_window_visible()
        api.GetForegroundWindow.assert_not_called()


def test_pet_hides_and_restores_without_rewriting_settings_or_replaying_bubbles():
    cat = pet.Pet.__new__(pet.Pet)
    cat.root = Mock()
    cat._hidden = False
    cat.bubble = Mock()
    bubble = cat.bubble
    with patch.object(winfocus, 'chat_window_visible', return_value=True), \
            patch.object(pet.db, 'save_config') as save:
        cat._visibility_tick()
        assert cat._hidden and cat.bubble is None
        bubble.destroy.assert_called_once()
        cat.root.withdraw.assert_called_once()
        cat.say('hidden greeting')  # no Tk or speech window while hidden
        cat._visibility_tick()
        cat.root.withdraw.assert_called_once()
        save.assert_not_called()
    with patch.object(winfocus, 'chat_window_visible', return_value=False):
        cat._visibility_tick()
        assert not cat._hidden
        cat.root.deiconify.assert_called_once()
        cat._visibility_tick()
        cat.root.deiconify.assert_called_once()



def test_snooze_survives_the_chat_window_and_waits_for_it_to_close():
    """「30分かくれる」 is not undone by opening and closing the chat window, and does not bring the
    cat back over a shown chat window (#251)."""
    cat = pet.Pet.__new__(pet.Pet)
    cat.root = Mock()
    cat._hidden = False
    cat.bubble = None
    now = [1000.0]
    with patch.object(pet.time, 'time', lambda: now[0]):
        with patch.object(winfocus, 'chat_window_visible', return_value=False):
            cat._hide()
            assert cat._hidden
            cat.root.withdraw.assert_called_once()
            cat.say('締切の知らせ')  # no bubble while snoozed
        with patch.object(winfocus, 'chat_window_visible', return_value=True):
            cat._visibility_tick()  # the chat window opens...
        with patch.object(winfocus, 'chat_window_visible', return_value=False):
            cat._visibility_tick()  # ...and closes: still snoozed
            assert cat._hidden
            cat.root.deiconify.assert_not_called()
        now[0] += 30 * 60 + 1
        with patch.object(winfocus, 'chat_window_visible', return_value=True):
            cat._visibility_tick()  # time is up, but the chat window is shown
            assert cat._hidden
            cat.root.deiconify.assert_not_called()
        with patch.object(winfocus, 'chat_window_visible', return_value=False):
            cat._visibility_tick()
            assert not cat._hidden
            cat.root.deiconify.assert_called_once()
    cat.root.after.assert_called_with(500, cat._visibility_tick)
