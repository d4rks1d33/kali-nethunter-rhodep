// Terminal-friendly Latin layout for the Plasma Mobile keyboard
// (motorola-rhodep). Derived from Qt's stock main.qml.
//
// Copyright (C) 2021 The Qt Company Ltd.
// SPDX-License-Identifier: LicenseRef-Qt-Commercial OR GPL-3.0-only
//
// Ctrl and Alt arm rather than type, the way Termux's extra keys row works, so
// any combination is reachable.
//
// How the combination is delivered, which took a protocol trace and a read of
// plasma-keyboard's source to get right. Passing Qt.ControlModifier to
// virtualKeyClick does nothing: src/inputlisteneritem.cpp sends
//
//     m_input.keysym(timestamp, key, InputPlugin::Pressed, 0);
//                                                          ^ hardcoded
//
// so the protocol's modifier field is always zero and no mask ever arrives. The
// terminal saw a bare "C". Holding Control_L down around the key does not work
// either: InputEngine delivers a key press on release, so the trace showed the
// letter going out first and Control_L after it.
//
// The same file shows the way through. A key whose event carries text is
// committed as a string instead:
//
//     if (event->text().isEmpty() || key == XKB_KEY_Return)
//         m_input.keysym(...);
//     else
//         m_input.commit(event->text());
//
// and Ctrl+C on a tty *is* a byte: 0x03. So these keys send the control
// character as text. The 0x40..0x5f block folds onto 0x00..0x1f, which is
// exactly what Ctrl does, and Alt is an Escape prefix, which is what every
// shell expects.
//
// What this cannot do is Ctrl+Shift+C for terminal copy: that is not a
// character, it is a combination the terminal interprets, and combinations are
// what the hardcoded zero above throws away. It needs a patched
// plasma-keyboard.
//
// The armed state is a property of the root object, not a ModeKey's own "mode":
// ModeKey does "onClicked: mode = !mode", so a binding to mode would work
// exactly once and then be overwritten.
//
// No digit row: the digits are one tap away on &123, and long press on q..p
// still reaches them through alternativeKeys.

import QtQuick
import QtQuick.VirtualKeyboard
import QtQuick.VirtualKeyboard.Components
import QtQuick.Layouts

