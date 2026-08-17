// Terminal-friendly Latin layout for the Plasma Mobile keyboard
// (motorola-rhodep). Derived from Qt's stock main.qml.
//
// Copyright (C) 2021 The Qt Company Ltd.
// SPDX-License-Identifier: LicenseRef-Qt-Commercial OR GPL-3.0-only
//
// Two additions over the stock layout: a row of digits, and a row with Esc,
// Tab, Ctrl, Alt and the arrows.
//
// Ctrl and Alt are modifiers that arm rather than type, the way Termux's extra
// keys row works, so Ctrl + Shift + C is composed with the keyboard's own Shift
// key and *any* combination is reachable - not just the handful a dedicated key
// could cover. They are ModeKeys, which the keyboard style shows as active, and
// they disarm after one use.
//
// The state lives in the ModeKeys themselves because ModeKey does
// "onClicked: mode = !mode": binding mode to an outer property would work once
// and then be overwritten.

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

    function armed() { return ctrlKey.mode || altKey.mode }

    // Shift comes from the keyboard's own Shift key, so Ctrl + Shift + C is
    // typed the way it would be on a hardware keyboard.
    function heldModifiers() {
        var m = 0
        if (ctrlKey.mode)
            m |= Qt.ControlModifier
        if (altKey.mode)
            m |= Qt.AltModifier
        if (InputContext.shiftActive)
            m |= Qt.ShiftModifier
        return m
    }

    function disarm() {
        ctrlKey.mode = false
        altKey.mode = false
    }

    // A character key that becomes part of a combination while Ctrl or Alt is
    // armed. noKeyEvent stops it typing its own character; clicked still fires,
    // which is what carries the combination.
    component TermKey: Key {
        noKeyEvent: root.armed()
        onClicked: {
            if (!root.armed())
                return
            InputContext.inputEngine.virtualKeyClick(key, "", root.heldModifiers())
            root.disarm()
        }
    }

    KeyboardRow {
        TermKey { key: Qt.Key_1; text: "1" }
        TermKey { key: Qt.Key_2; text: "2" }
        TermKey { key: Qt.Key_3; text: "3" }
        TermKey { key: Qt.Key_4; text: "4" }
        TermKey { key: Qt.Key_5; text: "5" }
        TermKey { key: Qt.Key_6; text: "6" }
        TermKey { key: Qt.Key_7; text: "7" }
        TermKey { key: Qt.Key_8; text: "8" }
        TermKey { key: Qt.Key_9; text: "9" }
        TermKey { key: Qt.Key_0; text: "0" }
    }
    KeyboardRow {
        Key { key: Qt.Key_Escape; displayText: "Esc"; functionKey: true }
        Key { key: Qt.Key_Tab; text: "\t"; displayText: "Tab"; functionKey: true }
        ModeKey { id: ctrlKey; displayText: "Ctrl" }
        ModeKey { id: altKey; displayText: "Alt" }
        Key { key: Qt.Key_Left; displayText: "\u2190"; functionKey: true }
        Key { key: Qt.Key_Up; displayText: "\u2191"; functionKey: true }
        Key { key: Qt.Key_Down; displayText: "\u2193"; functionKey: true }
        Key { key: Qt.Key_Right; displayText: "\u2192"; functionKey: true }
    }
    KeyboardRow {
        TermKey {
            key: Qt.Key_Q
            text: "q"
            alternativeKeys: "q1"
        }
        TermKey {
            id: normalKey
            key: Qt.Key_W
            text: "w"
            alternativeKeys: "w2"
        }
        TermKey {
            key: Qt.Key_E
            text: "e"
            alternativeKeys: "êe3ëèé"
        }
        TermKey {
            key: Qt.Key_R
            text: "r"
            alternativeKeys: "ŕr4ř"
        }
        TermKey {
            key: Qt.Key_T
            text: "t"
            alternativeKeys: "ţt5ŧť"
        }
        TermKey {
            key: Qt.Key_Y
            text: "y"
            alternativeKeys: "ÿy6ýŷ"
        }
        TermKey {
            key: Qt.Key_U
            text: "u"
            alternativeKeys: "űūũûüu7ùú"
        }
        TermKey {
            key: Qt.Key_I
            text: "i"
            alternativeKeys: "îïīĩi8ìí"
        }
        TermKey {
            key: Qt.Key_O
            text: "o"
            alternativeKeys: "œøõôöòóo9"
        }
        TermKey {
            key: Qt.Key_P
            text: "p"
            alternativeKeys: "p0"
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