KeyboardLayout {
    id: root
    inputMode: InputEngine.InputMode.Latin
    keyWeight: 160
    readonly property real normalKeyWidth: normalKey.width
    readonly property real functionKeyWidth: mapFromItem(normalKey, normalKey.width / 2, 0).x

    property bool ctrlArmed: false
    property bool altArmed: false

    function armed() { return ctrlArmed || altArmed }

    function disarm() {
        ctrlArmed = false
        altArmed = false
    }

    // The 0x40..0x5f block is what Ctrl folds onto 0x00..0x1f on a tty:
    // Ctrl+C is 0x03, Ctrl+[ is 0x1b (which is why Esc and Ctrl+[ are the same
    // key on a terminal), Ctrl+_ is 0x1f.
    function controlCharacter(keyCode) {
        if (keyCode >= 0x40 && keyCode <= 0x5f)
            return String.fromCharCode(keyCode & 0x1f)
        return ""
    }

    // Ctrl+V is left alone: it sends 0x16 like any other control character, and
    // zsh turns that into a clipboard paste - see userspace/terminal-clipboard.
    // Reading the clipboard here was tried and cannot work: the keyboard has no
    // keyboard focus, so KWin never sends it a selection offer. The trace showed
    // not one data_offer, and every read came back empty.
    // Sent as text, not as a modifier mask, because the mask is discarded on the
    // way out - see the note at the top of the file.
    function sendCombination(keyCode, plainText) {
        var out = plainText
        if (ctrlArmed) {
            var c = controlCharacter(keyCode)
            if (c !== "")
                out = c
        }
        if (altArmed)
            out = String.fromCharCode(0x1b) + out // Esc prefix: Alt on a tty
        disarm()
        if (out.length === 0)
            return
        InputContext.sendKeyClick(keyCode, out, 0)
    }

    // A character key that turns into part of a combination while a modifier is
    // armed: noKeyEvent stops it typing its own character, and clicked still
    // fires, which is what sends the combination.
    component TermKey: Key {
        noKeyEvent: root.armed()
        onClicked: {
            if (!root.armed())
                return
            root.sendCombination(key, uppercased ? text.toUpperCase() : text)
        }
    }

    // Esc, Tab, the modifiers and the arrows, in the same place on every page.
    KeyboardRow {
        Key { key: Qt.Key_Escape; displayText: "Esc"; functionKey: true; noModifier: true; weight: 210 }
        Key { key: Qt.Key_Tab; text: "\t"; displayText: "Tab"; functionKey: true; noModifier: true; weight: 210 }
        Key {
            displayText: "Ctrl"
            functionKey: true
            noKeyEvent: true
            noModifier: true
            weight: 210
            highlighted: root.ctrlArmed
            // A dot in the corner, so the label keeps its width.
            smallText: "\u25cf"
            smallTextVisible: root.ctrlArmed
            onClicked: root.ctrlArmed = !root.ctrlArmed
        }
        Key {
            displayText: "Alt"
            functionKey: true
            noKeyEvent: true
            noModifier: true
            weight: 210
            highlighted: root.altArmed
            // A dot in the corner, so the label keeps its width.
            smallText: "\u25cf"
            smallTextVisible: root.altArmed
            onClicked: root.altArmed = !root.altArmed
        }
        Key { key: Qt.Key_Left; displayText: "\u2190"; functionKey: true; noModifier: true; weight: 120 }
        Key { key: Qt.Key_Up; displayText: "\u2191"; functionKey: true; noModifier: true; weight: 120 }
        Key { key: Qt.Key_Down; displayText: "\u2193"; functionKey: true; noModifier: true; weight: 120 }
        Key { key: Qt.Key_Right; displayText: "\u2192"; functionKey: true; noModifier: true; weight: 120 }
    }
    KeyboardRow {
        TermKey {
            key: Qt.Key_Q
            text: "q"
            alternativeKeys: "q1"
            smallText: "1"
            smallTextVisible: true
        }
        TermKey {
            id: normalKey
            key: Qt.Key_W
            text: "w"
            alternativeKeys: "w2"
            smallText: "2"
            smallTextVisible: true
        }
        TermKey {
            key: Qt.Key_E
            text: "e"
            alternativeKeys: "êe3ëèé"
            smallText: "3"
            smallTextVisible: true
        }
        TermKey {
            key: Qt.Key_R
            text: "r"
            alternativeKeys: "ŕr4ř"
            smallText: "4"
            smallTextVisible: true
        }
        TermKey {
            key: Qt.Key_T
            text: "t"
            alternativeKeys: "ţt5ŧť"
            smallText: "5"
            smallTextVisible: true
        }
        TermKey {
            key: Qt.Key_Y
            text: "y"
            alternativeKeys: "ÿy6ýŷ"
            smallText: "6"
            smallTextVisible: true
        }
        TermKey {
            key: Qt.Key_U
            text: "u"
            alternativeKeys: "űūũûüu7ùú"
            smallText: "7"
            smallTextVisible: true
        }
        TermKey {
            key: Qt.Key_I
            text: "i"
            alternativeKeys: "îïīĩi8ìí"
            smallText: "8"
            smallTextVisible: true
        }
        TermKey {
            key: Qt.Key_O
            text: "o"
            alternativeKeys: "œøõôöòóo9"
            smallText: "9"
            smallTextVisible: true
        }
        TermKey {
            key: Qt.Key_P
            text: "p"
            alternativeKeys: "p0"
            smallText: "0"
            smallTextVisible: true
        }
    }
    KeyboardRow {
        KeyboardRow {
            Layout.preferredWidth: functionKeyWidth
            Layout.fillWidth: false
            FillerKey {
            }
            TermKey {
                key: Qt.Key_A
                text: "a"
                alternativeKeys: "aäåãâàá"
                weight: normalKeyWidth
                Layout.fillWidth: false
            }
        }
        TermKey {
            key: Qt.Key_S
            text: "s"
            alternativeKeys: "šsşś"
        }
        TermKey {
            key: Qt.Key_D
            text: "d"
            alternativeKeys: "dđď"
        }
        TermKey {
            key: Qt.Key_F
            text: "f"
        }
        TermKey {
            key: Qt.Key_G
            text: "g"
            alternativeKeys: "ġgģĝğ"
        }
        TermKey {
            key: Qt.Key_H
            text: "h"
        }
        TermKey {
            key: Qt.Key_J
            text: "j"
        }
        TermKey {
            key: Qt.Key_K
            text: "k"
        }
        KeyboardRow {
            Layout.preferredWidth: functionKeyWidth
            Layout.fillWidth: false
            TermKey {
                key: Qt.Key_L
                text: "l"
                alternativeKeys: "ĺŀłļľl"
                weight: normalKeyWidth
                Layout.fillWidth: false
            }
            FillerKey {
            }
        }
    }
    KeyboardRow {
        ShiftKey {
            weight: functionKeyWidth
            Layout.fillWidth: false
        }
        TermKey {
            key: Qt.Key_Z
            text: "z"
            alternativeKeys: "zžż"
        }
        TermKey {
            key: Qt.Key_X
            text: "x"
        }
        TermKey {
            key: Qt.Key_C
            text: "c"
            alternativeKeys: "çcċčć"
        }
        TermKey {
            key: Qt.Key_V
            text: "v"
        }
        TermKey {
            key: Qt.Key_B
            text: "b"
        }
        TermKey {
            key: Qt.Key_N
            text: "n"
            alternativeKeys: "ņńnň"
        }
        TermKey {
            key: Qt.Key_M
            text: "m"
        }
        BackspaceKey {
            weight: functionKeyWidth
            Layout.fillWidth: false
        }
    }
    KeyboardRow {
        SymbolModeKey {
            weight: functionKeyWidth
            Layout.fillWidth: false
        }
        ChangeLanguageKey {
            weight: normalKeyWidth
            Layout.fillWidth: false
        }
        Key {
            key: Qt.Key_Comma
            weight: normalKeyWidth
            Layout.fillWidth: false
            text: ","
            smallText: "\u2699"
            smallTextVisible: keyboard.isFunctionPopupListAvailable()
            highlighted: true
        }
        SpaceKey {
        }
        Key {
            key: Qt.Key_Period
            weight: normalKeyWidth
            Layout.fillWidth: false
            text: "."
            alternativeKeys: "!.?"
            smallText: "!?"
            smallTextVisible: true
            highlighted: true
        }
        HideKeyboardKey {
            weight: normalKeyWidth
            Layout.fillWidth: false
        }
        EnterKey {
            weight: functionKeyWidth
            Layout.fillWidth: false
        }
    }
}
